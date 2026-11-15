#!/usr/bin/env python3
"""p3_see_segments.py · P3 SEE (v16) · cut every clip into SHOTS and SEGMENTS, and say what each segment is.

Stitched from libraries (adapters in p3_see_models.py):
  shots     PySceneDetect AdaptiveDetector (TransNetV2 recorded beside it as a second opinion when installed)
  frames    PyAV decodes REAL frames at timestamps spread over the whole clip; the actual PTS is recorded
  regimes   ruptures.Pelt(model="l2", jump=1) on the fingerprints INSIDE each shot (a shot cut is always a boundary)
  per segment: hero frame (the lit sample nearest the segment's centre), tags (choose.tagger), people (YOLOX-S),
            faces + on-screen text (when faces_text is installed); OVERNIGHT adds a caption (llama.cpp + SmolVLM2)
            and speech / transcripts (faster-whisper) on the clips the rules allow

    python3 p3_see_segments.py --ledger <P2 ledger> [--catalog DIR] [--only TEXT] [--max-clips N]
                               [--pass quick|overnight] [--transcribe LIST.txt] [--force]

Writes (never on the drive; nothing deleted), filed under <catalog>/<drive>/P3_PROCESS/:
  <YYMMDD>_<drive>_see_segments_P3-05_<HHMM>.json      clips -> shots -> segments -> tags / people / faces / text / caption
  <YYMMDD>_<drive>_see_vectors_P3-06_<HHMM>.npz        per segment: fingerprint, tag vector, face vectors
  see_thumbs/<hash>.jpg                                 one 480 px hero frame per segment
A rerun reuses every clip whose size, duration and SEE rules are unchanged; the overnight pass only ADDS columns
to the same rows. Audio is never touched unless the clip's transcribe? tag is in vision.speech.run_on (speech
detection only), and transcripts only for the rel_paths listed in --transcribe.
Exit 0 = done · 4 = the core models are not installed (the pipeline carries on without SEE) · 2 = error.
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import p0_env  # noqa: E402
import p3_process_frames as p3  # noqa: E402  (pick, group_of, find_root: one place)
import p3_see_models as sm  # noqa: E402

THUMB_W = 480


def clip_id(drive, rel):
    return hashlib.sha1(("NAME:%s:%s" % (drive, rel)).encode("utf-8")).hexdigest()[:12]


def see_hash(v):
    keep = {k: v.get(k) for k in ("choose", "sample", "cuts", "regimes", "axes", "dark_luma", "people_min_score", "faces", "ocr")}
    keep["models"] = {k: [f["sha256"] for f in m["files"]] for k, m in v["models"].items() if m["group"] in ("core", "eyes", "faces_text")}
    return hashlib.sha1(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:12]


def shots_of(src, dur, v):
    """[(t_in, t_out)] from PySceneDetect; one shot when it finds no cut."""
    from scenedetect import detect, AdaptiveDetector
    try:
        sc = detect(str(src), AdaptiveDetector())
        cuts = [round(a.get_seconds(), 3) for a, _ in sc][1:]
    except Exception:
        cuts = []
    edges = [0.0] + [c for c in cuts if 0 < c < dur] + [dur]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)], cuts


def transnet_cuts(src, fps):
    """TransNetV2's own list of cut times (second opinion; recorded, not used as the boundary)."""
    import subprocess
    np = sm.np_()
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-vf", "scale=48:27", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=600).stdout
    if not raw or not fps:
        return []
    p = sm.transnet(np.frombuffer(raw, np.uint8).reshape(-1, 27, 48, 3))
    thr = sm.V()["cuts"]["transnet_threshold"]
    return [round((i + 1) / fps, 3) for i in (np.where((p[1:] >= thr) & (p[:-1] < thr))[0] + 1)]


def frames_at(src, times):
    """PyAV: decode the first frame at or after each target time. Returns [(actual_pts_s, rgb ndarray)]."""
    import av
    out = []
    with av.open(str(src)) as c:
        st = c.streams.video[0]
        st.thread_type = "AUTO"
        for t in times:
            try:
                c.seek(int(max(t - 0.5, 0) / st.time_base), stream=st, backward=True, any_frame=False)
                for fr in c.decode(st):
                    ft = float(fr.pts * st.time_base) if fr.pts is not None else t
                    if ft + 1e-3 >= t:
                        out.append((round(ft, 3), fr.to_ndarray(format="rgb24")))
                        break
            except Exception:
                continue
    return out


