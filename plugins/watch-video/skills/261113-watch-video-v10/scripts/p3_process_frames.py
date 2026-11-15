#!/usr/bin/env python3
"""p3_process_frames.py · Pass 5: contact sheets for a whole drive, from the Pass 4 drive ledger.

Reads <YYMMDD>_<drive>_ledger_media_P2-01_<HHMM>.csv (p2_ledger_media.py), picks the clips worth seeing, groups them
(one group per camera card / RED mag / shoot day, else per folder), pulls 2-3 frames per clip
with ffmpeg (POOL-SHEET MODE of references/watch-method.md; the keyframe formula is `keyframes` in
rules.json and nowhere else: HEAD, MID, and TAIL for long clips), writes one WATCH_STATE.json
per group, and renders each group with the watch method's renderer (scripts/render_html.py:
WeasyPrint -> PDF -> paged JPGs). Every cell carries the clip's filename (the filename-per-cell law).

State shape (contract: references/rules-and-objects.md): one scene per clip (SM01, SM02 ...), one
shot per keyframe with the FULL label as id (SM01-HEAD, SM01-MID, SM01-TAIL). Every shot carries
src (absolute clip path), t, file (clip file name), label, and an ABSOLUTE frame path; the scene
carries path/file/rel_path. Frame files are <scene>_<LABEL>_<sha1(src)[:8]>_<t ms>.jpg in
<group>/frames (unique per source and time, as in pool_frames.py). `desc` stays empty until a
watch writes one; the ledger's format line is `facts` (printed on the cell).

Rerun (contract section 7): a shot whose frame is unchanged keeps machine, macros and desc; a shot
whose frame changed drops machine/macros, sets verified=False and semantic_check="not_run", clears
witness_flag and moves desc to desc_stale; --force re-pulls everything and drops machine/macros
(desc is not staled). An existing state that fails to parse aborts with exit 2 and is never
replaced; only a missing file starts a new state. A pull that fails at the asked-for time is
retried at 0 s (seek_fallback: true; t records the time actually pulled, t_want the asked-for one).

Read-only on the drive: ffmpeg only reads clips. Everything is written under
<catalog>/<drive>/P3_PROCESS/<group>/ plus one LEDGER.csv row.

Usage:
  python3 p3_process_frames.py --ledger <drive>_ledger_<stamp>.csv [--catalog DIR] [--stamp yymmdd_hhmm]
                          [--group auto|folder] [--only TEXT] [--max-clips N] [--include-derived]
                          [--per-page 6] [--no-render] [--render-only] [--rules rules.json] [--force]
--only TEXT     only groups whose name contains TEXT (case-insensitive): use it for a one-group proof first.
--max-clips N   cap clips per group (proof runs). A group's state mirrors the clips selected in this run.
--no-render     pull frames and write state only (no WeasyPrint needed).
--render-only   render the existing group states (no frame pulls, no ffmpeg): run it after
                p4_staging_objects.py build so the chip line shows the macros.
--rules FILE    keyframes (points, tail, tail_min_s) from this rules.json (default: the skill's own;
                the built-in fallback, if that file is missing, has the same values).
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import p0_env                     # noqa: E402  (catalog, naming, temp: one place)
import p3b_driver_red                  # noqa: E402  (RED stills through REDline)
import pool_frames as pf          # noqa: E402  (keyframes formula, label check, DEFAULT_KF: one place for the rules)

SCRIPT = "p3_process_frames.py"
VERSION = "2"

# ---- rule table -------------------------------------------------------------------------------
SKIP_LABELS = {"SYSTEM", "TRASH", "APP_MANAGED"}          # never sheeted
DERIVED_LABELS = {"DERIVED"}                               # proxies/transcodes: only with --include-derived
PENDING_EXTS = {"r3d": "needs RED decoder", "braw": "needs Blackmagic decoder"}
PENDING = "PENDING"                                        # the ledger's marker for "no data yet"
# Keyframe numbers live in rules.json -> keyframes (points, tail, tail_min_s); the built-in fallback is
# pool_frames.DEFAULT_KF (same numbers as rules.json v1).
DEFAULT_RULES = HERE.parent / "rules.json"
FRAME_WIDTH = 640
POOL_MODES = ("pool", "pool-sheet")                        # states this script may update (v9 wrote pool-sheet)
LEDGER_HEADER = ["time_utc", "drive", "pass", "worker", "inputs", "output", "in", "out",
                 "balance", "secs", "cost", "status"]
# ------------------------------------------------------------------------------------------------


def load_rules(path=None):
    """(rules dict, note). path=None -> the skill's own rules.json, falling back to built-in values;
    an explicit path must exist and parse (ValueError otherwise)."""
    if path:
        try:
            r = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ValueError("cannot read rules %s: %s" % (path, e))
        if not isinstance(r, dict):
            raise ValueError("rules %s is not a JSON object" % path)
        return r, str(path)
    if DEFAULT_RULES.is_file():
        try:
            r = json.loads(DEFAULT_RULES.read_text(encoding="utf-8"))
            if isinstance(r, dict):
                return r, str(DEFAULT_RULES)
        except (OSError, ValueError):
            print("WARNING: %s does not parse; using built-in keyframes" % DEFAULT_RULES, file=sys.stderr)
    return {}, "built-in"


def slug(text, n=60):
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")
    return (s or "root")[:n]


def hms(seconds):
    try:
        s = int(round(float(seconds)))
    except (TypeError, ValueError):
        return ""
    return "%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60)


def mmss(seconds):
    s = int(round(seconds))
    return "%d:%02d" % (s // 60, s % 60)


def ledger_append(catalog, row):
    path = Path(catalog) / "LEDGER.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    clean = [re.sub(r"[\t\r\n]", " ", str(v)) for v in row]
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL, lineterminator="\n")
        if new:
            f.write(",".join(LEDGER_HEADER) + "\n")
        w.writerow(clean)


def group_of(row, mode):
    ctx = (row.get("context") or "").strip()
    if mode == "auto" and ctx and ctx != "unknown":
        return ctx
    return row.get("folder") or "."


def pick(rows, include_derived):
    """Split ledger rows into (clips to sheet, pending, skipped-with-reason).
    pending = needs a decoder (RED/BRAW) or the ledger holds no data yet (probe_status needs_scan / PENDING)."""
    clips, pending, skipped = [], [], []
    for r in rows:
        if r.get("kind") != "video":
            continue
        label = r.get("label") or ""
        ext = (r.get("ext") or "").lower()
        if label in SKIP_LABELS:
            skipped.append((r, "label " + label))
        elif label in DERIVED_LABELS and not include_derived:
            skipped.append((r, "derived (proxy/transcode); use --include-derived"))
        elif ext == "r3d" and "RED segment of" in (r.get("note") or ""):
            skipped.append((r, "RED segment (the clip is sheeted from its first segment)"))
        elif ext == "r3d" and (r.get("probe_status") or "") == "ok" and (r.get("duration_s") or "").replace(".", "", 1).isdigit():
            clips.append(r)                                  # read by REDline in Pass 4: stills come from REDline
        elif ext in PENDING_EXTS:
            pending.append((r, (r.get("note") or "").strip() or PENDING_EXTS[ext]))
        elif (r.get("probe_status") or "") == "needs_scan" or (r.get("duration_s") or "").strip() == PENDING:
            pending.append((r, (r.get("note") or "").strip() or "no probe data"))
        else:
            try:
                dur = float(r.get("duration_s") or 0)
            except ValueError:
                dur = 0.0
            if dur <= 0:
                skipped.append((r, "no duration in the ledger (probe %s)" % (r.get("probe_status") or "missing")))
            else:
                clips.append(r)
    return clips, pending, skipped


def frame_times(duration, rules=None):
    """[(LABEL, seconds)] from rules.json -> keyframes (HEAD/MID, plus TAIL for long clips)."""
    cap = max(duration - 0.05, 0.0)
    return [(label, round(min(max(t, 0.0), cap), 2)) for label, t in pf.keyframes(rules or {}, duration)]


def kf_text(rules):
    kf = dict(pf.DEFAULT_KF)
    kf.update((rules or {}).get("keyframes") or {})
    pts = ["%s %d%%" % (p[0], round(float(p[1]) * 100)) for p in kf["points"]]
    if kf.get("tail"):
        pts.append("%s %d%% on clips of %g s or more" % (kf["tail"][0], round(float(kf["tail"][1]) * 100),
                                                         float(kf.get("tail_min_s", 12))))
    return ", ".join(pts)


def frame_name(scene_id, label, src, t, recipe=""):
    """<scene>_<LABEL>_<sha1(src)[:8]>_<ms>.jpg: the same name rule as pool_frames.py.
    v17: a render recipe (the RED color rule for .r3d, "" otherwise) adds _c<sha1[:6]>, so a rule change
    gives a new name, the "same" check fails, and only those clips re-pull (no --force needed)."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", label)
    h8 = hashlib.sha1(str(src).encode("utf-8")).hexdigest()[:8]
    tail = "_c%s" % hashlib.sha1(recipe.encode("utf-8")).hexdigest()[:6] if recipe else ""
    return "%s_%s_%s_%d%s.jpg" % (scene_id, safe, h8, int(round(t * 1000)), tail)


