#!/usr/bin/env python3
"""extract_frames.py — idempotent frame puller (v3). Skips frames that already exist.

Usage:
  python3 extract_frames.py --video V.mp4 --dir frames --grid 0.5                # full 0.5s grid
  python3 extract_frames.py --video V.mp4 --dir frames --grid 0.5 --range 15 21  # grid over a range
  python3 extract_frames.py --video V.mp4 --dir frames --windows probe.json      # 0.25s around scored cuts
  python3 extract_frames.py --video V.mp4 --dir frames --times 3.0,6.0,19.0      # specific reps
Add --state WATCH_STATE.json to log pulled frame paths into state["frames"].
"""
import subprocess, sys, os, json

def arg(name, default=None):
    return sys.argv[sys.argv.index(name)+1] if name in sys.argv else default

def duration(video):
    out = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
        "-of","default=noprint_wrappers=1:nokey=1",video],capture_output=True,text=True).stdout
    return float(out)

def pull(video, t, d):
    path = os.path.join(d, f"f_{t}.jpg")
    if os.path.exists(path): return path, False
    subprocess.run(["ffmpeg","-loglevel","error","-ss",str(t),"-i",video,
                    "-frames:v","1","-q:v","2",path],check=True)
    return path, True

def main():
    video = arg("--video"); d = arg("--dir","frames"); os.makedirs(d, exist_ok=True)
    times = []
    if "--grid" in sys.argv:
        step = float(arg("--grid","0.5")); dur = duration(video)
        lo, hi = 0.0, dur
        if "--range" in sys.argv:
            i = sys.argv.index("--range"); lo, hi = float(sys.argv[i+1]), float(sys.argv[i+2])
        t = lo
        while t < min(hi, dur):
            times.append(round(t,2)); t += step
    if "--windows" in sys.argv:
        probe = json.load(open(arg("--windows")))
        for w in probe.get("refine_windows_0.25s", []):
            t = w[0]
            while t <= w[1]:
                times.append(round(t,2)); t += 0.25
    if "--times" in sys.argv:
        times += [round(float(x),2) for x in arg("--times").split(",")]
    times = sorted(set(times))
    new = 0; paths = {}
    for t in times:
        p, fresh = pull(video, t, d)
        paths[str(t)] = p; new += fresh
    print(json.dumps({"requested":len(times),"pulled_new":new,"dir":d}))
    sp = arg("--state")
    if sp:
        s = json.load(open(sp)); s.setdefault("frames",{}).update(paths)
        json.dump(s, open(sp,"w"), indent=2); print(f"logged {len(paths)} frame paths -> {sp}")

if __name__ == "__main__":
    main()
