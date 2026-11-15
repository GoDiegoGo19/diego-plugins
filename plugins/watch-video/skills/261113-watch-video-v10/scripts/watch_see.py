#!/usr/bin/env python3
"""watch_see.py - the SEE pass on its own, for one clip or a folder of clips (watch-video plugin 1.1.0, 261113).

Uses the scan plugin's vision engine, carried byte-for-byte in this skill (ENGINE.json, engine_sync.py):
  cuts      p3_see_segments.shots_of        PySceneDetect AdaptiveDetector
  frames    p3_see_segments.frames_at       PyAV, real frames at real timestamps (actual PTS recorded)
  segments  p3_see_segments.regimes         ruptures PELT on the DINOv2 fingerprints inside each shot
  families  p4_sort_families.group          leader walk -> frozen pass -> compactness (no chain merges)
  people    p3_see_models.people            YOLOX-S
  tags      p3_see_models.tag / tag_bank    CLIP, only when installed (p3_see_models.py install core)
  text vecs p3_see_models.embed_text        MiniLM over each segment's tags + facts (search text)
  premise   p3_quick_premise.hms + this file one line per clip; a tagline per card
  sheet     render_html.py                  the skill's own renderer (WeasyPrint); skipped with a reason if missing

    python3 watch_see.py <clip|folder> --out DIR [--bracket quick|standard|full] [--drive NAME] [--max-clips N]

Writes into DIR (never next to the media; nothing deleted):
  segments.csv  families.csv  tags.csv  hero/<id>.jpg  WATCH_STATE.json  <name>_Sheet.pdf/jpg (when WeasyPrint is there)
  vectors.npz   watch.json  (shared IDs: drive / card / clip_id / segment_id / t_in / t_out, the same clip_id
                             "<drive>/<card column>/<file name>" the Premiere plugin's ppf_ingest builds)
Exit 0 done · 4 the vision engine cannot run here (onnxruntime / models missing) · 2 usage or error.
"""
import argparse
import collections
import csv
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

VIDEO_EXT = {".mov", ".mp4", ".mxf", ".m4v", ".avi", ".mts", ".m2ts", ".mkv", ".webm", ".mpg", ".mpeg"}
DAY_RE = re.compile(r"^(\d{6})(?:_|$)")                  # same rule as ppf_ingest.DAY_RE
SKIP_DIRS = {"PROXY", "SUB", "AUDIO", "ICON", "THMBNL", "CACHE"}
BRACKETS = {   # how hard to look: overview samples per clip, every N seconds, faces/text when installed
    "quick": {"overview_max": 12, "overview_every_s": 4.0, "faces_text": False},
    "standard": {"overview_max": None, "overview_every_s": None, "faces_text": False},
    "full": {"overview_max": None, "overview_every_s": None, "faces_text": True},
}
CLIP_HINT = "tags: CLIP not installed; run p3_see_models.py install core"


def ffprobe(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=avg_frame_rate,width,height:format=duration", "-of", "json", str(path)],
                           capture_output=True, text=True, timeout=60)
        j = json.loads(r.stdout or "{}")
        st = (j.get("streams") or [{}])[0]
        num, _, den = (st.get("avg_frame_rate") or "0/1").partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 0.0
        return float(j.get("format", {}).get("duration") or 0), fps, st.get("width"), st.get("height")
    except Exception:
        return 0.0, 0.0, None, None


def card_of(rel):
    """(day, card, card column) from the folders, as ppf_ingest.day_and_card does (no metadata date here)."""
    parts = rel.replace("\\", "/").split("/")
    for i, p in enumerate(parts[:-1]):
        m = DAY_RE.match(p)
        if m:
            card = parts[i + 1] if i + 1 < len(parts) - 1 else "LOOSE"
            return m.group(1), card, "/".join(parts[:i + 2]) if card != "LOOSE" else "LOOSE"
    card = parts[0] if len(parts) > 1 else "LOOSE"
    return "", card, card


def find_clips(src):
    src = Path(src)
    if src.is_file():
        return src.parent, [src]
    out = []
    for dp, dns, fns in os.walk(src):
        dns[:] = sorted(d for d in dns if not d.startswith(".") and d.upper() not in SKIP_DIRS)
        for fn in sorted(fns):
            if not fn.startswith(".") and Path(fn).suffix.lower() in VIDEO_EXT:
                out.append(Path(dp) / fn)
    return src, out


