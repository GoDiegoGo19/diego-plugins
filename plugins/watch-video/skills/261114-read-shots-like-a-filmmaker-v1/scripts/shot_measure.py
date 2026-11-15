#!/usr/bin/env python3
"""shot_measure.py: the measured dimensions of a shot, as numbers and the rule that made them (P10).

Palmer's Table A ship rows only (wp/261114-read-shots-like-a-filmmaker-v1/inputs/research_dimensions_and_vision_lane.md).
Weightless numpy + OpenCV core (headless, no contrib). YuNet and YOLOX-S come only through the watch skill's engine
helpers (p3_see_models.mpath / session / V); nothing in the engine is edited.

  shot_measure.py <frames_dir|video> [--fps X] [--assume-transfer rec709|log|hlg] [--drive D --root R] --out measures.jsonl

One JSON line per frame (frames dir) or per clip (a video is one shot, #S01, until the cut lane hands over shots).
Every measure is {"value": ..., "trust": trusted|approximate|person, "rule": "..."}; a value that cannot be read
honestly is the string "unknown" with a "note". Rule of thirds and golden points are geometry, never a score (Palmer F010).
transfer rides on every row: Rec.709 / log / HLG / unknown. On log or unknown, levels, contrast and CCT write "unknown".
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parent.parent / "261113-watch-video-v10" / "scripts"
IMG_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
VID_EXT = {".mp4", ".mov", ".mxf", ".mts", ".m4v", ".avi", ".mkv"}
TRANSFER_MAP = {"bt709": "Rec.709", "smpte170m": "Rec.709", "bt470bg": "Rec.709", "bt470m": "Rec.709",
                "iec61966-2-1": "Rec.709", "gamma22": "Rec.709", "bt2020-10": "Rec.709", "bt2020-12": "Rec.709",
                "arib-std-b67": "HLG", "log100": "log", "log316": "log"}
ASSUME = {"rec709": "Rec.709", "log": "log", "hlg": "HLG", "unknown": "unknown"}


def M(value, trust, rule, note=None):
    d = {"value": value, "trust": trust, "rule": rule}
    if note:
        d["note"] = note
    return d


def r(x, n=2):
    return round(float(x), n)


# ---------------------------------------------------------------- detectors through the engine helpers
_SM = None


def engine():
    """p3_see_models from the watch skill, or None (then face/person boxes are not found; saliency stands in)."""
    global _SM
    if _SM is None:
        try:
            sys.path.insert(0, str(ENGINE))
            import p3_see_models as sm  # noqa: E402
            sm.mpath("yunet"), sm.mpath("yolox_s")
            _SM = sm
        except Exception as e:  # pragma: no cover - depends on the install
            print("shot_measure: engine helpers unavailable (%s); subject falls back to saliency" % e, file=sys.stderr)
            _SM = False
    return _SM or None


_YUNET = None


def face_boxes(bgr):
    """YuNet boxes [x, y, w, h, score], via the engine's model path and min_score (no SFace needed for boxes)."""
    global _YUNET
    sm = engine()
    if not sm:
        return []
    if _YUNET is None:
        _YUNET = cv2.FaceDetectorYN.create(str(sm.mpath("yunet")), "", (320, 320), sm.V()["faces"]["min_score"], 0.3, 50)
    h, w = bgr.shape[:2]
    _YUNET.setInputSize((w, h))
    _, found = _YUNET.detect(bgr)
    return [[float(v) for v in f[:4]] + [float(f[-1])] for f in (found if found is not None else [])]


