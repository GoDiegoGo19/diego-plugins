#!/usr/bin/env python3
"""ppf_organize.py: organize by content. Called by ppf_plan.py after the rule-based bins and
string-outs; returns plain ops (add_bin, add_sequence, place_clip), so revise and versioning
work unchanged and the writers need no change.

  ppf_organize.py objects.json [--notes notes.txt] [--top 5] [--engine auto|minilm|tfidf] [--date YYMMDD]
                               (prints the ops it would add; build runs it for you)

a. Scan data (objects from a P6 clips CSV; families + segments CSVs found beside it):
   per shoot day, <YYMMDD>_BROLL_<topic> per family (or top content tag) with >= 2 clips,
   placing that family's segments (in/out from the scan); <YYMMDD>_TAKES_<slug> when the
   segments (or clips) CSV shows the same spoken line more than once, every take back to back.
b. No scan data: MiniLM embeds each clip's words (folders, file name, sidecar .txt/.srt/.json
   transcript beside the file, kind); average-linkage grouping by cosine (threshold) -> topics,
   labelled by the most central clip's words. Same sequence shapes. Sidecar transcripts that
   repeat -> TAKES.
c. Notes -> selects: one note per line; MiniLM ranks clips per note; <YYMMDD>_SELECTS_<slug>
   holds the top N in note order; a markdown report lists notes, clips and scores.
Engine order: plugin models/minilm, then the scan plugin's copy, then TF-IDF. The engine line
is printed and stored in the plan. It sorts and suggests; the editor decides.
"""
import argparse, csv, difflib, glob, json, math, os, re, sys
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ppf_match
from ppf_common import load_json, conform_fps, conform_clips, seq_fps_of

THRESH = {"minilm": 0.80, "tfidf": 0.35}      # average-linkage cosine cut (tuned on TEST_DRIVE_v1, 261113)
STRUCT = {"dcim", "100media", "100_pana", "xdroot", "clip", "sub", "contents", "video", "audio", "proxy",
          "private", "avchd", "bdmv", "stream", "bpav", "clpr", "rdm", "rdc", "mp4", "mov", "mxf", "r3d",
          "braw", "shoot", "day", "media", "the", "a", "an", "of", "and", "to", "in", "on"}
TEXT_EXT = (".txt", ".srt", ".json", ".vtt")


def slug(s, n=32):
    s = re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")
    return (s[:n].rstrip("_")) or "topic"


def words_of(c):
    """Folder names (minus the day folder and camera-file plumbing), file stem, kind."""
    parts = c["rel_path"].replace("\\", "/").split("/")
    out = []
    for p in parts[:-1]:
        if re.match(r"^\d{6}(_|$)", p):
            continue
        out.append(p)
    out.append(os.path.splitext(parts[-1])[0])
    toks = [t for t in re.split(r"[^A-Za-z0-9]+", " ".join(out)) if t]
    toks = [t for t in toks if t.lower() not in STRUCT and not t.isdigit()]
    return toks + [c["kind"]]


def sidecar_text(c, limit=2000):
    """A .txt/.srt/.json/.vtt beside the media file with the same stem."""
    stem = os.path.splitext(c.get("path") or "")[0]
    for ext in TEXT_EXT:
        p = stem + ext
        if os.path.isfile(p):
            try:
                t = open(p, encoding="utf-8", errors="replace").read(limit * 4)
            except OSError:
                continue
            if ext == ".json":
                try:
                    j = json.loads(t)
                    t = j.get("text") if isinstance(j, dict) and isinstance(j.get("text"), str) else \
                        " ".join(s.get("text", "") for s in (j.get("segments") or []) if isinstance(s, dict)) if isinstance(j, dict) else ""
                except ValueError:
                    pass
            if ext in (".srt", ".vtt"):
                t = " ".join(l for l in t.splitlines() if l.strip() and not re.match(r"^(\d+|WEBVTT|[\d:,.\s\->]+)$", l.strip()))
            return re.sub(r"\s+", " ", t).strip()[:limit]
    return ""