def clip_installed(p0_env, V):
    m = V["models"].get("clip")
    return bool(m) and all(p0_env.model_file("clip", f["file"], f["bytes"]) for f in m["files"])


def tag_words(tags):
    return ", ".join("%s %s" % (a, t["value"]) for a, t in sorted(tags.items()) if t.get("value") not in (None, "CHECK"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="The SEE pass on one clip or a folder.")
    ap.add_argument("source")
    ap.add_argument("--out", required=True)
    ap.add_argument("--bracket", choices=sorted(BRACKETS), default="standard")
    ap.add_argument("--drive", default=None, help="drive name for the shared IDs (default: the folder's name)")
    ap.add_argument("--max-clips", type=int, default=0)
    ap.add_argument("--no-sheet", action="store_true")
    a = ap.parse_args(argv)

    if not Path(a.source).exists():
        print("watch_see: no such clip or folder: %s" % a.source)
        return 2
    try:
        import numpy as np
        import onnxruntime  # noqa: F401
        import p0_env
        import p3_see_models as sm
        import p3_see_segments as see
        import p4_sort_families as sort
        from p3_quick_premise import hms
    except ImportError as e:
        print("watch_see: the vision engine cannot run here (%s). Run: python3 scripts/watch_setup.py check" % e)
        return 4
    V = sm.V()
    br = BRACKETS[a.bracket]
    for k in ("overview_max", "overview_every_s"):
        if br[k]:
            V["sample"][k] = br[k]
    core = {k: p0_env.model_file(k, V["models"][k]["files"][0]["file"], V["models"][k]["files"][0]["bytes"])
            for k in ("dino_small", "yolox_s", "minilm")}
    if not all(core.values()):
        print("watch_see: missing bundled models: %s" % ", ".join(k for k, v in core.items() if not v))
        return 4
    has_clip = clip_installed(p0_env, V)
    faces_ok = br["faces_text"] and all(p0_env.model_file(k, f["file"], f["bytes"])
                                        for k in ("yunet", "sface") for f in V["models"][k]["files"])
    bank = sm.tag_bank("clip") if has_clip else None

    root, clips = find_clips(a.source)
    if a.max_clips:
        clips = clips[:a.max_clips]
    drive = a.drive or root.name or "DRIVE"
    out = Path(a.out).resolve()
    hero_dir = out / "hero"
    hero_dir.mkdir(parents=True, exist_ok=True)
    print("watch_see: %d clip(s) under %s · bracket %s · drive %s" % (len(clips), root, a.bracket, drive))
    print("  engines: DINOv2-small fingerprints, YOLOX-S people, MiniLM text%s" % (", CLIP tags" if has_clip else ""))
    if not has_clip:
        print("  " + CLIP_HINT)

    segs, fps_rows, clip_recs = [], [], []
    for path in clips:
        rel = os.path.relpath(path, root) if Path(a.source).is_dir() else path.name
        day, card, card_col = card_of(rel)
        cid = "%s/%s/%s" % (drive, card_col, path.name)
        dur, fps, w, h = ffprobe(path)
        # D5 (SHARED_IDS.md): a field called `card` beside a clip_id must hold the card the clip_id CARRIES,
        # which is the card column (day/card), not the card's short name. The short name is `card_name`.
        # 261114: this record held the short name, so a consumer rebuilding an ID from drive + card got a path
        # that does not exist. The shared-ID guard was red on exactly this, 10 records on the test drive.
        rec = {"clip_id": cid, "drive": drive, "card": card_col, "card_name": card, "card_column": card_col,
               "day": day, "clip": path.name,
               "rel_path": rel, "path": str(path.resolve()), "duration_s": round(dur, 3), "fps": round(fps, 3),
               "size": "%sx%s" % (w, h) if w else "", "status": "seen", "why": "", "shots": [], "segments": []}
        clip_recs.append(rec)
        if dur <= 0:
            rec.update(status="pending", why="ffprobe found no duration (driver lane or unreadable)")
            continue
        try:
            shots, cuts = see.shots_of(path, dur, V)
        except Exception as e:
            shots, cuts = [(0.0, dur)], []
            rec["why"] = "cuts failed: %s" % str(e)[:80]
        rec["shots"] = [[round(x, 3), round(y, 3)] for x, y in shots]
        samples = see.frames_at(path, see.overview_times(dur, V))
        if not samples:
            rec.update(status="pending", why="no frame could be decoded")
            continue
        pts = [p for p, _ in samples]
        imgs = [x for _, x in samples]
        fp = sm.embed_image(V["choose"]["fingerprint"], imgs)
        tv = sm.embed_image("clip", imgs) if has_clip else None
        good = sm.finite_rows(fp)
        luma = np.array([x.mean() for x in imgs])
        n = 0
        for si, (s_in, s_out) in enumerate(shots):
            idx = [i for i, p in enumerate(pts) if s_in - 1e-3 <= p < s_out - 1e-3 and good[i]]
            if not idx:
                continue
            starts = [idx[j] for j in see.regimes(fp[idx], V)] + [None]
            for b0, b1 in zip(starts[:-1], starts[1:]):
                sel = [i for i in idx if i >= b0 and (b1 is None or i < b1)]
                n += 1
                sid = "%s#S%02d" % (cid, n)
                centre = sm.unit(fp[sel].mean(0))
                lit = [i for i in sel if luma[i] >= float(V["dark_luma"])]
                hero = max(lit, key=lambda i: float(fp[i] @ centre)) if lit else max(sel, key=lambda i: luma[i])
                t_in = s_in if b0 == idx[0] else round((pts[b0 - 1] + pts[b0]) / 2, 3)
                t_out = round((pts[b1 - 1] + pts[b1]) / 2, 3) if b1 is not None else s_out
                hname = "%s_S%02d_%07d.jpg" % (re.sub(r"[^A-Za-z0-9]+", "_", path.stem), n, int(pts[hero] * 1000))
                ok, bgr = see.save_thumb(imgs[hero], hero_dir / hname)
                seg = {"segment_id": sid, "clip_id": cid, "drive": drive, "card": card_col, "card_name": card,
                       "clip": path.name,
                       "n": n, "shot": si + 1, "t_in": round(t_in, 3), "t_out": round(t_out, 3), "hero_t": pts[hero],
                       "hero": ("hero/" + hname) if ok else "", "dark": not lit, "samples": len(sel),
                       "people": sm.people(bgr), "tags": sm.tag(sm.unit(tv[sel].mean(0)), bank) if has_clip else {}}
                if faces_ok:
                    seg["faces"] = len(sm.faces(bgr))
                    seg["text"] = [x["text"] for x in sm.text(bgr)]
                rec["segments"].append(seg)
                segs.append(seg)
                fps_rows.append(centre)
        print("  %s · %s · %d shot(s) · %d segment(s)" % (cid, hms(dur), len(shots), n))

    # families across everything seen (the scan's SORT math, unchanged)
    fam_rows = []
    if segs:
        Vm = np.stack(fps_rows).astype("float32")
        fv = V["family"]
        assign, C = sort.group(Vm, float(fv["join_above"]), float(fv["min_compactness"]))
        for k in range(len(C)):
            mem = [i for i, x in enumerate(assign) if x == k]
            lead = max(mem, key=lambda i: float(Vm[i] @ C[k]))
            fid = "F%03d" % (k + 1)
            for i in mem:
                segs[i]["family"] = fid
            fam_rows.append({"family_id": fid, "members": len(mem), "hero_segment": segs[lead]["segment_id"],
                             "hero": segs[lead]["hero"], "clips": len({segs[i]["clip_id"] for i in mem}),
                             "segments": " ".join(segs[i]["segment_id"] for i in mem)})
        texts = ["%s · %s · %s · people %d" % (s["clip"], tag_words(s["tags"]) or "untagged", s["family"],
                                                s["people"]["count"]) for s in segs]
        for s, t in zip(segs, texts):
            s["search_text"] = t
        tvec = sm.embed_text("minilm", texts)
        np.savez(out / "vectors.npz", segment_id=np.array([s["segment_id"] for s in segs]),
                 fingerprint=Vm, text_vec=tvec)

    # premise per clip, tagline per card
    for rec in clip_recs:
        ss = rec["segments"]
        if not ss:
            rec["premise"] = "PENDING: %s" % (rec["why"] or "not seen")
            continue
        ppl = max(s["people"]["count"] for s in ss)
        top = collections.Counter(tag_words(s["tags"]) for s in ss if s["tags"]).most_common(1)
        rec["premise"] = "%s · %s · %d shot(s), %d segment(s), %d famil%s · %s · %s" % (
            rec["clip"], hms(rec["duration_s"]), len(rec["shots"]), len(ss), len({s["family"] for s in ss}),
            "y" if len({s["family"] for s in ss}) == 1 else "ies",
            "no people seen" if not ppl else "up to %d %s" % (ppl, "person" if ppl == 1 else "people"),
            top[0][0] if top else "untagged (CLIP not installed)")
    cards = collections.OrderedDict()
    for rec in clip_recs:
        c = cards.setdefault(rec["card_column"], {"clips": 0, "secs": 0.0, "segments": 0, "pending": 0})
        c["clips"] += 1
        c["secs"] += rec["duration_s"]
        c["segments"] += len(rec["segments"])
        c["pending"] += rec["status"] != "seen"
    tagline = " | ".join("%s: %d clip(s), %s, %d segment(s)%s" % (k, c["clips"], hms(c["secs"]), c["segments"],
                         ", %d pending" % c["pending"] if c["pending"] else "") for k, c in cards.items())

    # CSVs
    with open(out / "segments.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["drive", "card", "clip_id", "segment_id", "shot", "t_in", "t_out", "hero_t", "hero", "family",
                    "people", "dark"])
        for s in segs:
            w.writerow([s["drive"], s["card"], s["clip_id"], s["segment_id"], s["shot"], s["t_in"], s["t_out"],
                        s["hero_t"], s["hero"], s["family"], s["people"]["count"], s["dark"]])
    with open(out / "families.csv", "w", newline="") as f:
        w = csv.DictWriter(f, ["family_id", "members", "clips", "hero_segment", "hero", "segments"])
        w.writeheader()
        w.writerows(fam_rows)
    with open(out / "tags.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["segment_id", "axis", "value", "best", "p", "status"])
        for s in segs:
            for ax, t in sorted(s["tags"].items()):
                w.writerow([s["segment_id"], ax, t["value"], t["best"], t["p"], t["status"]])
        if not has_clip:
            w.writerow(["", "", "", "", "", CLIP_HINT])

    # shot sheet: one scene per clip, one cell per segment hero, via the skill's renderer
    state = {"title": "SEE · %s" % drive, "source": str(root), "scenes": []}
    for i, rec in enumerate(c for c in clip_recs if c["segments"]):
        state["scenes"].append({"id": "SC%02d" % (i + 1), "file": rec["clip"], "path": rec["path"],
                                "rel_path": rec["rel_path"], "shots": [
            {"id": "SC%02d-S%02d" % (i + 1, s["n"]), "src": rec["path"], "t": s["hero_t"], "file": rec["clip"],
             "label": "S%02d" % s["n"], "frame": str(out / s["hero"]), "desc": "",
             "facts": "%s-%s · %s · people %d" % (hms(s["t_in"]), hms(s["t_out"]), s["family"], s["people"]["count"])}
            for s in rec["segments"]]})
    (out / "WATCH_STATE.json").write_text(json.dumps(state, indent=1))
    sheet = "not rendered (--no-sheet)"
    if not a.no_sheet and state["scenes"]:
        r = subprocess.run([sys.executable, str(HERE / "render_html.py"), str(out / "WATCH_STATE.json"),
                            "--out", str(out / "SEE"), "--per-page", "12", "--cols", "4"],
                           capture_output=True, text=True)
        sheet = str(out / "SEE_Sheet.pdf") if r.returncode == 0 and (out / "SEE_Sheet.pdf").is_file() else \
            "not rendered: %s" % ((r.stderr or r.stdout).strip().splitlines() or ["renderer failed"])[-1][:160]

    watch = {"schema": "watch-see/1", "made": dt.datetime.now().isoformat(timespec="seconds"),
             "id_rule": "clip_id = <drive>/<card column>/<file name> (ppf_ingest); segment_id = <clip_id>#S<nn>",
             "drive": drive, "source": str(root), "bracket": a.bracket, "tagline": tagline,
             "engines": {"fingerprint": "dino_small", "people": "yolox_s", "text": "minilm",
                         "tags": "clip" if has_clip else None, "faces_text": bool(faces_ok)},
             "tags_note": "" if has_clip else CLIP_HINT, "sheet": sheet,
             "clips": [{k: v for k, v in r.items() if k != "segments"} | {"segments": [s["segment_id"] for s in r["segments"]]}
                       for r in clip_recs],
             "segments": segs, "families": fam_rows}
    (out / "watch.json").write_text(json.dumps(watch, indent=1))
    print("tagline: %s" % tagline)
    for rec in clip_recs:
        print("  premise: %s" % rec["premise"])
    print("segments %d · families %d · hero frames %d · sheet: %s" % (len(segs), len(fam_rows),
          sum(1 for s in segs if s["hero"]), sheet))
    print("wrote %s" % (out / "watch.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
