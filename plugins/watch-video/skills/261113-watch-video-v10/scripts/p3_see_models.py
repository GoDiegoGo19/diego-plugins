#!/usr/bin/env python3
"""p3_see_models.py · P3 SEE (v16) · install the pinned models, and one thin ADAPTER per job.

Stitched, not hand-built: every heavy job is a proven library or tool; this file only installs the pinned pieces and
gives each one a fixed input and output. Plug-ins never call each other: callers pass data with the same IDs.

    python3 p3_see_models.py status                       # every group: installed or missing
    python3 p3_see_models.py install core [eyes faces_text captions speech | all]   # pinned weights (+ llama.cpp)
    python3 p3_see_models.py try IMAGE.jpg                # tags, people, faces, text, caption on one picture

INSTALL: huggingface_hub downloads each file at its pinned revision straight into <models_dir>/<key>/ (one copy),
the sha256 is checked, then everything runs offline. YOLOX and llama.cpp come from their GitHub release, checked the
same way. Pins: rules.json -> vision.models / vision.tools.

ADAPTERS (input -> output):
  embed_image(key, rgb_list)      -> (N, D) unit vectors        CLIP / SigLIP 2 / DINOv2 (CLS), each its own preprocessing
  embed_text(key, texts)          -> (N, D) unit vectors        CLIP / SigLIP 2 text side, via the `tokenizers` library
  tag(vec, bank)                  -> {axis: value, p, status}   value CHECK when below the axis's min_prob
  people(bgr)                     -> {count, biggest_h}         YOLOX-S; input size read from the graph
  faces(bgr)                      -> [{box, score, vec}]        OpenCV FaceDetectorYN + FaceRecognizerSF
  text(bgr)                       -> [{text, conf}]             rapidocr on onnxruntime
  transnet(frames_48x27)          -> per-frame cut probability  TransNetV2 ONNX (windowing as in the official MIT repo)
  caption(jpg_path)               -> one sentence               llama.cpp llama-mtmd-cli + SmolVLM2
  speech(path) / transcribe(path) -> speech spans / segments    faster-whisper (Silero VAD bundled; no PyTorch)
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import p0_env  # noqa: E402

GROUPS = ("core", "eyes", "faces_text", "captions", "speech")
PREP = {  # each model's own preprocessing (never mix): shortest edge, crop, mean, std
    "clip": (224, 224, (0.48145466, 0.4578275, 0.40821073), (0.26862954, 0.26130258, 0.27577711)),
    "siglip2": (224, 224, (0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
    "dino": (256, 224, (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)),
}


def V(path=None):
    return json.load(open(path or (p0_env.SKILL_DIR / "rules.json")))["vision"]


def mpath(key, i=0):
    f = V()["models"][key]["files"][i]
    return p0_env.model_file(key, f["file"], f["bytes"]) or (p0_env.models_dir() / key / f["file"])


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def profile_key():
    prof = p0_env.detect_profile()[0]
    if prof == "mac":
        import platform
        return "mac_silicon" if platform.machine() == "arm64" else "mac_intel"
    return prof


# ---------------------------------------------------------------- install (huggingface_hub does the downloading)
def install(groups):
    from huggingface_hub import hf_hub_download
    v, d, bad = V(), p0_env.models_dir(), []
    for key, m in v["models"].items():
        if m["group"] not in groups:
            continue
        for f in m["files"]:
            dest = d / key / f["file"]
            if p0_env.model_file(key, f["file"], f["bytes"]):
                print("  have   %-15s %s%s" % (key, f["file"], "" if dest.is_file() else "  (bundled in the skill)"))
                continue
            print("  fetch  %-15s %s (%.1f MB)" % (key, f["file"], f["bytes"] / 1e6), flush=True)
            try:
                if m.get("url"):
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with urllib.request.urlopen(m["url"], timeout=300) as r, open(dest, "wb") as o:
                        shutil.copyfileobj(r, o, 1 << 20)
                else:
                    hf_hub_download(m["repo"], f["file"], revision=m["revision"], local_dir=str(d / key))
            except Exception as e:
                bad.append("%s/%s: %s" % (key, f["file"], str(e)[:100]))
                continue
            if sha256(dest) != f["sha256"]:
                bad.append("%s/%s: checksum does not match the pin" % (key, f["file"]))
    if "captions" in groups and profile_key().startswith("mac"):
        bad += install_llama()
    return bad


def install_llama():
    t = V()["tools"]["llama_cpp"]
    spec = t.get(profile_key())
    if not spec:
        return ["llama.cpp: no pinned build for %s" % profile_key()]
    root = p0_env.models_dir() / "tools"
    exe = root / ("llama-%s" % t["release"]) / "llama-mtmd-cli"
    if exe.is_file():
        return []
    root.mkdir(parents=True, exist_ok=True)
    tgz = root / Path(spec["url"]).name
    with urllib.request.urlopen(spec["url"], timeout=300) as r, open(tgz, "wb") as o:
        shutil.copyfileobj(r, o, 1 << 20)
    if sha256(tgz) != spec["sha256"]:
        return ["llama.cpp: checksum does not match the pin"]
    with tarfile.open(tgz) as tf:
        tf.extractall(root, filter="data")
    return [] if exe.is_file() else ["llama.cpp: llama-mtmd-cli not found after unpacking"]


def llama_exe():
    t = V()["tools"]["llama_cpp"]
    exe = p0_env.models_dir() / "tools" / ("llama-%s" % t["release"]) / "llama-mtmd-cli"
    return exe if exe.is_file() else (shutil.which("llama-mtmd-cli") and Path(shutil.which("llama-mtmd-cli")))


# ---------------------------------------------------------------- shared helpers
def np_():
    import numpy as np
    return np


@lru_cache(maxsize=None)
def session(path):
    import onnxruntime as ort
    o = ort.SessionOptions()
    o.log_severity_level = 3
    return ort.InferenceSession(str(path), o, providers=["CPUExecutionProvider"])


def unit(x):
    np = np_()
    n = np.linalg.norm(x, axis=-1, keepdims=True)
    return x / np.where(n > 0, n, 1.0)


def finite_rows(x):
    """Rows that are finite and non-zero (anything else is CHECK, never normalized into evidence)."""
    np = np_()
    return np.isfinite(x).all(axis=1) & (np.abs(x).sum(axis=1) > 0)


def fam_of(key):
    return "clip" if key == "clip" else "siglip2" if key.startswith("siglip2") else "dino"


def mfile(key, *needles):
    """The pinned file of a model whose name holds every needle (e.g. "text_model", "tokenizer.json")."""
    for f in V()["models"][key]["files"]:
        if all(n in f["file"] for n in needles):
            return p0_env.model_file(key, f["file"], f["bytes"]) or (p0_env.models_dir() / key / f["file"])
    raise KeyError("%s has no file with %s" % (key, needles))


def tagger_keys(name):
    """Tagger choice -> (image model key, text model key)."""
    return {"clip": ("clip", "clip"), "siglip2_vision": ("siglip2_vision", "siglip2_text"),
            "siglip2_base": ("siglip2_vision", "siglip2_text"),
            "siglip2_so400m": ("siglip2_so400m", "siglip2_so400m")}[name]


def prep(rgb_list, family, size=224):
    """RGB uint8 images -> NCHW float32 with the model family's own resize / crop / mean / std (Pillow, bicubic)."""
    from PIL import Image
    np = np_()
    short, crop, mean, std = PREP[family]
    if family == "siglip2":
        crop = short = size                            # SigLIP: straight resize to the model's square (224 or 384)
    out = []
    for a in rgb_list:
        im = Image.fromarray(a)
        if family == "siglip2":
            im = im.resize((crop, crop), Image.BICUBIC)
        else:
            w, h = im.size
            s = short / min(w, h)
            im = im.resize((max(crop, round(w * s)), max(crop, round(h * s))), Image.BICUBIC)
            w, h = im.size
            l, t = (w - crop) // 2, (h - crop) // 2
            im = im.crop((l, t, l + crop, t + crop))
        x = (np.asarray(im, dtype="float32") / 255.0 - np.array(mean, "float32")) / np.array(std, "float32")
        out.append(x.transpose(2, 0, 1))
    return np.stack(out) if out else np.zeros((0, 3, crop, crop), "float32")


