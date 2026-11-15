#!/usr/bin/env python3
"""shot_motion.py: how the camera moves, measured from optical flow (CameraBench primitives, Sexton's closed list).

Open vision models answer camera motion at chance (CameraBench, NeurIPS 2025; Palmer, research_shot_grammar.md), so the
MOVE slot comes from flow, never from a model. Per frame pair: Shi-Tomasi corners + pyramidal Lucas-Kanade (ORB match as the
fallback), RANSAC partial affine = the camera (dx, dy, scale, roll); the inlier residual = parallax; the outliers = the subject.

  measure(frames: list[path|ndarray] | video_path, dt=None, fps=None, t0=None, t1=None, n=6) -> {field: {value, trust, rule, note}}
  shot_motion.py <frames...|video|frames_dir> [--fps F | --dt S] [--t0 S --t1 S] --out motion.jsonl

Fields: camera_move, speed, steadiness, subject_manner, subject_direction, motion_numbers. Trust word: measured.
"""
import argparse
import json
import math
import os
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shot_measure import IMG_EXT, VID_EXT, M, ffprobe, grab, luma, r  # noqa: E402

T = "measured"
MOVES = ["static", "pan left", "pan right", "tilt up", "tilt down", "push in", "pull out", "zoom in", "zoom out",
         "truck left", "truck right", "pedestal up", "pedestal down", "roll", "handheld drift", "orbit", "follow"]


# ---------------------------------------------------------------- frames in (shared with shot_light / shot_decon)
def load_frames(frames, fps=None, t0=None, t1=None, n=6, step=None):
    """list of paths / BGR arrays, a frames dir, or a video -> (list of BGR, dt seconds between frames or None, source)."""
    if isinstance(frames, (str, Path)):
        p = Path(frames)
        if p.is_dir():
            frames = sorted(x for x in p.iterdir() if x.suffix.lower() in IMG_EXT)
        elif p.suffix.lower() in VID_EXT or p.suffix.lower() not in IMG_EXT:
            pr = ffprobe(p)
            f = fps or pr.get("fps") or 24.0
            dur = pr.get("duration_s") or 0
            a = t0 if t0 is not None else (dur / 2 if dur else 0.0)
            st = step or (((t1 - a) / max(1, n - 1)) if t1 is not None else 5.0 / f)
            with tempfile.TemporaryDirectory() as tmp:
                out = [g for g in (grab(p, a + k * st, tmp, k) for k in range(n)) if g is not None]
            return out, st, str(p)
        else:
            frames = [p]
    out = []
    for f in frames:
        img = f if isinstance(f, np.ndarray) else cv2.imread(str(f))
        if img is not None:
            out.append(img)
    dt = (1.0 / fps) if fps else None
    return out, dt, "frames"


def _gray(img, w=480):
    h0, w0 = img.shape[:2]
    sw = min(w, w0)
    return cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (sw, max(8, int(sw * h0 / w0))), interpolation=cv2.INTER_AREA)


# ---------------------------------------------------------------- the move, decided by AXIS (Diego, Q016 / D024)
# "Left to right is a pan, up and down is a tilt; if the whole camera moves left to right that is tracking or trucking;
#  a dolly in is a push-in. Pushing in has to be movement on the Z axis, not XY. A difference matte equals movement, so it
#  will always show something; that is why you get false positives."
# XY  = translation of the similarity fit at frame centre (the whole field shifts the same amount).
# Z   = the scale term of the similarity fit (the field expands / contracts about a point). Never a frame difference.
# PAR = parallax: spread of local motion across a 4x4 grid of cells, against the global move. Near things move more.
Z_FLOOR = 2.0        # % of frame per second: below this there is no Z move, whatever anything else says
XY_FLOOR = 0.6       # % of frame width per second: below this there is no XY move
PAR_THRESH = 0.25    # coefficient of variation of the per-cell motion along the move. Chosen from the synthetics in
#                      tests/test_motion_light_decon.py: a flat translated or scaled plane gives <= 0.05 (tracker noise);
#                      a near layer covering a quarter of the frame moving 2x gives ~0.43; one near cell in 16 at 2x gives
#                      ~0.2 and is left as no parallax. Uncalibrated on real stock: revisit when Diego marks real trucks.
GRID_N = 4


