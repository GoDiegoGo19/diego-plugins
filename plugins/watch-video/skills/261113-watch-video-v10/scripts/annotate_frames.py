#!/usr/bin/env python3
"""annotate_frames.py — MACHINE WITNESS (v8, sandbox-safe). Blind annotation pre-pass; idempotent.

Composed baby captioner: no HF, no GitHub releases, no keys required.
  objects  — MobileNet-SSD (23MB, repo-committed weights via raw.githubusercontent)
  faces    — OpenCV haar (bundled in the pip wheel, zero network)
  text     — tesseract (apt)
  stats    — brightness / busyness / warmth / saturation (pure OpenCV)
v8 adds (contract: references/rules-and-objects.md §2), all boxes normalised 0-1, x,y = top-left:
  detections [{label,score,x,y,w,h}] · faces [{x,y,w,h}] · ocr_boxes [{text,conf,x,y,w,h}]
  (tesseract TSV, word level, conf>=60) · dhash (64-bit, 16 hex) · size [w,h]. v7 keys unchanged.
Composes a template caption per frame: "2 people, 1 face visible, indoors bright,
1 small handled object, no readable text." Structural witness — verifies counts,
objects, text; it flags, it doesn't narrate. Naming stays with the primary watcher.

HAIKU LANE (dormant until keyed): if ANTHROPIC_API_KEY is set in the env, frames
are also captioned by claude-haiku via api.anthropic.com (allowlisted here) and
machine.caption upgrades from template to neural. Same JSON contract either way.

Usage:
  python3 annotate_frames.py --state WATCH_STATE.json            # annotate rep frame of every shot missing machine
  python3 annotate_frames.py --state WATCH_STATE.json --force    # re-annotate all
  (a shot whose frame is missing/unreadable gets machine = PENDING placeholder (needs_scan) and stays in missing_frames;
   --diff sets witness_flag when the rule fires and CLEARS it when it no longer does;
   add --rules rules.json to read witness.ssd_min_score [0.45] / witness.ocr_min_conf [60];
   relative frame paths resolve from the state file's dir; bad frames go to summary `missing_frames`)
  python3 annotate_frames.py --frames frames/ --out ann.json     # sidecar mode, no state
  python3 annotate_frames.py --state WATCH_STATE.json --diff     # set witness_flag on desc/caption disagreement
  python3 annotate_frames.py --ask "question" --frame f.jpg      # pointed question (requires ANTHROPIC_API_KEY)

Deps: pip install opencv-python-headless numpy · apt install tesseract-ocr
Model files auto-fetch once from raw.githubusercontent.com (allowed domain), cached beside this script.
"""
import sys, os, json, re, subprocess, base64, urllib.request

def arg(name, default=None):
    return sys.argv[sys.argv.index(name)+1] if name in sys.argv else default

HERE = os.path.dirname(os.path.abspath(__file__))
SSD_M = os.path.join(HERE, "mnssd.caffemodel"); SSD_P = os.path.join(HERE, "mnssd.prototxt")
SSD_URLS = [("https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master/mobilenet_iter_73000.caffemodel", SSD_M),
            ("https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master/deploy.prototxt", SSD_P)]
CLASSES = ["background","aeroplane","bicycle","bird","boat","bottle","bus","car","cat","chair","cow",
           "diningtable","dog","horse","motorbike","person","pottedplant","sheep","sofa","train","tvmonitor"]

STOP = set("a an the of in on at to with and or for by from is are was were be been this that these "
           "those it its his her their there here as into over under up down out off near beside "
           "behind toward camera frame shot close wide people person indoors outdoors bright dark".split())

def pending_placeholder(why, scan_by="any"):
    """The ONE marker for 'a step could not produce a value' (contract §0): never null, never a magic number."""
    return {"value": "PENDING", "status": "needs_scan", "why": why, "scan_by": scan_by}

def is_pending(v):
    return isinstance(v, dict) and v.get("status") == "needs_scan"

