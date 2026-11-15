#!/usr/bin/env python3
"""shot_decon.py: Diego's deconstruction passes as named, deterministic measures (Q015).

"Take a keyframe, posterize it, invert it, make it black and white, do a difference matte, and you get value out of it.
I'm deconstructing the image within frameworks that are consistent across the board." Same frame in -> same number out:
no random seeds, no models. Passes: posterize, invert, bw (black and white), difference (matte between arc frames),
edges (line geometry), depth (focus by 3x3 region). See references/deconstruction.md.

  measure(frames | video, levels=4) -> {field: {value, trust, rule, note}}
  shot_decon.py <frame|frames...|frames_dir|video> [--levels K] --out decon.jsonl
"""
import argparse
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shot_measure import M, luma, r  # noqa: E402
from shot_motion import _pair, load_frames  # noqa: E402

T = "measured"
GRID = [["top-left", "top", "top-right"], ["left", "centre", "right"], ["bottom-left", "bottom", "bottom-right"]]
WORK_W = 480


def _small(bgr):
    h, w = bgr.shape[:2]
    sw = min(WORK_W, w)
    return cv2.resize(bgr, (sw, max(8, int(round(sw * h / w)))), interpolation=cv2.INTER_AREA)


def _cells(mask):
    H, W = mask.shape
    return [[float(mask[i * H // 3:(i + 1) * H // 3, j * W // 3:(j + 1) * W // 3].mean()) for j in range(3)] for i in range(3)]


def _where(cells, k=2, floor=0.0):
    flat = sorted(((cells[i][j], GRID[i][j]) for i in range(3) for j in range(3)), reverse=True)
    return [n for v, n in flat[:k] if v > floor]


def posterize(y, k):
    lv = np.clip((y / 256.0 * k).astype(np.int32), 0, k - 1)
    counts = np.bincount(lv.ravel(), minlength=k) / lv.size
    big = int(np.argmax(counts))
    used = int((counts > 0.005).sum())
    # largest flat areas: biggest connected blob of the dominant level
    n, lab, st, cen = cv2.connectedComponentsWithStats((lv == big).astype(np.uint8), 8)
    blobs = sorted(range(1, n), key=lambda i: -st[i, cv2.CC_STAT_AREA])[:2]
    H, W = y.shape
    where = [GRID[min(2, int(cen[i][1] / H * 3))][min(2, int(cen[i][0] / W * 3))] for i in blobs]
    return M({"levels": k, "levels_used": used, "largest_level": big, "largest_share": r(counts[big], 3),
              "largest_flat_areas": where, "flat_area_share": r(st[blobs[0], cv2.CC_STAT_AREA] / y.size, 3) if blobs else 0.0},
             T, "Rec.709 luma quantised to %d equal bands; levels with > 0.5%% area; biggest 8-connected blob of the "
             "dominant band, centroid in a 3x3 grid" % k)


def invert(y):
    neg = 255 - y
    hi = (y >= 235).astype(np.uint8)                      # the blacks of the negative = what was blown
    n, _, st, _ = cv2.connectedComponentsWithStats(hi, 8)
    areas = st[1:, cv2.CC_STAT_AREA] if n > 1 else np.array([])
    blown = float(hi.mean())
    shape = "none"
    if len(areas):
        i = 1 + int(np.argmax(areas))
        w, h = st[i, cv2.CC_STAT_WIDTH], st[i, cv2.CC_STAT_HEIGHT]
        fill = st[i, cv2.CC_STAT_AREA] / max(1, w * h)
        shape = "band" if max(w, h) > 4 * min(w, h) else "blob" if fill > 0.6 else "ragged"
    top = (y >= np.percentile(y, 95)).astype(np.uint8)
    nh, _, sth, _ = cv2.connectedComponentsWithStats(top, 8)
    struct = int((sth[1:, cv2.CC_STAT_AREA] >= y.size * 0.001).sum()) if nh > 1 else 0
    return M({"highlight_structures": struct, "blown_share": r(blown, 4), "blown_shape": shape,
              "negative_p50": int(np.median(neg))}, T,
             "negative = 255 - Y'; highlight structures = 8-connected blobs of the top 5% luma >= 0.1% of frame; blown = Y' >= 235, "
             "largest blob: band if aspect > 4, blob if it fills > 60% of its box, else ragged")


def bw(y):
    hist = np.bincount(y.astype(np.uint8).ravel(), minlength=256).astype(np.float64)
    hist = np.convolve(hist / hist.sum(), np.ones(15) / 15, "same")
    p5, p50, p95 = np.percentile(y, [5, 50, 95])
    lin = lambda v: (max(v, 1.0) / 255.0) ** 2.2  # noqa: E731
    stops = math.log2(lin(p95) / lin(p5))
    peaks = [i for i in range(1, 255) if hist[i] >= hist[i - 1] and hist[i] > hist[i + 1] and hist[i] > 0.004]
    peaks = [p for p in peaks if hist[p] >= 0.35 * max(hist[q] for q in peaks)] if peaks else []
    sep = (max(peaks) - min(peaks)) if len(peaks) >= 2 else 0
    if p95 - p5 < 60:
        shape = "flat"
    elif sep > 90:
        shape = "bimodal"
    elif p50 < 60:
        shape = "low-key"
    elif p50 > 170:
        shape = "high-key"
    else:
        shape = "full-range"
    return M({"shape": shape, "tonal_range_stops": r(stops, 2), "p5": int(p5), "p50": int(p50), "p95": int(p95),
              "peaks": peaks[:4]}, T, "luma histogram (15-code smoothing): p95-p5 < 60 = flat; two peaks > 90 codes apart = "
             "bimodal; p50 < 60 low-key; > 170 high-key; else full-range; stops = log2 of linear p95/p5")


def difference(frames):
    if len(frames) < 2:
        return M("unknown", T, "absdiff of luma between arc frames", "needs two frames")
    a, b = _small(frames[0]), _small(frames[-1])
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]))
    ya, yb = luma(a), luma(b)
    d = np.abs(ya - yb) > 12
    frac = float(d.mean())
    cells = _cells(d.astype(np.float32))
    attr = "none"
    note = None
    if frac > 0.002:
        p = _pair(a, b)
        aligned_frac = frac
        if p:
            A = np.float32([[p["scale"] * math.cos(math.radians(p["rot"])), -p["scale"] * math.sin(math.radians(p["rot"])), 0],
                            [p["scale"] * math.sin(math.radians(p["rot"])), p["scale"] * math.cos(math.radians(p["rot"])), 0]])
            H, W = ya.shape
            A[0, 2] = p["dx"] * W - (A[0, 0] - 1) * W / 2 - A[0, 1] * H / 2
            A[1, 2] = p["dy"] * W - A[1, 0] * W / 2 - (A[1, 1] - 1) * H / 2
            wa = cv2.warpAffine(ya, A, (W, H), borderMode=cv2.BORDER_REFLECT)
            m = 12
            aligned_frac = float((np.abs(wa - yb) > 12)[m:-m, m:-m].mean())
            note = "after removing the camera (affine) %.3f of the frame still changes" % aligned_frac
        spread = sum(1 for row in cells for v in row if v > 0.05)
        if aligned_frac < frac * 0.35 or (spread >= 7 and frac > 0.3 and p is None):
            attr = "camera"
        elif aligned_frac > 0.002:
            attr = "subject" if spread <= 5 or aligned_frac < frac * 0.7 else "whole frame (light or cut)"
    return M({"changed_share": r(frac, 4), "where": _where(cells, 3, 0.02), "grid": [[r(v, 3) for v in row] for row in cells],
              "attribution": attr}, T, "|Y'a - Y'b| > 12 codes at 480 px between the first and last arc frame; re-tested after "
             "aligning frame a with the camera affine: change that vanishes = camera, change that stays local = subject", note)


def edges(y):
    g = y.astype(np.uint8)
    e = cv2.Canny(cv2.GaussianBlur(g, (5, 5), 0), 50, 150)
    dens = float((e > 0).mean())
    H, W = g.shape
    ls = cv2.HoughLinesP(e, 1, np.pi / 180, 40, minLineLength=max(20, W // 12), maxLineGap=6)
    bins = {"horizontal": 0.0, "vertical": 0.0, "diagonal": 0.0}
    angs = []
    if ls is not None:
        for x1, y1, x2, y2 in ls[:, 0]:
            L = math.hypot(x2 - x1, y2 - y1)
            a = math.degrees(math.atan2(-(y2 - y1), x2 - x1)) % 180
            angs.append((a, L, (x1 + x2) / 2, (y1 + y2) / 2))
            k = "horizontal" if min(a, 180 - a) < 12 else "vertical" if abs(a - 90) < 12 else "diagonal"
            bins[k] += L
    tot = sum(bins.values())
    words = []
    if tot:
        sh = {k: v / tot for k, v in bins.items()}
        if sh["horizontal"] > 0.35:
            words.append("horizon")
        if sh["vertical"] > 0.3:
            words.append("verticals")
        if sh["diagonal"] > 0.35:
            words.append("diagonals")
        diag = [(a, L, cx, cy) for a, L, cx, cy in angs if 15 < a < 165 and abs(a - 90) > 15]
        if len(diag) >= 4:      # radial: diagonals whose extensions meet near one point
            pts = []
            for i in range(len(diag)):
                for j in range(i + 1, min(len(diag), i + 8)):
                    a1, _, x1, y1 = diag[i]
                    a2, _, x2, y2 = diag[j]
                    t1, t2 = math.tan(math.radians(a1)), math.tan(math.radians(a2))
                    if abs(t1 - t2) < 0.2:
                        continue
                    x = (y1 - y2 + t1 * x1 - t2 * x2) / (t1 - t2)
                    pts.append((x, y1 - t1 * (x - x1)))
            if len(pts) >= 4:
                P = np.array(pts)
                spread = np.median(np.linalg.norm(P - np.median(P, 0), axis=1))
                if spread < 0.15 * math.hypot(W, H):
                    words.append("radial")
    dom = sorted(bins, key=bins.get, reverse=True)[0] if tot else "none"
    return M({"edge_density": r(dens, 4), "line_share": {k: r(v / tot, 2) if tot else 0.0 for k, v in bins.items()},
              "dominant": dom, "geometry": words or ["none"]}, T,
             "Canny 50/150 on blurred luma; HoughLinesP; length-weighted share within 12 deg of level / plumb / else diagonal; "
             "horizon > 35%, verticals > 30%, diagonals > 35%; radial when diagonal extensions meet within 15% of the diagonal")


def depth(bgr):
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    lv = cv2.Laplacian(g, cv2.CV_64F)
    H, W = g.shape
    cells = [[float(lv[i * H // 3:(i + 1) * H // 3, j * W // 3:(j + 1) * W // 3].var()) for j in range(3)] for i in range(3)]
    flat = [v for row in cells for v in row]
    mx = max(flat) + 1e-6
    i, j = max(((i, j) for i in range(3) for j in range(3)), key=lambda c: cells[c[0]][c[1]])
    ratio = mx / (np.median(flat) + 1e-6)
    dof = "shallow" if ratio > 4 else "deep" if ratio < 2 else "medium"
    return M({"focus_sits": GRID[i][j], "depth_of_field": dof, "peak_to_median": r(ratio, 2),
              "grid_rel": [[r(v / mx, 2) for v in row] for row in cells]}, T,
             "variance of Laplacian per 3x3 cell at 480 px; focus = sharpest cell; peak/median > 4 shallow, < 2 deep, else medium")


def measure(frames, levels=4, **kw):
    imgs, _, _ = load_frames(frames, n=kw.get("n", 3), t0=kw.get("t0"), t1=kw.get("t1"))
    if not imgs:
        u = M("unknown", T, "needs a frame", "no frame decoded")
        return {k: u for k in ("posterize", "invert", "bw", "difference", "edges", "depth")}
    key = _small(imgs[0])
    y = np.round(luma(key))
    return {"posterize": posterize(y, levels), "invert": invert(y), "bw": bw(y), "difference": difference(imgs),
            "edges": edges(y), "depth": depth(key)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--levels", type=int, default=4)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    src = a.inputs[0] if len(a.inputs) == 1 else a.inputs
    row = {"source": src, "lane": "shot_decon", "fields": measure(src, levels=a.levels)}
    with open(a.out, "a") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps({k: v["value"] for k, v in row["fields"].items()}))


if __name__ == "__main__":
    main()