# ---- engine: MiniLM vectors (numpy) or TF-IDF sparse dicts, one interface
class Engine:
    def __init__(self, want="auto", model_dir=None):
        self.name, self.model_dir, self.why = "tfidf", None, ""
        md = ppf_match.find_model(model_dir)
        if want in ("auto", "minilm") and md:
            try:
                self.emb = ppf_match.minilm_embedder(md); self.name, self.model_dir = "minilm", md
            except Exception as e:                    # onnxruntime / numpy missing
                if want == "minilm":
                    raise
                self.why = "%s: %s" % (type(e).__name__, e)
        elif want == "minilm":
            raise FileNotFoundError("no MiniLM model folder")
        elif want == "auto":
            self.why = "no model folder"

    def line(self):
        l = ppf_match.engine_line(self.name, self.model_dir)
        return l + (" [%s]" % self.why if self.why else "")

    def vectors(self, texts):
        if self.name == "minilm":
            return [self.emb(t) for t in texts]
        tok = lambda s: re.findall(r"[a-z0-9]+", s.lower())
        self._docs = [Counter(tok(t)) for t in texts]
        df = Counter(w for d in self._docs for w in d); n = len(texts)
        self._idf = lambda w: math.log((n + 1) / (df.get(w, 0) + 1)) + 1
        self._tok = tok
        return [self._vec(d) for d in self._docs]

    def _vec(self, cnt):
        v = {w: f * self._idf(w) for w, f in cnt.items()}; z = math.sqrt(sum(x * x for x in v.values())) or 1
        return {w: x / z for w, x in v.items()}

    def query(self, text):
        return self.emb(text) if self.name == "minilm" else self._vec(Counter(self._tok(text)))

    @staticmethod
    def sim(a, b):
        if isinstance(a, dict):
            return sum(x * b.get(w, 0) for w, x in a.items())
        return float(a @ b)


def cluster(vecs, thresh, sim):
    """Average-linkage agglomerative grouping: merge the closest pair while its mean cosine >= thresh."""
    n = len(vecs)
    S = [[sim(vecs[i], vecs[j]) for j in range(n)] for i in range(n)]
    groups = [[i] for i in range(n)]
    while len(groups) > 1:
        best, bi, bj = -2, -1, -1
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                s = sum(S[a][b] for a in groups[i] for b in groups[j]) / (len(groups[i]) * len(groups[j]))
                if s > best:
                    best, bi, bj = s, i, j
        if best < thresh:
            break
        groups[bi] += groups.pop(bj)
    return [sorted(g) for g in groups], S


def central(g, S):
    return max(g, key=lambda i: sum(S[i][j] for j in g))


class Seqs:
    """Collects add_sequence/place_clip ops with unique names; makes bins as needed."""
    def __init__(self, existing_names, existing_bins):
        self.names, self.bins, self.ops = set(existing_names), set(existing_bins), []

    def add(self, name, bin_, role, placements, fmt, extra=None):
        base, k = name, 2
        while name in self.names:
            name = "%s_%d" % (base, k); k += 1
        self.names.add(name)
        for i in range(1, len(bin_.split("/")) + 1):
            p = "/".join(bin_.split("/")[:i])
            if p not in self.bins:
                self.ops.append({"op": "add_bin", "path": p}); self.bins.add(p)
        self.ops.append(dict({"op": "add_sequence", "name": name, "bin": bin_, "role": role}, **dict(fmt, **(extra or {}))))
        t = 0.0
        for c, i, o in placements:
            op = {"op": "place_clip", "seq": name, "clip": c["clip_id"], "track": "V1",
                  "at_s": round(t, 3), "in_s": round(i, 3), "out_s": round(o, 3)}
            if c.get("fps_measured") is not None:          # measured rate + VFR flag ride on the op
                op["fps_measured"], op["vfr"] = c["fps_measured"], bool(c.get("vfr"))
            self.ops.append(op)
            t += o - i
        return name


def scan_csvs(objects):
    """(segments_csv, families_csv) beside the clips CSV the objects came from; None when absent."""
    src = objects.get("source") or ""
    if objects.get("mode") != "scan" or not src.lower().endswith(".csv"):
        return None, None
    d, b = os.path.dirname(src), os.path.basename(src)
    pre = b.split("_clips")[0] if "_clips" in b else ""
    pick = lambda word: (sorted(glob.glob(os.path.join(d, pre + "*" + word + "*.csv"))) or sorted(glob.glob(os.path.join(d, "*" + word + "*.csv"))) or [None])[0]
    return pick("segments"), pick("families")


def norm_line(t):
    return re.sub(r"[^a-z0-9 ]+", "", (t or "").lower()).strip()


def takes(items, seqs, day, fmt):
    """items: [(clip, in_s, out_s, text)] -> TAKES sequences for lines said more than once."""
    groups = []
    for it in items:
        n = norm_line(it[3])
        if len(n) < 8:
            continue
        for g in groups:
            if difflib.SequenceMatcher(None, n, norm_line(g[0][3])).ratio() >= 0.85:
                g.append(it); break
        else:
            groups.append([it])
    made = []
    for g in groups:
        if len(g) >= 2:
            made.append(seqs.add("%s_TAKES_%s" % (day, slug(" ".join(g[0][3].split()[:5]))), "01_SEQUENCES/%s/TAKES" % day,
                                 "takes", [(c, i, o) for c, i, o, _ in g], fmt, {"line": g[0][3][:120]}))
    return made