def proc_size(key):
    """The model's own preprocessor size (preprocessor_config.json), 224 when it has none."""
    try:
        c = json.load(open(mfile(key, "preprocessor_config.json")))
        sz = c.get("size") or {}
        return int(sz.get("height") or sz.get("shortest_edge") or c.get("crop_size", {}).get("height") or 224)
    except (KeyError, OSError, ValueError):
        return 224


def embed_image(key, rgb_list, batch=8):
    """key: clip | siglip2_vision | siglip2_so400m | dino_small | dinov2_base  ->  (N, D) unit vectors."""
    np = np_()
    fam = fam_of(key)
    s = session(mfile(key, "vision") if fam != "dino" else mpath(key, 0))
    inp = s.get_inputs()[0]
    size = inp.shape[2] if isinstance(inp.shape[2], int) else proc_size(key)   # 384 for So400m: graph, else its config
    outs = []
    for i in range(0, len(rgb_list), batch):
        r = s.run(None, {inp.name: prep(rgb_list[i:i + batch], fam, size)})
        if fam == "dino":
            outs.append(r[0][:, 0, :])                   # final CLS token (pooling policy: rules.json, never mixed)
        else:
            names = [o.name for o in s.get_outputs()]
            outs.append(r[names.index("image_embeds")] if "image_embeds" in names else (
                r[names.index("pooler_output")] if "pooler_output" in names else r[-1]))
    x = np.concatenate(outs).astype("float32") if outs else np.zeros((0, 1), "float32")
    return unit(x)


