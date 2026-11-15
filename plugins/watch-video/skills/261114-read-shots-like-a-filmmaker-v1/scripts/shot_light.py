#!/usr/bin/env python3
"""shot_light.py: time of day and light words, each with the numbers under it (the poet must show the numbers).

Measured from shot_measure's helpers: McCamy CCT, Rec.709 luma p50 (IRE), sun_warmth (R/B of the top third vs the bottom
third), sky presence (smooth, bright top band; blue or warm), shadow length proxy (dark run under a person box), light
direction from face/person-box halves, light quality from the gradient histogram at the subject.
CinemaCLIP's time-of-day head is a PROBE only (--cinemanet DIR): it writes time_of_day_probe with agree/disagree and never
overwrites the measured call. Without the model it writes "not installed" and points to references/cinemanet.md.

  measure(frames | video, transfer="Rec.709", cinemanet=None) -> {field: {value, trust, rule, note}}
  shot_light.py <frame|frames_dir|video> [--cinemanet DIR] [--assume-transfer rec709|log] --out light.jsonl
"""
import argparse
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shot_measure import M, face_boxes, ire, luma, m_cct, person_boxes, r, saliency_point, to_linear  # noqa: E402
from shot_motion import load_frames  # noqa: E402

T = "measured"
TOD = ["dawn", "day", "golden hour", "sunset", "night", "interior", "unknown"]
CN_MAP = {"dawn-dusk": {"dawn", "sunset"}, "day": {"day"}, "night": {"night"},
          "sunrise-sunset": {"sunset", "golden hour", "dawn"}, "na": {"interior", "unknown"}}
CN_DOC = "references/cinemanet.md"


def _boxes(bgr):
    try:
        f = face_boxes(bgr) or []
    except Exception:
        f = []
    try:
        p = person_boxes(bgr) or []
    except Exception:
        p = []
    return f, p


def _box(b):
    """engine boxes may be tuples (x, y, w, h[, score]) or dicts; return ints x, y, w, h."""
    if isinstance(b, dict):
        b = b.get("box") or [b.get("x"), b.get("y"), b.get("w"), b.get("h")]
    return [int(v) for v in list(b)[:4]]


