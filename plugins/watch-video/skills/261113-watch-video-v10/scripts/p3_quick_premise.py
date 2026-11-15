#!/usr/bin/env python3
"""p3_quick_premise.py · the QUICK bracket (0-5 min): tagline + premise + a light PDF. v16, work brief §10.

    python3 p3_quick_premise.py --ledger <P2 ledger> [--catalog DIR] [--only TEXT] [--workers N]

TAGLINE (seconds, facts only, no frames): per card - camera family, shoot day, clip count, total time, what waits.
PREMISE: one thumbnail per clip, cheapest source first (the if/then):
  1. the card's own thumbnail (cards.json -> thumbnails), IF it exists, opens and is not blank
  2. ELSE on a Mac: macOS's own thumbnail engine (qlmanage -t, what Finder shows)
  3. ELSE ffmpeg, first keyframe only (no full decode)
  RED / BRAW wait for their decoder (PENDING with the reason).
then the fast tagger (vision.choose.quick_tagger) + fingerprint per thumbnail -> one premise line per clip.
Missing or stripped camera thumbnails are normal, never an error. Thumbnails are made by a bounded worker pool.

Writes (nothing deleted, never on the drive):
  P3_PROCESS/quick_thumbs/<hash>.jpg
  P3_PROCESS/<YYMMDD>_<drive>_quick_premise_P3-07_<HHMM>.json     clips -> thumb, thumb_source, premise, tags
  P5_PDF/<YYMMDD>_<drive>_pdf_quick_P5-02_<HHMM>.pdf              the tagline + one thumbnail and premise per clip
"""
import argparse
import collections
import concurrent.futures as cf
import csv
import datetime as dt
import hashlib
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import p0_env  # noqa: E402
import p3_process_frames as p3  # noqa: E402
import p3_see_models as sm  # noqa: E402

THUMB_W = 480


def ok_image(path):
    import cv2
    img = cv2.imread(str(path))
    return img is not None and img.size > 0 and float(img.mean()) > 3 and float(img.std()) > 2


def card_thumb(clip, card_rules):
    for tpl in card_rules:
        cand = Path(tpl.format(dir=str(clip.parent), stem=clip.stem))
        for c in (cand, cand.with_suffix(cand.suffix.lower())):
            if c.is_file() and ok_image(c):
                return c
    return None


def to_jpg(src_img, out):
    import cv2
    img = cv2.imread(str(src_img))
    h, w = img.shape[:2]
    img = cv2.resize(img, (THUMB_W, max(2, int(round(h * THUMB_W / w / 2)) * 2)), interpolation=cv2.INTER_AREA)
    return bool(cv2.imwrite(str(out), img, [cv2.IMWRITE_JPEG_QUALITY, 85]))


def quicklook(clip, out):
    if sys.platform != "darwin" or not shutil.which("qlmanage"):
        return False
    tmp = Path(tempfile.mkdtemp(prefix="ql_", dir=p0_env.tmp_root()))
    try:
        subprocess.run(["qlmanage", "-t", "-s", str(THUMB_W), "-o", str(tmp), str(clip)], capture_output=True, timeout=60)
        made = list(tmp.glob("*.png"))
        return bool(made) and ok_image(made[0]) and to_jpg(made[0], out)
    except (subprocess.TimeoutExpired, OSError):
        return False


def ffmpeg_key(clip, out, dur):
    t = min(max(dur * 0.1, 0.0), 2.0) if dur else 0.0       # just past the head, never a long seek
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-ss", "%.2f" % t, "-skip_frame", "nokey", "-i", str(clip),
           "-frames:v", "1", "-vf", "scale=%d:-2" % THUMB_W, "-q:v", "4", "-y", str(out)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60)
        return r.returncode == 0 and out.is_file() and out.stat().st_size > 0 and ok_image(out)
    except subprocess.TimeoutExpired:
        return False


def thumb_for(r, clip, tdir, card_rules, drive):
    """(rel_path, thumb jpg or None, source or why)."""
    rel = r["rel_path"]
    out = tdir / ("%s.jpg" % hashlib.sha1(("%s/%s" % (drive, rel)).encode()).hexdigest()[:16])
    if rel.lower().endswith(".r3d") and clip and clip.is_file():
        try:                                      # v17 step 0: REDline is real here -> one eighth-res still at 10%
            import p3b_driver_red as red
            if red.find_redline():
                dur = float(r.get("duration_s") or 0)
                ok, _ = red.still(clip, dur * 0.1, out, float(r.get("fps") or 0) or None,
                                  {"red": dict(red.rules_red(), res=8)})
                if ok:
                    return rel, out, "redline"
        except (ImportError, ValueError, OSError):
            pass
    if rel.lower().endswith((".r3d", ".braw")):
        return rel, None, "driver lane: waits for its decoder"
    if not clip or not clip.is_file():
        return rel, None, "clip not reachable on this machine"
    try:
        dur = float(r.get("duration_s") or 0)
    except ValueError:
        dur = 0.0
    c = card_thumb(clip, card_rules) if card_rules else None
    if c and to_jpg(c, out):
        return rel, out, "camera"
    if quicklook(clip, out):
        return rel, out, "quicklook"
    if ffmpeg_key(clip, out, dur):
        return rel, out, "ffmpeg"
    return rel, None, "no thumbnail could be made"


