#!/usr/bin/env python3
"""macros.py - rules layer (contract rules-and-objects.md section 3).

  python3 macros.py --state WATCH_STATE.json [--rules rules.json] [--video V] [--force]

For every shot with a `machine` dict, derives M1-M8 and writes shot["macros"].
Each macro is {"value", "status": resolved|unresolved|not_run, "why"}.
A macro never guesses. Thresholds live in rules.json only.
EVERY shot gets all of M1-M8 (no silent skips): a shot with no usable machine data (missing, PENDING
placeholder) gets the PENDING placeholder for each macro. needs_scan = no data yet, unresolved = data
present but the rule can't settle it, not_run = optional engine not installed. Macros written by
`objects.py fill` (filled_by) are kept unless --force.
Python 3.9 compatible; cv2 / webrtcvad / CLIP are imported lazily.
"""
import json, os, re, subprocess, sys, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from annotate_frames import pending_placeholder, is_pending
DEFAULT_RULES = os.path.join(HERE, "..", "rules.json")
MACROS = ["M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8"]

# Fallback defaults: a rules.json missing a whole section (or a key) must still run.
DEFAULTS = {
    "M1_shot_size": {"face_cu": 0.30, "face_mcu": 0.12, "person_ms": 0.40},
    "M2_motion": {"static_max_diff": 6.0, "min_shot_s": 0.6, "sample_offset_s": 0.25, "edge_pad_s": 0.05},
    "M3_speech": {"speech_min_pct": 40, "frame_ms": 30, "aggressiveness": 2,
                  "max_audio_s": 60, "sample_window_s": 10, "sample_points": [0.15, 0.40, 0.60, 0.85]},
    "M4_role": {"sot_min_s": 20, "broll_max_s": 12},
    "M5_light": {"night_max_brightness": 60, "day_min_brightness": 110},
    "M6_text": {"lower_third_min_y": 0.62, "min_word_len": 3, "min_word_conf": 80, "min_words": 1,
                "slate_words": ["camera", "cam", "director", "dir", "prod", "date", "dp", "sound", "fps", "roll"],
                "slate_strong_words": ["scene", "take", "slate"],
                "slate_min_weak": 2, "card_min_chars": 12,
                "lower_frac": 0.70, "above_y": 0.45, "card_max_lower_frac": 0.5},
    "M7_tags": {"words": ["drone", "aerial", "beach", "crowd", "interview", "product", "car",
                          "sunset", "night", "food", "city street", "forest"], "min_score": 0.25},
    "M8_dupes": {"dup_max_hamming": 4, "near_max_hamming": 10, "min_bits": 2, "max_bits": 62},
    "flat": {"min_busyness": 15},
}
TC_RE = re.compile(r"\d\d:\d\d:\d\d[:;.]\d\d")   # frame separator may be : ; or .


def section(rules, name):
    d = dict(DEFAULTS.get(name, {}))
    got = (rules or {}).get(name)
    if isinstance(got, dict):
        d.update(got)
    return d


def resolve_path(p, base):
    """PATH RULE: a relative path in a state is resolved against the STATE FILE's directory."""
    if not p:
        return None
    return p if os.path.isabs(p) else os.path.join(base, p)


def mk(value, status, why, **extra):
    d = {"value": value, "status": status, "why": why}
    d.update(extra)
    return d


