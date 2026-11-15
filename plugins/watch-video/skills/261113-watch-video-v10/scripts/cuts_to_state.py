#!/usr/bin/env python3
"""cuts_to_state.py — one call from a video to a WATCH_STATE with shots in time order (R1 + R2, 261114).

  python3 cuts_to_state.py <job_dir> <video> [--see SEE/watch.json] [--title "Day 1 Intro"]
                           [--seq 101] [--threshold T] [--min-scene 0.6] [--engine adaptive|ffmpeg] [--no-frames]

1. init <job_dir>/WATCH_STATE.json for <video> (job = --title, default the file name);
2. detect cuts (detect_cuts.py, imported) in FRAMES at the file's real fps; the cut list is
   kept in the state under "cuts" (frames + seconds), no side file;
3. scenes from SEE segments (watch.json; the default is <job_dir>/SEE/watch.json when it
   exists) for this clip, else one scene over the whole clip;
4. one shot per cut interval, never dropped for being short (a 1-frame shot is kept);
   tc_in / tc_out in seconds = frame / fps, frame_in / frame_out (exclusive) in frames;
5. each shot is filed under the scene whose start is the latest at or before the shot's
   midpoint (the first scene when it starts before every scene), never "the last scene";
   scene spans then grow to cover their shots; empty scenes are dropped; shots in time order;
6. one midpoint frame per shot at <job_dir>/frames/<shot id>.jpg (skip with --no-frames);
7. sound fields are strings (""), verified False; the state is written once, atomically.

Plugin-only (not ENGINE). Adapted from the 261113 glue watch_build_state.py + watch_state_fix.py.
"""
import argparse, json, os, subprocess, sys
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import detect_cuts  # noqa: E402  (engine)
import watch_state  # noqa: E402  (engine)


def shot_bounds(cut_frames, total_frames):
    """[(frame_in, frame_out_exclusive)], every interval of length >= 1 frame kept."""
    cuts = sorted({int(c) for c in cut_frames if 0 < int(c) < total_frames})
    edges = [0] + cuts + [total_frames]
    return [(a, b) for a, b in zip(edges, edges[1:]) if b > a]


def see_scenes(see_path, video, duration):
    """[(slug, t_in, t_out)] from SEE segments for this clip, sorted by start; [] if none."""
    try:
        with open(see_path) as f:
            segs = json.load(f).get("segments") or []
    except (OSError, ValueError):
        return []
    name = os.path.basename(video)
    mine = [g for g in segs if g.get("clip") == name]
    segs = mine or ([] if any("clip" in g for g in segs) and len({g.get("clip") for g in segs}) > 1 else segs)
    out = []
    for g in segs:
        try:
            t_in, t_out = float(g["t_in"]), float(g["t_out"])
        except (KeyError, TypeError, ValueError):
            continue
        tag = ((g.get("tags") or {}).get("content") or {}).get("value") or "SEG"
        out.append((str(tag).upper().replace(" ", "_"), t_in, min(t_out, duration)))
    return sorted(out, key=lambda s: s[1])


def file_shots(scenes, shots):
    """scenes: [dict(id, slug, tc_in, tc_out, shots=[])] ; shots: [dict(tc_in, tc_out, ...)].
    Files by midpoint into the latest scene starting at or before it; returns non-empty scenes
    in time order with spans grown to cover their shots."""
    scs = sorted(scenes, key=lambda s: s["tc_in"])
    for sc in scs:
        sc["shots"] = []
    for sh in sorted(shots, key=lambda s: s["tc_in"]):
        mid = (sh["tc_in"] + sh["tc_out"]) / 2
        at = [s for s in scs if s["tc_in"] <= mid]
        (at[-1] if at else scs[0])["shots"].append(sh)
    for sc in scs:
        if sc["shots"]:
            sc["tc_in"] = min(sc["tc_in"], sc["shots"][0]["tc_in"])
            sc["tc_out"] = max(sc["tc_out"], sc["shots"][-1]["tc_out"])
    keep = [s for s in scs if s["shots"]]
    return sorted(keep, key=lambda s: s["tc_in"])