def hms(s):
    try:
        s = float(s)
    except (TypeError, ValueError):
        return "?"
    return "%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60) if s >= 3600 else "%d:%02d" % (s // 60, s % 60)


def tagline(rows, card_of):
    """Per card, from file facts only (seconds)."""
    cards = collections.OrderedDict()
    for r in rows:
        if r.get("kind") != "video" or "RED segment of" in (r.get("note") or ""):   # v17: a RED clip counts once
            continue
        c = cards.setdefault(card_of(r), {"clips": 0, "secs": 0.0, "pending": 0, "family": collections.Counter(),
                                          "codecs": collections.Counter()})
        c["clips"] += 1
        try:
            c["secs"] += float(r.get("duration_s") or 0)
        except ValueError:
            c["pending"] += 1
        c["family"][r.get("card_family") or r.get("label") or "?"] += 1
        if r.get("video_codec"):
            c["codecs"][r["video_codec"]] += 1
    return cards


def pdf(out, drive, cards, clips, card_of, made, partial):
    from weasyprint import HTML
    css = ("@page{size:A4 landscape;margin:12mm}body{font-family:Helvetica,Arial,sans-serif;color:#222;font-size:9pt}"
           "h1{font-size:16pt;margin:0 0 2mm}h2{font-size:11pt;margin:5mm 0 2mm;color:#2f5688}.tl{color:#555}"
           ".g{display:flex;flex-wrap:wrap;gap:3mm}.c{width:52mm}.c img{width:52mm;height:29mm;object-fit:cover;background:#ddd}"
           ".n{font-weight:bold;font-size:8pt;overflow:hidden;white-space:nowrap}.p{font-size:7.5pt;color:#444}"
           ".pend{width:52mm;height:29mm;background:#eee;color:#888;font-size:7pt;display:flex;align-items:center;"
           "justify-content:center;text-align:center}.ban{background:#c76a75;color:#fff;padding:2mm 3mm;margin:2mm 0}")
    h = ["<html><head><meta charset='utf-8'><style>%s</style></head><body>" % css,
         "<h1>%s · quick scan</h1><div class='tl'>%s · %d cards · %d clips · %s</div>" % (
             html.escape(drive), made, len(cards), sum(c["clips"] for c in cards.values()),
             hms(sum(c["secs"] for c in cards.values())))]
    if partial:
        h.append("<div class='ban'>PARTIAL · %s</div>" % html.escape(partial))
    by = collections.defaultdict(list)
    for c in clips:
        by[c["card"]].append(c)
    for name, c in cards.items():
        fam = ", ".join(k for k, _ in c["family"].most_common(2))
        h.append("<h2>%s</h2><div class='tl'>%s · %d clips · %s%s</div><div class='g'>" % (
            html.escape(name), html.escape(fam), c["clips"], hms(c["secs"]),
            (" · %d waiting" % c["pending"]) if c["pending"] else ""))
        for k in by.get(name, []):
            img = ("<img src='file://%s'>" % html.escape(str(k["_abs_thumb"]))) if k.get("_abs_thumb") else \
                  "<div class='pend'>%s</div>" % html.escape(k.get("why") or "")
            h.append("<div class='c'>%s<div class='n'>%s · %s</div><div class='p'>%s</div></div>" % (
                img, html.escape(k["file_name"]), hms(k["duration_s"]), html.escape(k.get("premise") or "")))
        h.append("</div>")
    h.append("</body></html>")
    HTML(string="".join(h)).write_pdf(str(out))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--catalog", default=str(p0_env.catalog()))
    ap.add_argument("--only", default=None)
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--max-clips", type=int, default=0, help="Quick on a huge drive: cap per card (PDF says PARTIAL)")
    a = ap.parse_args()
    t0 = time.time()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    ledger = Path(a.ledger).expanduser()
    rows = list(csv.DictReader(open(ledger, encoding="utf-8-sig", newline="")))
    pn = p0_env.parse_name(ledger.name)
    drive = rows[0].get("drive") or (pn["drive"] if pn else "drive")
    catalog = Path(os.path.abspath(os.path.expanduser(a.catalog)))
    root = p3.find_root(catalog, drive, rows)
    card_of = lambda r: p3.group_of(r, "auto")
    cards = tagline(rows, card_of)
    print("quick: TAGLINE · %s · %d cards · %d clips · %.0f s" % (drive, len(cards), sum(c["clips"] for c in cards.values()), time.time() - t0), flush=True)
    for n, c in cards.items():
        print("  %-48s %-14s %4d clips  %s%s" % (n[:48], ", ".join(k for k, _ in c["family"].most_common(1)), c["clips"],
                                               hms(c["secs"]), ("  · %d waiting" % c["pending"]) if c["pending"] else ""))
    rules = {c["id"]: c.get("thumbnails") or [] for c in p0_env.cards()}
    vids = [r for r in rows if r.get("kind") == "video" and r.get("lane") != "derived" and r.get("label") not in p3.SKIP_LABELS
            and "RED segment of" not in (r.get("note") or "")]   # v17: RED _002+ segments are the same clip
    if a.only:
        vids = [r for r in vids if p0_env.matches(a.only, card_of(r))]
    partial = ""
    if a.max_clips:
        per = collections.defaultdict(int)
        keep = []
        for r in vids:
            per[card_of(r)] += 1
            if per[card_of(r)] <= a.max_clips:
                keep.append(r)
        if len(keep) < len(vids):
            partial = "%d of %d clips (first %d per card)" % (len(keep), len(vids), a.max_clips)
        vids = keep
    if a.only:
        partial = (partial + " · " if partial else "") + "only cards matching \"%s\"" % a.only
    base = p0_env.phase_dir(catalog, drive, "P3")
    tdir = base / "quick_thumbs"
    tdir.mkdir(exist_ok=True)
    nw = a.workers or max(2, p0_env.workers())
    res = {}
    with cf.ThreadPoolExecutor(max_workers=nw) as ex:            # clockwork: bounded pool for the thumbnail gear
        futs = [ex.submit(thumb_for, r, (root / r["rel_path"]) if root else None, tdir,
                          rules.get(r.get("card_family") or "", []), drive) for r in vids]
        for f in cf.as_completed(futs):
            rel, th, why = f.result()
            res[rel] = (th, why)
    src = collections.Counter(why if th else "none" for th, why in res.values())
    print("quick: thumbnails · %d clips · %s · %d worker(s) · %.0f s" % (len(vids), dict(src), nw, time.time() - t0), flush=True)
    if vids:                                              # v17: the measured rate, read by pipeline.py projection()
        try:
            (catalog / p0_env.RUNS_DIR).mkdir(parents=True, exist_ok=True)
            json.dump({"s_per_clip_per_worker": (time.time() - t0) * nw / len(vids), "clips": len(vids),
                       "when": dt.datetime.now().strftime("%y%m%d %H:%M")},
                      open(catalog / p0_env.RUNS_DIR / "quick_rate.json", "w"))
        except OSError:
            pass
    have = [r for r in vids if res[r["rel_path"]][0]]
    clips = []
    tagger_ok = p0_env.models_ok()[0]
    if not tagger_ok:
        print("quick: the tagger (CLIP) is not installed yet: thumbnails + PDF now; tags after 'p3_see_models.py install core'")
        have_tags = []
    else:
        have_tags = have
    tags = {}
    if have_tags:
        import cv2
        v = sm.V()
        tk = v["choose"].get(v["brackets"]["quick"].get("tagger", "quick_tagger"), v["choose"]["tagger"])
        ik, txk = sm.tagger_keys(tk)
        imgs = [cv2.cvtColor(cv2.imread(str(res[r["rel_path"]][0])), cv2.COLOR_BGR2RGB) for r in have_tags]
        tv = sm.embed_image(ik, imgs, batch=32)
        bank = sm.tag_bank(txk)
        tags = {r["rel_path"]: sm.tag(vec, bank) for r, vec in zip(have_tags, tv)}
    for r in vids:
        th, why = res[r["rel_path"]]
        t = tags.get(r["rel_path"])
        clips.append({"clip_id": hashlib.sha1(("NAME:%s:%s" % (drive, r["rel_path"])).encode()).hexdigest()[:12],
                      "rel_path": r["rel_path"], "card": card_of(r), "file_name": r.get("file_name"),
                      "duration_s": r.get("duration_s"), "thumb": ("quick_thumbs/" + th.name) if th else "",
                      "_abs_thumb": str(th) if th else "", "thumb_source": why if th else "",
                      "why": "" if th else why,
                      "premise": " · ".join(x["value"] for x in t.values()) if t else "",
                      "tags": t or {}})
    when = p0_env.stamp()
    out_js = p0_env.out_path(catalog, drive, "P3", "quick", "premise", 7, "json", when)
    with open(out_js, "w", encoding="utf-8") as f:
        json.dump({"drive": drive, "made": dt.datetime.now().isoformat(timespec="seconds"), "ledger": ledger.name,
                   "partial": partial, "tagline": {k: {kk: (dict(vv) if isinstance(vv, collections.Counter) else vv)
                                                       for kk, vv in c.items()} for k, c in cards.items()},
                   "clips": [{k: v_ for k, v_ in c.items() if not k.startswith("_")} for c in clips]}, f, indent=1)
    out_pdf = p0_env.out_path(catalog, drive, "P5", "pdf", "quick", 2, "pdf", when)
    pdf(out_pdf, drive, cards, clips, card_of, dt.datetime.now().strftime("%y%m%d %H:%M"), partial)
    print("quick: done · %d clips · premise for %d · %.0f s -> %s" % (len(clips), len(have), time.time() - t0, out_pdf.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