def _pair(a, b):
    ga, gb = _gray(a), _gray(b)
    sw = ga.shape[1]
    H, W = ga.shape
    src = dst = None
    p0 = cv2.goodFeaturesToTrack(ga, 800, 0.01, 6)
    if p0 is not None and len(p0) >= 15:
        p1, st, _ = cv2.calcOpticalFlowPyrLK(ga, gb, p0, None, winSize=(21, 21), maxLevel=4)
        ok = st.ravel() == 1
        if ok.sum() >= 15:
            src, dst, how = p0[ok].reshape(-1, 2), p1[ok].reshape(-1, 2), "LK"
    if src is None:  # far-apart arc frames: ORB descriptors survive big moves
        orb = cv2.ORB_create(1500)
        ka, da = orb.detectAndCompute(ga, None)
        kb, db = orb.detectAndCompute(gb, None)
        if da is None or db is None:
            return None
        mt = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db)
        if len(mt) < 12:
            return None
        src = np.float32([ka[m.queryIdx].pt for m in mt])
        dst = np.float32([kb[m.trainIdx].pt for m in mt])
        how = "ORB"
    A, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=1.5)
    if A is None:
        return None
    inl = inl.ravel().astype(bool)
    c = np.array([W / 2.0, H / 2.0], np.float32)
    gx = float(A[0, 0] * c[0] + A[0, 1] * c[1] + A[0, 2] - c[0])          # global XY at frame centre, px
    gy = float(A[1, 0] * c[0] + A[1, 1] * c[1] + A[1, 2] - c[1])
    scale = math.hypot(A[0, 0], A[1, 0])
    flow = dst - src
    # per-cell local motion (median of the tracked points in each cell)
    cx = np.clip((src[:, 0] / W * GRID_N).astype(int), 0, GRID_N - 1)
    cy = np.clip((src[:, 1] / H * GRID_N).astype(int), 0, GRID_N - 1)
    along, radial = [], []
    g = np.array([gx, gy])
    gn = np.linalg.norm(g)
    for i in range(GRID_N):
        for j in range(GRID_N):
            m = (cy == i) & (cx == j)
            if m.sum() < 3:
                continue
            t = np.median(flow[m], 0)
            pc = np.median(src[m], 0) - c
            rn = np.linalg.norm(pc)
            if gn > 1e-6:
                along.append(float(t @ g / gn))                              # local shift along the global move
            if rn > 0.15 * min(W, H):
                radial.append(float((t - g) @ pc / rn / rn))                 # local scale about the centre
    par_xy = float(np.std(along) / (abs(np.mean(along)) + 1e-6)) if len(along) >= 4 and gn > 0.05 else 0.0
    par_z = float(np.std(radial) / (abs(np.mean(radial)) + 1e-6)) if len(radial) >= 4 and abs(scale - 1) > 1e-4 else 0.0
    pred = src @ A[:, :2].T + A[:, 2]
    res = dst - pred
    sub = None
    out_idx = ~inl
    if out_idx.sum() >= max(6, 0.01 * len(src)):
        rv = res[out_idx]
        mag = np.linalg.norm(rv, axis=1)
        keep = mag > 1.0
        if keep.sum() >= 4:
            pts = src[out_idx][keep]
            v = rv[keep]
            cc = pts.mean(0)
            rad = pts - cc
            div = float(np.mean(np.sum(rad * v, 1) / (np.linalg.norm(rad, axis=1) ** 2 + 1)))   # >0 spreading = toward
            sub = {"dx": float(np.median(v[:, 0])) / sw, "dy": float(np.median(v[:, 1])) / sw, "mag": float(np.median(mag)) / sw,
                   "div": div, "share": float(keep.sum()) / len(src)}
    ya, yb = luma(cv2.cvtColor(ga, cv2.COLOR_GRAY2BGR)), luma(cv2.cvtColor(gb, cv2.COLOR_GRAY2BGR))
    return {"dx": gx / sw, "dy": gy / sw, "scale": scale, "rot": math.degrees(math.atan2(A[1, 0], A[0, 0])),
            "par_xy": par_xy, "par_z": par_z, "inliers": float(inl.mean()), "n": int(len(src)), "how": how, "sub": sub,
            "diff_share": float((np.abs(ya - yb) > 12).mean())}


