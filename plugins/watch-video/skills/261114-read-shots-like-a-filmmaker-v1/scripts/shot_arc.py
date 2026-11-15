#!/usr/bin/env python3
"""shot_arc.py: the arc of a shot, head -> mid -> tail, sampled where the measured change spikes (Sexton section 3).

  shot_arc.py <video> --in S --out S [--n 3|5] [--assume-transfer rec709|log|hlg]   # prints JSON

Frames at 10 / 50 / 90 % of the window (10 / 30 / 50 / 70 / 90 % for holds over 8 s, or --n 5), plus one frame where a
measured change spikes on a 12-frame scan: subject-box size, flow magnitude, sharpness, or frame difference against the
head frame (shot_measure functions). Each arc frame runs shot_measure.measure on itself + its next frame, and gets a size
word, a subject state (in / entering / leaving / absent) and a focus word (sharp / soft). From those: arc_head / mid / tail,
arc_shape (closed: held, push, pull/reveal, pass-through, rack, soft, whip, follow, orbit, enters/leaves) and arc_energy
(dynamic / modest / held), plus the measured slots direction, manner and sun_warmth. Machine proposes; trust machine~.
"""
import argparse
import json
import math
import os
import subprocess
import sys
import tempfile

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shot_ledger as L  # noqa: E402
import shot_measure as SM  # noqa: E402

SIZES = ["EWS", "WS", "MWS", "MS", "MCU", "CU", "ECU"]
STATE_WORD = {"in": "subject", "entering": "entering", "leaving": "leaving", "absent": "empty", "part": "part"}
MANNER = {"still": "still", "slow": "slowly", "medium": "steadily", "fast": "fast"}


def grab2(path, t, tmp, tag, step):
    """Frame at t and the next frame (for motion), full resolution."""
    out = os.path.join(tmp, tag + "_%02d.png")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "%.3f" % max(0.0, t), "-i", str(path), "-frames:v", "2", out],
                   capture_output=True, timeout=120)
    fr = [cv2.imread(out % i) for i in (1, 2)]
    return [f for f in fr if f is not None]


def scan(path, t_in, t_out, n=12, width=160):
    """n small frames across the window in one ffmpeg pipe."""
    probe = SM.ffprobe(path)
    w, h = probe.get("width") or 320, probe.get("height") or 180
    sh = max(2, int(round(width * h / w / 2)) * 2)
    dur = max(0.05, t_out - t_in)
    cmd = ["ffmpeg", "-v", "error", "-ss", "%.3f" % t_in, "-t", "%.3f" % dur, "-i", str(path), "-vf",
           "fps=%.4f,scale=%d:%d" % (n / dur, width, sh), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    raw = subprocess.run(cmd, capture_output=True, timeout=300).stdout
    sz = width * sh * 3
    frames = [np.frombuffer(raw[i:i + sz], np.uint8).reshape(sh, width, 3) for i in range(0, len(raw) - sz + 1, sz)]
    return frames[:n], (dur / max(1, len(frames[:n])))


def box_h(bgr):
    b = SM.face_boxes(bgr) or SM.person_boxes(bgr)
    return max(q[3] for q in b) / bgr.shape[0] if b else 0.0


def spike(path, t_in, t_out):
    """Position (0-1) and signal of the largest measured change between successive scan frames, or None."""
    fr, dt = scan(path, t_in, t_out)
    if len(fr) < 3:
        return None
    g = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in fr]
    diff = [float(np.mean(np.abs(x.astype(np.int16) - g[0].astype(np.int16)))) for x in g]
    sharp = [math.log1p(SM.m_sharpness(x)["laplacian_var"]) for x in g]
    boxes = [box_h(f) for f in fr]
    flow = [0.0] + [SM.m_motion([a, b], dt)[0]["speed_pct_w"] for a, b in zip(fr, fr[1:])]
    med = float(np.median(flow[1:])) or 1.0
    best = None
    for i in range(1, len(fr)):
        cand = {"frame difference vs head": abs(diff[i] - diff[i - 1]) / 12.0,
                "sharpness": abs(sharp[i] - sharp[i - 1]) / 0.7,
                "subject-box size": abs(boxes[i] - boxes[i - 1]) / 0.15,
                "flow magnitude": (flow[i] / med / 2.0) if flow[i] > 10 else 0.0}
        sig, sc = max(cand.items(), key=lambda kv: kv[1])
        if sc >= 1.0 and (best is None or sc > best[2]):
            best = ((i + 0.5) / len(fr), sig, sc)
    return best