def person_boxes(bgr):
    """YOLOX-S person boxes. The engine's people() returns only count and height, so the boxes are decoded here from
    the engine's own session(); the decode follows people() line for line (YOLOX ONNX demo, Apache-2.0)."""
    sm = engine()
    if not sm:
        return []
    min_score = sm.V()["people_min_score"]
    s = sm.session(sm.mpath("yolox_s"))
    inp = s.get_inputs()[0]
    H, W = int(inp.shape[2]), int(inp.shape[3])
    h0, w0 = bgr.shape[:2]
    k = min(H / h0, W / w0)
    pad = np.full((H, W, 3), 114, np.uint8)
    pad[: int(h0 * k), : int(w0 * k)] = cv2.resize(bgr, (int(w0 * k), int(h0 * k)))
    out = s.run(None, {inp.name: pad.transpose(2, 0, 1)[None].astype("float32")})[0][0]
    grids, strides = [], []
    for st in (8, 16, 32):
        hs, ws = H // st, W // st
        xv, yv = np.meshgrid(np.arange(ws), np.arange(hs))
        grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
        strides.append(np.full((hs * ws, 1), st))
    g, st = np.concatenate(grids), np.concatenate(strides)
    xy, wh = (out[:, :2] + g) * st, np.exp(out[:, 2:4]) * st
    score = out[:, 4] * out[:, 5]
    keep = score >= min_score
    if not keep.any():
        return []
    xy, wh, score = xy[keep] / k, wh[keep] / k, score[keep]
    boxes = [[float(x - bw / 2), float(y - bh / 2), float(bw), float(bh)] for (x, y), (bw, bh) in zip(xy, wh)]
    idx = np.array(cv2.dnn.NMSBoxes(boxes, [float(v) for v in score], min_score, 0.45)).reshape(-1)
    return [boxes[int(i)] + [float(score[int(i)])] for i in idx]