def organize(objects, plan_ops, date, notes=None, top=5, engine="auto", model_dir=None, fmt_of=None, log=print):
    """-> (ops, info). ops are appended to the plan; info = {engine, topics, takes, selects, report}."""
    dom, vfr_ids = conform_clips(objects["clips"])      # idempotent; ppf_plan has usually done it already
    fmt_of = fmt_of or (lambda cl: default_fmt(cl, dom))
    eng = Engine(engine, model_dir)
    log("organize: " + eng.line())
    seqs = Seqs([o["name"] for o in plan_ops if o["op"] == "add_sequence"], [o["path"] for o in plan_ops if o["op"] == "add_bin"])
    usable = [c for c in objects["clips"] if c["role"] == "master" and c.get("duration_s") and c["kind"] in ("interview", "broll")
              and c.get("orientation") != "vertical"]          # verticals live in <day>_VERTICAL_stringout only (G4)
    by_rel = {c["rel_path"]: c for c in usable}
    info = {"engine": eng.name, "engine_line": eng.line(), "model_dir": eng.model_dir, "topics": [], "takes": [],
            "selects": [], "report": None,
            "vfr_clips": [{"clip": c["clip_id"], "fps_measured": c["fps_measured"], "fps": c["fps"]}
                          for c in objects["clips"] if c.get("vfr")]}
    seg_csv, fam_csv = scan_csvs(objects)
    seg_words = {}
    days = sorted({c["day"] for c in usable})
    if seg_csv:                                                   # ---- a. scan data
        info["mode"] = "scan families" if fam_csv else "scan tags"
        fams = {r["family"]: r for r in csv.DictReader(open(fam_csv, encoding="utf-8"))} if fam_csv else {}
        segs = [r for r in csv.DictReader(open(seg_csv, encoding="utf-8")) if r.get("rel_path") in by_rel]
        for r in segs:
            seg_words.setdefault(r["rel_path"], []).append(" ".join(r.get(k) or "" for k in ("content", "shot_size", "light", "caption", "text")))
        for d in days:
            ds = [r for r in segs if by_rel[r["rel_path"]]["day"] == d]
            key = (lambda r: r.get("family")) if fams else (lambda r: r.get("content"))
            topics = {}
            for r in ds:
                if key(r):
                    topics.setdefault(key(r), []).append(r)
            for k in sorted(topics, key=lambda k: (-len({r["rel_path"] for r in topics[k]}), k)):
                rs = topics[k]
                cl = {r["rel_path"] for r in rs}
                if len(cl) < 2 or all(by_rel[x]["kind"] == "interview" for x in cl):
                    continue
                label = fams[k]["name"] if k in fams else k
                rs = sorted(rs, key=lambda r: (r["rel_path"], float(r.get("t_in") or 0)))
                pl = [(by_rel[r["rel_path"]], float(r.get("t_in") or 0), float(r.get("t_out") or by_rel[r["rel_path"]]["duration_s"])) for r in rs]
                n = seqs.add("%s_BROLL_%s" % (d, slug(label)), "01_SEQUENCES/%s/TOPICS" % d, "topic", pl,
                             fmt_of([by_rel[x] for x in cl]), {"topic": label, "family": k if k in fams else None})
                info["topics"].append({"day": d, "seq": n, "label": label, "clips": sorted(by_rel[x]["clip_id"] for x in cl)})
            items = [(by_rel[r["rel_path"]], float(r.get("t_in") or 0), float(r.get("t_out") or 0), r.get("text") or "")
                     for r in ds if (r.get("text") or "").strip()]
            src = objects.get("source")
            if not items and src and os.path.isfile(src):          # clip-level transcript column
                for r in csv.DictReader(open(src, encoding="utf-8")):
                    c = by_rel.get(r.get("rel_path"))
                    if c and c["day"] == d and (r.get("transcript") or "").strip():
                        items.append((c, 0.0, c["duration_s"], r["transcript"]))
            info["takes"] += takes(items, seqs, d, fmt_of([i[0] for i in items]))
    else:                                                         # ---- b. no scan data: embed and group
        info["mode"] = "embedding groups"
        for d in days:
            day = sorted([c for c in usable if c["day"] == d], key=lambda c: c["rel_path"])
            side = {c["clip_id"]: sidecar_text(c) for c in day}
            broll = [c for c in day if c["kind"] == "broll"]
            if len(broll) >= 2:
                texts = [" ".join(words_of(c)) + (" . " + side[c["clip_id"]] if side[c["clip_id"]] else "") for c in broll]
                vecs = eng.vectors(texts)
                groups, S = cluster(vecs, THRESH[eng.name], eng.sim)
                for g in sorted(groups, key=lambda g: (-len(g), g)):
                    if len(g) < 2:
                        continue
                    mid = broll[central(g, S)]
                    mw = list(dict.fromkeys(w.lower() for w in words_of(mid)[:-1]))          # central clip's words, deduped
                    shared = [w for w in mw if all(w in [x.lower() for x in words_of(broll[i])] for i in g)]
                    label = " ".join((shared or mw)[:3])
                    n = seqs.add("%s_BROLL_%s" % (d, slug(label)), "01_SEQUENCES/%s/TOPICS" % d, "topic",
                                 [(broll[i], 0.0, broll[i]["duration_s"]) for i in g], fmt_of([broll[i] for i in g]),
                                 {"topic": label, "cohesion": round(sum(S[i][j] for i in g for j in g) / len(g) ** 2, 3)})
                    info["topics"].append({"day": d, "seq": n, "label": label, "clips": [broll[i]["clip_id"] for i in g]})
            items = [(c, 0.0, c["duration_s"], side[c["clip_id"]]) for c in day if side[c["clip_id"]]]
            info["takes"] += takes(items, seqs, d, fmt_of([i[0] for i in items]))
    for t in info["topics"]:
        log("organize: %s  (%d clips: %s)" % (t["seq"], len(t["clips"]), t["label"]))
    for t in info["takes"]:
        log("organize: %s" % t)
    # ---- c. notes -> selects
    lines = [l.strip() for l in (notes or "").splitlines() if l.strip() and not l.strip().startswith("#")]
    if lines and usable:
        pool = sorted(usable, key=lambda c: c["rel_path"])
        texts = [" ".join(words_of(c)) + " " + " ".join(seg_words.get(c["rel_path"], [])) + " " + sidecar_text(c, 600) for c in pool]
        vecs = eng.vectors(texts)
        rep = ["# Selects report", "", "Engine: `%s`. Scores are cosine similarity between the note and each clip's words "
               "(folders, file name, kind, scan tags, sidecar transcript). The model ranks; check every pick." % eng.line(), ""]
        for k, note in enumerate(lines, 1):
            q = eng.query(note)
            ranked = sorted(((eng.sim(q, v), c) for v, c in zip(vecs, pool)), key=lambda x: (-x[0], x[1]["rel_path"]))[:top]
            n = seqs.add("%s_SELECTS_%s" % (date, slug(note)), "01_SEQUENCES/SELECTS", "selects",
                         [(c, 0.0, c["duration_s"]) for _, c in ranked], fmt_of([c for _, c in ranked]), {"note": note})
            info["selects"].append({"note": note, "seq": n, "picks": [(round(s, 3), c["clip_id"]) for s, c in ranked]})
            log("organize: %s <- %r (first: %s %.3f)" % (n, note, ranked[0][1]["clip_id"], ranked[0][0]))
            rep += ["## %d. %s" % (k, note), "", "Sequence `%s`" % n, "", "| # | score | clip |", "|---|---|---|"]
            rep += ["| %d | %.3f | `%s` |" % (i, s, c["clip_id"]) for i, (s, c) in enumerate(ranked, 1)] + [""]
        info["report"] = "\n".join(rep)
    if info["vfr_clips"]:                                         # flag only: no transcode (white paper 261113 Q2)
        rep = (info["report"].split("\n") if info["report"] else ["# Organize report", ""])
        rep += ["## VFR clips (phone footage)", "",
                "%d clips record a variable frame rate. Each is conformed to a standard rate in its sequences; "
                "consider a constant-rate (ProRes LT) transcode before the online. Nothing was transcoded." % len(info["vfr_clips"]),
                "", "| clip | measured fps | conformed fps |", "|---|---|---|"]
        rep += ["| `%s` | %.3f | %s |" % (v["clip"], v["fps_measured"], v["fps"]) for v in info["vfr_clips"]] + [""]
        info["report"] = "\n".join(rep)
        log("organize: %d VFR clips conformed (flagged, not transcoded)" % len(info["vfr_clips"]))
    return seqs.ops, info


def default_fmt(clips, dom):
    """Fallback fmt_of: conform each clip (day's dominant standard rate) BEFORE counting."""
    c = Counter((seq_fps_of(x, dom.get(x.get("day"))), x.get("width"), x.get("height"))
                for x in clips if x.get("fps"))
    if not c:
        return {"fps": 23.976, "width": 1920, "height": 1080}
    (fps, w, h), _ = c.most_common(1)[0]
    return {"fps": fps, "width": w or 1920, "height": h or 1080}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("objects"); ap.add_argument("--notes"); ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--engine", default="auto", choices=["auto", "minilm", "tfidf"]); ap.add_argument("--date", default="000000")
    a = ap.parse_args(argv)
    ops, info = organize(load_json(a.objects), [], a.date, open(a.notes, encoding="utf-8").read() if a.notes else None, a.top, a.engine)
    print(json.dumps(ops, indent=1))
    return ops, info


if __name__ == "__main__":
    from ppf_common import maybe_reexec
    maybe_reexec()
    main()