def content_words(text):
    return {w for w in re.findall(r"[a-z]+", (text or "").lower()) if w not in STOP and len(w) > 2}

SSD_MIN = 0.45   # witness.ssd_min_score in rules.json
OCR_MIN = 60.0   # witness.ocr_min_conf in rules.json

def load_thresholds(path):
    global SSD_MIN, OCR_MIN
    SSD_MIN, OCR_MIN = 0.45, 60.0
    if path is None:
        path = os.path.join(os.path.dirname(HERE), "rules.json")
    try:
        w = json.load(open(path)).get("witness") or {}
        SSD_MIN = float(w.get("ssd_min_score", SSD_MIN)); OCR_MIN = float(w.get("ocr_min_conf", OCR_MIN))
    except Exception:
        pass

def fetch_models():
    for url, path in SSD_URLS:
        if not os.path.exists(path):
            urllib.request.urlretrieve(url, path)

# ---------- structural witness ----------
_NET = {}
def witness(image_path):
    import cv2, numpy as np
    if not _NET:
        fetch_models()
        # OpenCV 5.x dropped Caffe import: degrade to no detections instead of crashing (use opencv 4.x for SSD)
        _NET["ssd"] = cv2.dnn.readNetFromCaffe(SSD_P, SSD_M) if hasattr(cv2.dnn, "readNetFromCaffe") else None
        _NET["haar"] = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError("unreadable image")
    h, w = img.shape[:2]

    if _NET["ssd"] is not None:
        blob = cv2.dnn.blobFromImage(cv2.resize(img, (300, 300)), 0.007843, (300, 300), 127.5)
        _NET["ssd"].setInput(blob)
        det = _NET["ssd"].forward()
    else:
        det = np.zeros((1, 1, 0, 7), dtype="float32")
    objects = []; detections = []
    for i in range(det.shape[2]):
        conf = float(det[0, 0, i, 2])
        if conf > SSD_MIN:
            cls = CLASSES[int(det[0, 0, i, 1])]
            objects.append({"class": cls, "conf": round(conf, 2)})
            x1, y1, x2, y2 = [min(max(float(v), 0.0), 1.0) for v in det[0, 0, i, 3:7]]
            detections.append({"label": cls, "score": round(conf, 3), "x": round(x1, 4), "y": round(y1, 4),
                               "w": round(max(x2 - x1, 0.0), 4), "h": round(max(y2 - y1, 0.0), 4)})

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    face_rects = _NET["haar"].detectMultiScale(gray, 1.1, 5)
    faces = len(face_rects)
    face_boxes = [{"x": round(float(x) / w, 4), "y": round(float(y) / h, 4),
                   "w": round(float(fw) / w, 4), "h": round(float(fh) / h, 4)} for x, y, fw, fh in face_rects]

    ocr = []; ocr_boxes = []
    try:
        tsv = subprocess.run(["tesseract", image_path, "stdout", "--psm", "11", "tsv"],
                             capture_output=True, text=True, timeout=30).stdout
        lines = {}  # (block, par, line) -> words, to rebuild the v7 line list from the same single call
        for row in tsv.splitlines()[1:]:
            c = row.split("\t")
            if len(c) < 12 or c[0] != "5":
                continue
            word = c[11].strip()
            try:
                conf = float(c[10])
            except ValueError:
                continue
            if not word:
                continue
            lines.setdefault((c[2], c[3], c[4]), []).append(word)
            if conf >= OCR_MIN:
                ocr_boxes.append({"text": word, "conf": round(conf, 1), "x": round(int(c[6]) / w, 4),
                                  "y": round(int(c[7]) / h, 4), "w": round(int(c[8]) / w, 4),
                                  "h": round(int(c[9]) / h, 4)})
        for words in lines.values():
            l = " ".join(words).strip()
            alnum = sum(ch.isalnum() for ch in l)
            if len(l) > 3 and alnum >= 4 and alnum / max(len(l), 1) > 0.6:
                ocr.append(l)
        ocr = ocr[:8]
    except Exception:
        pass

    bright = float(gray.mean())
    busy = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    warmth = float(img[:, :, 2].mean() - img[:, :, 0].mean())  # BGR: mean R - mean B
    sat = float(cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[:, :, 1].mean())
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits_ = (small[:, 1:] > small[:, :-1]).flatten()
    dhash = "%016x" % int("".join("1" if b else "0" for b in bits_), 2)

    persons = sum(1 for o in objects if o["class"] == "person")
    others = [o["class"] for o in objects if o["class"] != "person"]
    bits = [f"{persons} " + ("person" if persons == 1 else "people") + " detected",
            f"{faces} face{'s' if faces != 1 else ''} visible",
            "bright interior" if bright > 100 else "dark interior"]
    if others: bits.append("objects: " + ", ".join(sorted(set(others))))
    bits.append("readable text on screen: " + "; ".join(ocr[:3]) if ocr else "no readable text")
    caption = " · ".join(bits)

    return {"caption": caption, "regions": [o["class"] for o in objects], "ocr": ocr,
            "tags": sorted(content_words(caption) | {o["class"] for o in objects}),
            "counts": {"persons": persons, "faces": faces},
            "stats": {"brightness": round(bright, 1), "busyness": round(busy, 1),
                      "warmth": round(warmth, 1), "saturation": round(sat, 1)},
            "detections": detections, "faces": face_boxes, "ocr_boxes": ocr_boxes,
            "dhash": dhash, "size": [w, h],
            "witness": "structural"}