def overview_times(dur, v):
    s = v["sample"]
    n = int(min(s["overview_max"], max(1, math.ceil(dur / float(s["overview_every_s"])))))
    if n == 1:
        return [dur / 2]
    return [round(dur * (i + 0.5) / n, 3) for i in range(n)]


def regimes(vecs, v):
    """Indices where segments start inside one shot (ruptures PELT, l2)."""
    r = v["regimes"]
    if len(vecs) < 2 * int(r["min_samples"]):
        return [0]
    import ruptures as rpt
    ends = rpt.Pelt(model=r["model"], min_size=int(r["min_samples"]), jump=int(r["jump"])).fit(vecs).predict(pen=float(r["penalty"]))
    return [0] + [e for e in ends[:-1]]                        # the last index returned is the end, not a cut


def save_thumb(rgb, out):
    import cv2
    h, w = rgb.shape[:2]
    img = cv2.resize(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), (THUMB_W, max(2, int(round(h * THUMB_W / w / 2)) * 2)),
                     interpolation=cv2.INTER_AREA)
    return bool(cv2.imwrite(str(out), img, [cv2.IMWRITE_JPEG_QUALITY, 88])), img


def driver_stills(catalog, drive):
    """v17: {rel_path: [(t, frame path)]} for .r3d/.braw clips that already have stills in a P3 WATCH_STATE
    (REDline pulled them). Those clips are seen from their sheet keyframes instead of waiting as PENDING."""
    out = {}
    base = p0_env.phase_dir(catalog, drive, "P3")
    for st in sorted(Path(base).glob("*/WATCH_STATE.json")):
        try:
            state = json.load(open(st, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for sc in state.get("scenes", []):
            rel = sc.get("rel_path") or ""
            if not rel.lower().endswith((".r3d", ".braw")):
                continue
            got = []
            for sh in sc.get("shots", []):
                fr = sh.get("frame") or ""
                fp = Path(fr) if os.path.isabs(fr) else st.parent / fr
                if fr and fp.is_file() and fp.stat().st_size > 0:
                    got.append((float(sh.get("t") or 0.0), str(fp)))
            if got:
                out[rel] = sorted(got)
    return out


def frames_from_stills(stills):
    """[(t, rgb ndarray)] from still JPGs (the sheet keyframes)."""
    import cv2
    out = []
    for t, p in stills:
        img = cv2.imread(p)
        if img is not None:
            out.append((float(t), cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
    return out


def see_clip(r, src, dur, v, bank, drive, tdir, groups, stills=None):
    """One clip -> (record, {seg_id: {"fp", "tv", "faces"}}). stills (v17): [(t, jpg)] for driver-lane clips."""
    np = sm.np_()
    ch = v["choose"]
    rel = r["rel_path"]
    cid = clip_id(drive, rel)
    try:
        fps = float(r.get("fps") or 0)
    except ValueError:
        fps = 0.0
    rec = {"clip_id": cid, "rel_path": rel, "file_name": r.get("file_name") or Path(rel).name,
           "size_bytes": r.get("size_bytes") or "", "duration_s": round(dur, 3), "status": "seen", "why": "",
           "shots": [], "cuts_transnet": None, "segments": []}
    if stills:
        shots, cuts = [(0.0, max(dur, stills[-1][0] + 1e-3))], []
        rec["see_from"] = "sheet keyframes (driver lane)"
    else:
        shots, cuts = shots_of(src, dur, v)
    rec["shots"] = [[round(a, 3), round(b, 3)] for a, b in shots]
    if not stills and groups.get("eyes") and v["cuts"].get("second_opinion") == "transnetv2":
        try:
            rec["cuts_transnet"] = transnet_cuts(src, fps)
        except Exception as e:
            rec["cuts_transnet"] = "failed: %s" % str(e)[:80]
    samples = frames_from_stills(stills) if stills else frames_at(src, overview_times(dur, v))
    if not samples:
        rec.update(status="needs_scan", value="PENDING", scan_by="any", why="no frame could be decoded")
        return rec, {}
    pts = [p for p, _ in samples]
    imgs = [a for _, a in samples]
    fp = sm.embed_image(ch["fingerprint"], imgs)
    tv = sm.embed_image(sm.tagger_keys(ch["tagger"])[0], imgs)
    good = sm.finite_rows(fp) & sm.finite_rows(tv)
    luma = np.array([a.mean() for a in imgs])
    vecs, n = {}, 0
    for s_in, s_out in shots:
        idx = [i for i, p in enumerate(pts) if s_in - 1e-3 <= p < s_out - 1e-3 and good[i]]
        if not idx:
            continue
        starts = [idx[j] for j in regimes(fp[idx], v)] + [None]
        for a, b in zip(starts[:-1], starts[1:]):
            seg_idx = [i for i in idx if i >= a and (b is None or i < b)]
            n += 1
            sid = "%s/S%02d" % (cid, n)
            centre = sm.unit(fp[seg_idx].mean(0))
            lit = [i for i in seg_idx if luma[i] >= float(v["dark_luma"])]
            hero = max(lit, key=lambda i: float(fp[i] @ centre)) if lit else max(seg_idx, key=lambda i: luma[i])
            t_in = s_in if a == idx[0] else round((pts[a - 1] + pts[a]) / 2, 3)
            nxt = [i for i in idx if b is not None and i >= b]
            t_out = round((pts[b - 1] + pts[b]) / 2, 3) if nxt else s_out
            th = tdir / ("%s.jpg" % hashlib.sha1(("%s/%s@%.3f" % (drive, rel, pts[hero])).encode()).hexdigest()[:16])
            ok, bgr = save_thumb(imgs[hero], th)
            seg = {"seg_id": sid, "n": n, "shot": shots.index((s_in, s_out)) + 1, "t_in": round(t_in, 3),
                   "t_out": round(t_out, 3), "hero_t": pts[hero], "samples": len(seg_idx),
                   "thumb": ("see_thumbs/" + th.name) if ok else "", "dark": not lit,
                   "tags": sm.tag(sm.unit(tv[seg_idx].mean(0)), bank), "people": sm.people(bgr)}
            if seg["people"]["biggest_h"] >= 0.7 and seg["tags"]["shot"]["value"] == "wide":
                seg["tags"]["shot"].update(value="CHECK", status="unresolved",
                                           why="tagged wide, but a person fills most of the frame")
            fv = []
            if groups.get("faces_text"):
                f = sm.faces(bgr)
                seg["faces"] = len(f)
                fv = [x["vec"] for x in f]
                seg["text"] = [x["text"] for x in sm.text(bgr)]
            rec["segments"].append(seg)
            vecs[sid] = {"fp": centre, "tv": sm.unit(tv[seg_idx].mean(0)), "faces": fv}
    return rec, vecs


def see_one(r, src, dur, v, bank, drive, tdir, groups, stills=None):
    """One clip in a worker process (clockwork gear). Models load once per worker and stay loaded."""
    try:
        return see_clip(r, Path(src), dur, v, bank, drive, Path(tdir), groups, stills)
    except Exception as e:
        return ({"clip_id": clip_id(drive, r["rel_path"]), "rel_path": r["rel_path"], "file_name": r.get("file_name") or "",
                 "size_bytes": r.get("size_bytes") or "", "duration_s": round(dur, 3), "status": "needs_scan",
                 "value": "PENDING", "scan_by": "any", "why": "SEE failed: %s" % str(e)[:120], "segments": []}, {})


def overnight(rec, src, r, v, base, groups, ticked):
    """Adds columns to the SAME rows: caption per segment, speech / transcript per clip (only where the rules allow)."""
    if groups.get("captions"):
        for s in rec["segments"]:
            if s.get("thumb") and "caption" not in s:
                s["caption"] = sm.caption(base / s["thumb"])
    tr = (r.get("transcribe") or "").strip()
    if groups.get("speech") and tr in v["speech"]["run_on"] and "speech" not in rec:
        try:
            rec["speech"] = sm.speech(src)
        except Exception as e:
            rec["speech"] = {"value": "PENDING", "status": "needs_scan", "why": "speech check failed: %s" % str(e)[:80]}
    if groups.get("speech") and rec["rel_path"] in ticked and "transcript" not in rec:
        try:
            rec["transcript"] = sm.transcribe(src)
        except Exception as e:
            rec["transcript"] = {"value": "PENDING", "status": "needs_scan", "why": "transcript failed: %s" % str(e)[:80]}
    return rec


def previous(catalog, drive, h):
    np = sm.np_()
    js = p0_env.newest(catalog, drive, "P3", "see", "segments", "json")
    if not js:
        return {}
    try:
        old = json.load(open(js, encoding="utf-8"))
        if old.get("see_hash") != h:
            return {}
        z = np.load(js.with_name(old["vectors"]), allow_pickle=False)
        ids = [str(x) for x in z["ids"]]
        fps, tvs = dict(zip(ids, z["fp"])), dict(zip(ids, z["tv"]))
        fids, fvecs = [str(x) for x in z["face_seg"]], z["face_vec"]
    except (OSError, ValueError, KeyError):
        return {}
    faces = {}
    for s, fvec in zip(fids, fvecs):
        faces.setdefault(s, []).append(fvec)
    return {c["rel_path"]: (c, {s["seg_id"]: {"fp": fps[s["seg_id"]], "tv": tvs[s["seg_id"]], "faces": faces.get(s["seg_id"], [])}
                                for s in c["segments"] if s["seg_id"] in fps}) for c in old.get("clips", [])}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--catalog", default=str(p0_env.catalog()))
    ap.add_argument("--only", default=None)
    ap.add_argument("--max-clips", type=int, default=0)
    ap.add_argument("--include-derived", action="store_true")
    ap.add_argument("--pass", dest="pass_", choices=["quick", "overnight"], default="quick")
    ap.add_argument("--transcribe", default=None, help="a text file of rel_paths (one per line) to transcribe")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--tagger", default=None, help="override vision.choose.tagger (clip | siglip2_base | siglip2_so400m)")
    ap.add_argument("--workers", type=int, default=0, help="clockwork: clips in parallel (default: sized from this machine)")
    a = ap.parse_args()
    t0 = time.time()
    ok, why = p0_env.models_ok()
    if not ok:
        print("see: SKIPPED · the core vision models are not installed (%s). Install: python3 scripts/p3_see_models.py "
              "install core. Everything else carries on." % why)
        return 4
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    v = sm.V()
    if a.tagger:
        v["choose"]["tagger"] = a.tagger
    groups = {g: have for g, (have, _) in p0_env.model_groups().items()}
    groups["captions"] = groups.get("captions") and bool(sm.llama_exe())
    h = see_hash(v)
    ledger = Path(a.ledger).expanduser()
    rows = list(csv.DictReader(open(ledger, encoding="utf-8-sig", newline="")))
    pn = p0_env.parse_name(ledger.name)
    drive = rows[0].get("drive") or (pn["drive"] if pn else ledger.name.split("_ledger_")[0])
    catalog = Path(os.path.abspath(os.path.expanduser(a.catalog)))
    root = p3.find_root(catalog, drive, rows)
    clips, pending, _ = p3.pick(rows, a.include_derived)
    groups_by = {}
    for r in clips:
        groups_by.setdefault(p3.group_of(r, "auto"), []).append(r)
    names = sorted(groups_by)
    if a.only:
        names = [g for g in names if p0_env.matches(a.only, g)]
    ticked = set()
    if a.transcribe:
        ticked = {l.strip() for l in open(a.transcribe, encoding="utf-8") if l.strip()}
    prev = {} if a.force else previous(catalog, drive, h)
    base = p0_env.phase_dir(catalog, drive, "P3")
    tdir = base / "see_thumbs"
    tdir.mkdir(exist_ok=True)
    bank = sm.tag_bank(sm.tagger_keys(v["choose"]["tagger"])[1])
    todo = [(g, r) for g in names for r in sorted(groups_by[g], key=lambda r: r.get("rel_path", ""))[: a.max_clips or None]]
    print("see: %s · %s pass · %d clips in %d groups · tagger %s · fingerprint %s · groups %s" % (
        drive, a.pass_, len(todo), len(names), v["choose"]["tagger"], v["choose"]["fingerprint"],
        ", ".join(g for g, ok_ in sorted(groups.items()) if ok_)), flush=True)
    heavy = v["choose"]["tagger"] == "siglip2_so400m"
    nw = a.workers or p0_env.workers(heavy=heavy)
    cap = nw * int(v.get("clockwork", {}).get("queue_per_worker", 2))
    print("see: clockwork · %d worker(s) · at most %d clips in flight" % (nw, cap), flush=True)
    jobs, recs_by, vecs, reused = [], {}, {}, 0
    dstills = driver_stills(catalog, drive)
    for k, (g, r) in enumerate(todo):
        rel = r["rel_path"]
        try:
            dur = float(r.get("duration_s") or 0)
        except ValueError:
            dur = 0.0
        src = (root / rel) if root else None
        old = prev.get(rel)
        if old and old[0].get("size_bytes") == (r.get("size_bytes") or "") and abs(old[0].get("duration_s", -1) - round(dur, 3)) < 1e-3 \
                and old[0].get("status") == "seen":
            recs_by[k] = old
            reused += 1
        elif rel in dstills:
            jobs.append((k, r, str(src), dur))           # v17: driver-lane clip with stills -> seen from its keyframes
        elif src is None or not src.is_file() or rel.lower().endswith((".r3d", ".braw")):
            recs_by[k] = ({"clip_id": clip_id(drive, rel), "rel_path": rel, "file_name": r.get("file_name") or "",
                           "size_bytes": r.get("size_bytes") or "", "duration_s": round(dur, 3), "status": "needs_scan",
                           "value": "PENDING", "scan_by": "any", "segments": [],
                           "why": "driver lane: SEE runs on these once a decoder makes frames" if rel.lower().endswith((".r3d", ".braw"))
                           else "the clip file is not reachable on this machine"}, {})
        else:
            jobs.append((k, r, str(src), dur))
    import concurrent.futures as cf
    done = 0
    with cf.ProcessPoolExecutor(max_workers=nw) as ex:
        pending_f, it = {}, iter(jobs)
        while True:
            while len(pending_f) < cap:                  # capped queue: never more than cap clips in flight
                nxt = next(it, None)
                if nxt is None:
                    break
                k, r, src, dur = nxt
                pending_f[ex.submit(see_one, r, src, dur, v, bank, drive, str(tdir), groups, dstills.get(r["rel_path"]))] = k
            if not pending_f:
                break
            fin, _ = cf.wait(pending_f, return_when=cf.FIRST_COMPLETED)
            for f in fin:
                k = pending_f.pop(f)
                recs_by[k] = f.result()
                done += 1
                if done % 10 == 0 or done == len(jobs):
                    el = time.time() - t0
                    print("see: %d of %d clips · about %d s left" % (done, len(jobs), el / done * (len(jobs) - done)), flush=True)
    recs = []
    for k, (g, r) in enumerate(todo):
        rec, fv = recs_by[k]
        src = (root / r["rel_path"]) if root else None
        if a.pass_ == "overnight" and rec.get("status") == "seen" and src is not None and src.is_file():
            rec = overnight(rec, src, r, v, base, groups, ticked)
        rec["group"] = g
        rec["transcribe_tag"] = r.get("transcribe") or ""
        recs.append(rec)
        vecs.update(fv)
    np = sm.np_()
    when = p0_env.stamp()
    out_js = p0_env.out_path(catalog, drive, "P3", "see", "segments", 5, "json", when)
    out_np = p0_env.out_path(catalog, drive, "P3", "see", "vectors", 6, "npz", when)
    ids = list(vecs)
    face_seg = [s for s in ids for _ in vecs[s]["faces"]]
    face_vec = [f for s in ids for f in vecs[s]["faces"]]
    with open(out_np, "wb") as f:
        np.savez(f, ids=np.array(ids), fp=np.stack([vecs[i]["fp"] for i in ids]) if ids else np.zeros((0, 1), "float32"),
                 tv=np.stack([vecs[i]["tv"] for i in ids]) if ids else np.zeros((0, 1), "float32"),
                 face_seg=np.array(face_seg), face_vec=np.stack(face_vec) if face_vec else np.zeros((0, 128), "float32"))
    doc = {"drive": drive, "made": dt.datetime.now().isoformat(timespec="seconds"), "ledger": ledger.name, "pass": a.pass_,
           "see_hash": h, "vectors": out_np.name, "only": a.only or "", "max_clips": a.max_clips,
           "choose": v["choose"], "groups": groups, "pending_elsewhere": len(pending), "clips": recs}
    with open(out_js, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    nseg = sum(len(c["segments"]) for c in recs)
    print("see: done · %d clips (%d reused) · %d segments · %d pending · %.0f s -> %s" % (
        len(recs), reused, nseg, sum(1 for c in recs if c["status"] != "seen"), time.time() - t0, out_js.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
