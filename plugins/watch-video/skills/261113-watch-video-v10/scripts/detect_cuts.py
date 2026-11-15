#!/usr/bin/env python3
"""detect_cuts.py v5 — probe cut cadence + suggest density; cuts in FRAMES at the file's real fps.

Primary engine: PySceneDetect AdaptiveDetector (two-pass rolling average —
mitigates handheld/camera-movement false positives, the exact failure mode
raw ffmpeg scene-scoring hit on this footage).
Fallback: ffmpeg scene score, if scenedetect isn't importable.
Same JSON contract either way, so downstream (state, sheet) doesn't care.

v5 (261114, R1): --help works; --json-out PATH writes the JSON as well as printing it;
fps is read from ffprobe (r_frame_rate, e.g. 24000/1001), never assumed 24; every cut is
stored as a frame number (`cut_frames`) with seconds = frame / fps (`cut_times`);
the minimum scene length is a flag in seconds (default 0.6 s), converted to frames at the
file's fps (at least 1 frame).

Scores PROPOSE; eyes DISPOSE.
"""
import argparse, json, re, subprocess, sys
from fractions import Fraction


def probe(video):
    """(duration_s, fps Fraction) from ffprobe; fps from the first video stream."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=r_frame_rate,avg_frame_rate:format=duration",
                          "-of", "json", video], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"ffprobe failed on {video}: {out.stderr.strip()[:200]}")
    j = json.loads(out.stdout)
    dur = float(j["format"]["duration"])
    st = (j.get("streams") or [{}])[0]
    fps = None
    for k in ("r_frame_rate", "avg_frame_rate"):
        v = st.get(k, "0/0")
        try:
            f = Fraction(v)
            if f > 0:
                fps = f
                break
        except (ValueError, ZeroDivisionError):
            continue
    if fps is None:
        raise SystemExit(f"no frame rate in {video}")
    return dur, fps


def min_frames(min_scene_s, fps):
    return max(1, round(min_scene_s * float(fps)))


def cuts_adaptive(video, thr, min_len_frames):
    from scenedetect import detect, AdaptiveDetector
    scenes = detect(video, AdaptiveDetector(adaptive_threshold=thr, min_scene_len=min_len_frames))
    # scene list -> cut points = each scene start after the first, as frame numbers
    fn = lambda tc: tc.frame_num if hasattr(tc, "frame_num") else tc.get_frames()  # noqa: E731 (0.6.6+ renamed it)
    return sorted(int(fn(s[0])) for s in scenes[1:]) if scenes else []


def cuts_ffmpeg(video, thr, fps, min_len_frames):
    r = subprocess.run(["ffmpeg", "-nostdin", "-i", video, "-vf", f"select='gt(scene,{thr})',showinfo",
                        "-f", "null", "-"], capture_output=True, text=True)
    frames = sorted(set(round(float(m) * fps) for m in re.findall(r"pts_time:([0-9.]+)", r.stderr)))
    kept, last = [], 0
    for f in frames:
        if f - last >= min_len_frames:
            kept.append(f); last = f
    return kept


def sec(frame, fps):
    return round(float(Fraction(frame) / fps), 4)


def detect_cuts(video, threshold=None, min_scene=0.6, engine="adaptive"):
    """Importable: returns the JSON-ready dict (cut_frames + cut_times + fps)."""
    dur, fps = probe(video)
    mlen = min_frames(min_scene, fps)
    used = engine
    if engine == "adaptive":
        try:
            frames = cuts_adaptive(video, float(3.0 if threshold is None else threshold), mlen)
        except Exception as e:
            frames = cuts_ffmpeg(video, 0.30, fps, mlen)
            used = f"ffmpeg (adaptive unavailable: {type(e).__name__})"
    else:
        frames = cuts_ffmpeg(video, float(0.30 if threshold is None else threshold), fps, mlen)
    cuts = [sec(f, fps) for f in frames]
    shots = len(cuts) + 1
    cadence = len(cuts) / dur * 10 if dur else 0
    lo, hi = round(dur * 1.0), round(dur * 1.3)
    target = min(max(shots, round(dur * 1.15)), round(dur * 1.35))
    windows = [[max(0, round(t - 0.5, 2)), round(t + 0.5, 2)] for t in cuts]
    return {
        "video": video, "engine": used, "duration_s": round(dur, 4),
        "fps": round(float(fps), 6), "fps_rational": f"{fps.numerator}/{fps.denominator}",
        "duration_frames": round(dur * fps),
        "min_scene_s": min_scene, "min_scene_frames": mlen,
        "cut_frames": frames, "cut_times": cuts, "shot_count_estimate": shots,
        "cadence_cuts_per_10s": round(cadence, 1),
        "read": "fast cutter" if cadence >= 7 else "slow cutter",
        "density_band_cells": [lo, hi], "suggested_cell_target": target,
        "padding_needed": max(0, target - shots),
        "refine_windows_0.25s": windows,
        "note": "cut_frames are the truth (first frame of the new shot); cut_times = frame / fps. "
                "scores propose; eyes dispose — verify every boundary from viewed frames",
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="detect_cuts.py", description="Detect hard cuts; print (and optionally write) "
                                 "cut frames + seconds at the file's real fps. Scores propose; eyes dispose.")
    ap.add_argument("video")
    ap.add_argument("--threshold", type=float, default=None,
                    help="adaptive threshold (default 3.0) or ffmpeg scene score (default 0.30)")
    ap.add_argument("--min-scene", type=float, default=0.6,
                    help="minimum scene length in seconds, converted to frames at the file's fps (default 0.6; floor 1 frame)")
    ap.add_argument("--engine", choices=["adaptive", "ffmpeg"], default="adaptive")
    ap.add_argument("--json-out", metavar="PATH", help="also write the JSON to PATH")
    a = ap.parse_args(argv)
    res = detect_cuts(a.video, a.threshold, a.min_scene, a.engine)
    txt = json.dumps(res, indent=2)
    if a.json_out:
        with open(a.json_out, "w") as f:
            f.write(txt + "\n")
    print(txt)


if __name__ == "__main__":
    main()
