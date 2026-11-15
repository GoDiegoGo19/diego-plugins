#!/usr/bin/env python3
"""read_shots.py: the front door. A clip, a folder or a watch.json in; a ledger of shot sentences out.

  read_shots.py <clip|folder|watch.json> --out DIR [--assume-transfer rec709|log|hlg] [--notes notes.txt]

cuts (the watch skill's detect_cuts, imported, never edited; or the segments of a watch.json) -> per shot: arc + measure
(shot_arc.py, shot_measure.py) -> the other lanes when present (shot_motion, shot_light, shot_decon, read_lane; each called
through read_shot(video, t_in, t_out, measures) -> {field: {value, evidence[, origin]}}, else skipped with a note) ->
ledger build + validate (shot_ledger.py) -> sentences (shot_sentence.py).
Writes DIR/ledger.jsonl, DIR/sentences.md, DIR/read_shots.json (+ measures.jsonl). Never overwrites: a second run writes
ledger_v002.jsonl, sentences_v002.md, read_shots_v002.json, and so on.
notes.txt: one line per shot, `<segment_id or file name>: free text`; `casting=<up to 4 words>` in the text sets var_casting
as a person fact (P). The rest rides on the row as `notes` (the encyclopedic layer; it never drives a rank).
"""
import argparse
import datetime as dt
import importlib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import shot_arc as A  # noqa: E402
import shot_ledger as L  # noqa: E402
import shot_measure as SM  # noqa: E402
import shot_sentence as S  # noqa: E402

V10 = HERE.parent.parent / "261113-watch-video-v10" / "scripts"
PROXY_DIRS = {"sub", "proxy"}
LANES = [("shot_motion", "grammar"), ("shot_light", "grammar"), ("shot_decon", "grammar"), ("read_lane", "read")]
# camera and quality class from the card structure or extension (source facts, never guessed from pixels)
CAMERAS = [(r"\.r3d$|/RED", "RED", "cinema-B"), (r"\.ari$|ARRI|/ALEXA", "ARRI", "cinema-A"), (r"\.braw$|BMPCC|URSA", "Blackmagic", "cinema-B"),
           (r"VENICE", "Sony Venice", "cinema-A"), (r"XDROOT|FX6|FX3|FX9", "Sony FX", "prosumer"), (r"DJI", "DJI", "drone"),
           (r"GH5|_PANA|LUMIX", "Panasonic GH", "DSLR"), (r"P2_|/CONTENTS/VIDEO", "Panasonic P2", "prosumer"),
           (r"BPAV|EX1", "Sony EX", "prosumer"), (r"GOPRO|/GX\d|/GH0\d", "GoPro", "prosumer"), (r"IPHONE|/IMG_\d+\.(MOV|HEIC)", "iPhone", "phone")]
SHOT_ON = {"cinema-A": "cinema", "cinema-B": "cinema", "prosumer": "prosumer", "drone": "drone", "DSLR": "DSLR-mirrorless", "phone": "phone"}


def next_name(out, stem, ext):
    p = out / (stem + ext)
    if not p.exists():
        return p, ""
    n = 2
    while (out / ("%s_v%03d%s" % (stem, n, ext))).exists():
        n += 1
    return out / ("%s_v%03d%s" % (stem, n, ext)), "_v%03d" % n


def source_facts(path, probe):
    up = str(path).replace("\\", "/").upper()
    g = {}
    for rx, cam, cls in CAMERAS:
        if re.search(rx, up, re.I):
            g["source_camera"] = {"value": cam, "evidence": "card structure / extension in the path"}
            g["source_quality_class"] = {"value": cls, "evidence": "camera %s (a fact to weigh, never a rank)" % cam}
            break
    if probe.get("codec"):
        g["source_codec"] = {"value": "%s %s-bit" % (probe["codec"], probe["bit_depth"]) if probe.get("bit_depth") else probe["codec"],
                             "evidence": "ffprobe"}
    return g


def clips_of(src):
    """[(path, root, [(segment_id or None, t_in, t_out)] or None)]; None = detect cuts."""
    p = Path(src)
    if p.suffix.lower() == ".json":
        w = json.loads(p.read_text())
        segs = {}
        for s in w.get("segments", []):
            segs.setdefault(s["clip_id"], []).append((s["segment_id"], float(s["t_in"]), float(s["t_out"])))
        return [(Path(c["path"]), Path(w.get("source") or Path(c["path"]).parent), segs.get(c["clip_id"]) or None)
                for c in w.get("clips", []) if c.get("status", "seen") == "seen"], []
    if p.is_dir():
        found, skipped = [], []
        for f in sorted(p.rglob("*")):
            if f.suffix.lower() in SM.VID_EXT and f.is_file():
                if {x.lower() for x in f.relative_to(p).parts[:-1]} & PROXY_DIRS:
                    skipped.append((str(f.relative_to(p)), "proxy of a camera original"))
                    continue
                found.append((f, p, None))
        return found, skipped
    return [(p, p.parent, None)], []


