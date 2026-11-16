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
PLUGIN = HERE.parent.parent.parent
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


def hero_jpg(path, t, tmp, sid):
    """Write the shot's hero frame to a file for the reader. Returns the path, or None if it cannot be written.

    The name carries the SHOT id, not just the timestamp. 261116: it was `hero_<t>.jpg` in one shared folder,
    and three clips whose hero frame both landed at 2.000 s wrote the same path. The reader caches image
    features by path, so every shot after the first was read from the FIRST shot's picture and came back with
    confident, identical answers for different footage. The wrong answer that looks right is the worst kind.
    """
    import re as _re
    import subprocess
    safe = _re.sub(r"[^A-Za-z0-9_.-]", "_", sid)[-80:]
    f = Path(tmp) / ("hero_%s_%.3f.jpg" % (safe, t))
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", "%.3f" % max(t, 0), "-i", str(path),
                        "-update", "1", "-frames:v", "1", str(f)], capture_output=True)
    return f if (r.returncode == 0 and f.exists() and f.stat().st_size > 0) else None


def read_lane_values(mod, path, t_in, t_out, r, model, tmp, measured_sid):
    """The reader, on this shot's hero frame. {slot: {value, origin R, trust, evidence}} for what it resolved.

    261116: this is what "the reader is installed but nothing uses it" was. The lane existed, the model loaded,
    and the front door never called it, so every R slot stayed unread on every machine. An unresolved slot is
    left out on purpose: the ledger then says unread, which is true, instead of carrying a guess.
    """
    f = hero_jpg(path, r["hero_t"], tmp, measured_sid)
    if f is None:
        return {}, "read_lane: could not write a hero frame (ffmpeg)"
    measured = {"segment_id": "x", "source": str(f), "measures": r["mid_measures"], "transfer": r["transfer"]}
    slots = [x for x in mod.READ_SLOTS if x not in mod.FORBIDDEN]
    fields = mod.read(str(f), measured, slots, model=model)
    out, unresolved = {}, 0
    for k, v in fields.items():
        if v["value"] in (mod.U, None):
            unresolved += 1
            continue
        out[k] = {"value": v["value"], "origin": "R", "trust": v.get("trust", "approximate"),
                  "evidence": v.get("evidence") or v.get("rule")}
        if v.get("awaits_person"):
            out[k]["awaits_person"] = True   # Diego's slot: a suggestion until he says otherwise
    return out, "read_lane: %d of %d slot(s) read on the hero frame" % (len(out), len(slots))


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
            # A test, or a run that only wants the measured half, says so and the model is never loaded:
            # 261116, the suite went from seconds to minutes once a reader was present on the machine.
            if os.environ.get("READ_SHOTS_NO_VISION"):
                notes.append("read_lane: skipped (READ_SHOTS_NO_VISION); R slots stay unread")
                continue
            model, why = mod.load_model() if hasattr(mod, "load_model") else (None, "no loader")
            if model is None:
                notes.append("read_lane: present but no usable vision model (%s); R slots stay unread" % why)
            else:
                got[name] = ("read_lane", None, "read", mod, model)
                notes.append("read_lane: reading the hero frame of every shot (%s)"
                             % ", ".join(sorted(getattr(model, "loaded", {}).values())) or "model loaded")
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


def auto_provision():
    """Fetch anything missing before the run, without asking (Diego's rule, 261116: it has to just work).

    Free, pinned, sha256-checked weights into the plugin's own folder, and never fatal: no network means a
    reduced run with a line saying what is reduced, not a failure. Set WATCH_NO_AUTO_INSTALL=1 to skip.
    Anything paid, or anything outside this folder (apt, brew, pip, sudo), is still only ever reported.
    """
    if os.environ.get("WATCH_NO_AUTO_INSTALL"):
        return ["auto-provision off (WATCH_NO_AUTO_INSTALL)"]
    try:
        import importlib.util
        f = PLUGIN / "skills" / "261113-watch-video-v10" / "scripts" / "watch_setup.py"
        if not f.exists():
            return []
        sp = importlib.util.spec_from_file_location("watch_setup", f)
        m = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(m)
        return m.ensure(download=True, quiet=False)
    except Exception as e:  # noqa: BLE001  provisioning must never sink the run
        return ["could not auto-provision: %s" % str(e)[:90]]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("src", help="a clip, a folder of clips, or a watch.json from the SEE pass")
    ap.add_argument("--out", required=True)
    ap.add_argument("--assume-transfer", choices=sorted(SM.ASSUME))
    ap.add_argument("--notes")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    reduced = auto_provision()
    ledger_p, suf = next_name(out, "ledger", ".jsonl")
    meas_p, src_p, read_p = (out / ("%s%s.jsonl" % (s, suf)) for s in ("measures", "source", "read"))
    md_p, sum_p = out / ("sentences%s.md" % suf), out / ("read_shots%s.json" % suf)
    hooks, lane_notes = lanes()
    lane_notes += ["reduced: " + n for n in reduced]  # what this machine could not provision goes in the record
    notes = read_notes(a.notes)
    clips, skipped = clips_of(a.src)
    meas, srcs, reads, cut_engines, pending = [], [], [], set(), []
    import tempfile
    _tmp = tempfile.TemporaryDirectory(prefix="read_shots_")
    tmpdir = _tmp.name
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
                    if name == "read_lane":
                        got, note = read_lane_values(entry[3], path, t_in, t_out, r, entry[4], tmpdir, sid)
                        if sid == segs[0][0] or len(lane_notes) < 40:
                            lane_notes.append("%s %s" % (sid, note))
                        rd.update(got)
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