def _speed_word(v, slow, fast):
    return "slow" if v < slow else "medium" if v < fast else "fast"


def measure(frames, dt=None, fps=None, t0=None, t1=None, n=6, **kw):
    imgs, ddt, src = load_frames(frames, fps=fps, t0=t0, t1=t1, n=n)
    dt = dt or ddt
    keys = ("camera_move", "speed", "steadiness", "subject_manner", "subject_direction")
    if len(imgs) < 2:
        u = M("unknown", T, "needs two frames", "fewer than two frames decoded")
        return {k: u for k in keys}
    pairs = [p for p in (_pair(a, b) for a, b in zip(imgs, imgs[1:])) if p]
    if not pairs:
        u = M("unknown", T, "RANSAC similarity on tracked corners", "no trackable texture between frames")
        return {k: u for k in keys}
    per = (lambda v: v / dt * 100) if dt else (lambda v: v * 100 * 24)      # no fps: assume 24 fps for the floors only
    unit = "% of frame width per second" if dt else "% of frame width per second ASSUMING 24 fps (no fps given)"
    dx = per(np.mean([p["dx"] for p in pairs]))
    dy = per(np.mean([p["dy"] for p in pairs]))
    z = per(np.mean([p["scale"] for p in pairs]) - 1)
    rot = np.mean([p["rot"] for p in pairs]) / (dt if dt else 1 / 24)
    pxy = float(np.median([p["par_xy"] for p in pairs]))
    pz = float(np.median([p["par_z"] for p in pairs]))
    tr = math.hypot(dx, dy)
    jit = None
    if len(pairs) >= 2:
        jit = per(float(np.mean([math.hypot(b["dx"] - a["dx"], b["dy"] - a["dy"]) for a, b in zip(pairs, pairs[1:])])))
    subs = [p["sub"] for p in pairs if p["sub"]]
    has_z, has_xy = abs(z) >= Z_FLOOR, tr >= XY_FLOOR
    note = None
    # RULE (D024): Z decides push/pull vs zoom by parallax; XY decides pan/tilt vs truck/pedestal by parallax.
    if has_z and abs(z) >= tr * 0.5:
        if pz >= PAR_THRESH:
            move = "push in" if z > 0 else "pull out"
            note = "Z with parallax %.2f >= %.2f: the camera travelled (dolly)" % (pz, PAR_THRESH)
        else:
            move = "zoom in" if z > 0 else "zoom out"
            note = "Z without parallax (%.2f < %.2f): the field scales as one plane (lens zoom, or a still pushed in post)" % (pz, PAR_THRESH)
        sw = _speed_word(abs(z), 4, 8)
    elif has_xy:
        follow = bool(subs) and tr > XY_FLOOR * 4 and np.mean(
            [math.hypot(p["sub"]["dx"] + p["dx"], p["sub"]["dy"] + p["dy"]) for p in pairs if p["sub"]]) * (
            100 / dt if dt else 2400) < tr * 0.35
        horiz = abs(dx) >= abs(dy)
        if follow:
            move = "follow"
            note = "subject holds its place in frame while the background slides; orbit is not separable from follow in 2-D flow"
        elif pxy >= PAR_THRESH:
            move = ("truck right" if dx < 0 else "truck left") if horiz else ("pedestal down" if dy < 0 else "pedestal up")
            note = "XY with parallax %.2f >= %.2f: near and far move by different amounts, the camera travelled" % (pxy, PAR_THRESH)
        else:
            move = ("pan right" if dx < 0 else "pan left") if horiz else ("tilt down" if dy < 0 else "tilt up")
            note = "XY without parallax (%.2f < %.2f): the whole field shifts as one, the camera turned" % (pxy, PAR_THRESH)
        sw = _speed_word(tr, 5, 20)
        if has_z:
            note += "; Z %.2f %%/s present but smaller than XY" % z
    elif abs(rot) >= 0.5:
        move, sw = "roll", "slow" if abs(rot) < 5 else "fast"
    else:
        move, sw = "static", "none"
        if abs(z) > 0:
            note = "Z %.2f %%/s below the %.1f floor: no push, whatever the difference matte shows" % (z, Z_FLOOR)
    if not dt and sw != "none":
        sw = "unknown"
    if jit is None:
        steady = "locked" if move == "static" else "unknown"
        snote = "steadiness needs three frames; two given"
    else:
        steady = ("locked" if move == "static" and jit < 0.5 else "steady" if jit < 2 else "handheld" if jit < 6 else "shaky")
        snote = None
        if move == "static" and steady in ("handheld", "shaky"):
            move = "handheld drift"
    nums = {"dx": r(dx, 2), "dy": r(dy, 2), "z": r(z, 2), "roll_deg_s": r(rot, 2), "parallax_xy": r(pxy, 3),
            "parallax_z": r(pz, 3), "jitter": r(jit, 2) if jit is not None else None, "unit": unit, "pairs": len(pairs),
            "tracker": sorted({p["how"] for p in pairs}), "inlier_share": r(np.mean([p["inliers"] for p in pairs]), 2),
            "difference_matte_share": r(np.mean([p["diff_share"] for p in pairs]), 3),
            "floors": {"z": Z_FLOOR, "xy": XY_FLOOR, "parallax": PAR_THRESH}}
    out = {
        "camera_move": M(move, T, "by axis (D024): similarity fit -> Z (scale) and XY (translation at centre); parallax = CV "
                         "of per-cell motion along the move (4x4); Z >= %.1f%%/s with parallax = push/pull, without = zoom; "
                         "XY >= %.1f%%/s with parallax = truck/pedestal, without = pan/tilt; the difference matte is "
                         "evidence only" % (Z_FLOOR, XY_FLOOR), note),
        "speed": M(sw, T, "XY: slow < 5, medium < 20, fast >= 20; Z: slow < 4, medium < 8, fast >= 8 (%s)" % unit,
                   None if dt else "no fps: speed word unknown"),
        "steadiness": M(steady, T, "mean change in camera translation between successive pairs: locked < 0.5, steady < 2, "
                        "handheld < 6, shaky >= 6 (% width/s)", snote),
        "motion_numbers": M(nums, T, "camera XY at frame centre, Z = scale-1, roll, parallax CVs, difference matte share "
                            "(evidence only, never a label)"),
    }
    if subs:
        sdx = per(float(np.median([s["dx"] for s in subs])))
        sdy = per(float(np.median([s["dy"] for s in subs])))
        smag = per(float(np.median([s["mag"] for s in subs])))
        div = float(np.median([s["div"] for s in subs]))
        manner = _speed_word(smag, 3, 12)
        if abs(div) > 0.02 and abs(div) * 100 > math.hypot(sdx, sdy) * (dt or 1 / 24) * 0.5:
            dirn = "toward" if div > 0 else "away"
        else:
            dirn = ("R" if sdx > 0 else "L") if abs(sdx) >= abs(sdy) else ("down" if sdy > 0 else "up")
        share = r(np.mean([s["share"] for s in subs]), 2)
        out["subject_manner"] = M(manner, T, "median residual of RANSAC outliers after the camera is removed: slow < 3, "
                                  "medium < 12, fast >= 12 %s" % unit, "outlier share %s of tracked points" % share)
        out["subject_direction"] = M(dirn, T, "outlier residual: spreading = toward, shrinking = away, else sign of dx (screen L/R)")
    else:
        out["subject_manner"] = M("still", T, "no coherent outlier motion once the camera is removed")
        out["subject_direction"] = M("none", T, "no coherent outlier motion once the camera is removed")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--fps", type=float)
    ap.add_argument("--dt", type=float, help="seconds between the given frames")
    ap.add_argument("--t0", type=float)
    ap.add_argument("--t1", type=float)
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    src = a.inputs[0] if len(a.inputs) == 1 else a.inputs
    row = {"source": src if isinstance(src, str) else list(src), "lane": "shot_motion",
           "fields": measure(src, dt=a.dt, fps=a.fps, t0=a.t0, t1=a.t1, n=a.n)}
    with open(a.out, "a") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps({k: v["value"] for k, v in row["fields"].items()}))


if __name__ == "__main__":
    main()