def shots_of(path):
    sys.path.insert(0, str(V10))
    import detect_cuts  # noqa: E402  (watch engine; imported, never edited)
    c = detect_cuts.detect_cuts(str(path))
    fps, total = c["fps"], c["duration_frames"]
    b = [0] + [f for f in c["cut_frames"] if 0 < f < total] + [total]
    return [(None, b[i] / fps, b[i + 1] / fps) for i in range(len(b) - 1) if b[i + 1] > b[i]], c["engine"]


# the other lanes, called through their own measure() when present: (module, call, {their field: our ledger field})
LANE_MAP = {
    "shot_motion": (lambda mod, path, t_in, t_out, r: mod.measure(str(path), t0=t_in, t1=t_out),
                    {"camera_move": "camera_move", "speed": "move_speed", "subject_manner": "manner", "subject_direction": "direction"}),
    "shot_light": (lambda mod, path, t_in, t_out, r: mod.measure(str(path), transfer=r["transfer"], t0=r["hero_t"]),
                   {"time_of_day": "time_of_day", "light_quality": "quality_of_light"}),
}


def lanes():
    """{name: (call, field map, kind)} for the lanes present, and a note per lane (present, called or skipped)."""
    got, notes = {}, []
    for name, kind in LANES:
        if not (HERE / (name + ".py")).exists():
            notes.append("%s: not present yet; skipped (its slots stay unknown / unread)" % name)
            continue
        try:
            mod = importlib.import_module(name)
        except Exception as e:
            notes.append("%s: present but did not import (%s); skipped" % (name, type(e).__name__))
            continue
        if hasattr(mod, "read_shot"):
            got[name] = (lambda mod, path, t_in, t_out, r: mod.read_shot(str(path), t_in, t_out, r["mid_measures"]), None, kind, mod)
            notes.append("%s: called through read_shot()" % name)
        elif name in LANE_MAP:
            got[name] = (LANE_MAP[name][0], LANE_MAP[name][1], kind, mod)
            notes.append("%s: called through measure(); maps %s" % (name, ", ".join("%s->%s" % kv for kv in LANE_MAP[name][1].items())))
        elif name == "read_lane":
            found = mod.find_model()[0] if hasattr(mod, "find_model") else None
            notes.append("read_lane: present; %s" % ("model found but per-shot hero frames are not wired this round; R slots stay unread"
                                                     if found else "no vision model installed; R slots stay unread"))
        elif name == "shot_decon" and hasattr(mod, "measure"):
            got[name] = (lambda mod, path, t_in, t_out, r: mod.measure(str(path), t0=r["hero_t"], t1=min(t_out, r["hero_t"] + 0.5)),
                         None, "decon", mod)
            notes.append("shot_decon: called on the hero frame; its passes ride in measures.jsonl under decon (no ledger slot)")
        else:
            notes.append("%s: present; no ledger slot maps to its output yet (it runs on its own CLI)" % name)
    return got, notes


def lane_values(name, entry, path, t_in, t_out, r):
    call, fmap, kind, mod = entry
    got = call(mod, path, t_in, t_out, r) or {}
    out = {}
    for k, d in got.items():
        ours = fmap.get(k) if fmap else k
        if not ours or ours not in L.SCHEMA or not isinstance(d, dict):
            continue
        v = d.get("value")
        if isinstance(v, str) and v not in (L.U, L.UNREAD) and L.check_value(ours, v) is None:
            ev = d.get("evidence") or d.get("note") or d.get("rule") or name
            org = "M" if kind != "read" else (d.get("origin") or L.SCHEMA[ours][0])   # a measured lane writes M (D009)
            out[ours] = {"value": v, "evidence": "%s: %s" % (name, str(ev)[:160]), "origin": org}
    return out


