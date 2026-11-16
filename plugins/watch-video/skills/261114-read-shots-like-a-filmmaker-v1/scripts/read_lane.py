#!/usr/bin/env python3
"""read_lane.py: the vision lane (P12). One question per slot, closed list, measured facts in the prompt, evidence checked against the numbers.

Rules (references/how-a-filmmaker-watches.md): 1. one dimension per question, never "describe this shot"; 2. the poet must show the
numbers: a read value whose words contradict a measured one is rejected (unresolved, rejected text kept in note).

    read(frame_path, measured, slots, model=None) -> {field: {value, trust, rule, note, evidence, margin}}
    python3 read_lane.py <frame> --measured measures.jsonl --out read.jsonl [--model DIR] [--slots a b c] [--lane local|hosted]

Paths, in order of preference (each answer records which ran in `rule`):
  logits : the model scores every allowed label as a continuation of the slot question (log-prob, mean per token); best wins;
           margin to the runner-up below MARGIN -> unresolved.               (Palmer, Table B: score, don't generate)
  text   : the model only generates; the first allowed label found in its output wins, else unresolved.
  none   : no model installed -> every requested slot is unresolved, reason "model not installed"; exit 0.
Model: SmolVLM-256M/500M-Instruct, official ONNX export, onnxruntime only (no torch). Install: references/vision-lane.md.
var_casting is NEVER asked of the model (Sexton: source or person only); asking for it raises ValueError.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
PLUGIN = SKILL.parents[1]
sys.path.insert(0, str(HERE))
from shot_ledger import SCHEMA, vocab  # noqa: E402  (read-only: the closed lists live there and in vocab.json)

U = "unresolved"
MARGIN = 0.35          # nats, mean log-prob per label token, top minus runner-up
EVIDENCE_WORDS = 25
# A free-text answer that names the medium, or starts like a caption, is the model describing the picture
# instead of answering the question. Refused for every bounded free-text slot (261116).
MEDIUM = re.compile(r"\b(image|images|photo|photograph|picture|video|movie|film|frame|screenshot|footage|shot)\b", re.I)
OPENER = re.compile(r"^\s*(the|this|that|there|it|a|an|in|here)\b\s+(is|are|was|were|appears|shows|depicts|looks|seems|we|you|image|picture|photo)\b", re.I)
FORBIDDEN = {"var_casting": "var_casting comes from the stock listing or a person, never from the model (Sexton, grammar section 2)"}
# the slots the lane fills: every R field in the ledger schema
READ_SLOTS = [k for k, v in SCHEMA.items() if v[0] == "R"]
PERSON = {k for k, v in SCHEMA.items() if v[0] == "R" and v[1] == "person"}
QUESTION = {
    "subject": "What is the main subject? Answer with one head noun.",
    "subject_mods": "Up to three words that set the main subject apart (not ethnicity, not a count).",
    "var_form": "Is the subject solo, a couple, a family, a group or a crowd?",
    "var_age": "What age band is the main subject?",
    "var_state": "In at most two words, the subject's visible state (e.g. 'in white', 'wet', 'silhouetted').",
    "verb": "What is the subject doing? Answer with one verb.",
    "place": "Where is this, in at most four words?",
    "camera_angle": "Where does the camera stand relative to the subject: its angle?",
    "lighting_style": "Name the lighting style.",
    "quality_of_light": "Name the quality of the light.",
    "time_of_day": "What time of day does the light say?",
    "colour_design": "Name the colour design.",
    "depth": "How is depth staged in the frame?",
    "mood": "Name the mood.",
    "tone": "Name the tone.",
    "production_value": "Rate the production value.",
    "pv_set": "How is the set dressed?",
    "pv_props": "What are the props like?",
    "pv_performers": "How do the performers behave?",
    "pv_staging": "How is the action staged?",
    "believability": "Does this read as real?",
    "cliche_markers": "Which stock cliche, if any, is in the frame?",
}
# which measured facts ride with which question (so the model may not contradict them)
FACTS_FOR = {"lighting_style": ("p50", "key", "ratio"), "quality_of_light": ("ratio", "p50"), "time_of_day": ("p50", "cct"),
             "colour_design": ("cct", "sat"), "mood": ("p50", "key", "cct"), "camera_angle": ("horizon",),
             "verb": ("camera_move",), "production_value": ("p50", "noise")}


# ---------------------------------------------------------------- measured facts
def _v(m, k):
    x = m.get(k)
    return x.get("value") if isinstance(x, dict) and "value" in x else x


def facts(measured):
    """Flatten a shot_measure `measures` dict (or a flat dict) to the few numbers the questions and the contradiction table use."""
    m = measured.get("measures", measured) if isinstance(measured, dict) else {}
    f = {}
    lv = _v(m, "levels")
    if isinstance(lv, dict) and isinstance(lv.get("p50_ire"), (int, float)):
        f["p50"] = lv["p50_ire"]
    elif isinstance(m.get("p50_ire"), (int, float)):
        f["p50"] = m["p50_ire"]
    if "p50" in f:
        f["key"] = "low" if f["p50"] < 30 else "high" if f["p50"] > 65 else "mid"
    c = _v(m, "cct")
    if isinstance(c, dict) and isinstance(c.get("kelvin"), (int, float)):
        f["cct"] = c["kelvin"]
    elif isinstance(m.get("cct_k"), (int, float)):
        f["cct"] = m["cct_k"]
    r = _v(m, "lighting_ratio")
    if isinstance(r, dict) and r.get("ratio"):
        f["ratio"] = r["ratio"]
    s = _v(m, "saturation")
    if isinstance(s, dict) and "mean" in s:
        f["sat"] = s["mean"]
    n = _v(m, "noise")
    if isinstance(n, dict) and isinstance(n.get("sigma"), (int, float)):
        f["noise"] = n["sigma"]
    h = _v(m, "horizon")
    if isinstance(h, dict) and isinstance(h.get("tilt_deg"), (int, float)):
        f["horizon"] = h["tilt_deg"]
    cm = _v(m, "camera_move")
    if isinstance(cm, str) and cm not in ("unknown", ""):
        f["camera_move"] = cm
    else:
        mo = _v(m, "motion")
        if isinstance(mo, dict) and "per second" in str(mo.get("unit", "")):
            cam = mo["camera"]
            f["camera_move"] = "locked" if cam["speed_pct_w"] < 1 and abs(cam["zoom_pct"]) < 3 else "moving"
    return f


FACT_TEXT = {"p50": "levels p50 %s IRE", "key": "key %s", "cct": "colour temperature %s K", "ratio": "lighting ratio %s",
             "sat": "saturation mean %s", "noise": "noise sigma %s", "horizon": "horizon tilt %s deg", "camera_move": "camera %s"}


def fact_line(slot, f):
    keys = FACTS_FOR.get(slot, ())
    return ", ".join(FACT_TEXT[k] % f[k] for k in keys if k in f)


# ---------------------------------------------------------------- the contradiction table (rule 2)
STATIC = {"static", "locked"}
CONTRA = [  # (pattern in the read text, fact, test(fact) -> contradicts, why)
    (r"\blow[- ]key\b", "p50", lambda v: v > 45, "'low key' but levels p50 %s IRE > 45"),
    (r"\bhigh[- ]key\b", "p50", lambda v: v < 30, "'high key' but levels p50 %s IRE < 30"),
    (r"\b(sunset|golden)\b", "cct", lambda v: v > 6500, "'sunset/golden' but colour temperature %s K > 6500"),
    (r"\b(static|locked)\b", "camera_move", lambda v: v not in STATIC, "'static' but measured camera move is %s"),
    (r"\bnight\b", "p50", lambda v: v > 40, "'night' but levels p50 %s IRE > 40"),
]


def contradiction(text, f):
    t = (text or "").lower()
    for pat, k, bad, why in CONTRA:
        if k in f and re.search(pat, t) and bad(f[k]):
            return why % f[k]
    return None


# ---------------------------------------------------------------- label matching (text path)
def match_label(text, labels):
    """The first allowed label in the output (earliest position; longest label wins a tie); None if no label appears."""
    t = (text or "").lower()
    best = None
    for lab in labels:
        m = re.search(r"(?<![\w-])" + re.escape(lab.lower()) + r"(?![\w-])", t)
        if m and (best is None or m.start() < best[0] or (m.start() == best[0] and len(lab) > len(best[1]))):
            best = (m.start(), lab)
    return best[1] if best else None


def labels_for(slot):
    spec = SCHEMA[slot][2]
    if isinstance(spec, list):
        return spec
    if isinstance(spec, tuple) and spec[0] == "vocab":
        return vocab(spec[1])
    return None  # bounded free text


def bound_words(slot):
    spec = SCHEMA[slot][2]
    return spec[1] if isinstance(spec, tuple) and spec[0] == "words" else None


def clip_evidence(text):
    w = re.sub(r"\s+", " ", (text or "").strip()).split(" ")
    return " ".join(w[:EVIDENCE_WORDS]) if w != [""] else ""


def prompt_for(slot, f, labels=None):
    p = "You are a cinematographer reading one frame. Field: %s. %s" % (slot, QUESTION.get(slot, "Answer for this field only."))
    fl = fact_line(slot, f)
    if fl:
        p += " Measured (do not contradict): %s." % fl
    if labels:
        p += " Answer with one of: %s." % " / ".join(labels)
    return p


# ---------------------------------------------------------------- the model adapter
def model_dirs(flag=None):
    """Where to look for the reader. An explicit --model is the ONLY place looked.

    261116: the flag used to be the first of several candidates, so `--model /some/empty/dir` quietly loaded
    whatever reader happened to be installed elsewhere. Naming a model has to mean that model, or a run that
    thinks it is probing one build is really reporting another.
    """
    if flag:
        return [Path(flag)]
    out = [PLUGIN / "models" / "smolvlm", Path.home() / ".scan-and-index" / "models" / "smolvlm"]
    if os.environ.get("WATCH_MODELS_DIR"):
        out.append(Path(os.environ["WATCH_MODELS_DIR"]) / "smolvlm")
    return out


ONNX_PARTS = ("vision_encoder", "embed_tokens", "decoder_model_merged")
# Per-part preference, most wanted first. Each part is resolved on its own: a published export mixes
# quantizations freely, and onnxruntime takes each graph separately, so one part must never veto another.
# 261114 (Diego's Mac, probe 1): the int8 vision encoder is signed-int8 ConvInteger, and the CPU provider has
# no kernel for it -> "NOT_IMPLEMENTED ... ConvInteger(10)". So int8 goes last for the vision tower only;
# the decoder and the embedding table are MatMulInteger and load fine at int8.
QUANT = {
    "vision_encoder": ("_uint8", "_q4", "_fp16", "", "_quantized", "_int8", "_q4f16", "_bnb4"),
    "embed_tokens": ("_int8", "_uint8", "_quantized", "_q4", "_fp16", "", "_q4f16", "_bnb4"),
    "decoder_model_merged": ("_int8", "_uint8", "_quantized", "_q4", "_fp16", "", "_q4f16", "_bnb4"),
}


def part_candidates(d, part):
    """Every on-disk file for one part, in preference order."""
    return [d / "onnx" / ("%s%s.onnx" % (part, q)) for q in QUANT[part]
            if (d / "onnx" / ("%s%s.onnx" % (part, q))).exists()]


def find_model(flag=None):
    """(dir, {part: [candidate paths, best first]}) for the first usable SmolVLM export, else (None, reason).

    "Usable" is one or more files per part. Which one actually loads is decided by onnxruntime in SmolVLM,
    part by part, because a file can exist and still have no kernel on this machine.
    """
    missing = []
    for d in model_dirs(flag):
        if not (d / "config.json").exists() or not (d / "tokenizer.json").exists():
            continue
        cands = {p: part_candidates(d, p) for p in ONNX_PARTS}
        empty = [p for p, c in cands.items() if not c]
        if empty:
            missing.append("%s (no onnx file for %s)" % (d, ", ".join(empty)))
            continue
        return d, cands
    if missing:
        return None, "model incomplete: " + "; ".join(missing)
    return None, "model not installed (looked in %s)" % ", ".join(str(d) for d in model_dirs(flag))


class SmolVLM:
    """SmolVLM ONNX on onnxruntime, no torch. Global 512 px image only (no tiling): 64 image tokens. Exposes score() (logits path)
    and generate() (evidence). Written to the published export layout; first run on the Mac is its probe (references/vision-lane.md)."""
    path = "logits"

    def __init__(self, d, parts):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.np = np
        cfg = json.load(open(d / "config.json"))
        tc = cfg.get("text_config", cfg)
        self.layers = tc["num_hidden_layers"]
        self.kv_heads = tc.get("num_key_value_heads", tc["num_attention_heads"])
        self.head_dim = tc.get("head_dim", tc["hidden_size"] // tc["num_attention_heads"])
        self.image_id = cfg.get("image_token_id", 49190)
        self.size = (cfg.get("vision_config") or {}).get("image_size", 512)
        self.n_img = cfg.get("image_seq_len", 64)
        self.tok = Tokenizer.from_file(str(d / "tokenizer.json"))
        so = ort.SessionOptions()
        self.loaded = {}
        self.vis = self._session(ort, so, parts, "vision_encoder")
        self.emb = self._session(ort, so, parts, "embed_tokens")
        self.dec = self._session(ort, so, parts, "decoder_model_merged")
        self.eos = {self.tok.token_to_id(t) for t in ("<end_of_utterance>", "<|im_end|>", "</s>") if self.tok.token_to_id(t) is not None}
        self._img = {}

    def _session(self, ort, so, parts, part):
        """First candidate for this part that onnxruntime can actually build a session for.

        A file that exists is not a file that runs: an int8 export can need an operator kernel this build does
        not have. Walking the list per part keeps a working decoder from being thrown away over the vision tower.
        """
        cands = parts[part] if isinstance(parts[part], (list, tuple)) else [parts[part]]
        errs = []
        for f in cands:
            try:
                sess = ort.InferenceSession(str(f), so, providers=["CPUExecutionProvider"])
                self.loaded[part] = f.name
                return sess
            except Exception as e:  # noqa: BLE001  try the next quantization, report them all if none load
                errs.append("%s: %s" % (f.name, str(e).split("\n")[0][:120]))
        raise RuntimeError("%s: no loadable file (%s)" % (part, " | ".join(errs)))

    def _pixels(self, frame_path):
        from PIL import Image
        np = self.np
        im = Image.open(frame_path).convert("RGB").resize((self.size, self.size), Image.BICUBIC)
        x = (np.asarray(im, np.float32) / 255.0 - 0.5) / 0.5
        return x.transpose(2, 0, 1)[None, None]  # [1, 1, 3, H, W]

    def _image_features(self, frame_path):
        if frame_path not in self._img:
            np = self.np
            px = self._pixels(frame_path)
            feeds = {"pixel_values": px}
            if any(i.name == "pixel_attention_mask" for i in self.vis.get_inputs()):
                feeds["pixel_attention_mask"] = np.ones((1, 1, self.size, self.size), bool)
            self._img[frame_path] = self.vis.run(None, feeds)[0].reshape(-1, self._hidden())
        return self._img[frame_path]

    def _hidden(self):
        return self.emb.get_outputs()[0].shape[-1] if isinstance(self.emb.get_outputs()[0].shape[-1], int) else 576

    def _embed(self, ids):
        return self.emb.run(None, {"input_ids": self.np.array([ids], self.np.int64)})[0]

    def _chat_ids(self, prompt):
        img = "<fake_token_around_image><global-img>" + "<image>" * self.n_img + "<fake_token_around_image>"
        text = "<|im_start|>User:%s%s<end_of_utterance>\nAssistant:" % (img, prompt)
        return self.tok.encode(text, add_special_tokens=False).ids

    def _empty_past(self):
        np = self.np
        return {"past_key_values.%d.%s" % (i, kv): np.zeros((1, self.kv_heads, 0, self.head_dim), np.float32)
                for i in range(self.layers) for kv in ("key", "value")}

    def _step(self, embeds, past, start, total):
        np = self.np
        n = embeds.shape[1]
        feeds = dict(past)
        feeds.update({"inputs_embeds": embeds.astype(np.float32), "attention_mask": np.ones((1, total), np.int64),
                      "position_ids": np.arange(start, start + n, dtype=np.int64)[None]})
        want = {i.name for i in self.dec.get_inputs()}
        out = self.dec.run(None, {k: v for k, v in feeds.items() if k in want})
        names = [o.name for o in self.dec.get_outputs()]
        logits = out[0][0]
        present = {n.replace("present", "past_key_values"): o for n, o in zip(names[1:], out[1:])}
        return logits, present

    def _prefill(self, frame_path, prompt):
        np = self.np
        ids = self._chat_ids(prompt)
        e = self._embed(ids)
        feats = self._image_features(frame_path)
        pos = [i for i, t in enumerate(ids) if t == self.image_id]
        e[0, pos[:len(feats)]] = feats[:len(pos)]
        logits, past = self._step(e, self._empty_past(), 0, len(ids))
        return ids, logits, past

    def _logsoftmax(self, x):
        np = self.np
        x = x - x.max(-1, keepdims=True)
        return x - np.log(np.exp(x).sum(-1, keepdims=True))

    def score(self, frame_path, prompt, labels):
        """Mean log-prob per token of each label as the answer's continuation."""
        ids, logits, past = self._prefill(frame_path, prompt)
        last = self._logsoftmax(logits[-1])
        out = []
        for lab in labels:
            lid = self.tok.encode(" " + lab, add_special_tokens=False).ids
            lp = float(last[lid[0]])
            if len(lid) > 1:
                lg, _ = self._step(self._embed(lid[:-1]), past, len(ids), len(ids) + len(lid) - 1)
                ls = self._logsoftmax(lg)
                lp += sum(float(ls[j, lid[j + 1]]) for j in range(len(lid) - 1))
            out.append(lp / len(lid))
        return out

    def generate(self, frame_path, prompt, max_tokens=30):
        ids, logits, past = self._prefill(frame_path, prompt)
        out, n = [], len(ids)
        for _ in range(max_tokens):
            t = int(logits[-1].argmax())
            if t in self.eos:
                break
            out.append(t)
            logits, past = self._step(self._embed([t]), past, n, n + 1)
            n += 1
        return self.tok.decode(out).strip()


