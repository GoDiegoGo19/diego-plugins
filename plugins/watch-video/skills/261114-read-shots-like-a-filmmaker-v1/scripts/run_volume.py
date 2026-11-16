#!/usr/bin/env python3
"""One command for a whole volume. The local models do the work. No agents, no workers.

    run_volume.py /Volumes/IC_23/18_SKO_27/clips/pull6 --out /Volumes/IC_23/.../_planning/watch_pull6

Diego, 261116, the order this must follow:
    1. identify the volume, identify the media
    2. route the models and scan
    3. run the models ON THE LOCAL MACHINE
    4. the models do the work
    5. do not spawn workers or anything like that

So this walks the tree itself, groups the media into jobs (one per leaf folder: an act, a card, a day),
runs the measured lanes and the local reader over every clip, and writes a content ledger per job plus a
summary. Nothing here calls out to a model service and nothing here starts an agent. If the reader is not
installed it is fetched once, pinned and sha256-checked; with no network the run still completes and says
which columns are thinner for it.
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VIDEO = {".mp4", ".mov", ".mxf", ".m4v", ".avi", ".mkv", ".mts", ".braw", ".r3d", ".webm"}

# File names on Diego's pulls: <cell>_<clip id>_<title>_By_<filmmaker>_<site>.mp4
NAME = re.compile(r"^(?P<cell>[A-Za-z0-9]+)[_-](?P<clip>\d{3,})[_-](?P<title>.+?)[_-]By[_-](?P<maker>.+?)[_-](?P<site>[A-Za-z0-9]+)$", re.I)


def parse_name(stem):
    m = NAME.match(stem)
    if not m:
        return {"cell": "", "clip_no": "", "title": stem.replace("_", " ").strip(), "filmmaker": "", "site": ""}
    d = m.groupdict()
    return {"cell": d["cell"], "clip_no": d["clip"], "title": d["title"].replace("_", " ").strip(),
            "filmmaker": d["maker"].replace("_", " ").strip(), "site": d["site"]}


# ---------------------------------------------------------------- 1. identify
def jobs_of(root):
    """[(job name, folder, [clips])] - one job per leaf folder that holds media. The tree names the job."""
    found = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        clips = sorted(Path(dirpath) / f for f in filenames
                       if Path(f).suffix.lower() in VIDEO and not f.startswith("."))
        if clips:
            rel = os.path.relpath(dirpath, root)
            name = "ALL" if rel == "." else re.sub(r"[^A-Za-z0-9]+", "_", rel).strip("_")
            found[name] = (Path(dirpath), clips)
    return [(n, p, c) for n, (p, c) in sorted(found.items())]


# ---------------------------------------------------------------- 4. the models do the work
def v(row, field):
    d = (row.get("fields") or {}).get(field)
    if not isinstance(d, dict):
        return ""
    x = d.get("value")
    return "" if x in (None, "unread", "unknown", "unresolved") else str(x)


# A CUT is only ever called on something observable. 261116: "any cliche the reader named" was a CUT rule,
# and a 256M model looking at a grey test frame called a stethoscope, so every clip in the act was cut by a
# hallucination. A model's guess can move a clip down the list; it cannot veto it. The two rules left here
# are the ones backed by a detector or a closed measured value.
CUT_RULES = [
    ("on-screen text or a logo", lambda r: v(r, "cliche_markers") in ("burned-in text", "logo")),
    ("talking to camera", lambda r: v(r, "pv_performers") == "looking at lens"),
    ("flat and bright, no modelling", lambda r: v(r, "lighting_ratio") == "1:1 flat" and v(r, "levels") == "lifted (milky)"),
]
MARGINAL_RULES = [
    ("the reader named a cliche: %s", lambda r: v(r, "cliche_markers") not in ("", "none")),
    ("reads as staged", lambda r: v(r, "believability") == "staged-obvious"),
    ("flat light", lambda r: v(r, "lighting_ratio") == "1:1 flat"),
    ("no depth", lambda r: v(r, "depth") == "flat against a wall"),
    ("soft throughout", lambda r: v(r, "clarity") == "soft overall"),
    ("lifted blacks", lambda r: v(r, "levels") == "lifted (milky)"),
]


def brief_fit(row):
    """KEEP / MARGINAL / CUT from the row's own measured and read fields, with the fields as the reason.

    Rules only, over closed values. No model is asked to judge a brief, and nothing here reads a title:
    a title is what a seller wrote, and Diego's rule is to judge from the frames.
    """
    for why, test in CUT_RULES:
        if test(row):
            return "CUT", why
    hits = [(why % v(row, "cliche_markers") if "%s" in why else why) for why, test in MARGINAL_RULES if test(row)]
    if len(hits) >= 2:
        return "MARGINAL", "; ".join(hits)
    if hits:
        return "MARGINAL", hits[0]
    bits = [b for b in (v(row, "lighting_style"), v(row, "depth"), v(row, "quality_of_light")) if b]
    return "KEEP", ", ".join(bits) or "no fault found"


COLUMNS = ["cell", "clip_id", "title", "filmmaker", "site", "duration_s", "shot_sentence", "size", "camera_move",
           "light", "time_of_day", "what_is_on_screen", "brief_fit", "reason"]


def on_screen(row):
    """What is in the frame, in the row's own words, capped at 20 words."""
    bits = [b for b in (v(row, "var_form"), v(row, "var_age"), v(row, "subject_mods"), v(row, "subject"),
                        v(row, "verb"), v(row, "manner"), v(row, "place")) if b]
    return " ".join(" ".join(bits).split()[:20])