def read_notes(path):
    out = []
    if path:
        for line in Path(path).read_text().splitlines():
            if ":" in line and line.strip() and not line.startswith("#"):
                k, _, t = line.partition(":")
                out.append((k.strip(), t.strip()))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("src", help="a clip, a folder of clips, or a watch.json from the SEE pass")
    ap.add_argument("--out", required=True)
    ap.add_argument("--assume-transfer", choices=sorted(SM.ASSUME))
    ap.add_argument("--notes")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ledger_p, suf = next_name(out, "ledger", ".jsonl")
    meas_p, src_p, read_p = (out / ("%s%s.jsonl" % (s, suf)) for s in ("measures", "source", "read"))
    md_p, sum_p = out / ("sentences%s.md" % suf), out / ("read_shots%s.json" % suf)
    hooks, lane_notes = lanes()
    notes = read_notes(a.notes)
    clips, skipped = clips_of(a.src)
    meas, srcs, reads, cut_engines, pending = [], [], [], set(), []
    for path, root, segs in clips:
        probe = SM.ffprobe(path)
        if not probe.get("width"):
            skipped.append((str(path), "no video stream (audio or unreadable)"))
            continue
        rel = os.path.relpath(path, root)
        cid = "%s/%s/%s" % (Path(root).name or "DRIVE", SM.card_of(rel), path.name)
        if segs is None:
            segs, eng = shots_of(path)
            cut_engines.add(eng)
        for i, (sid, t_in, t_out) in enumerate(segs, 1):
            sid = sid or "%s#S%02d" % (cid, i)
            r = A.arc(path, t_in, t_out, None, a.assume_transfer)
            if r.get("status") != "seen":
                pending.append((sid, r.get("why")))
                continue
            g = dict(r["grammar"])
            g.update(source_facts(path, probe))
            rd = {"segment_id": sid}
            decon = None
            for name, entry in hooks.items():
                try:
                    if entry[2] == "decon":
                        decon = entry[0](entry[3], path, t_in, t_out, r)
                        continue
                    got = lane_values(name, entry, path, t_in, t_out, r)
                except Exception as e:  # a lane in progress must not stop the door
                    lane_notes.append("%s: failed on %s (%s: %s)" % (name, sid, type(e).__name__, str(e)[:80]))
                    continue
                (rd if entry[2] == "read" else g).update(got)
            for key, text in notes:
                if key and (key == sid or key in str(path)):
                    m = re.search(r"casting=([^;,]+)", text)
                    if m:
                        rd["var_casting"] = {"value": m.group(1).strip(), "origin": "P", "evidence": "notes.txt"}
            meas.append({"clip_id": cid, "segment_id": sid, "source": str(path), "t_in": r["t_in"], "t_out": r["t_out"],
                         "hero_t": r["hero_t"], "transfer": r["transfer"], "transfer_source": r["transfer_source"],
                         "measures": r["mid_measures"], "grammar": g, "arc_spike": r["spike"],
                         **({"decon": decon} if decon is not None else {})})
            cls = (g.get("source_quality_class") or {}).get("value")
            srcs.append({"segment_id": sid, **({"shot_on": SHOT_ON[cls]} if cls else {})})
            reads.append(rd)
    for p, rows in ((meas_p, meas), (src_p, srcs), (read_p, reads)):
        with open(p, "w") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
    if not meas:
        print("read_shots: no shots read", file=sys.stderr)
        return 1
    L.main(["build", "--measures", str(meas_p), "--read", str(read_p), "--source", str(src_p), "--out", str(ledger_p)])
    rows = [json.loads(l) for l in ledger_p.read_text().splitlines() if l.strip()]
    for row, m in zip(rows, meas):
        for key, text in notes:
            if key and (key == row["segment_id"] or key in m["source"]):
                row["notes"] = re.sub(r"casting=[^;,]+[;,]?\s*", "", text).strip()
    with open(ledger_p, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    ok = L.validate(str(ledger_p)) == 0
    md_p.write_text(S.markdown(rows, "Shots: %s" % Path(a.src).name))
    unread = sorted({k for r in rows for k, d in r["fields"].items() if d["value"] == L.UNREAD})
    summary = {"schema": "read-shots/1", "made": dt.datetime.now().isoformat(timespec="seconds"), "source": str(a.src),
               "shots": len(rows), "clips": len({r["clip_id"] for r in rows}), "valid": ok,
               "indistinct": [r["segment_id"] for r in rows if r.get("indistinct")],
               "arc_shapes": {s: sum(1 for r in rows if r["fields"]["arc_shape"]["value"] == s) for s in
                              sorted({r["fields"]["arc_shape"]["value"] for r in rows})},
               "cut_engine": sorted(cut_engines), "lanes": lane_notes, "skipped": skipped, "pending": pending,
               "unread_pending_vision_lane": unread,
               "files": {"ledger": ledger_p.name, "sentences": md_p.name, "measures": meas_p.name, "summary": sum_p.name}}
    sum_p.write_text(json.dumps(summary, indent=1))
    print("read_shots: %d shot(s) from %d clip(s) -> %s, %s, %s (%s)" % (len(rows), summary["clips"], ledger_p, md_p, sum_p,
                                                                       "valid" if ok else "INVALID"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