@lru_cache(maxsize=None)
def tokenizer(key):
    from tokenizers import Tokenizer
    return Tokenizer.from_file(str(mfile(key, "tokenizer.json")))


def embed_text(key, texts):
    """key: clip | siglip2_text | siglip2_so400m (tag words) | minilm (search text) -> (N, D) unit vectors."""
    np = np_()
    tok = tokenizer(key)
    if key == "minilm":                                 # sentence-transformers recipe: mean of tokens under the mask
        tok.enable_truncation(256)
        tok.enable_padding()
        enc = tok.encode_batch(list(texts))
        ids = np.array([e.ids for e in enc], "int64")
        mask = np.array([e.attention_mask for e in enc], "int64")
        s = session(mfile(key, "model"))
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in [i.name for i in s.get_inputs()]:
            feed["token_type_ids"] = np.zeros_like(ids)
        h = s.run(None, feed)[0]
        m = mask[..., None].astype("float32")
        return unit((h * m).sum(1) / np.maximum(m.sum(1), 1e-9))
    n = 77 if key == "clip" else 64
    tok.enable_truncation(n)
    if key == "clip":
        eot = tok.token_to_id("<|endoftext|>")
        tok.enable_padding(length=n, pad_id=eot, pad_token="<|endoftext|>")
    else:                                              # SigLIP: lowercased, padded to 64 with <pad>
        tok.enable_padding(length=n, pad_id=tok.token_to_id("<pad>") or 0)
    ids = np.array([e.ids for e in tok.encode_batch([t if key == "clip" else t.lower() for t in texts])], dtype="int64")
    s = session(mfile(key, "text_model") if key != "clip" else mpath(key, 1))
    r = s.run(None, {s.get_inputs()[0].name: ids})
    names = [o.name for o in s.get_outputs()]
    x = r[names.index("text_embeds")] if "text_embeds" in names else (
        r[names.index("pooler_output")] if "pooler_output" in names else r[-1])
    return unit(x.astype("float32"))


def tag_bank(text_key):
    """{axis: (labels, (L, D), min_prob, scale)} from rules.json -> vision.axes, cached per word-list version and model."""
    np = np_()
    v = V()
    axes = v["axes"]
    scale = float(v.get("tagging", {}).get("clip" if text_key == "clip" else "siglip", {}).get("scale", 100.0))
    h = hashlib.sha1((text_key + json.dumps(axes, sort_keys=True)).encode()).hexdigest()[:12]
    cache = p0_env.models_dir() / ("tags_%s_%s.npz" % (text_key, h))
    if cache.is_file():
        z = np.load(cache)
        return {a: (list(axes[a]["labels"]), z[a], axes[a]["min_prob"], scale) for a in axes}
    bank = {a: (list(sp["labels"]), np.stack([unit(embed_text(text_key, sp["labels"][l]).mean(0)) for l in sp["labels"]]),
                sp["min_prob"], scale) for a, sp in axes.items()}
    with open(cache, "wb") as f:
        np.savez(f, **{a: b[1] for a, b in bank.items()})
    return bank


def tag(vec, bank):
    """Per axis: softmax over the labels of cosine x the model family's scale (CLIP 100; SigLIP uncalibrated, R16)."""
    np = np_()
    out = {}
    for a, (labels, T, min_p, scale) in bank.items():
        z = scale * (T @ vec)
        p = np.exp(z - z.max())
        p /= p.sum()
        i = int(p.argmax())
        sure = float(p[i]) >= min_p
        out[a] = {"value": labels[i] if sure else "CHECK", "best": labels[i], "p": round(float(p[i]), 3),
                  "status": "verified" if sure else "unresolved"}
    return out