def load_model(flag=None):
    """(model, None) or (None, reason). A model that fails to load is reported, never raised."""
    d, parts = find_model(flag)
    if d is None:
        return None, parts
    try:
        return SmolVLM(d, parts), None
    except Exception as e:  # noqa: BLE001  a broken install must not stop the run
        return None, "model at %s failed to load: %s" % (d, str(e)[:400])  # 261114: 160 cut the node name off the one error that mattered


# ---------------------------------------------------------------- one frame, one slot at a time
def ans(value, slot, rule, note=None, evidence=None, margin=None):
    """One answered slot. 261114 (Diego Mac probe 2): a value this lane produced is NEVER trust "person".

    The lane had been stamping trust "person" on nine slots (mood, tone, believability, colour_design and the four
    production-value slots) because the schema reserves them for Diego. The schema is right about who owns them and
    the stamp was a lie about who said them: the row claimed Diego's judgement for a model's guess. A model answer is
    "approximate" with origin R, always. A person slot additionally carries awaits_person, so the sheet can ask Diego
    to confirm or overwrite it, and nothing downstream mistakes the suggestion for his call.
    """
    a = {"value": value, "trust": "approximate", "rule": rule, "note": note, "evidence": evidence, "margin": margin}
    if slot in PERSON:
        a["awaits_person"] = True  # Diego's slot: this is a suggestion until he says so
    return a