# ---------- Haiku lane (dormant until ANTHROPIC_API_KEY exists) ----------
def haiku(image_path, prompt="Describe this film frame in one plain sentence: who/what, doing what, where. No style commentary."):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    b64 = base64.b64encode(open(image_path, "rb").read()).decode()
    body = json.dumps({"model": "claude-haiku-4-5-20251001", "max_tokens": 200, "messages": [{"role": "user",
        "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": prompt}]}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body,
        headers={"Content-Type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"})
    out = json.load(urllib.request.urlopen(req))
    return " ".join(b.get("text", "") for b in out.get("content", [])).strip()

# ---------- disagree rule (count-based; SSD counts are a floor, not truth) ----------
NUMWORDS = {"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,
            "a":1,"an":1,"single":1,"pair":2,"couple":2,"both":2}
PERSON_NOUNS = r"(?:person|people|student|students|instructor|teacher|nurse|firefighter|mechanic|" \
               r"man|men|woman|women|worker|workers|crew|walker|walkers|adult|adults|figure|figures|" \
               r"actor|actors|patient)"

def implied_people(desc):
    """Parse a rough people-count from natural prose. Returns None when unsure — None never flags."""
    d = (desc or "").lower()
    if not re.search(PERSON_NOUNS, d): return None
    if re.search(r"\b(crew|group|crowd|walkers pass|people pass)\b", d): return None  # open-ended
    total = 0
    for m in re.finditer(r"\b(one|two|three|four|five|six|a|an|single|pair|couple|both|\d+)\s+(?:\w+\s+)?" + PERSON_NOUNS, d):
        w = m.group(1); total += int(w) if w.isdigit() else NUMWORDS.get(w, 1)
    if total == 0:
        total = len(re.findall(PERSON_NOUNS, d))
    return total if total > 0 else None

def diff_flag(desc, machine):
    """Flag when the machine sees MORE people than described (+1 tolerance — SSD is a floor),
    or when strong OCR text exists and the description names no graphic at all.
    Fewer-than-described never flags (occluding gear drops people). Role errors are invisible here
    by design — those belong to the Stage D blind re-look."""
    counts = (machine or {}).get("counts", {})
    mp = counts.get("persons")
    dp = implied_people(desc)
    if mp is not None and dp is not None and mp > dp + 1:
        return True
    ocr = [o for o in (machine or {}).get("ocr", []) if sum(c.isalnum() for c in o) >= 4]
    if ocr and not re.search(r"\b(cards?|tags?|type|titles?|graphics?|lower thirds?|stats?|text|bubbles?|lockups?|logos?)\b", (desc or "").lower()):
        return True
    return False