# ---------------------------------------------------------------- people (YOLOX-S; decode adapted from YOLOX's own ONNX demo, Apache-2.0)
def people(bgr, min_score=None):
    import cv2
    np = np_()
    min_score = V()["people_min_score"] if min_score is None else min_score
    s = session(mpath("yolox_s"))
    inp = s.get_inputs()[0]
    H, W = int(inp.shape[2]), int(inp.shape[3])            # 640x640 for YOLOX-S (416 for nano): read, never assume
    h0, w0 = bgr.shape[:2]
    r = min(H / h0, W / w0)
    pad = np.full((H, W, 3), 114, np.uint8)
    pad[: int(h0 * r), : int(w0 * r)] = cv2.resize(bgr, (int(w0 * r), int(h0 * r)), interpolation=cv2.INTER_LINEAR)
    out = s.run(None, {inp.name: pad.transpose(2, 0, 1)[None].astype("float32")})[0][0]
    grids, strides = [], []
    for st in (8, 16, 32):
        hs, ws = H // st, W // st
        xv, yv = np.meshgrid(np.arange(ws), np.arange(hs))
        grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
        strides.append(np.full((hs * ws, 1), st))
    g, st = np.concatenate(grids), np.concatenate(strides)
    xy, wh = (out[:, :2] + g) * st, np.exp(out[:, 2:4]) * st
    score = out[:, 4] * out[:, 5]                              # objectness x person (COCO class 0)
    keep = score >= min_score
    if not keep.any():
        return {"count": 0, "biggest_h": 0.0}
    xy, wh, score = xy[keep] / r, wh[keep] / r, score[keep]
    boxes = [[float(x - w / 2), float(y - h / 2), float(w), float(h)] for (x, y), (w, h) in zip(xy, wh)]
    idx = [int(i) for i in np.array(cv2.dnn.NMSBoxes(boxes, [float(v) for v in score], min_score, 0.45)).reshape(-1)]
    return {"count": len(idx), "biggest_h": round(max(min(boxes[i][3] / h0, 1.0) for i in idx), 3) if idx else 0.0}


# ---------------------------------------------------------------- faces (OpenCV's own detector + recognizer)
@lru_cache(maxsize=None)
def _face_models():
    import cv2
    det = cv2.FaceDetectorYN.create(str(mpath("yunet")), "", (320, 320), V()["faces"]["min_score"], 0.3, 50)
    rec = cv2.FaceRecognizerSF.create(str(mpath("sface")), "")
    return det, rec


def faces(bgr):
    np = np_()
    det, rec = _face_models()
    h, w = bgr.shape[:2]
    det.setInputSize((w, h))
    _, found = det.detect(bgr)
    out = []
    for f in (found if found is not None else []):
        vec = rec.feature(rec.alignCrop(bgr, f)).reshape(-1).astype("float32")
        out.append({"box": [round(float(v), 1) for v in f[:4]], "score": round(float(f[-1]), 3),
                    "vec": unit(vec[None])[0]})
    return out


def same_person(a, b):
    return float(a @ b) >= V()["faces"]["same_person_above"]


# ---------------------------------------------------------------- on-screen text (rapidocr on onnxruntime)
@lru_cache(maxsize=None)
def _ocr():
    import logging
    from rapidocr import RapidOCR
    eng = RapidOCR()
    for name in list(logging.root.manager.loggerDict):          # it logs every model load and empty frame otherwise
        if "rapidocr" in name.lower():
            logging.getLogger(name).setLevel(logging.ERROR)
    return eng


def text(bgr):
    o = V()["ocr"]
    r = _ocr()(bgr)
    txts, scores = (getattr(r, "txts", None) or ()), (getattr(r, "scores", None) or ())
    return [{"text": t, "conf": round(float(c), 3)} for t, c in zip(txts, scores)
            if float(c) >= o["min_conf"] and len(t.strip()) >= o["min_len"]]


# ---------------------------------------------------------------- TransNetV2 (neural shot cuts, second opinion)
def transnet(frames_48x27):
    """(N, 27, 48, 3) uint8 RGB at the clip's frame rate -> (N,) probability that frame i is the LAST frame of a shot.
    Windowing as in the official TransNetV2 inference code (MIT): 100-frame windows, 25 padded each side, keep the middle 50."""
    np = np_()
    s = session(mpath("transnetv2"))
    n = len(frames_48x27)
    if not n:
        return np.zeros(0, "float32")
    pad_end = 25 + 50 - (n % 50 if n % 50 else 50)
    v = np.concatenate([np.repeat(frames_48x27[:1], 25, 0), frames_48x27, np.repeat(frames_48x27[-1:], pad_end, 0)])
    probs, inp = [], s.get_inputs()[0]
    for i in range(0, len(v) - 50, 50):
        win = v[i:i + 100][None].astype("float32")      # input: float32 0..255, (1, 100, 27, 48, 3)
        out = s.run(None, {inp.name: win})[0]
        probs.append(out.reshape(100, -1)[25:75, 0])     # this export already outputs probabilities (no 2nd sigmoid)
    # a high value marks the LAST frame of a shot: the cut is at the next frame
    return np.concatenate(probs)[:n]


