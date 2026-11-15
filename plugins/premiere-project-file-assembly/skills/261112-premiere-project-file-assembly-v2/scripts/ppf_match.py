#!/usr/bin/env python3
"""ppf_match.py (probe): notes or a prompt -> clips ranked by meaning.

  ppf_match.py objects.json --text "aerial over the city" [--segments P6_segments.csv] [--top 5] [--engine auto|minilm|tfidf]

Clip text = file name, card, day, kind, plus the scan's segment words (content, shot size,
light, caption) when the P6 segments CSV is given. Engine: MiniLM (all-MiniLM-L6-v2, ONNX,
quantized, 23 MB) from this plugin's own models/minilm FIRST, the scan plugin's folder second,
run with onnxruntime+numpy if both import; otherwise a stdlib TF-IDF fallback. The first line says
which engine ran. It ranks; it never decides. Matching is not part of build: an LLM uses
the ranking to write ops (references/ops.md).
"""
import argparse, csv, json, math, os, re, sys, unicodedata
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ppf_common import load_json

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.normpath(os.path.join(HERE, "../../.."))
MODEL_DIRS = [os.path.join(PLUGIN, "models", "minilm"),                      # 1. bundled (MODELS.md)
              os.path.normpath(os.path.join(PLUGIN, "../scan-and-index-hard-drives/skills/scan-and-index-hard-drives/models/minilm"))]  # 2. scan plugin
# 3. neither present (or onnxruntime/numpy missing) -> TF-IDF fallback. Pinned source in models/MODELS.md.


def find_model(model_dir=None):
    """First folder holding onnx/model_quantized.onnx + tokenizer.json; None if none."""
    for d in ([model_dir] if model_dir else []) + MODEL_DIRS:
        if d and os.path.isfile(os.path.join(d, "onnx", "model_quantized.onnx")) and os.path.isfile(os.path.join(d, "tokenizer.json")):
            return d
    return None


MINILM = find_model() or MODEL_DIRS[0]


def clip_texts(objects, segments_csv=None):
    seg = {}
    if segments_csv:
        for r in csv.DictReader(open(segments_csv, encoding="utf-8")):
            seg.setdefault(r["rel_path"], []).append(" ".join(r.get(k) or "" for k in ("content", "shot_size", "light", "caption", "text")))
    out = {}
    for c in objects["clips"]:
        words = [c["clip"], c["card"], c["day"], c["kind"]] + seg.get(c["rel_path"], [])
        out[c["clip_id"]] = re.sub(r"[_./]+", " ", " ".join(words))
    return out


# ---- WordPiece (BERT uncased) from tokenizer.json, stdlib
class WordPiece:
    def __init__(self, path):
        t = json.load(open(path, encoding="utf-8"))
        self.vocab = t["model"]["vocab"]; self.unk = self.vocab["[UNK]"]

    def encode(self, text, max_len=128):
        text = unicodedata.normalize("NFD", text.lower())
        text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
        ids = [self.vocab["[CLS]"]]
        for w in re.findall(r"\w+|[^\w\s]", text):
            start, pieces = 0, []
            while start < len(w):
                end, cur = len(w), None
                while start < end:
                    s = ("##" if start else "") + w[start:end]
                    if s in self.vocab:
                        cur = self.vocab[s]; break
                    end -= 1
                if cur is None:
                    pieces = [self.unk]; break
                pieces.append(cur); start = end
            ids += pieces
        return ids[:max_len - 1] + [self.vocab["[SEP]"]]


_EMB = {}


def minilm_embedder(model_dir):
    """-> emb(text) returning a unit numpy vector (mean-pooled MiniLM). Cached per folder."""
    if model_dir not in _EMB:
        import numpy as np, onnxruntime as ort
        tok = WordPiece(os.path.join(model_dir, "tokenizer.json"))
        sess = ort.InferenceSession(os.path.join(model_dir, "onnx", "model_quantized.onnx"))

        def emb(s):
            ids = np.array([tok.encode(s)], dtype=np.int64); m = np.ones_like(ids)
            h = sess.run(None, {"input_ids": ids, "attention_mask": m, "token_type_ids": np.zeros_like(ids)})[0][0]
            v = h.mean(axis=0); return v / (np.linalg.norm(v) + 1e-9)
        _EMB[model_dir] = emb
    return _EMB[model_dir]


def rank_minilm(query, texts, model_dir):
    if not model_dir or not os.path.isdir(model_dir):
        raise FileNotFoundError("no MiniLM folder (looked in %s)" % ", ".join(MODEL_DIRS))
    emb = minilm_embedder(model_dir)
    q = emb(query)
    return sorted(((float(q @ emb(t)), k) for k, t in texts.items()), reverse=True)


def engine_line(eng, model_dir=None):
    if eng != "minilm":
        return "engine: tfidf (fallback: MiniLM not usable here)"
    where = "bundled models/minilm" if os.path.normpath(model_dir or "") == os.path.normpath(MODEL_DIRS[0]) else model_dir
    return "engine: minilm (%s)" % where


def rank_tfidf(query, texts):
    tokz = lambda s: re.findall(r"[a-z0-9]+", s.lower())
    docs = {k: Counter(tokz(t)) for k, t in texts.items()}
    df = Counter(w for d in docs.values() for w in d)
    n = len(docs)
    idf = lambda w: math.log((n + 1) / (df.get(w, 0) + 1)) + 1

    def vec(c):
        v = {w: f * idf(w) for w, f in c.items()}; z = math.sqrt(sum(x * x for x in v.values())) or 1
        return {w: x / z for w, x in v.items()}
    q = vec(Counter(tokz(query)))
    return sorted(((sum(q.get(w, 0) * x for w, x in vec(d).items()), k) for k, d in docs.items()), reverse=True)


def rank(query, objects, segments=None, engine="auto", model_dir=None):
    model_dir = find_model(model_dir)
    texts = clip_texts(objects, segments)
    if engine in ("auto", "minilm"):
        try:
            return "minilm", rank_minilm(query, texts, model_dir)
        except Exception as e:                        # missing onnxruntime/numpy/model
            if engine == "minilm":
                raise
            print("match: MiniLM unavailable (%s: %s); TF-IDF fallback" % (type(e).__name__, e))
    return "tfidf", rank_tfidf(query, texts)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("objects"); ap.add_argument("--text", required=True); ap.add_argument("--segments")
    ap.add_argument("--top", type=int, default=5); ap.add_argument("--engine", default="auto", choices=["auto", "minilm", "tfidf"])
    ap.add_argument("--model-dir", help="MiniLM folder (default: plugin models/minilm, then the scan plugin's)")
    a = ap.parse_args(argv)
    eng, r = rank(a.text, load_json(a.objects), a.segments, a.engine, a.model_dir)
    print(engine_line(eng, find_model(a.model_dir)))
    for s, k in r[:a.top]:
        print("%.3f  %s" % (s, k))
    return eng, r


if __name__ == "__main__":
    from ppf_common import maybe_reexec
    maybe_reexec()
    main()