def annotate(fp):
    ann = witness(fp)
    neural = haiku(fp)
    if neural:
        ann["template_caption"] = ann["caption"]
        ann["caption"] = neural
        ann["witness"] = "haiku+structural"
    return ann

def main():
    if "--ask" in sys.argv:
        a = haiku(arg("--frame"), arg("--ask"))
        print(json.dumps({"answer": a} if a else {"error": "ANTHROPIC_API_KEY not set — ask lane requires a key"}))
        return

    load_thresholds(arg("--rules"))
    sp = arg("--state")
    if sp:
        sp = os.path.abspath(sp); sdir = os.path.dirname(sp)
        s = json.load(open(sp))
        annotated = flagged = 0; missing = []

        def save():
            tmp = sp + ".tmp"
            with open(tmp, "w") as fh: json.dump(s, fh, indent=2)
            os.replace(tmp, sp)
        def miss(sh, fp, reason):
            """Frame missing/unreadable: write the PENDING placeholder (never leave the shot blank)."""
            missing.append({"shot": sh.get("id"), "frame": fp, "reason": reason})
            if not (isinstance(sh.get("machine"), dict) and not is_pending(sh["machine"])):
                sh["machine"] = pending_placeholder("frame %s: %s" % ("missing" if not fp else "unusable", reason))

        for sc in s.get("scenes", []):
            for sh in sc.get("shots", []):
                real = isinstance(sh.get("machine"), dict) and sh["machine"] and not is_pending(sh["machine"])
                if "--diff" in sys.argv:
                    if real:
                        # a blank description has nothing to disagree with; flag is CLEARED when the rule no longer fires
                        if (sh.get("desc") or "").strip() and diff_flag(sh.get("desc", ""), sh["machine"]):
                            sh["witness_flag"] = True; flagged += 1
                        else:
                            sh.pop("witness_flag", None)
                    continue
                if real and "--force" not in sys.argv: continue
                fp = sh.get("frame")
                if not fp:
                    miss(sh, None, "no frame"); continue
                if not os.path.isabs(fp): fp = os.path.join(sdir, fp)  # relative -> state file's dir
                if not os.path.exists(fp):
                    miss(sh, fp, "not found"); continue
                if os.path.getsize(fp) == 0:
                    miss(sh, fp, "0-byte file"); continue
                try:
                    ann = annotate(fp)
                except Exception as e:
                    miss(sh, fp, "unreadable: %s" % (str(e) or type(e).__name__)); continue
                sh["machine"] = ann
                sh.setdefault("semantic_check", "not_run")  # Stage D re-look flips this; never silently passed
                annotated += 1
                if annotated % 25 == 0: save()
        save()
        print(json.dumps({"annotated": annotated, "flagged": flagged, "state": sp, "missing_frames": missing}))
        return

    d = arg("--frames")
    if d:
        out = {}; unreadable = []
        for f in sorted(os.listdir(d)):
            if f.lower().endswith((".jpg", ".jpeg", ".png")):
                fp = os.path.join(d, f)
                try:
                    if os.path.getsize(fp) == 0: raise ValueError("0-byte file")
                    out[f] = annotate(fp)
                except Exception as e:
                    why = "unreadable: %s" % (str(e) or type(e).__name__)
                    out[f] = pending_placeholder(why)
                    unreadable.append({"frame": f, "reason": why})
        op = arg("--out", "annotations.json")
        json.dump(out, open(op, "w"), indent=2)
        print(json.dumps({"annotated": len(out) - len(unreadable), "out": op, "unreadable": unreadable}))
        return

    print(__doc__)

if __name__ == "__main__":
    main()