# ---------------------------------------------------------------- captions (llama.cpp + SmolVLM2; overnight pass)
def caption(jpg_path, timeout=300):
    exe = llama_exe()
    if not exe:
        return {"value": "PENDING", "status": "needs_scan", "why": "llama.cpp not installed (install captions)"}
    c = V()["captions"]
    cmd = [str(exe), "-m", str(mpath("smolvlm2", 0)), "--mmproj", str(mpath("smolvlm2", 1)), "--image", str(jpg_path),
           "-p", c["prompt"], "-n", str(c["max_tokens"]), "--temp", "0", "--no-warmup"]
    if c.get("threads"):
        cmd += ["-t", str(c["threads"])]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"value": "PENDING", "status": "needs_scan", "why": "caption took longer than %d s" % timeout}
    line = " ".join(r.stdout.strip().split())
    return {"value": line, "status": "verified" if (r.returncode == 0 and line) else "unresolved",
            "why": "" if line else (r.stderr.strip().splitlines() or ["no output"])[-1][:120]}


# ---------------------------------------------------------------- speech + transcripts (faster-whisper; audio only when asked)
def speech(path):
    """Seconds of detected speech, using the Silero VAD that ships inside faster-whisper (no PyTorch, no transcript)."""
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import get_speech_timestamps, VadOptions
    audio = decode_audio(str(path), sampling_rate=16000)
    spans = get_speech_timestamps(audio, VadOptions())
    return {"speech_s": round(sum(s["end"] - s["start"] for s in spans) / 16000, 1),
            "spans": [(round(s["start"] / 16000, 2), round(s["end"] / 16000, 2)) for s in spans]}


@lru_cache(maxsize=None)
def _whisper():
    from faster_whisper import WhisperModel
    return WhisperModel(str(p0_env.models_dir() / "whisper_turbo"), device="cpu", compute_type="int8")


def transcribe(path):
    segs, info = _whisper().transcribe(str(path), vad_filter=True, language=V()["speech"].get("language"))
    return {"language": info.language, "segments": [{"t_in": round(s.start, 2), "t_out": round(s.end, 2), "text": s.text.strip()}
                                                    for s in segs]}


# ---------------------------------------------------------------- CLI
def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        ok, why = p0_env.models_ok()
        print("vision · %s · %s" % (p0_env.models_dir(), why))
        for g, (have, miss) in sorted(p0_env.model_groups().items(), key=lambda kv: GROUPS.index(kv[0])):
            mb = sum(f["bytes"] for m in V()["models"].values() if m["group"] == g for f in m["files"]) / 1e6
            print("  %s %-11s %7.0f MB%s" % ("OK  " if have else "MISS", g, mb, "" if have else "  (%d files)" % len(miss)))
        print("  %s llama.cpp (captions)" % ("OK  " if llama_exe() else "MISS"))
        return 0 if ok else 1
    if cmd == "install":
        want = set(GROUPS) if "all" in argv[2:] else set(argv[2:] or ["core"])
        bad = install(want)
        for b in bad:
            print("  FAIL " + b)
        return 1 if bad else 0
    if cmd == "try" and len(argv) > 2:
        import cv2
        bgr = cv2.imread(argv[2])
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        ch = V()["choose"]
        tk = ch["tagger"]
        for a, t in tag(embed_image(tk if tk == "clip" else "siglip2_vision", [rgb])[0],
                        tag_bank(tk if tk == "clip" else "siglip2_text")).items():
            print("  %-8s %-18s p=%.2f" % (a, t["value"], t["p"]))
        print("  people   %s" % people(bgr))
        for name, fn in (("faces", lambda: len(faces(bgr))), ("text", lambda: text(bgr)), ("caption", lambda: caption(argv[2]))):
            try:
                print("  %-8s %s" % (name, fn()))
            except Exception as e:                    # a group that is not installed says so; the rest still answer
                print("  %-8s not available (%s)" % (name, str(e)[:80]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