def parse_tc(v):
    """seconds float from number, 'SS.s', 'MM:SS(.s)' or 'HH:MM:SS(.s)'; None if unusable."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        parts = [float(p) for p in str(v).strip().split(":")]
    except ValueError:
        return None
    t = 0.0
    for p in parts:
        t = t * 60 + p
    return t


def run_ffmpeg(args, capture=True):
    return subprocess.run(["ffmpeg", "-nostdin", "-v", "error"] + args,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def ffprobe_duration(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", path],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return float(r.stdout.decode().strip())
    except Exception:
        return None


# ---------------------------------------------------------------- per-shot rules

def m1(machine, r):
    if "faces" not in machine and "detections" not in machine:
        return mk("unresolved", "unresolved", "no face/person boxes (v7 witness only); needs a look")
    faces = machine.get("faces") or []
    persons = [d for d in (machine.get("detections") or []) if d.get("label") == "person"]
    fh = max([f.get("h", 0) for f in faces] or [0])
    ph = max([p.get("h", 0) for p in persons] or [0])
    if fh >= r["face_cu"]:
        return mk("CU", "resolved", "largest face %d%% of height" % round(fh * 100))
    if fh >= r["face_mcu"]:
        return mk("MCU", "resolved", "largest face %d%% of height" % round(fh * 100))
    if ph >= r["person_ms"]:
        return mk("MS", "resolved", "largest person %d%% of height" % round(ph * 100))
    if faces or persons:
        return mk("WS", "resolved", "person present, largest box %d%% of height" % round(max(fh, ph) * 100))
    return mk("unresolved", "unresolved", "no person; could be INSERT or WS")


def is_flat(machine, flat):
    b = (machine.get("stats") or {}).get("busyness")
    return b is not None and b < flat["min_busyness"]


def m5(machine, r, flat):
    st = machine.get("stats") or {}
    if is_flat(machine, flat):
        return mk("unresolved", "unresolved", "flat frame (busyness %.0f < %s)" % (st["busyness"], flat["min_busyness"]))
    b = st.get("brightness")
    if b is None:
        return mk("unresolved", "unresolved", "no brightness stat")
    warm = st.get("warmth")
    note = "" if warm is None else ", warmth %+.0f" % warm
    if b <= r["night_max_brightness"]:
        return mk("night", "resolved", "brightness %.0f <= %s%s" % (b, r["night_max_brightness"], note))
    if b >= r["day_min_brightness"]:
        return mk("day", "resolved", "brightness %.0f >= %s%s" % (b, r["day_min_brightness"], note))
    return mk("unresolved", "unresolved", "brightness %.0f between night and day limits%s" % (b, note))


def m6(machine, r):
    if "ocr_boxes" not in machine:
        if machine.get("ocr"):
            return mk("unresolved", "unresolved", "v7 OCR lines only, no word boxes")
        if "ocr" in machine:
            return mk("none", "resolved", "OCR found no text")
        return mk("unresolved", "unresolved", "no OCR data")
    boxes = machine["ocr_boxes"] or []
    if not boxes:
        return mk("none", "resolved", "no text boxes")
    # burned-in timecode is its own value and takes no part in the other rules
    tcs = [b for b in boxes if TC_RE.search(str(b.get("text", "")))]
    boxes = [b for b in boxes if b not in tcs]
    if tcs and not boxes:
        return mk("timecode", "resolved", "burned-in timecode '%s'" % tcs[0].get("text", ""))
    # OCR noise filter (261106 Cowork run: sofas and lodge walls read as "te Py | Be in," and became
    # LOWER THIRD). A word counts only with min_word_len letters/digits and conf >= min_word_conf;
    # fewer than min_words such words is no text.
    d6 = DEFAULTS["M6_text"]
    wl, wc, wn = (r.get("min_word_len", d6["min_word_len"]), r.get("min_word_conf", d6["min_word_conf"]),
                  r.get("min_words", d6["min_words"]))
    strong = set(w.lower() for w in r.get("slate_strong_words", d6.get("slate_strong_words", [])))

    def real_words(b):
        if b.get("conf") is not None and float(b.get("conf")) < wc:
            return 0
        return sum(1 for t in str(b.get("text", "")).split()
                   if sum(ch.isalnum() for ch in t) >= wl or re.sub(r"[^\w]", "", t.lower()) in strong)
    real = [b for b in boxes if real_words(b)]
    if sum(real_words(b) for b in real) < wn:
        if tcs:
            return mk("timecode", "resolved", "burned-in timecode '%s' (other OCR text below the word filter)" % tcs[0].get("text", ""))
        return mk("none", "resolved", "%d OCR box(es), no real word (needs %d+ letters or digits at confidence %s+): noise"
                  % (len(boxes), wl, wc))
    # Real words decide WHETHER there is text; the rules below still see every box (a slate's "12" counts).
    res = m6_text(boxes, r)
    if tcs:
        res["why"] += " (burned-in timecode excluded)"
        res["also"] = "timecode"
    return res


def m6_text(boxes, r):
    # A slate needs one strong word (scene/take/slate) or slate_min_weak distinct weak words.
    strong = set(w.lower() for w in r["slate_strong_words"])
    weak = set(w.lower() for w in r["slate_words"]) - strong
    toks = [re.sub(r"[^\w]", "", str(b.get("text", "")).lower()) for b in boxes]
    hit = [t for t in toks if t in strong]
    if hit:
        return mk("slate", "resolved", "slate word '%s'" % hit[0])
    weak_hits = sorted(set(t for t in toks if t in weak))
    if len(weak_hits) >= int(r["slate_min_weak"]):
        return mk("slate", "resolved", "slate words %s" % ",".join(weak_hits))
    lt = r["lower_third_min_y"]
    sized = [(len("".join(str(b.get("text", "")).split())), b.get("y", 0) + b.get("h", 0) / 2.0) for b in boxes]
    chars = sum(n for n, _ in sized)
    if chars == 0:
        return mk("unresolved", "unresolved", "text boxes without characters")
    low = sum(n for n, cy in sized if cy >= lt)
    frac = float(low) / chars
    d6 = DEFAULTS["M6_text"]
    above_y = r.get("above_y", d6["above_y"])
    above = [cy for _, cy in sized if cy < above_y]
    if frac >= r.get("lower_frac", d6["lower_frac"]) and not above:
        return mk("lower_third", "resolved", "%d%% of %d chars at or below y=%s" % (round(frac * 100), chars, lt))
    if frac < r.get("card_max_lower_frac", d6["card_max_lower_frac"]) and chars >= r["card_min_chars"]:
        return mk("card", "resolved", "%d chars, only %d%% in the lower band" % (chars, round(frac * 100)))
    return mk("unresolved", "unresolved",
              "%d chars, %d%% in lower band, %d box(es) above y=%s: fits no slate/lower-third/card rule"
              % (chars, round(frac * 100), len(above), above_y))


_CLIP = {}


def m7(frame, r):
    if "model" not in _CLIP:
        _CLIP["model"] = None
        _CLIP["why"] = "CLIP not installed"
        try:
            import open_clip, torch
            model, _, pre = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai")
            _CLIP["model"] = ("open_clip", model.eval(), pre, open_clip.tokenize, torch)
        except Exception as e1:
            try:
                import clip, torch
                model, pre = clip.load("ViT-B/32", device="cpu")
                _CLIP["model"] = ("clip", model.eval(), pre, clip.tokenize, torch)
            except Exception as e2:
                if "open_clip" in sys.modules or "clip" in sys.modules:
                    _CLIP["why"] = "CLIP installed but model failed to load: %s" % str(e2)[:80]
    if _CLIP["model"] is None:
        return mk([], "not_run", _CLIP["why"])
    fp = frame
    if not fp or not os.path.exists(fp):
        return mk([], "unresolved", "no frame on disk")
    try:
        from PIL import Image
        _, model, pre, tok, torch = _CLIP["model"]
        words = r["words"]
        with torch.no_grad():
            im = pre(Image.open(fp).convert("RGB")).unsqueeze(0)
            ti = tok(["a photo of " + w for w in words])
            fi = model.encode_image(im); ft = model.encode_text(ti)
            fi = fi / fi.norm(dim=-1, keepdim=True); ft = ft / ft.norm(dim=-1, keepdim=True)
            p = (100.0 * fi @ ft.T).softmax(dim=-1)[0].tolist()
    except Exception as e:
        return mk([], "not_run", "CLIP failed: %s" % str(e)[:80])
    hits = sorted([{"word": w, "score": round(s, 3)} for w, s in zip(words, p) if s >= r["min_score"]],
                  key=lambda d: -d["score"])
    return mk(hits, "resolved", "%d of %d words >= %s" % (len(hits), len(words), r["min_score"]))


def hamming(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def popcount(h):
    return bin(int(h, 16)).count("1")


def m8(entries, idx, r, flat):
    """entries: list of (scene, shot, clipid); dhash distance to earlier shots of a different clip.
    A flat frame (low busyness, or a hash that is nearly all 0s or all 1s) matches every other
    flat frame, so it is never judged: unresolved."""
    sc, sh, cid = entries[idx]
    mc = sh.get("machine") or {}
    h = mc.get("dhash")
    if not h:
        return mk("unresolved", "unresolved", "no dhash")

    def flat_hash(hh):
        try:
            n = popcount(hh)
        except ValueError:
            return False
        return n < r["min_bits"] or n > r["max_bits"]

    if is_flat(mc, flat) or flat_hash(h):
        return mk("unresolved", "unresolved", "flat frame (busyness/hash carries no picture detail)")
    best = None
    for j in range(idx):
        sc2, sh2, cid2 = entries[j]
        mc2 = sh2.get("machine") or {}
        h2 = mc2.get("dhash")
        if cid2 == cid or not h2:
            continue
        if is_flat(mc2, flat) or flat_hash(h2):
            continue   # a flat frame cannot be a dup of a real picture; it is judged on its own row
        try:
            d = hamming(h, h2)
        except ValueError:
            continue
        if best is None or d < best[0]:
            best = (d, sh2.get("id"))
    if best is not None and best[0] <= r["near_max_hamming"]:
        d, sid = best
        if d <= r["dup_max_hamming"]:
            return mk("dup", "resolved", "dHash distance %d to %s" % (d, sid), of=sid)
        return mk("near", "resolved", "dHash distance %d to %s" % (d, sid), of=sid)
    if best is None:
        return mk("none", "resolved", "no earlier shot from another clip to compare")
    return mk("none", "resolved", "nearest earlier shot %s at distance %d" % (best[1], best[0]))


# ---------------------------------------------------------------- M2 motion

def thumb(path):
    import cv2
    im = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if im is None:
        return None
    return cv2.resize(im, (64, 36), interpolation=cv2.INTER_AREA)


def mean_diff(pa, pb):
    import numpy as np
    a, b = thumb(pa), thumb(pb)
    if a is None or b is None:
        return None
    return float(np.mean(np.abs(a.astype("float32") - b.astype("float32"))))


def pull_frame(src, t, out):
    r = run_ffmpeg(["-ss", "%.3f" % max(t, 0.0), "-i", src, "-frames:v", "1", "-vf", "scale=320:-2", "-y", out])
    return os.path.exists(out) and os.path.getsize(out) > 0


def motion_verdict(diff, r, how):
    if diff is None:
        return mk("unresolved", "unresolved", "could not read both frames")
    v = "static" if diff <= r["static_max_diff"] else "moving"
    return mk(v, "resolved", "mean grey diff %.1f vs limit %s (%s)" % (diff, r["static_max_diff"], how))


def m2_for(group, source_of, video, r, tmp, base):
    """group: list of (scene, shot) of one clip (or one lone shot). Returns M2 dict.

    Pool mode: HEAD and MID of a clip are far apart in time, so their diff measures
    'the clip changes between keyframes' - still the useful signal for static vs moving.
    Lone shot of a cut: pull two frames INSIDE the shot, midpoint +/- sample_offset_s, both clamped to
    [tc_in+edge_pad_s, tc_out-edge_pad_s]; a shot shorter than min_shot_s is unresolved (all in rules.json).
    A pool frame pulled by the -ss 0 fallback (seek_fallback) makes the clip's M2 unresolved.
    """
    cand = [(resolve_path(sh.get("frame"), base), sh) for _, sh in group]
    cand = [(f, sh) for f, sh in cand if f and os.path.exists(f)]
    if len(cand) >= 2:
        fb = [sh.get("id") for _, sh in cand[:2] if sh.get("seek_fallback")]
        if fb:
            return mk("unresolved", "unresolved",
                      "frame(s) %s pulled by the -ss 0 fallback, not at the keyframe time; motion not judged" % ",".join(map(str, fb)))
        return motion_verdict(mean_diff(cand[0][0], cand[1][0]), r, "two frames of the clip")
    sc, sh = group[0]
    src = source_of(sc, sh) or video
    if not src or not os.path.exists(src):
        return mk("unresolved", "unresolved", "one frame only and no source video to pull a second")
    t_in, t_out = parse_tc(sh.get("tc_in")), parse_tc(sh.get("tc_out"))
    if t_in is None or t_out is None:
        return mk("unresolved", "unresolved", "shot has no tc_in/tc_out to sample inside")
    d2 = DEFAULTS["M2_motion"]
    min_s, off, pad = (r.get(k, d2[k]) for k in ("min_shot_s", "sample_offset_s", "edge_pad_s"))
    if t_out - t_in < min_s - 1e-6:
        return mk("unresolved", "unresolved", "shot shorter than %s s; too short to sample two frames" % min_s)
    lo, hi = t_in + pad, t_out - pad
    mid = (t_in + t_out) / 2.0
    t0, t1 = max(lo, mid - off), min(hi, mid + off)
    a, b = os.path.join(tmp, "%s_a.jpg" % sh.get("id")), os.path.join(tmp, "%s_b.jpg" % sh.get("id"))
    if not (pull_frame(src, t0, a) and pull_frame(src, t1, b)):
        return mk("unresolved", "unresolved", "ffmpeg could not pull frames")
    return motion_verdict(mean_diff(a, b), r, "pulled %.2fs apart inside the shot" % (t1 - t0))


# ---------------------------------------------------------------- M3 speech

NO_AUDIO_WHY = "audio not requested (default: ask before a speech check or transcript)"
_SAMPLED = {}  # (src, t0, t1) -> 'sampled …' note, empty when the whole slice was decoded
_PCT = {}   # (src, t0, t1) -> (pct or None, why); only the percentage is kept, never the audio


def decode_pcm(src, t0=None, t1=None):
    """Decode just the asked slice (or the whole file); the caller drops it after counting."""
    args = []
    if t0 is not None and t1 is not None and t1 > t0:
        args += ["-ss", "%.3f" % t0, "-t", "%.3f" % (t1 - t0)]
    res = run_ffmpeg(args + ["-i", src, "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"])
    if res.returncode != 0 or not res.stdout:
        err = res.stderr.decode(errors="ignore").strip().splitlines()
        return None, "no audio stream" if res.returncode != 0 or not err else "empty audio"
    return res.stdout, None


def speech_pct(src, r, t0=None, t1=None):
    """Return (pct or None, why-if-none)."""
    key = (src, t0, t1)
    if key in _PCT:
        return _PCT[key]
    try:
        import webrtcvad
    except ImportError:
        return None, "webrtcvad not installed"
    # A long clip is SAMPLED, never decoded whole: a 12 GB GoPro file took ~25 min whole (261106,
    # Elements). Windows of sample_window_s around sample_points, fast-seeked; only for a whole clip.
    windows, _SAMPLED[key] = [(t0, t1)], ""
    if t0 is None and t1 is None:
        dur = ffprobe_duration(src)
        cap = float(r.get("max_audio_s", 60))
        if dur and dur > cap:
            win = float(r.get("sample_window_s", 10))
            windows = [(max(0.0, dur * f - win / 2), min(dur, dur * f + win / 2))
                       for f in r.get("sample_points", [0.15, 0.40, 0.60, 0.85])]
            _SAMPLED[key] = "sampled %d x %gs of %.0fs" % (len(windows), win, dur)
    n = int(16000 * r["frame_ms"] / 1000) * 2
    vad = webrtcvad.Vad(int(r["aggressiveness"]))
    total = speech = 0
    err = None
    for w0, w1 in windows:
        pcm, err = decode_pcm(src, w0, w1)
        if pcm is None:
            continue
        for i in range(0, len(pcm) - n + 1, n):
            total += 1
            if vad.is_speech(pcm[i:i + n], 16000):
                speech += 1
        del pcm
    if not total:
        _PCT[key] = (None, err or "audio shorter than one VAD frame")
        return _PCT[key]
    _PCT[key] = (100.0 * speech / total, None)
    return _PCT[key]


def m3_for(src, r, t0=None, t1=None):
    if not src or not os.path.exists(src):
        return mk("not_run", "not_run", "no source media (shot path, scene path or --video)")
    pct, why = speech_pct(src, r, t0, t1)
    if pct is None:
        return mk("not_run", "not_run", why)
    v = "speech" if pct >= r["speech_min_pct"] else "no_speech"
    note = _SAMPLED.get((src, t0, t1)) or ""
    return mk(v, "resolved", "%.0f%% speech frames vs limit %s%%%s" % (pct, r["speech_min_pct"], (", " + note) if note else ""),
              pct=round(pct, 1))


# ---------------------------------------------------------------- M4 role

def m4_for(faces_n, m2s, m3, r, duration, scope="clip"):
    """faces_n: most faces seen on any frame of the unit (None = unknown). Unit = a pool clip, or
    ONE shot of a cut (single-video mode, scope='shot'); never the whole video at once."""
    if duration is None:
        return mk("unresolved", "unresolved", "%s duration unknown" % scope)
    speech = m3["value"] if m3 and m3["status"] == "resolved" else None
    motion = m2s["value"] if m2s and m2s["status"] == "resolved" else None
    nfaces = faces_n
    if duration >= r["sot_min_s"] and motion == "static" and speech == "speech" and nfaces is not None and 1 <= nfaces <= 2:
        return mk("sot", "resolved", "%.0fs, static, speech, %d face(s)" % (duration, nfaces))
    if duration <= r["broll_max_s"] and speech == "no_speech" and (motion == "moving" or nfaces == 0):
        return mk("broll", "resolved", "%.0fs, %s, no speech%s" % (duration, motion or "motion unknown",
                                                                  ", no faces" if nfaces == 0 else ""))
    return mk("unresolved", "unresolved",
              "%.0fs, motion=%s, speech=%s, faces=%s fit neither sot nor broll" % (duration, motion, speech, nfaces))


def faces_in(shots):
    n = None
    for sh in shots:
        mc = sh.get("machine") or {}
        if "faces" in mc:
            n = max(n or 0, len(mc["faces"] or []))
    return n


# ---------------------------------------------------------------- driver

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    def arg(name, default=None):
        return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default

    sp = arg("--state")
    if not sp:
        print(__doc__)
        return 2
    with open(arg("--rules", DEFAULT_RULES)) as fh:
        rules = json.load(fh)
    R = dict((k, section(rules, k)) for k in DEFAULTS)
    video = arg("--video")
    force = "--force" in argv
    # Audio is opt-in (Diego, 261106): --no-audio writes M3 not_run for every shot; --audio-only F1|F2
    # runs M3 only for those clip file names. A later run without --no-audio upgrades the not_run.
    no_audio = "--no-audio" in argv
    audio_only = set(x for x in (arg("--audio-only") or "").split("|") if x)
    base = os.path.dirname(os.path.abspath(sp))
    with open(sp) as fh:
        state = json.load(fh)
    video = video or resolve_path((state.get("video") or {}).get("path"), base)

    _PCT.clear()
    entries = []  # (scene, shot, clipid) for every shot with REAL machine data, in order
    no_data = []  # shots with no usable machine data: they get the PENDING placeholder for every macro
    for sc in state.get("scenes", []):
        for sh in sc.get("shots", []):
            mc = sh.get("machine")
            if isinstance(mc, dict) and not is_pending(mc):
                cid = sh.get("file") or sc.get("file") or sc.get("id") or "scene"
                entries.append((sc, sh, cid))
            else:
                no_data.append(sh)

    def audio_wanted(sh, sc=None):
        if no_audio:
            return False
        if audio_only:
            return (sh.get("file") or (sc or {}).get("file")) in audio_only
        return True

    def stale_audio(sh, m, sc=None):
        d = (sh.get("macros") or {}).get(m) if isinstance(sh.get("macros"), dict) else None
        return (m == "M3" and isinstance(d, dict) and d.get("why") == NO_AUDIO_WHY and audio_wanted(sh, sc))

    def has_all(sh, allow_pending):
        mac = sh.get("macros")
        return (isinstance(mac, dict) and all(isinstance(mac.get(m), dict) and (allow_pending or mac[m].get("status") != "needs_scan")
                                              and not stale_audio(sh, m) for m in MACROS))

    def keep(sh, m):
        """Without --force an existing macro that is not a placeholder stays (this includes `objects.py fill`
        judgments); only missing keys and needs_scan placeholders are (re)computed."""
        d = (sh.get("macros") or {}).get(m) if isinstance(sh.get("macros"), dict) else None
        return d if (isinstance(d, dict) and d.get("status") != "needs_scan" and not force
                     and not stale_audio(sh, m)) else None

    todo = [i for i, (sc, sh, c) in enumerate(entries) if force or not has_all(sh, False)]
    new = dict((i, {}) for i in todo)
    tmp = tempfile.mkdtemp(prefix="macros_")

    def source_of(sc, sh):
        # `src` is the absolute source pool_frames stores on each shot (Gemini review 261107).
        for p in (sh.get("path"), sc.get("path"), sh.get("src")):
            p = resolve_path(p, base)
            if p and os.path.exists(p):
                return p
        return None

    def has_file(sc, sh):
        return bool(sh.get("file") or sc.get("file"))

    try:
        # per-shot rules
        for i in todo:
            sc, sh, cid = entries[i]
            mc = sh["machine"]
            new[i]["M1"] = m1(mc, R["M1_shot_size"])
            new[i]["M5"] = m5(mc, R["M5_light"], R["flat"])
            new[i]["M6"] = m6(mc, R["M6_text"])
            new[i]["M7"] = m7(resolve_path(sh.get("frame"), base), R["M7_tags"])
            new[i]["M8"] = m8(entries, i, R["M8_dupes"], R["flat"])

        # clip groups: shots sharing `file` (shot or scene); others stand alone
        groups = {}
        for i, (sc, sh, c) in enumerate(entries):
            key = sh.get("file") or sc.get("file")
            groups.setdefault(key if key else ("__solo__", i), []).append(i)

        for key, idxs in groups.items():
            if not any(i in new for i in idxs):
                continue
            pool = not isinstance(key, tuple)
            # M2 per clip (pool) or per shot (no file)
            if pool:
                m2d = m2_for([entries[i][:2] for i in idxs], source_of, video, R["M2_motion"], tmp, base)
                for i in idxs:
                    if i in new:
                        new[i]["M2"] = m2d
            else:
                i = idxs[0]
                new[i]["M2"] = m2_for([entries[i][:2]], source_of, video, R["M2_motion"], tmp, base)
            # M3: whole source for a pool clip; this shot's own slice of the video for a cut
            for i in idxs:
                if i not in new:
                    continue
                sc, sh, c = entries[i]
                src = source_of(sc, sh)
                if not audio_wanted(sh, sc):
                    new[i]["M3"] = mk("not_run", "not_run", NO_AUDIO_WHY)
                elif src and pool:
                    new[i]["M3"] = m3_for(src, R["M3_speech"])
                elif pool:
                    new[i]["M3"] = m3_for(None, R["M3_speech"])
                else:
                    t0, t1 = parse_tc(sh.get("tc_in")), parse_tc(sh.get("tc_out"))
                    if src:
                        new[i]["M3"] = m3_for(src, R["M3_speech"], t0, t1)
                    elif t0 is None or t1 is None or t1 <= t0:
                        new[i]["M3"] = mk("not_run", "not_run", "no shot timecodes to slice the video")
                    else:
                        new[i]["M3"] = m3_for(video, R["M3_speech"], t0, t1)

        def role_motion(ms):
            ms = [m for m in ms if m and m["status"] == "resolved"]
            if any(m["value"] == "moving" for m in ms):
                return mk("moving", "resolved", "")
            if ms and all(m["value"] == "static" for m in ms):
                return mk("static", "resolved", "")
            return None

        def old_or_new(i, k):
            # A value that stays (keep: a fill judgment or an earlier result) is what the shot will
            # carry, so the role must be judged on it, not on a fresh recompute that gets discarded
            # (Gemini review 261107: a hand-filled M2/M3 was ignored by M4).
            kept = keep(entries[i][1], k)
            if kept is not None:
                return kept
            return new[i].get(k) if i in new else (entries[i][1].get("macros") or {}).get(k)

        # M4, single-video mode: shots with no `file` are a CUT, not a pool. Judge each shot on its
        # own slice (M3), M2 and faces; never stamp one role over the whole video.
        for i in todo:
            sc, sh, c = entries[i]
            if has_file(sc, sh):
                continue
            a, b = parse_tc(sh.get("tc_in")), parse_tc(sh.get("tc_out"))
            dur = (b - a) if a is not None and b is not None and b > a else None
            new[i]["M4"] = m4_for(faces_in([sh]), role_motion([old_or_new(i, "M2")]), old_or_new(i, "M3"),
                                  R["M4_role"], dur, "shot")

        # M4, pool: a clip = shots sharing file
        clips = {}
        for i, (sc, sh, c) in enumerate(entries):
            if has_file(sc, sh):
                clips.setdefault(sh.get("file") or sc.get("file"), []).append(i)
        for key, idxs in clips.items():
            if not any(i in new for i in idxs):
                continue
            clip = [entries[i][:2] for i in idxs]
            m2c = role_motion([old_or_new(i, "M2") for i in idxs])
            sc0, sh0 = clip[0]
            src = source_of(sc0, sh0)
            # The role uses the clip's own M3 as computed above (or not_run when audio was not asked for).
            # It never runs a second speech check (261106: that silently decoded every GoPro file whole).
            m3c = old_or_new(idxs[0], "M3")
            dur = None
            a, b = parse_tc(sc0.get("tc_in")), parse_tc(sc0.get("tc_out"))
            if a is not None and b is not None and b > a:
                dur = b - a
            if dur is None and src:
                dur = ffprobe_duration(src)
            if dur is None:
                ends = [parse_tc(s.get("tc_out")) for _, s in clip]
                starts = [parse_tc(s.get("tc_in")) for _, s in clip]
                if all(x is not None for x in ends + starts) and ends:
                    dur = max(ends) - min(starts)
            m4d = m4_for(faces_in([s for _, s in clip]), m2c, m3c, R["M4_role"], dur)
            for i in idxs:
                if i in new:
                    new[i]["M4"] = m4d

        counts = dict((m, {"resolved": 0, "unresolved": 0, "not_run": 0, "needs_scan": 0}) for m in MACROS)
        for i in todo:
            sc, sh, c = entries[i]
            sh["macros"] = dict((m, keep(sh, m) or new[i][m]) for m in MACROS if m in new[i])
            for m, d in sh["macros"].items():
                counts[m][d["status"]] += 1
        n_pending = 0
        for sh in no_data:
            if not force and has_all(sh, True): continue
            sh["macros"] = dict((m, keep(sh, m) or pending_placeholder("no witness data")) for m in MACROS)
            n_pending += 1
            for m, d in sh["macros"].items():
                counts[m][d["status"]] += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    with open(sp, "w") as fh:
        json.dump(state, fh, indent=2)
    print(json.dumps({"shots_processed": len(todo) + n_pending, "pending_shots": n_pending, "rules_version": rules.get("version"),
                      "counts": counts, "state": sp}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