def check_slots(slots):
    for s in slots:
        if s in FORBIDDEN:
            raise ValueError(FORBIDDEN[s])
        if s not in READ_SLOTS:
            raise ValueError("%s is not a read slot; read slots: %s" % (s, ", ".join(READ_SLOTS)))


INSIDE_Q = ("Is this frame indoors or outdoors? Answer with one word: indoors or outdoors.", ("indoors", "outdoors"))


def inside_gate(model, frame_path, margin_min=MARGIN):
    """(value, margin) for indoors / outdoors, or (None, margin) when the two are within the margin.

    261114 (Diego Mac probe 2): the lane read "blue hour" at margin 0.834 on a man at a lit computer monitor. The
    label list does hold "interior"; the measured facts riding with the question (p50 0.3 IRE, 6970 K) read as dark
    and cool, which is also what dusk outdoors looks like, and the model took the outdoor reading. Light cannot tell
    the time of day inside, so the question is asked in the right order instead: indoors first, and indoors ends it.
    """
    q, labels = INSIDE_Q
    sc = model.score(frame_path, q, list(labels))
    order = sorted(range(len(labels)), key=lambda i: -sc[i])
    mg = sc[order[0]] - sc[order[1]]
    return (labels[order[0]] if mg >= margin_min else None), round(mg, 3)