def render_recipe(src):
    """The recipe string a frame name hashes: the RED color rule for .r3d, "" for everything else."""
    if not str(src).lower().endswith(".r3d"):
        return ""
    try:
        import p3b_driver_red
        return p3b_driver_red.color_recipe()
    except Exception:
        return ""


def _pull(src, ss, out):
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", str(ss), "-i", str(src),
           "-frames:v", "1", "-vf", "scale=%d:-2" % FRAME_WIDTH, "-q:v", "3", "-y", str(out)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return False, "ffmpeg timeout", True
    if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        msg = (r.stderr.strip().splitlines() or ["exit %d" % r.returncode])[-1]
        return False, msg[:160], False
    return True, "", False


RED_FPS = {}   # source path -> fps, for RED clips (stills come from REDline, never ffmpeg)


def pull_frame(src, t, out):
    """(ok, used_fallback, message). A pull that yields nothing at t is retried at 0 s (seek_fallback).
    RED (.R3D): one REDline still at frame round(t * fps); no fallback (a wrong time is worse than none)."""
    if str(src).lower().endswith(".r3d"):
        ok, err = p3b_driver_red.still(src, t, out, RED_FPS.get(str(src)))
        return (True, False, "") if ok else (False, False, err or "REDline still failed")
    ok, msg, timed_out = _pull(src, t, out)
    if ok:
        return True, False, ""
    if timed_out:
        return False, False, msg
    ok2, _, _ = _pull(src, 0, out)
    return (True, True, "") if ok2 else (False, False, msg)