# ---------------------------------------------------------------- probe facts
def ffprobe(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=codec_name,width,height,pix_fmt,r_frame_rate,bit_rate,color_transfer,"
                              "color_primaries,color_space,bits_per_raw_sample:format=duration,bit_rate",
                              "-of", "json", str(path)], capture_output=True, text=True, timeout=60).stdout
        j = json.loads(out or "{}")
    except Exception:
        return {}
    s = (j.get("streams") or [{}])[0]
    f = j.get("format") or {}
    num, _, den = (s.get("r_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    pix = s.get("pix_fmt") or ""
    m = re.search(r"p(\d{2})", pix)
    depth = int(m.group(1)) if m else (int(s["bits_per_raw_sample"]) if str(s.get("bits_per_raw_sample", "")).isdigit() else 8)
    chroma = "4:4:4" if "444" in pix else "4:2:2" if "422" in pix else "4:2:0" if "420" in pix else "unknown"
    w, h = int(s.get("width") or 0), int(s.get("height") or 0)
    br = float(s.get("bit_rate") or f.get("bit_rate") or 0)
    return {"codec": s.get("codec_name", "unknown"), "width": w, "height": h, "fps": r(fps, 3), "pix_fmt": pix,
            "bit_depth": depth, "chroma": chroma, "duration_s": r(float(f.get("duration") or 0), 3),
            "bits_per_pixel": r(br / (w * h * fps), 4) if br and w and h and fps else "unknown",
            "color_transfer": s.get("color_transfer") or "unknown", "color_primaries": s.get("color_primaries") or "unknown"}


def sidecar_gamma(path):
    """Sony XDCAM / FX sidecar (<stem>M01.XML) names the capture gamma; S-Log/HLG there beats an untagged stream."""
    p = Path(path)
    for c in list(p.parent.glob(p.stem + "M01.XML")) + list(p.parent.parent.glob("Clip/" + p.stem.split("_")[0] + "*M01.XML"))[:1]:
        try:
            t = c.read_text(errors="ignore")
        except OSError:
            continue
        m = re.search(r'CaptureGammaEquation"\s+value="([^"]+)"', t) or re.search(r"(s-log\d?|slog\d?|hlg)", t, re.I)
        if m:
            g = m.group(1).lower()
            return "log" if "log" in g else "HLG" if "hlg" in g else "Rec.709" if "709" in g or "rec" in g else None
    return None


def transfer_of(path, probe, assume):
    if assume:
        return ASSUME[assume], "assumed by operator (--assume-transfer)"
    if probe:
        t = TRANSFER_MAP.get(probe.get("color_transfer", ""), None)
        if t:
            return t, "ffprobe color_transfer=%s" % probe["color_transfer"]
        sc = sidecar_gamma(path)
        if sc:
            return sc, "camera sidecar XML gamma"
        if probe.get("color_transfer") == "smpte2084":
            return "unknown", "ffprobe color_transfer=smpte2084 (PQ, not in the 709/log/HLG set)"
        return "unknown", "ffprobe color_transfer=%s" % probe.get("color_transfer", "unknown")
    return "unknown", "frames carry no colour tags; pass --assume-transfer when the grade is known"


# ---------------------------------------------------------------- pixel measures
def luma(bgr):
    b, g, rr = [bgr[..., i].astype(np.float32) for i in range(3)]
    return 0.2126 * rr + 0.7152 * g + 0.0722 * b          # Rec.709 Y' on 0-255 code values


def ire(y):
    return (y - 16.0) / 219.0 * 100.0


def to_linear(v01):
    return np.power(np.clip(v01, 0, 1), 2.2)


def m_levels(y):
    p1, p50, p99 = np.percentile(y, [1, 50, 99])
    lo, hi = float((y <= 16.5).mean() * 100), float((y >= 234.5).mean() * 100)
    key = "low" if ire(p50) < 30 else "high" if ire(p50) > 65 else "mid"
    return {"p1_ire": r(ire(p1), 1), "p50_ire": r(ire(p50), 1), "p99_ire": r(ire(p99), 1),
            "pct_at_or_below_16": r(lo, 2), "pct_at_or_above_235": r(hi, 2), "key": key}


def m_contrast(y):
    lin = to_linear(y / 255.0)
    p5, p95 = np.percentile(lin, [5, 95])
    return {"rms": r(np.std(y / 255.0), 4), "stops_p95_p5": r(math.log2((p95 + 1e-4) / (p5 + 1e-4)), 2)}


def m_ratio(y, faces, people):
    if faces:
        x, yy, w, h = max(faces, key=lambda b: b[2] * b[3])[:4]
        src = "face"
    elif people:
        x, yy, w, h = max(people, key=lambda b: b[2] * b[3])[:4]
        src = "person"
    else:
        return "unknown", "no face or person box; key:fill needs a subject (Sexton: no scene ratio here)"
    H, W = y.shape
    x0, y0, x1, y1 = max(0, int(x)), max(0, int(yy)), min(W, int(x + w)), min(H, int(yy + h))
    box = to_linear(y[y0:y1, x0:x1] / 255.0)
    if box.shape[0] < 4 or box.shape[1] < 4:
        return "unknown", "subject box under 4 px"
    mid = box.shape[1] // 2
    mh = box.shape[0] // 2
    halves = [box[:, :mid].mean(), box[:, mid:].mean(), box[:mh].mean(), box[mh:].mean()]
    pairs = [(halves[0], halves[1]), (halves[2], halves[3])]
    a, b = max(pairs, key=lambda p: abs(math.log2((p[0] + 1e-4) / (p[1] + 1e-4))))
    st = abs(math.log2((max(a, b) + 1e-4) / (min(a, b) + 1e-4)))
    return {"stops": r(st, 2), "ratio": "%.1f:1" % (2 ** st), "ratio_source": src}, None


def mccamy(bgr):
    rgb = bgr[..., ::-1].reshape(-1, 3).astype(np.float32) / 255.0
    mx, mn = rgb.max(1), rgb.min(1)
    sat = (mx - mn) / (mx + 1e-6)
    v = mx
    sel = (sat < 0.25) & (v > np.percentile(v, 40)) & (v < 0.97)
    how = "bright-neutral pixels"
    if sel.sum() < 0.01 * len(v):
        sel = (v > 0.03) & (v < 0.97)
        how = "grey-world"
    lin = to_linear(rgb[sel]).mean(0) if sel.any() else to_linear(rgb).mean(0)
    Mx = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    X, Y, Z = Mx @ lin
    s = X + Y + Z + 1e-9
    x, yy = X / s, Y / s
    n = (x - 0.3320) / (0.1858 - yy + 1e-9)
    T = 449 * n ** 3 + 3525 * n ** 2 + 6823.3 * n + 5520.33
    T = float(np.clip(T, 1000, 25000))
    u, v1 = 4 * X / (X + 15 * Y + 3 * Z + 1e-9), 6 * Y / (X + 15 * Y + 3 * Z + 1e-9)
    up = (0.860117757 + 1.54118254e-4 * T + 1.28641212e-7 * T * T) / (1 + 8.42420235e-4 * T + 7.08145163e-7 * T * T)
    vp = (0.317398726 + 4.22806245e-5 * T + 4.20481691e-8 * T * T) / (1 - 2.89741816e-5 * T + 1.61456053e-7 * T * T)
    duv = math.copysign(math.hypot(u - up, v1 - vp), v1 - vp)
    return T, duv, how


def m_cct(bgr):
    T, duv, how = mccamy(bgr)
    w = bgr.shape[1] // 2
    tl, _, _ = mccamy(bgr[:, :w])
    tr, _, _ = mccamy(bgr[:, w:])
    tint = "green" if duv > 0.006 else "magenta" if duv < -0.006 else "none"
    return {"kelvin": int(round(T, -1)), "duv": r(duv, 4), "tint": tint, "label": "cast_estimate", "pixels": how,
            "left_right_kelvin": [int(round(tl, -1)), int(round(tr, -1))]}


def m_saturation(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    s = hsv[..., 1].astype(np.float32) / 255.0
    b, g, rr = [bgr[..., i].astype(np.float32) for i in range(3)]
    rg, yb = rr - g, 0.5 * (rr + g) - b
    cf = math.hypot(rg.std(), yb.std()) + 0.3 * math.hypot(rg.mean(), yb.mean())
    return {"mean": r(s.mean(), 3), "p90": r(np.percentile(s, 90), 3), "colourfulness": r(cf, 1)}


def m_palette(bgr):
    small = cv2.resize(bgr, (160, max(1, int(160 * bgr.shape[0] / bgr.shape[1]))), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    k = min(5, len(np.unique(lab, axis=0)))
    cv2.setRNGSeed(7)
    _, lbl, cen = cv2.kmeans(lab, k, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 0.5), 3,
                             cv2.KMEANS_PP_CENTERS)
    share = np.bincount(lbl.reshape(-1), minlength=k) / len(lbl)
    cols = []
    for c, sh in sorted(zip(cen, share), key=lambda t: -t[1]):
        L, a, b = c[0] * 100 / 255, c[1] - 128, c[2] - 128
        cols.append({"L": r(L, 1), "a": r(a, 1), "b": r(b, 1), "share": r(sh, 3)})
    chroma = [(math.atan2(c["b"], c["a"]), math.hypot(c["a"], c["b"]), c["share"]) for c in cols]
    wts = [ch * sh for _, ch, sh in chroma]
    if sum(wts) < 1e-6:
        spread = 0.0
    else:
        C = sum(w * math.cos(h) for (h, _, _), w in zip(chroma, wts)) / sum(wts)
        S = sum(w * math.sin(h) for (h, _, _), w in zip(chroma, wts)) / sum(wts)
        spread = math.degrees(math.sqrt(max(0.0, -2 * math.log(max(math.hypot(C, S), 1e-9)))))
    return cols, r(spread, 1)


def m_sharpness(gray):
    lv = cv2.Laplacian(gray, cv2.CV_64F)
    H, W = gray.shape
    cells = [[float(lv[i * H // 3:(i + 1) * H // 3, j * W // 3:(j + 1) * W // 3].var()) for j in range(3)] for i in range(3)]
    best = max(((i, j) for i in range(3) for j in range(3)), key=lambda c: cells[c[0]][c[1]])
    return {"laplacian_var": r(lv.var(), 1), "sharp_region": {"cell_row_col": list(best),
            "laplacian_var": r(cells[best[0]][best[1]], 1)}, "grid": [[r(v, 1) for v in row] for row in cells]}


def m_noise(gray):
    K = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], np.float32)
    H, W = gray.shape
    conv = cv2.filter2D(gray.astype(np.float32), -1, K)[1:-1, 1:-1]
    return r(math.sqrt(math.pi / 2) / (6 * (W - 2) * (H - 2)) * np.abs(conv).sum(), 2)


def m_clipping(bgr):
    hit = bgr >= 254
    return {"pct_any_channel_255": r(hit.any(2).mean() * 100, 2), "pct_all_channels_255": r(hit.all(2).mean() * 100, 2),
            "pct_any_channel_0": r((bgr <= 1).any(2).mean() * 100, 2)}


def m_blockiness(gray):
    g = gray.astype(np.float32)
    dh = np.abs(np.diff(g, axis=1))
    if dh.shape[1] < 16:
        return "unknown"
    cols = np.arange(dh.shape[1])
    edge = dh[:, (cols % 8) == 7].mean()
    inner = dh[:, (cols % 8) != 7].mean() + 1e-6
    return r(edge / inner, 3)


def saliency_point(gray):
    """Spectral residual (Hou & Zhang 2007) in numpy FFT; returns (x, y) in 0-1 and the map's peak share."""
    h, w = gray.shape
    sw = 64
    sh = max(8, int(round(sw * h / w)))
    g = cv2.resize(gray, (sw, sh), interpolation=cv2.INTER_AREA).astype(np.float32)
    F = np.fft.fft2(g)
    la = np.log(np.abs(F) + 1e-6)
    ph = np.angle(F)
    res = la - cv2.blur(la, (3, 3))
    sal = np.abs(np.fft.ifft2(np.exp(res + 1j * ph))) ** 2
    sal = cv2.GaussianBlur(sal.astype(np.float32), (0, 0), 2.5)
    m = sal >= np.percentile(sal, 95)
    ys, xs = np.nonzero(m)
    wts = sal[m]
    return float((xs * wts).sum() / wts.sum() + 0.5) / sw, float((ys * wts).sum() / wts.sum() + 0.5) / sh


def geometry(px, py, w, h):
    diag = math.hypot(w, h)
    X, Y = px * w, py * h
    thirds = [(w * a, h * b) for a in (1 / 3, 2 / 3) for b in (1 / 3, 2 / 3)]
    phi = [(w * a, h * b) for a in (0.382, 0.618) for b in (0.382, 0.618)]
    dt = min(math.hypot(X - a, Y - b) for a, b in thirds) / diag * 100
    dp = min(math.hypot(X - a, Y - b) for a, b in phi) / diag * 100
    dc = math.hypot(X - w / 2, Y - h / 2) / diag * 100
    return {"dist_thirds_pct_diag": r(dt, 1), "dist_phi_pct_diag": r(dp, 1), "dist_centre_pct_diag": r(dc, 1),
            "side": "left" if px < 0.45 else "right" if px > 0.55 else "centre"}


def m_horizon(gray):
    e = cv2.Canny(gray, 60, 160)
    H, W = gray.shape
    lines = cv2.HoughLinesP(e, 1, np.pi / 360, threshold=max(20, W // 12), minLineLength=max(20, W // 6), maxLineGap=6)
    angs, lens = [], []
    for x1, y1, x2, y2 in (lines.reshape(-1, 4) if lines is not None else []):
        a = -math.degrees(math.atan2(y2 - y1, x2 - x1))
        if a > 90:
            a -= 180
        if a < -90:
            a += 180
        if abs(a) <= 20:
            angs.append(a)
            lens.append(math.hypot(x2 - x1, y2 - y1))
    if not angs:
        return {"tilt_deg": "unknown", "confidence": 0.0, "lines": 0}
    lens = np.array(lens)
    tilt = float(np.average(angs, weights=lens))
    spread = float(np.sqrt(np.average((np.array(angs) - tilt) ** 2, weights=lens)))
    conf = min(1.0, lens.sum() / (1.5 * W)) * (1.0 if spread < 3 else 0.5)
    return {"tilt_deg": r(tilt, 2), "confidence": r(conf, 2), "lines": len(angs), "spread_deg": r(spread, 2)}


def m_motion(frames, dt):
    """Farneback flow at 320 px between consecutive frames; global partial affine = camera; residual p90 = subject."""
    if len(frames) < 2:
        return None
    pairs = []
    for a, b in zip(frames, frames[1:]):
        h, w = a.shape[:2]
        sw = min(320, w)
        sh = max(8, int(sw * h / w))
        ga = cv2.resize(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), (sw, sh))
        gb = cv2.resize(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), (sw, sh))
        flow = cv2.calcOpticalFlowFarneback(ga, gb, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        ys, xs = np.mgrid[4:sh:8, 4:sw:8]
        src = np.stack([xs.ravel(), ys.ravel()], 1).astype(np.float32)
        dst = src + flow[ys.ravel(), xs.ravel()]
        A, _ = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=1.0)
        if A is None:
            A = np.array([[1, 0, 0], [0, 1, 0]], np.float32)
        pred = src @ A[:, :2].T + A[:, 2]
        resid = np.linalg.norm(dst - pred, axis=1)
        scale = math.hypot(A[0, 0], A[1, 0])
        pairs.append({"dx": float(A[0, 2]) / sw, "dy": float(A[1, 2]) / sw, "scale": scale,
                      "rot_deg": math.degrees(math.atan2(A[1, 0], A[0, 0])), "resid_p90": float(np.percentile(resid, 90)) / sw})
    per = lambda v: v / dt * 100 if dt else v * 100  # noqa: E731  % of frame width per second (or per frame)
    dx = np.mean([p["dx"] for p in pairs])
    dy = np.mean([p["dy"] for p in pairs])
    sc = np.mean([p["scale"] for p in pairs])
    cam = {"dx_pct_w": r(per(dx), 2), "dy_pct_w": r(per(dy), 2), "zoom_pct": r(per(sc - 1), 2),
           "rot_deg": r(np.mean([p["rot_deg"] for p in pairs]), 2), "speed_pct_w": r(per(math.hypot(dx, dy)), 2)}
    sub = {"p90_residual_pct_w": r(per(np.mean([p["resid_p90"] for p in pairs])), 2)}
    shake = None
    if len(pairs) >= 2:
        jit = [math.hypot(b["dx"] - a["dx"], b["dy"] - a["dy"]) for a, b in zip(pairs, pairs[1:])]
        shake = r(np.mean(jit) * 100, 3)
    return cam, sub, shake, ("per second" if dt else "per frame (no --fps)"), len(frames)


# ---------------------------------------------------------------- one shot
def measure(frames, transfer, transfer_src, probe=None, dt=None):
    """frames: [hero, next, ...] BGR. Returns the measures dict."""
    hero = frames[0]
    H, W = hero.shape[:2]
    gray = cv2.cvtColor(hero, cv2.COLOR_BGR2GRAY)
    y = luma(hero)
    out = {}

    note_log = "transfer is %s: levels, contrast and CCT are not read on ungraded or untagged footage (Palmer)" % transfer
    if transfer == "Rec.709":
        out["levels"] = M(m_levels(y), "trusted", "Rec.709 Y' percentiles p1/p50/p99 as IRE ((Y-16)/219*100); % at code 16/235")
        out["contrast"] = M(m_contrast(y), "trusted", "RMS of Y'/255; log2(p95/p5) of linear luma (stops)")
        out["cct"] = M(m_cct(hero), "approximate", "bright-neutral (else grey-world) linear sRGB -> XYZ -> xy -> McCamy 1992; Duv from Krystek locus",
                       "cast_estimate: the camera already white-balanced the scene; a sunset reads warm, not a fault")
    else:
        why = note_log if transfer != "HLG" else None
        for k in ("levels", "contrast", "cct"):
            out[k] = M("unknown", "trusted", "not measured", why or "HLG: Rec.709 thresholds do not hold")
    faces = face_boxes(hero)
    people = person_boxes(hero) if not faces else []
    if transfer == "Rec.709":
        v, why = m_ratio(y, faces, people)
        out["lighting_ratio"] = M(v, "approximate", "mean linear luma, brightest vs darkest half of the largest face box (else person box), in stops", why)
    else:
        out["lighting_ratio"] = M("unknown", "approximate", "not measured", note_log)
    out["saturation"] = M(m_saturation(hero), "trusted", "HSV-S mean/p90; Hasler-Suesstrunk M = std(rg,yb) + 0.3*mean(rg,yb)")
    pal, spread = m_palette(hero)
    out["palette"] = M({"lab": pal, "hue_spread_deg": spread}, "trusted", "k-means k=5 in Lab at 160 px; chroma-weighted circular std of hue")
    out["sharpness"] = M(m_sharpness(gray), "approximate", "variance of Laplacian, whole frame and max over a 3x3 grid (sharp_region)",
                         "intended softness reads the same as missed focus")
    out["noise"] = M({"sigma": m_noise(gray)}, "trusted", "Immerkaer 1996 fast noise sigma (Laplacian-difference mask) on 8-bit grey",
                     "grain added in the grade counts as noise")
    out["clipping"] = M(m_clipping(hero), "trusted", "% pixels at code 254+ (any / all channels) and at 0-1")
    mo = m_motion(frames, dt)
    if mo:
        cam, sub, shake, unit, n = mo
        out["motion"] = M({"camera": cam, "subject": sub, "unit": "% of frame width " + unit, "frames": n}, "approximate",
                          "Farneback flow at 320 px; RANSAC partial affine = camera, p90 residual = subject")
        out["shake"] = M({"jitter_pct_w": shake} if shake is not None else "unknown", "approximate",
                         "mean change of the camera translation between successive pairs", None if shake is not None else "needs three frames")
    else:
        out["motion"] = M("unknown", "approximate", "not measured", "one frame only; motion needs two")
        out["shake"] = M("unknown", "approximate", "not measured", "one frame only")
    if faces:
        b = max(faces, key=lambda q: q[2] * q[3])
        src = "face"
    elif people:
        b = max(people, key=lambda q: q[2] * q[3])
        src = "person"
    else:
        b, src = None, "saliency"
    if b:
        px, py = (b[0] + b[2] / 2) / W, (b[1] + b[3] / 2) / H
        box = {"x": r(b[0] / W, 3), "y": r(b[1] / H, 3), "w": r(b[2] / W, 3), "h": r(b[3] / H, 3)}
    else:
        px, py = saliency_point(gray)
        box = None
    out["subject"] = M({"x": r(px, 3), "y": r(py, 3), "subject_source": src, "box": box,
                        "faces": len(faces) > 0}, "approximate", "largest YuNet face, else YOLOX-S person, else spectral-residual saliency centroid",
                       "saliency is weak on cluttered frames (BMVC 2011)" if src == "saliency" else None)
    out["framing_geometry"] = M(geometry(px, py, W, H), "approximate",
                                "distance from subject point to nearest thirds / phi(0.382,0.618) intersection and centre, % of diagonal",
                                "geometry only, never a score (Amirshahi 2014; Hoh 2023)")
    if b:
        gap = max(0.0, b[1]) / max(b[3], 1)
        out["headroom"] = M({"pct_of_box_height": r(gap * 100, 1), "box": src}, "approximate", "gap from box top to frame top, % of box height",
                            "cropped-on-purpose close-ups read as tight")
    else:
        out["headroom"] = M("unknown", "approximate", "not measured", "no face or person box")
    out["horizon"] = M(m_horizon(gray), "trusted", "Canny + probabilistic Hough; length-weighted angle of lines within 20 deg of level; + = right side up",
                       "intended Dutch reads the same as accidental")
    blk = m_blockiness(gray)
    fid = dict(probe or {})
    fid.update({"frame_size": "%dx%d" % (W, H), "blockiness_8x8": blk})
    out["fidelity"] = M(fid, "trusted", "ffprobe stream facts; blockiness = mean |dY| on 8-px column edges / elsewhere (probe flag)",
                        "blockiness_8x8 is a probe; maybe_upscaled not built this round")
    return out


# ---------------------------------------------------------------- inputs
def card_of(rel):
    """(card column) from the folders, the same rule as watch_see.card_of (<day>/<card> or first folder or LOOSE)."""
    parts = rel.replace("\\", "/").split("/")
    for i, p in enumerate(parts[:-1]):
        if re.match(r"^(\d{6})(?:[_-].*)?$", p):
            card = parts[i + 1] if i + 1 < len(parts) - 1 else "LOOSE"
            return "/".join(parts[:i + 2]) if card != "LOOSE" else "LOOSE"
    return parts[0] if len(parts) > 1 else "LOOSE"


def grab(path, t, tmp, i):
    p = os.path.join(tmp, "f%03d.png" % i)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "%.3f" % max(0, t), "-i", str(path), "-frames:v", "1", p],
                   capture_output=True, timeout=120)
    return cv2.imread(p) if os.path.exists(p) else None


def run_video(path, a):
    path = Path(path)
    root = Path(a.root) if a.root else path.parent
    drive = a.drive or root.name or "DRIVE"
    rel = os.path.relpath(path, root)
    cid = "%s/%s/%s" % (drive, card_of(rel), path.name)
    probe = ffprobe(path)
    fps = a.fps or probe.get("fps") or 24.0
    dur = probe.get("duration_s") or 0
    t = dur / 2 if dur else 0.0
    step = 3.0 / fps
    frames = [f for f in (grab(path, t + k * step, a.tmp, k) for k in range(3)) if f is not None]
    if not frames:
        return [{"clip_id": cid, "segment_id": cid + "#S01", "status": "pending", "why": "ffmpeg could not decode a frame"}]
    tr, trs = transfer_of(path, probe, a.assume_transfer)
    return [{"clip_id": cid, "segment_id": cid + "#S01", "source": str(path), "hero_t": r(t, 3), "transfer": tr,
             "transfer_source": trs, "measures": measure(frames, tr, trs, probe, step)}]


def run_frames(d, a):
    d = Path(d)
    files = sorted(p for p in d.iterdir() if p.suffix.lower() in IMG_EXT)
    drive = a.drive or "FRAMES"
    cid = "%s/LOOSE/%s" % (drive, d.name)
    tr, trs = transfer_of(None, None, a.assume_transfer)
    dt = 1.0 / a.fps if a.fps else None
    imgs = [cv2.imread(str(p)) for p in files]
    rows = []
    for i, (p, im) in enumerate(zip(files, imgs)):
        if im is None:
            continue
        nxt = [imgs[i + 1]] if i + 1 < len(imgs) and imgs[i + 1] is not None and imgs[i + 1].shape == im.shape else []
        rows.append({"clip_id": cid, "segment_id": "%s#S%02d" % (cid, len(rows) + 1), "source": str(p), "transfer": tr,
                     "transfer_source": trs, "measures": measure([im] + nxt, tr, trs, None, dt)})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("src", nargs="+", help="a frames dir, or one or more video files")
    ap.add_argument("--fps", type=float, help="frame rate of a frames dir (motion per second); overrides ffprobe for video")
    ap.add_argument("--assume-transfer", choices=sorted(ASSUME), help="state the transfer when tags are missing (recorded as assumed)")
    ap.add_argument("--drive", help="drive name for clip_id (default: the root folder's name)")
    ap.add_argument("--root", help="drive root, so clip_id = <drive>/<card column>/<file>")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        a.tmp = tmp
        for s in a.src:
            if not os.path.exists(s):
                sys.stderr.write("shot_measure: no such file or folder: %s\n" % s)
                return 2
            if os.path.isdir(s):
                rows += run_frames(s, a)
            elif Path(s).suffix.lower() in IMG_EXT:
                im = cv2.imread(s)
                if im is None:  # 261114: a missing or truncated frame said "NoneType has no attribute shape" six frames deep
                    sys.stderr.write("shot_measure: cannot read image %s (missing, empty or not a picture ffmpeg wrote)\n" % s)
                    return 2
                tr, trs = transfer_of(None, None, a.assume_transfer)
                cid = "%s/LOOSE/%s" % (a.drive or "FRAMES", Path(s).name)
                rows.append({"clip_id": cid, "segment_id": cid + "#S01", "source": s, "transfer": tr, "transfer_source": trs,
                             "measures": measure([im], tr, trs)})
            else:
                rows += run_video(s, a)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    print("shot_measure: %d row(s) -> %s" % (len(rows), a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