def read_slot(model, frame_path, slot, f, margin_min=MARGIN):
    if slot == "time_of_day" and hasattr(model, "score"):
        where, mg = inside_gate(model, frame_path, margin_min)
        rule = "gate: indoors / outdoors asked first; indoors ends the question (light cannot date an interior)"
        if where == "indoors":
            return ans("interior", slot, rule, None, "read as indoors (margin %.3f); no daylight to read" % mg, mg)
        if where is None:
            return ans(U, slot, rule, "low margin on indoors vs outdoors: %.3f" % mg, None, mg)
    labels = labels_for(slot)
    path = "logits" if (labels and hasattr(model, "score")) else "text"
    prompt = prompt_for(slot, f, labels if labels and len(labels) <= 20 else None)
    if path == "logits":
        sc = model.score(frame_path, prompt, labels)
        order = sorted(range(len(labels)), key=lambda i: -sc[i])
        top, mg = labels[order[0]], (sc[order[0]] - sc[order[1]]) if len(order) > 1 else None
        rule = "logits: mean log-prob per token over %d allowed labels; margin >= %.2f" % (len(labels), margin_min)
        if mg is not None and mg < margin_min:
            return ans(U, slot, rule, "low margin: %s %.3f over %s" % (top, mg, labels[order[1]]), None, round(mg, 3))
        value, mg = top, (round(mg, 3) if mg is not None else None)
    else:
        raw = model.generate(frame_path, prompt, 30)
        mg = None
        if labels:
            value = match_label(raw, labels)
            rule = "text: first allowed label in the generated output"
            if value is None:
                return ans(U, slot, rule, "no allowed label in output: %r" % clip_evidence(raw))
        else:
            n = bound_words(slot) or 4
            w = re.sub(r"[^\w' -]", " ", raw).split()
            value = " ".join(w[:n]) if w and not re.search(r"\d", " ".join(w[:n])) else None
            rule = "text: bounded free text, <= %d words, no digits, not about the medium"
            rule = rule % n
            # 261116: a free-text slot was taking the model's sentence opener, so subject_mods came back as
            # "The image is" and a manner as "This is a movie". A descriptor of what is IN the frame never
            # names the frame, and never opens like a caption. Both are refused, and the slot stays unresolved.
            if value and (MEDIUM.search(value) or OPENER.match(value)):
                return ans(U, slot, rule, "describes the medium or opens like a caption: %r" % clip_evidence(raw))
            if not value:
                return ans(U, slot, rule, "empty or numeric output: %r" % clip_evidence(raw))
    ev = clip_evidence(model.generate(frame_path, "%s Field %s is '%s'. In one short line, what in the frame shows it?"
                                      % (prompt_for(slot, f), slot, value), 30)) if hasattr(model, "generate") else ""
    bad = contradiction("%s %s" % (value, ev), f)
    if bad:
        return ans(U, slot, rule, "rejected %r (evidence %r): %s" % (value, ev, bad), None, mg)
    return ans(value, slot, rule, None, ev or None, mg)