def frame_read(m, size_word):
    sub = (m.get("subject") or {}).get("value") or {}
    box = sub.get("box") if isinstance(sub, dict) else None
    if not box:
        state = "absent"
    elif box["x"] < 0.02 or box["x"] + box["w"] > 0.98:
        state = "edge"
    else:
        state = "in"
    sh = (m.get("sharpness") or {}).get("value") or {}
    return {"size": size_word, "subject_state": state, "sharp": sh.get("sharp_region", {}).get("laplacian_var", 0.0),
            "cell": sh.get("sharp_region", {}).get("cell_row_col"), "grid": sh.get("grid"), "box": box}


def arc(path, t_in, t_out, n=None, assume=None):
    probe = SM.ffprobe(path)
    fps = probe.get("fps") or 24.0
    dur = max(0.0, t_out - t_in)
    n = n or (5 if dur > 8 else 3)
    pos = [0.1, 0.5, 0.9] if n == 3 else [0.1, 0.3, 0.5, 0.7, 0.9]
    sp = spike(path, t_in, t_out) if dur > 0.5 else None
    if sp and all(abs(sp[0] - p) > 0.08 for p in pos):
        pos = sorted(pos + [round(sp[0], 3)])
    tr, trs = SM.transfer_of(path, probe, assume)
    step = 1.0 / fps
    frames, measures = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for i, p in enumerate(pos):
            t = t_in + p * dur
            fr = grab2(path, min(t, max(t_in, t_out - 2 * step)), tmp, "a%d" % i, step)
            if not fr:
                continue
            m = SM.measure(fr, tr, trs, probe, step)
            w = L.words({"measures": m})
            measures.append((p, t, m, w))
            frames.append(dict(frame_read(m, w.get("shot_size", (L.U,))[0]), pos=p, t=round(t, 3)))
    if not frames:
        return {"status": "pending", "why": "ffmpeg could not decode a frame in the window"}
    # focus words, relative to the sharpest arc frame (absolute floor for frames soft overall)
    top = max(f["sharp"] for f in frames) or 1.0
    for f in frames:
        f["focus"] = "soft" if f["sharp"] < 50 or f["sharp"] < 0.35 * top else "sharp"
    # edge boxes: entering at the head side, leaving at the tail side
    for i, f in enumerate(frames):
        if f["subject_state"] == "edge":
            f["subject_state"] = "entering" if i < len(frames) / 2 else "leaving"
    head, mid, tail = frames[0], frames[len(frames) // 2], frames[-1]
    mids = measures[len(measures) // 2]
    mw = mids[3]
    mo = (mids[2].get("motion") or {}).get("value")
    cam = mo.get("camera", {}) if isinstance(mo, dict) else {}
    idx = lambda f: SIZES.index(f["size"]) if f["size"] in SIZES else None  # noqa: E731
    sizes = [idx(f) for f in frames if idx(f) is not None]
    steps = (sizes[-1] - sizes[0]) if len(sizes) >= 2 else 0
    span = (max(sizes) - min(sizes)) if sizes else 0
    states = [f["subject_state"] for f in frames]
    speeds = [((m.get("motion") or {}).get("value") or {}) for _, _, m, _ in measures]
    cam_speed = [s.get("camera", {}).get("speed_pct_w", 0) for s in speeds if isinstance(s, dict)]
    zooms = [s.get("camera", {}).get("zoom_pct", 0) for s in speeds if isinstance(s, dict)]
    moving = cam_speed and float(np.median(cam_speed)) >= 6
    why = []
    if cam_speed and max(cam_speed) >= 60:
        shape = "whip"; why.append("camera %.0f%% width/s at one sample" % max(cam_speed))
    elif all(f["focus"] == "soft" for f in frames):
        shape = "soft"; why.append("every arc frame soft (max cell Laplacian %.0f)" % top)
    elif states[0] in ("absent", "entering") and states[-1] in ("absent", "leaving") and "in" in states[1:-1]:
        shape = "pass-through"; why.append("subject %s" % " / ".join(states))
    elif states[0] != states[-1] and ("absent" in (states[0], states[-1]) or {"entering", "leaving"} & {states[0], states[-1]}):
        shape = "enters/leaves"; why.append("subject %s" % " / ".join(states))
    elif steps >= 1 or (zooms and float(np.median(zooms)) >= 8):
        shape = "push"; why.append("size %s -> %s" % (head["size"], tail["size"]))
    elif steps <= -1 or (zooms and float(np.median(zooms)) <= -8):
        shape = "pull/reveal"; why.append("size %s -> %s" % (head["size"], tail["size"]))
    elif rack(head, tail) and not moving:
        shape = "rack"; why.append("sharpest cell %s -> %s, the head cell lost over half its sharpness, camera still" % (head["cell"], tail["cell"]))
    elif moving and "in" in states and span == 0:
        drone = "DJI" in str(path).upper()
        shape = "orbit" if drone else "follow"; why.append("camera %.1f%% width/s, subject size held" % float(np.median(cam_speed)))
    else:
        shape = "held"; why.append("no size step, no move, subject stays")
    energy = "dynamic" if span >= 2 or shape in ("pull/reveal", "pass-through", "whip") else "held" if shape == "held" else "modest"
    phrase = lambda f: "%s %s %s" % (f["size"] if f["size"] != L.U else "frame", STATE_WORD[f["subject_state"]], f["focus"])  # noqa
    g = {"arc_frames": {"value": [{k: f[k] for k in ("pos", "size", "subject_state", "focus")} for f in frames],
                        "evidence": "%d frames at %s of the window" % (len(frames), ", ".join("%d%%" % (f["pos"] * 100) for f in frames))},
         "arc_head": {"value": phrase(head)}, "arc_mid": {"value": phrase(mid)}, "arc_tail": {"value": phrase(tail)},
         "arc_shape": {"value": shape, "evidence": "; ".join(why)},
         "arc_energy": {"value": energy, "evidence": "size span %d step(s)" % span}}
    # direction from the subject box head -> tail
    hb, tb = head["box"], tail["box"]
    if hb and tb:
        dx = (tb["x"] + tb["w"] / 2) - (hb["x"] + hb["w"] / 2)
        gr = tb["h"] / max(hb["h"], 1e-3)
        d = ("left to right" if dx > 0 else "right to left") if abs(dx) > 0.15 else \
            "toward camera" if gr > 1.3 else "away from camera" if gr < 0.75 else "static"
        g["direction"] = {"value": d, "evidence": "box centre %+.2f of width, height x%.2f head to tail" % (dx, gr)}
    if "subject_motion" in mw:
        g["manner"] = {"value": MANNER[mw["subject_motion"][0]], "evidence": mw["subject_motion"][1]}
    g["sun_warmth"] = sun_warmth(grab_mid(measures, path), tr)
    return {"status": "seen", "path": str(path), "t_in": round(t_in, 3), "t_out": round(t_out, 3), "transfer": tr,
            "transfer_source": trs, "spike": {"pos": sp[0], "signal": sp[1], "score": round(sp[2], 2)} if sp else None,
            "frames": [{k: f[k] for k in ("pos", "t", "size", "subject_state", "focus")} for f in frames],
            "grammar": g, "mid_measures": mids[2], "hero_t": round(mids[1], 3), "camera": cam}


def rack(head, tail):
    """Focus changed plane: the sharpest cell moves AND the head's sharp cell goes soft (not just a moving pattern)."""
    if not (head["cell"] and tail["cell"] and head["grid"] and tail["grid"]) or head["cell"] == tail["cell"]:
        return False
    (hr, hc), (tr_, tc) = head["cell"], tail["cell"]
    return tail["grid"][hr][hc] < 0.5 * head["grid"][hr][hc] and head["grid"][tr_][tc] < 0.5 * tail["grid"][tr_][tc]


def grab_mid(measures, path):
    p, t, m, w = measures[len(measures) // 2]
    with tempfile.TemporaryDirectory() as tmp:
        fr = grab2(path, t, tmp, "w", 0)
    return fr[0] if fr else None


def sun_warmth(bgr, transfer):
    """Red/blue ratio of the brightest 5 % of the frame (Sexton: redder = sunset has a number under it)."""
    if bgr is None or transfer != "Rec.709":
        return {"value": L.U, "evidence": "transfer %s: highlight colour not read" % transfer}
    y = SM.luma(bgr)
    sel = y >= np.percentile(y, 95)
    b, g, r = [float(bgr[..., c][sel].mean()) + 1.0 for c in range(3)]
    k = r / b
    word = "sunset-red" if k > 1.6 else "warm" if k > 1.15 else "cool" if k < 0.9 else "neutral"
    return {"value": word, "evidence": "highlight R/B %.2f (top 5%% luma)" % k}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("video")
    ap.add_argument("--in", dest="t_in", type=float, default=0.0)
    ap.add_argument("--out", dest="t_out", type=float, help="seconds (default: clip end)")
    ap.add_argument("--n", type=int, choices=[3, 5])
    ap.add_argument("--assume-transfer", choices=sorted(SM.ASSUME))
    a = ap.parse_args(argv)
    t_out = a.t_out if a.t_out is not None else (SM.ffprobe(a.video).get("duration_s") or 0.0)
    res = arc(a.video, a.t_in, t_out, a.n, a.assume_transfer)
    res.pop("mid_measures", None)
    print(json.dumps(res, indent=1))
    return 0 if res.get("status") == "seen" else 1


if __name__ == "__main__":
    sys.exit(main())