def grab(video, t, dst):
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", "%.4f" % t, "-i", video,
                    "-frames:v", "1", "-vf", "scale=640:-2", dst], check=True)


def build(job_dir, video, see=None, title=None, seq="101", threshold=None, min_scene=0.6,
          engine="adaptive", frames=True, cuts=None):
    """Returns (state_path, summary). `cuts` (a detect_cuts result) skips detection (tests)."""
    os.makedirs(job_dir, exist_ok=True)
    cuts = cuts or detect_cuts.detect_cuts(video, threshold, min_scene, engine)
    fps = Fraction(cuts["fps_rational"])
    total = int(cuts["duration_frames"])
    sec = lambda f: round(float(Fraction(f) / fps), 4)  # noqa: E731
    st = watch_state.new_state(video, title or os.path.splitext(os.path.basename(video))[0], seq)
    st["cuts"] = {k: cuts[k] for k in ("engine", "fps", "fps_rational", "duration_frames", "min_scene_s",
                                       "min_scene_frames", "cut_frames", "cut_times") if k in cuts}
    dur = sec(total)
    sc_src = see_scenes(see, video, dur) if see else []
    scenes = [{"id": "", "slug": slug, "tc_in": a, "tc_out": b, "see_segment": i + 1}
              for i, (slug, a, b) in enumerate(sc_src)] or \
             [{"id": "", "slug": "WHOLE_CLIP", "tc_in": 0.0, "tc_out": dur}]
    shots = []
    for n, (a, b) in enumerate(shot_bounds(cuts["cut_frames"], total), 1):
        sid = "%s-%d" % (seq, n)
        shots.append({"id": sid, "type": "?", "tc_in": sec(a), "tc_out": sec(b),
                      "frame_in": a, "frame_out": b, "frames": b - a,
                      "frame": "frames/%s.jpg" % sid, "desc": "",
                      "snd_in": "", "snd_off": "", "snd_beat": "", "verified": False})
    filed = file_shots(scenes, shots)
    for i, sc in enumerate(filed, 1):
        sc["id"] = "SC%02d" % i
    st["scenes"] = filed
    if frames:
        os.makedirs(os.path.join(job_dir, "frames"), exist_ok=True)
        for sh in shots:
            mid = (sh["frame_in"] + sh["frame_out"] - 1) // 2   # the shot's own middle frame
            grab(video, float((Fraction(mid) + Fraction(1, 4)) / fps), os.path.join(job_dir, sh["frame"]))
    sp = os.path.join(job_dir, "WATCH_STATE.json")
    tmp = sp + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=2)
    os.replace(tmp, sp)
    return sp, {"state": sp, "engine": cuts["engine"], "fps": cuts["fps_rational"], "duration_frames": total,
                "cuts": len(cuts["cut_frames"]), "shots": len(shots), "scenes": len(filed),
                "scene_source": "SEE" if sc_src else "one scene"}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cuts_to_state.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("job_dir")
    ap.add_argument("video")
    ap.add_argument("--see", help="SEE watch.json (default <job_dir>/SEE/watch.json when present)")
    ap.add_argument("--title", help="readable title stored as the state's job (default: the file name)")
    ap.add_argument("--seq", default="101")
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--min-scene", type=float, default=0.6, help="detector minimum scene length, seconds")
    ap.add_argument("--engine", choices=["adaptive", "ffmpeg"], default="adaptive")
    ap.add_argument("--no-frames", action="store_true", help="do not pull the midpoint frames")
    a = ap.parse_args(argv)
    see = a.see
    if see and not os.path.exists(see) and os.path.exists(os.path.join(a.job_dir, see)):
        see = os.path.join(a.job_dir, see)
    if see and not os.path.exists(see):
        sys.exit("no SEE file at %s" % see)
    if not see and os.path.exists(os.path.join(a.job_dir, "SEE", "watch.json")):
        see = os.path.join(a.job_dir, "SEE", "watch.json")
    _, summary = build(a.job_dir, a.video, see, a.title, a.seq, a.threshold, a.min_scene, a.engine,
                       not a.no_frames)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