def durations_of(measures):
    """{tail of segment_id: seconds} from measures.jsonl. The ledger carries no duration field."""
    got = {}
    for line in (measures.read_text().splitlines() if measures.exists() else []):
        if not line.strip():
            continue
        r = json.loads(line)
        t0, t1 = r.get("t_in"), r.get("t_out")
        if t0 is not None and t1 is not None:
            got[r.get("segment_id", "").split("/")[-1]] = round(float(t1) - float(t0), 2)
    return got


def rows_for(ledger, sentences, durations):
    out = []
    for r in ledger:
        sid = r.get("segment_id", "")
        fname = sid.split("/")[-1].split("#")[0]
        meta = parse_name(Path(fname).stem)
        tail = sid.split("/")[-1]
        fit, why = brief_fit(r)
        out.append({"cell": meta["cell"], "clip_id": sid, "title": meta["title"], "filmmaker": meta["filmmaker"],
                    "site": meta["site"], "duration_s": durations.get(tail, ""),
                    "shot_sentence": sentences.get(tail, ""), "size": v(r, "shot_size"),
                    "camera_move": " ".join(x for x in (v(r, "camera_move"), v(r, "move_speed")) if x),
                    "light": " ".join(x for x in (v(r, "quality_of_light"), v(r, "lighting_ratio")) if x),
                    "time_of_day": v(r, "time_of_day"), "what_is_on_screen": on_screen(r),
                    "brief_fit": fit, "reason": why})
    return out


def sentences_of(md):
    """{tail of segment_id: sentence} from sentences.md, which code already printed from the ledger.

    Keyed by the FILE part. 261116: sentences.md writes `name.mp4#S01` while the ledger writes
    `DRIVE/CARD/name.mp4#S01`, so matching on the whole id put an empty sentence in every row.
    """
    got = {}
    for line in (md.read_text().splitlines() if md.exists() else []):
        m = re.match(r"^- `([^`]+)`\s+(.*)$", line.strip())
        if m:
            got[m.group(1).split("/")[-1]] = m.group(2)
    return got