def caption(r):
    bits = [hms(r.get("duration_s"))]
    v = " ".join(x for x in [r.get("video_codec"),
                             ("%sx%s" % (r.get("width"), r.get("height"))) if r.get("width") else "",
                             (r.get("fps") + " fps") if r.get("fps") else ""] if x)
    if v:
        bits.append(v)
    if r.get("audio_channels"):
        bits.append("audio %sch %s" % (r.get("audio_channels"), r.get("sample_rate") or ""))
    if r.get("start_timecode"):
        bits.append("TC " + r["start_timecode"])
    if r.get("card_family"):
        bits.append(r["card_family"])
    return " · ".join(b.strip() for b in bits if b and b.strip())


def load_state(path):
    """The existing state, or None when the file is missing. ValueError when it exists but is unusable:
    it is never replaced (only a missing file starts a new state)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        raise ValueError("state %s cannot be read (%s); refusing to replace it" % (path, e))
    try:
        st = json.loads(text)
    except ValueError as e:
        raise ValueError("state %s does not parse (%s); refusing to replace it" % (path, e))
    if not isinstance(st, dict) or not isinstance(st.get("scenes", []), list):
        raise ValueError("state %s is not a watch state object; refusing to replace it" % path)
    if st.get("mode") not in POOL_MODES:
        raise ValueError("state %s has mode %r, not a pool state; refusing to replace it" % (path, st.get("mode")))
    return st


def stale_shot(sh):
    """The picture changed (contract section 7): the old analysis and description no longer describe it."""
    sh.pop("machine", None)
    sh.pop("macros", None)
    sh["verified"] = False
    sh["semantic_check"] = "not_run"
    sh.pop("witness_flag", None)
    if (sh.get("desc") or "").strip():
        sh["desc_stale"] = sh["desc"]
        sh["desc"] = ""


def build_group(drive, group, clips, root, gdir, rules, force, pending, prev):
    """Pull the keyframes of one group and return (state dict, frames pulled or kept, error list, clips with no frame)."""
    fdir = gdir / "frames"
    fdir.mkdir(parents=True, exist_ok=True)
    # F1: a sheet made on one machine (e.g. the Linux box) must carry over on another (the Mac): previous shots
    # are found by the clip's drive-relative path, never by an absolute machine path.
    prev_by_path = {}
    for sc in (prev or {}).get("scenes", []):
        if sc.get("rel_path"):
            prev_by_path[sc["rel_path"]] = sc
    srcs = [os.path.abspath(str(root / r["rel_path"])) for r in clips]
    rels = {s_: r["rel_path"] for r, s_ in zip(clips, srcs)}
    used = {prev_by_path[rels[s_]]["id"] for s_ in srcs if rels[s_] in prev_by_path}
    ids = {}
    for i, s_ in enumerate(srcs, 1):
        if rels[s_] in prev_by_path:
            ids[s_] = prev_by_path[rels[s_]]["id"]
        else:
            k = i
            while "SM%02d" % k in used:
                k += 1
            used.add("SM%02d" % k)
            ids[s_] = "SM%02d" % k
    scenes, nframes, errs, no_frame = [], 0, [], 0
    total = sum(float(c.get("duration_s") or 0) for c in clips)
    for r, src in zip(clips, srcs):
        dur = float(r["duration_s"])
        if src.lower().endswith(".r3d"):
            try:
                RED_FPS[src] = float(r.get("fps") or 0) or None
            except ValueError:
                RED_FPS[src] = None
        name = r.get("file_name") or ""
        old_sc = prev_by_path.get(r["rel_path"])
        sid = ids[src]
        have = {sh.get("id"): sh for sh in (old_sc or {}).get("shots", [])}
        shots, got = [], 0
        for label, t in frame_times(dur, rules):
            shot_id = "%s-%s" % (sid, label)
            jpg = fdir / frame_name(sid, label, "%s/%s" % (drive, r["rel_path"]), t,
                                    render_recipe(src))   # same name on every machine; recipe hash re-pulls RED on a rule change
            jrel = "frames/" + jpg.name                                                  # stored relative to the sheet
            old = have.get(shot_id)
            old_t = old.get("t_want", old.get("t")) if old else None
            same = (old is not None and isinstance(old_t, (int, float))
                    and abs(old_t - t) < 1e-6 and old.get("frame") in (jrel, str(jpg)))
            if same and jpg.is_file() and jpg.stat().st_size > 0 and not force:
                sh = old
            else:
                ok, fb, msg = pull_frame(src, t, jpg)
                if not ok:
                    errs.append("%s: %s" % (name, msg))
                    continue
                sh = dict(old) if old else {"id": shot_id, "type": "", "desc": "", "verified": False,
                                            "semantic_check": "not_run"}
                if old and (not same or force):
                    sh.pop("machine", None)
                    sh.pop("macros", None)
                if old and not same:
                    stale_shot(sh)
                at = 0.0 if fb else t
                sh.update({"tc_in": at, "tc_out": at, "t": at, "tc": mmss(at), "frame": jrel})
                if fb:
                    sh["seek_fallback"] = True
                    sh["t_want"] = t
                else:
                    sh.pop("seek_fallback", None)
                    sh.pop("t_want", None)
            sh["frame"] = jrel                       # relative to the sheet folder (contract: resolve against the state's folder)
            sh.update({"id": shot_id, "label": label, "type": "", "scene": sid, "src": src, "file": name,
                       "facts": caption(r)})        # type stays empty as in pool_frames.py: it is the shot SIZE slot (WS/MS/CU)
            shots.append(sh)
            got += 1
        nframes += got
        if not got:
            no_frame += 1
        if shots:
            sc = dict(old_sc) if old_sc else {}
            sc.update({"id": sid, "slug": name or sid, "file": name, "path": src, "rel_path": r.get("rel_path"),
                       "tc_in": 0.0, "tc_out": round(dur, 3), "shots": shots})
            scenes.append(sc)
    scenes.sort(key=lambda x: (len(x["id"]), x["id"]))
    codecs = {}
    for r in clips:
        key = " ".join(x for x in [r.get("video_codec"), ("%sx%s" % (r.get("width"), r.get("height"))) if r.get("width") else "",
                                   (r.get("fps") + " fps") if r.get("fps") else ""] if x) or "unknown"
        codecs[key] = codecs.get(key, 0) + 1
    fam = sorted({r.get("card_family") for r in clips if r.get("card_family")})
    macro = dict((prev or {}).get("macro") or {})
    macro.update({
        "logline": "Drive contact sheet · %s · %s" % (drive, group),
        "synopsis": "%d clips, %s total, %d frames (%s)." % (len(scenes), hms(total), nframes, kf_text(rules)),
        "intent": "Pass 5 pool sheet: see what is on this %s before anyone picks a shot. Frames only; "
                  "descriptions and register flags come from a watch (references/watch-method.md)." % ("card" if fam else "folder"),
        "structure": "Formats: " + "; ".join("%s × %d" % (k, v) for k, v in sorted(codecs.items())),
        "visual": ("Card family: " + ", ".join(fam)) if fam else "",
        "credits": "Generated by %s@%s from the drive ledger; not yet watched (semantic_check not_run)." % (SCRIPT, VERSION),
    })
    for k in ("existing_track", "pacing"):
        macro.pop(k, None)
    if pending:
        macro["existing_track"] = "Not shown: %d clip(s) pending (%s)." % (
            len(pending), ", ".join(sorted({why for _, why in pending})))
    if errs:
        macro["pacing"] = "Frame errors on %d clip(s): %s" % (len(errs), "; ".join(errs[:5]))
    state = dict(prev) if prev else {"probe": {}, "frames": {}, "coverage": []}
    state.update({"job": "%s · %s" % (drive, group), "seq": "P5", "lens": "drive-sheet", "case_use": "ingest",
                  "renderer": "weasyprint", "mode": "pool", "video": {"duration": total}, "macro": macro,
                  "scenes": scenes})
    state.setdefault("deliverables", [])
    return state, nframes, errs, no_frame


def write_state(path, state):
    """A plain write (F3: mounted catalogs such as Cowork's ~/mnt/Downloads refuse renames and deletes).
    The JSON is built in memory first, so a failed dump never leaves half a file."""
    text = json.dumps(state, indent=2, ensure_ascii=False)
    Path(path).write_text(text, encoding="utf-8")


def find_root(catalog, drive, rows):
    """The drive root: from the newest Pass 3 TSV (absolute paths) matched against the ledger's rel_paths,
    else /Volumes/<drive>."""
    rel = {r["rel_path"] for r in rows if r.get("rel_path")}
    newest_tsv = p0_env.newest(catalog, drive, "P1", "scan", "files", "tsv")
    tsvs = [newest_tsv] if newest_tsv else []
    if tsvs and rel:
        with open(tsvs[-1], encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) != 3:
                    continue
                for rp in rel:
                    if parts[2].endswith("/" + rp):
                        cand = Path(parts[2][: -len(rp) - 1])
                        if cand.is_dir():                 # F1: the scan may come from another machine (Linux box)
                            return cand
                        break
                else:
                    continue
                break
    for base in ["/Volumes"] + p0_env.drive_roots(p0_env.detect_profile()[0]):   # A2: the profile's drive folders
        cand = Path(base) / drive
        if cand.is_dir():
            return cand
    return None


def render(state_path, group_dir, out_name, per_page):
    """Each render writes into a new pages_<YYMMDD>_<HHMM>[_n] folder beside the state: no page is ever
    overwritten or deleted in the catalog (F3), so stale pages cannot mix in. P5 takes the newest folder."""
    d, t = p0_env.stamp()
    pdir, n = Path(group_dir) / ("pages_%s_%s" % (d, t)), 1
    while pdir.exists():
        n += 1
        pdir = Path(group_dir) / ("pages_%s_%s_%d" % (d, t, n))
    pdir.mkdir(parents=True)
    cmd = [sys.executable, str(HERE / "render_html.py"), str(Path(state_path).resolve()), "--out", out_name,
           "--per-page", str(per_page)]
    r = subprocess.run(cmd, cwd=str(pdir), capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        return [], (r.stderr.strip().splitlines() or ["exit %d" % r.returncode])[-1][:200]
    try:
        info = json.loads(r.stdout.strip().splitlines()[-1])
        return info.get("pages", []), ""
    except (ValueError, IndexError):
        return [], "renderer output not understood"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--catalog", default=str(p0_env.catalog()))
    ap.add_argument("--stamp", default=dt.datetime.now().strftime("%y%m%d_%H%M"))
    ap.add_argument("--group", choices=["auto", "folder"], default="auto")
    ap.add_argument("--only", default=None)
    ap.add_argument("--max-clips", type=int, default=0)
    ap.add_argument("--include-derived", action="store_true")
    ap.add_argument("--per-page", type=int, default=36)   # v17 house layout: Letter landscape 6x6
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--rules", default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    t0 = time.time()

    if a.render_only and (a.no_render or a.force):
        print("sheets: ERROR --render-only cannot be combined with --no-render or --force", file=sys.stderr)
        return 2
    if not a.render_only and not shutil.which("ffmpeg"):
        print("sheets: ERROR ffmpeg not found (requirement). Install with: brew install ffmpeg", file=sys.stderr)
        return 2
    try:
        rules, rules_src = load_rules(a.rules)
        pf.labels_of(rules)
    except ValueError as e:
        print("sheets: ERROR %s" % e, file=sys.stderr)
        return 2
    ledger = Path(a.ledger).expanduser()
    with open(ledger, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("sheets: ERROR ledger is empty: %s" % ledger, file=sys.stderr)
        return 2
    pn = p0_env.parse_name(ledger.name)
    drive = rows[0].get("drive") or (pn["drive"] if pn else ledger.name.split("_ledger_")[0])
    catalog = Path(os.path.abspath(os.path.expanduser(a.catalog)))
    root = None
    if not a.render_only:
        root = find_root(catalog, drive, rows)
        if root is None or not root.is_dir():
            print("sheets: ERROR cannot find the drive root for %s (is it mounted?)" % drive, file=sys.stderr)
            return 2
        try:
            if catalog.resolve() == root.resolve() or root.resolve() in catalog.resolve().parents:
                print("refused: catalog %s is inside the drive %s" % (catalog, root), file=sys.stderr)
                return 2
        except OSError:
            pass

    clips, pending, skipped = pick(rows, a.include_derived)
    groups = {}
    for r in clips:
        groups.setdefault(group_of(r, a.group), []).append(r)
    pend_by = {}
    for r, why in pending:
        pend_by.setdefault(group_of(r, a.group), []).append((r, why))
    names = sorted(set(groups) | set(pend_by))
    if a.only:
        names = [g for g in names if p0_env.matches(a.only, g)]

    base = catalog / drive / "P3_PROCESS"
    gdirs = {g: base / slug(g if g != "." else "drive_root") for g in names}
    prevs = {}
    for g in names:                       # an unusable existing state aborts the run before anything is written
        try:
            prevs[g] = load_state(gdirs[g] / "WATCH_STATE.json")
        except ValueError as e:
            print("sheets: ERROR %s" % e, file=sys.stderr)
            return 2
    totals = dict(groups=0, clips=0, frames=0, pending=len([1 for g in names for _ in pend_by.get(g, [])]),
                  errors=0, pages=0)
    index_rows = []
    for g in names:
        gdir = gdirs[g]
        out_name = slug(drive + "_" + (g if g != "." else "root"), 80)
        errs = []
        if a.render_only:
            state = prevs[g]
            if state is None:
                print("  group: %s  no state yet (run p3_process_frames.py without --render-only first)" % g)
                totals["errors"] += 1
                continue
        else:
            gclips = sorted(groups.get(g, []), key=lambda r: r.get("rel_path", ""))
            if a.max_clips:
                gclips = gclips[: a.max_clips]
            gdir.mkdir(parents=True, exist_ok=True)
            state, _, errs, no_frame = build_group(drive, g, gclips, root, gdir, rules, a.force,
                                                   pend_by.get(g, []), prevs[g])
            totals["errors"] += no_frame
            write_state(gdir / "WATCH_STATE.json", state)
        nframes = sum(len(sc.get("shots", [])) for sc in state["scenes"])
        pages, rerr = [], ""
        if not a.no_render and state["scenes"]:
            pages, rerr = render(gdir / "WATCH_STATE.json", gdir, out_name, a.per_page)
            if rerr:
                totals["errors"] += 1
        totals["groups"] += 1
        totals["clips"] += len(state["scenes"])
        totals["frames"] += nframes
        totals["pages"] += len(pages)
        index_rows.append([g, len(state["scenes"]), nframes, len(pend_by.get(g, [])), len(pages),
                           str(gdir), rerr or ("; ".join(errs[:3]) if errs else "")])
        print("  group: %s  clips=%d  frames=%d  pending=%d  pages=%d%s" % (
            g, len(state["scenes"]), nframes, len(pend_by.get(g, [])), len(pages),
            ("  ERROR " + rerr) if rerr else ""))

    base.mkdir(parents=True, exist_ok=True)
    day, hhmm = a.stamp.split("_")
    index = base / ("%s_%s_process_sheets_P3-01_%s.csv" % (day, drive, hhmm))
    with open(index, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["group", "clips", "frames", "pending", "pages", "folder", "errors"])
        w.writerows(index_rows)

    selected = sum(min(len(groups.get(g, [])), a.max_clips or 10 ** 9) for g in names)
    if a.render_only:
        balance = "OK" if not totals["errors"] else "CHECK (%d group(s) not rendered)" % totals["errors"]
    else:
        balance = "OK" if totals["clips"] + 0 == selected else "CHECK (%d clips sheeted of %d selected)" % (totals["clips"], selected)
    secs = int(time.time() - t0)
    status = "OK" if not totals["errors"] else "ERRORS %d" % totals["errors"]
    if totals["pending"]:
        status += "; PENDING %d" % totals["pending"]
    if skipped:
        status += "; skipped %d" % len(skipped)
    ledger_append(catalog, [dt.datetime.now(dt.timezone.utc).strftime("%y%m%d %H:%M"), drive,
                            "P5 pdf render" if a.render_only else "P3 process frames",
                            "%s@%s" % (SCRIPT, VERSION), ledger.name, index.name, len(clips), totals["clips"],
                            balance, secs, "free", status])
    print("sheets: %s  groups=%d  clips=%d  frames=%d  pending=%d  skipped=%d  errors=%d  pages=%d  seconds=%d  -> %s"
          % (drive, totals["groups"], totals["clips"], totals["frames"], totals["pending"], len(skipped),
             totals["errors"], totals["pages"], secs, base))
    return 0 if not totals["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