def read(frame_path, measured, slots, model=None, reason=None, margin_min=MARGIN):
    """{field: {value, trust, rule, note, evidence, margin}} for each requested slot. model=None + reason -> all unresolved."""
    slots = list(slots or READ_SLOTS)
    check_slots(slots)
    f = facts(measured or {})
    if model is None:
        why = reason or "model not installed"
        return {s: ans(U, s, "none: no vision model ran", why) for s in slots}
    out = {}
    for s in slots:
        try:
            out[s] = read_slot(model, str(frame_path), s, f, margin_min)
        except Exception as e:  # noqa: BLE001  one bad slot never sinks the frame
            out[s] = ans(U, s, "error", "%s: %s" % (type(e).__name__, str(e)[:160]))
    return out


# ---------------------------------------------------------------- hosted lane (paid): refuses without Diego's recorded yes
def hosted_gate(yes_paid):
    if not yes_paid:
        return "hosted lane is paid: refused without --yes-paid (Diego's yes must be recorded)"
    kf = ""
    try:
        sys.path.insert(0, str(PLUGIN / "skills" / "261113-watch-video-v10" / "scripts"))
        import p0_env
        kf = p0_env.keys_file()
    except Exception:  # noqa: BLE001
        kf = os.environ.get("WORKFORCE_KEYS", "")
    if not kf or not os.path.exists(kf):
        return "hosted lane: no keys file (p0_env.keys_file() / WORKFORCE_KEYS); key never printed"
    return None