def sky_and_warmth(bgr):
    H = bgr.shape[0]
    f = bgr.astype(np.float32) + 1.0
    top, bot = f[: H // 3], f[2 * H // 3:]
    rb_top = float(top[..., 2].mean() / top[..., 0].mean())
    rb_bot = float(bot[..., 2].mean() / bot[..., 0].mean())
    band = bgr[: max(4, H // 5)]
    g = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    tex = float(cv2.Laplacian(g, cv2.CV_64F).var())
    y = luma(bgr)
    yb, yall = float(luma(band).mean()), float(y.mean())
    b, gg, rr = [band[..., i].astype(np.float32).mean() for i in range(3)]
    present = tex < 60 and yb > yall * 1.05 and yb > 40
    tint = "blue" if b > rr * 1.05 else "warm" if rr > b * 1.15 else "neutral"
    return rb_top, rb_bot, present, tint, tex, yb


def shadow_proxy(bgr, people):
    if not people:
        return M("unknown", T, "dark run below the person box / box height", "no person box (engine detector absent or no person)")
    x, yb, w, h = _box(max(people, key=lambda b: _box(b)[3]))
    y = luma(bgr)
    H = y.shape[0]
    col = y[min(H - 1, yb + h): H, max(0, x): x + w]
    if col.size == 0:
        return M("unknown", T, "dark run below the person box / box height", "box touches the frame bottom")
    thr = np.percentile(y, 25)
    dark = (col.mean(1) < thr)
    run = int(np.argmin(dark)) if not dark.all() else len(dark)
    return M(r(run / max(1, h), 2), T, "rows of below-quartile luma directly under the person box, / box height "
             "(long shadow > 0.5 leans low sun)")


def tod_call(bgr, transfer):
    if transfer not in ("Rec.709", "HLG"):
        return "unknown", "transfer %s: levels and CCT are not readable" % transfer, {}
    cct = m_cct(bgr)
    K = cct["kelvin"]
    p50 = r(ire(np.percentile(luma(bgr), 50)), 1)
    rb_top, rb_bot, sky, tint, tex, yb = sky_and_warmth(bgr)
    warm = rb_top / max(rb_bot, 1e-3)
    ev = {"cct_k": K, "p50_ire": p50, "rb_top": r(rb_top, 2), "rb_bottom": r(rb_bot, 2), "sun_warmth": r(warm, 2),
          "sky": sky, "sky_tint": tint}
    line = "CCT %dK, p50 %.0f IRE, top R/B %.2f vs bottom %.2f, sky %s" % (K, p50, rb_top, rb_bot, (tint if sky else "none"))
    warm_top = rb_top > 1.25 or (sky and tint == "warm")
    if p50 < 12 and not (sky and yb > 80):
        call = "night"
    elif sky and warm_top:
        call = "sunset" if p50 < 35 or rb_top > 1.6 else "golden hour"
    elif sky and tint == "blue" and p50 < 30:
        call = "dawn"
    elif sky:
        call = "day"
    elif K < 3800 and p50 < 55:
        call = "interior"
    elif warm_top and p50 < 35:
        call = "sunset"
    elif p50 >= 35 and K >= 4800:
        call = "day"
    else:
        call = "unknown"
    return call, line, ev


def direction(bgr, faces, people):
    src = "face" if faces else "person" if people else None
    if not src:
        return M("unknown", T, "luma halves of the face box, else the person box", "no face or person box")
    x, y, w, h = _box(max(faces or people, key=lambda b: _box(b)[2] * _box(b)[3]))
    Y = to_linear(luma(bgr) / 255.0)
    H, W = Y.shape
    box = Y[max(0, y): y + h, max(0, x): x + w]
    if box.size < 16:
        return M("unknown", T, "luma halves of the box", "box too small")
    lr = math.log2((box[:, w // 2:].mean() + 1e-4) / (box[:, : w // 2].mean() + 1e-4))
    tb = math.log2((box[: h // 2].mean() + 1e-4) / (box[h // 2:].mean() + 1e-4))
    pad = Y[max(0, y - h // 2): min(H, y + h + h // 2), max(0, x - w // 2): min(W, x + w + w // 2)]
    bg = (pad.sum() - box.sum()) / max(1, pad.size - box.size)
    back = math.log2((box.mean() + 1e-4) / (bg + 1e-4))
    if back < -1.0:
        v = "back"
    elif abs(lr) >= 1.0:
        v = "side"
    elif tb >= 1.0:
        v = "top"
    else:
        v = "front"
    return M(v, T, "%s box in linear luma: subject vs surround < -1 stop = back; |right-left| >= 1 stop = side; "
             "top-bottom >= 1 stop = top; else front" % src, "right-left %.2f st, top-bottom %.2f st, subject-surround %.2f st"
             % (lr, tb, back))


def quality(bgr, faces, people):
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    H, W = g.shape
    if faces or people:
        x, y, w, h = _box(max(faces or people, key=lambda b: _box(b)[2] * _box(b)[3]))
        x0, y0, x1, y1 = max(0, x - w // 4), max(0, y - h // 4), min(W, x + w + w // 4), min(H, y + h + h // 4)
        where = "face" if faces else "person"
    else:
        try:
            px, py = saliency_point(g)[:2]
            px, py = (px * W, py * H) if px <= 1 and py <= 1 else (px, py)
        except Exception:
            px, py = W / 2, H / 2
        x0, y0, x1, y1 = int(max(0, px - W / 6)), int(max(0, py - H / 6)), int(min(W, px + W / 6)), int(min(H, py + H / 6))
        where = "saliency point"
    reg = g[y0:y1, x0:x1].astype(np.float32)
    if reg.size < 64:
        return M("unknown", T, "gradient histogram at the subject", "region too small")
    mag = cv2.magnitude(cv2.Sobel(reg, cv2.CV_32F, 1, 0), cv2.Sobel(reg, cv2.CV_32F, 0, 1))
    m = mag[mag > 4]
    if m.size < 32:
        return M("soft", T, "gradient histogram at the subject", "almost no edges at the %s" % where)
    tail = float(np.percentile(m, 99) / (np.percentile(m, 50) + 1e-3))
    return M("hard" if tail > 9 else "soft", T, "Sobel magnitude at the %s: p99/p50 of edge pixels > 9 = hard (crisp shadow "
             "edges), else soft; threshold uncalibrated" % where, "p99/p50 %.1f" % tail)


def probe(bgr, cdir, call):
    if not cdir:
        return M("not run", "probe", "CinemaCLIP time-of-day head, compared, never written over the measured call",
                 "pass --cinemanet DIR; install per %s" % CN_DOC)
    d = Path(cdir)
    onnx = next((p for p in (d / "ImageEncoder.onnx", d / "ImageEncoder_fp16.onnx") if p.exists()), None)
    schema = d / "CinemaNetSchema.json"
    try:
        import onnxruntime as ort
    except Exception:
        ort = None
    if not onnx or not schema.exists() or ort is None:
        why = "missing: " + ", ".join(n for n, ok in (("ImageEncoder.onnx", onnx), ("CinemaNetSchema.json", schema.exists()),
                                                      ("onnxruntime", ort is not None)) if not ok)
        return M("not installed", "probe", "CinemaCLIP time-of-day head (OpenRAIL-M)", "%s; pinned download in %s" % (why, CN_DOC))
    labels = json.load(open(schema))["probabilities_labels"]
    idx = {l.split(".")[-1]: i for i, l in enumerate(labels) if l.startswith("shot.time-of-day.")}
    rgb = cv2.resize(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), (256, 256), interpolation=cv2.INTER_CUBIC)
    x = (rgb.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]          # [0,1], no mean/std (model card)
    sess = ort.InferenceSession(str(onnx), providers=["CPUExecutionProvider"])
    outs = sess.run(None, {sess.get_inputs()[0].name: x})
    probs = np.asarray(outs[-1]).reshape(-1)
    sc = {k: float(probs[i]) for k, i in idx.items() if i < probs.size}
    top = max(sc, key=sc.get)
    agree = call in CN_MAP.get(top, set())
    return M({"label": top, "p": r(sc[top], 3), "all": {k: r(v, 3) for k, v in sc.items()},
              "agrees_with_measured": agree}, "probe", "CinemaCLIP ImageEncoder.onnx, 256x256 bicubic RGB [0,1]; "
             "head shot.time-of-day; mapped dawn-dusk->dawn/sunset, sunrise-sunset->sunset/golden hour/dawn",
             "agrees" if agree else "disagrees: the row keeps the measured call; mark unresolved for a person")


def measure(frames, transfer="Rec.709", cinemanet=None, **kw):
    imgs, _, _ = load_frames(frames, n=kw.get("n", 1), t0=kw.get("t0"))
    if not imgs:
        u = M("unknown", T, "needs a frame", "no frame decoded")
        return {k: u for k in ("time_of_day", "light_direction", "light_quality", "sun_warmth", "sky", "shadow_length")}
    bgr = imgs[len(imgs) // 2]
    faces, people = _boxes(bgr)
    call, line, ev = tod_call(bgr, transfer)
    out = {
        "time_of_day": M(call, T, "night: p50 < 12 IRE without a lit sky; sky + warm top (R/B > 1.25): sunset if p50 < 35 or "
                         "R/B > 1.6 else golden hour; sky blue and p50 < 30: dawn (blue hour); sky: day; no sky, CCT < 3800K, "
                         "p50 < 55: interior; else day/unknown", line + ("; dawn and sunset share a light, the edit names "
                                                                          "which" if call in ("sunset", "dawn") else "")),
        "sun_warmth": M(ev.get("sun_warmth", "unknown"), T, "mean R/B of the top third / mean R/B of the bottom third",
                        None if ev else line),
        "sky": M(({"present": ev["sky"], "tint": ev["sky_tint"]} if ev else "unknown"), T,
                 "top fifth: Laplacian var < 60 and brighter than the frame by 5%; tint blue if B > 1.05 R, warm if R > 1.15 B"),
        "shadow_length": shadow_proxy(bgr, people),
        "light_direction": direction(bgr, faces, people),
        "light_quality": quality(bgr, faces, people),
        "light_evidence": M(ev or "unknown", T, "the numbers under the words"),
    }
    out["time_of_day_probe"] = probe(bgr, cinemanet, call)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--cinemanet", help="folder with ImageEncoder.onnx + CinemaNetSchema.json (see references/cinemanet.md)")
    ap.add_argument("--assume-transfer", default="rec709", choices=["rec709", "log", "hlg", "unknown"])
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    tr = {"rec709": "Rec.709", "log": "log", "hlg": "HLG", "unknown": "unknown"}[a.assume_transfer]
    with open(a.out, "a") as f:
        for src in a.inputs:
            row = {"source": src, "lane": "shot_light", "fields": measure(src, transfer=tr, cinemanet=a.cinemanet)}
            f.write(json.dumps(row) + "\n")
            print(json.dumps({k: v["value"] for k, v in row["fields"].items() if k != "light_evidence"}))


if __name__ == "__main__":
    main()