def write_job(out_dir, job, rows, stamp):
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_p = out_dir / ("%s_%s_content_ledger.csv" % (stamp, job))
    with open(csv_p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    cut = [r for r in rows if r["brief_fit"] == "CUT"]
    marg = [r for r in rows if r["brief_fit"] == "MARGINAL"]
    keep = [r for r in rows if r["brief_fit"] == "KEEP"]
    md = ["# %s - content ledger (%s)" % (job, stamp), "",
          "%d clip(s): %d KEEP, %d MARGINAL, %d CUT. Judged from the frames by the local models, never from titles."
          % (len(rows), len(keep), len(marg), len(cut)), "",
          "| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for r in rows:
        md.append("| " + " | ".join(str(r[c]).replace("|", "/") for c in COLUMNS) + " |")
    for title, group in (("CUT", cut), ("MARGINAL", marg)):
        md += ["", "## %s (%d)" % (title, len(group)), ""]
        md += ["- `%s` %s - %s" % (r["clip_id"].split("/")[-1], r["title"], r["reason"]) for r in group] or ["- none"]
    seen = {}
    for r in rows:
        key = (r["title"].lower(), r["filmmaker"].lower())
        seen.setdefault(key, []).append(r["clip_id"].split("/")[-1])
    dupes = {k: v2 for k, v2 in seen.items() if len(v2) > 1 and k[0]}
    md += ["", "## Same title or same shoot (RULE ZERO: never twice in one video)", ""]
    md += ["- %s by %s: %s" % (k[0], k[1] or "?", ", ".join(v2)) for k, v2 in dupes.items()] or ["- none found by title"]
    md_p = out_dir / ("%s_%s_content_ledger.md" % (stamp, job))
    md_p.write_text("\n".join(md) + "\n")
    return csv_p, md_p, (len(keep), len(marg), len(cut))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("volume", help="a volume, a folder, or any tree that holds the media")
    ap.add_argument("--out", required=True, help="where the ledgers go")
    ap.add_argument("--assume-transfer", default="rec709")
    ap.add_argument("--only", help="run only jobs whose name contains this")
    ap.add_argument("--list", action="store_true", help="identify the media and stop (step 1 only)")
    a = ap.parse_args(argv)

    root = Path(a.volume).resolve()
    if not root.exists():
        print("run_volume: no such volume or folder: %s" % root, file=sys.stderr)
        return 2
    jobs = jobs_of(root)
    if a.only:
        jobs = [j for j in jobs if a.only.lower() in j[0].lower()]
    total = sum(len(c) for _n, _p, c in jobs)
    print("volume   %s" % root)
    print("media    %d clip(s) in %d job(s): %s" % (total, len(jobs), ", ".join("%s (%d)" % (n, len(c)) for n, _p, c in jobs)))
    if a.list or not jobs:
        return 0 if jobs else 1

    out = Path(a.out).resolve()
    stamp = dt.datetime.now().strftime("%y%m%d")
    done, summary = [], []
    vision_ok, vision_notes, vision_by_job = False, [], {}
    for i, (job, folder, clips) in enumerate(jobs, 1):
        work = out / job
        print("\n[%d/%d] %s - %d clip(s), models running here" % (i, len(jobs), job, len(clips)), flush=True)
        r = subprocess.run([sys.executable, "-I", "-B", str(HERE / "read_shots.py"), str(folder),
                            "--out", str(work), "--assume-transfer", a.assume_transfer])
        led = work / "ledger.jsonl"
        if r.returncode != 0 or not led.exists():
            print("   no ledger for %s, skipped" % job)
            summary.append((job, len(clips), None))
            continue
        rows = [json.loads(x) for x in led.read_text().splitlines() if x.strip()]
        summ = work / "read_shots.json"
        if summ.exists():
            lanes = (json.loads(summ.read_text()) or {}).get("lanes") or []
            vision_by_job[job] = any("read_lane: reading the hero frame" in x for x in lanes)
            for ln in lanes:
                if "read_lane: reading the hero frame" in ln:
                    vision_ok = True
                elif "read_lane" in ln or "CANNOT RUN" in ln or ln.startswith("reduced:"):
                    if ln not in vision_notes:
                        vision_notes.append(ln)
        csv_p, md_p, counts = write_job(work, job, rows_for(rows, sentences_of(work / "sentences.md"),
                                                                 durations_of(work / "measures.jsonl")), stamp)
        print("   %s  KEEP %d / MARGINAL %d / CUT %d" % (csv_p.name, *counts))
        done.append((job, csv_p, md_p))
        summary.append((job, len(clips), counts))

    s = ["# %s - volume summary" % stamp, "", "Volume: `%s`" % root, "",
         "| job | clips | KEEP | MARGINAL | CUT | vision |", "|---|---|---|---|---|---|"]
    for job, n, c in summary:
        vz = "read" if job in vision_by_job and vision_by_job[job] else ("OFF" if job in vision_by_job else "-")
        s.append("| %s | %d | %s | %s |" % (job, n, " | ".join(str(x) for x in c) if c else "not read | - | -", vz))
    # 261115: this line said the models judged every row even on a run where the reader never loaded. A summary
    # that overstates what ran is worse than no summary: it is the one file a person believes without checking.
    s += ["", "## What actually ran", ""]
    if vision_ok:
        s += ["The measured lanes and the local reader both ran. Light, time of day, camera move and framing are",
              "measured from the pixels; subject, action, place and mood are read. No agent was involved and no",
              "title was read as evidence."]
    else:
        s += ["**VISION OFF: subject, verb and place are unread on every row.** The reader did not run, so those",
              "columns are empty and `what_is_on_screen` is thin. Everything measured (light, time of day, camera",
              "move, framing, levels) is still true. Why it did not run:", ""]
        s += ["- %s" % n for n in (vision_notes or ["reason not recorded"])]
        s += ["", "Fix that and run again for the read columns."]
    s += ["", "The ledgers are beside this file, one folder per job."]
    (out).mkdir(parents=True, exist_ok=True)
    sp = out / ("%s_volume_summary.md" % stamp)
    sp.write_text("\n".join(s) + "\n")
    print("\nsummary  %s" % sp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