def load_measured(path, frame):
    if not path:
        return {}, None
    if not Path(path).exists():  # 261114: a failed measure step upstream should name itself, not traceback in pathlib
        raise SystemExit("read_lane: --measured file not found: %s (did shot_measure run and exit 0?)" % path)
    rows = [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]
    fr = os.path.abspath(frame)
    for r in rows:
        if r.get("source") and os.path.abspath(r["source"]) == fr:
            return r, r.get("segment_id")
    for r in rows:
        if r.get("source") and Path(r["source"]).name == Path(frame).name:
            return r, r.get("segment_id")
    return (rows[0], rows[0].get("segment_id")) if len(rows) == 1 else ({}, None)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("frame")
    ap.add_argument("--measured", help="shot_measure.py jsonl; the row whose source is this frame (or the only row)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", help="SmolVLM ONNX dir (else plugin models/smolvlm, ~/.scan-and-index/models/smolvlm, $WATCH_MODELS_DIR/smolvlm)")
    ap.add_argument("--slots", nargs="+", help="read slots (default: all R fields); var_casting is refused")
    ap.add_argument("--lane", choices=["local", "hosted"], default="local")
    ap.add_argument("--yes-paid", action="store_true", help="Diego's recorded yes for a paid lane")
    ap.add_argument("--margin", type=float, default=MARGIN)
    a = ap.parse_args(argv)
    try:
        check_slots(a.slots or READ_SLOTS)
    except ValueError as e:
        print("read_lane: %s" % e, file=sys.stderr)
        return 2
    measured, seg = load_measured(a.measured, a.frame)
    if a.lane == "hosted":
        why = hosted_gate(a.yes_paid)
        if why:
            print("read_lane: %s" % why, file=sys.stderr)
            return 2
        model, reason = None, "hosted lane not wired yet (gate passed; no call made)"
    else:
        model, reason = load_model(a.model)
    fields = read(a.frame, measured, a.slots, model, reason, a.margin)
    lane = "none" if model is None else getattr(model, "path", "logits")
    row = {"segment_id": seg, "source": a.frame, "lane": a.lane, "path": lane, "reason": reason, "fields": fields}
    if model is not None and getattr(model, "loaded", None):
        row["files"] = model.loaded  # which quantization each part actually loaded on this machine
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w") as fo:
        fo.write(json.dumps(row) + "\n")
    n = sum(1 for v in fields.values() if v["value"] != U)
    files = (" [" + ", ".join(sorted(row["files"].values())) + "]") if row.get("files") else ""
    print("read_lane: %d/%d slot(s) read, path %s%s%s -> %s" % (n, len(fields), lane, (" (" + reason + ")") if reason else "", files, a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
