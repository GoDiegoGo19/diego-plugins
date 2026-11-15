#!/usr/bin/env python3
"""pool_frames.py — POOL-SHEET MODE by formula (v8). Idempotent; never crashes on a bad file.

One scene per clip (SM01, SM02 ... in sorted path order), one shot per keyframe
(SM01-HEAD / -MID / -TAIL, the FULL label; duplicate labels in rules are rejected). Keyframe times are fractions of the clip duration from rules.json
`keyframes` (default HEAD 0.15, MID 0.60, TAIL 0.85 when duration >= 12 s).

Usage:
  python3 pool_frames.py <clip files or folders...> --state WATCH_STATE.json
         [--rules rules.json] [--frames-dir DIR] [--job NAME] [--force] [--allow-mixed]
Frames dir defaults to <state dir>/frames; frame files are <scene>_<LABEL>_<sha1(src)[:8]>_<t ms>.jpg.
`frame` and `path` in the state are ABSOLUTE; relative ones (other writers) resolve from the state's dir.
A state that fails to parse aborts (exit 2) and is never replaced; a non-"pool" state needs --allow-mixed.
Rerun re-pulls shots whose keyframe time/label changed (snd_* kept for surviving labels; when the frame
changed: machine/macros dropped, verified=False, semantic_check="not_run", desc moved to desc_stale);
--force re-pulls everything. When the -ss 0 fallback fires, t/tc_in/tc_out record the time actually pulled
(0.0), t_want the asked-for one, seek_fallback is true.
Clips yielding no frame (after an -ss 0 retry) or with no video stream go to `skipped`.
Prints one JSON summary line. Deps: ffmpeg, ffprobe.
"""
import sys, os, re, json, subprocess, hashlib

EXTS = (".mov", ".mp4", ".mxf", ".m4v", ".avi", ".mts")
DEFAULT_KF = {"points": [["HEAD", 0.15], ["MID", 0.60]], "tail": ["TAIL", 0.85], "tail_min_s": 12}

def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default

def positional():
    flags = {"--state", "--rules", "--frames-dir", "--job"}
    out, skip = [], False
    for a in sys.argv[1:]:
        if skip: skip = False; continue
        if a in flags: skip = True; continue
        if a.startswith("--"): continue
        out.append(a)
    return out

def collect(paths):
    files, skipped = [], []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, names in os.walk(p):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for n in names:
                    if not n.startswith(".") and n.lower().endswith(EXTS):
                        files.append(os.path.abspath(os.path.join(root, n)))
        elif os.path.isfile(p):
            n = os.path.basename(p)
            if n.startswith("."): skipped.append({"file": p, "reason": "hidden file"})
            elif n.lower().endswith(EXTS): files.append(os.path.abspath(p))
            else: skipped.append({"file": p, "reason": "not a video extension"})
        else:
            skipped.append({"file": p, "reason": "not found"})
    return sorted(set(files)), skipped

def keyframes(rules, dur):
    kf = dict(DEFAULT_KF); kf.update((rules or {}).get("keyframes") or {})
    pts = [list(p) for p in kf["points"]]
    tail = kf.get("tail")
    if tail and dur >= float(kf.get("tail_min_s", 12)): pts.append(list(tail))
    return [(str(l), round(dur * float(f), 2)) for l, f in pts]

def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
                       capture_output=True, text=True, timeout=60)
    d = float(json.loads(r.stdout)["format"]["duration"])
    if d <= 0: raise ValueError("zero duration")
    return d

def die(msg):
    sys.stderr.write("pool_frames: %s\n" % msg); sys.exit(2)

class Unreadable(Exception):
    pass

def has_video(path):
    """True/False for a readable file; Unreadable for a zero-byte or corrupt one (never 'audio-only')."""
    if os.path.getsize(path) == 0: raise Unreadable("zero-byte file")
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                        "stream=codec_type", "-of", "json", path], capture_output=True, text=True, timeout=60)
    if r.returncode != 0: raise Unreadable((r.stderr or "ffprobe failed").strip().splitlines()[-1][:120])
    try: return bool(json.loads(r.stdout or "{}").get("streams"))
    except ValueError: raise Unreadable("ffprobe output unparseable")

def labels_of(rules):
    kf = dict(DEFAULT_KF); kf.update((rules or {}).get("keyframes") or {})
    labs = [str(p[0]) for p in kf["points"]]
    if kf.get("tail"): labs.append(str(kf["tail"][0]))
    seen = set()
    for l in labs:
        if l.upper() in seen: die("duplicate keyframe label %r in rules" % l)
        seen.add(l.upper())

def pull(src, t, jpg):
    """Pull one frame at t, retry at 0. Returns (ok, used_fallback)."""
    for ss, fb in ((t, False), (0, True)):
        if os.path.exists(jpg): os.remove(jpg)
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-ss", str(ss), "-i", src, "-frames:v", "1",
                        "-vf", "scale=640:-2", jpg], capture_output=True, text=True)
        if os.path.exists(jpg) and os.path.getsize(jpg) > 0: return True, fb
        if os.path.exists(jpg): os.remove(jpg)
    return False, False

def new_state(job):
    return {"job": job or "", "seq": "101", "lens": "", "mode": "pool", "video": None, "probe": {},
            "macro": {}, "scenes": [], "frames": {}, "coverage": [], "deliverables": []}

def main():
    sp = arg("--state")
    inputs = positional()
    if not sp or not inputs:
        print(__doc__); return
    sp = os.path.abspath(sp); sdir = os.path.dirname(sp)
    rules = {}
    rp = arg("--rules")
    if rp is None:
        cand = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rules.json")
        rp = cand if os.path.exists(cand) else None
    if rp:
        try: rules = json.load(open(rp))
        except Exception: rules = {}
    labels_of(rules)
    try:
        with open(sp) as fh: s = json.load(fh)
    except FileNotFoundError:
        s = new_state(arg("--job"))
    except Exception as e:
        die("state %s does not parse (%s); refusing to replace it" % (sp, e))
    if not isinstance(s, dict) or not isinstance(s.get("scenes", []), list):
        die("state %s is not a watch state object; refusing to replace it" % sp)
    if s.get("mode") != "pool" and "--allow-mixed" not in sys.argv:
        die("state %s has mode %r, not 'pool'; pass --allow-mixed to add pool scenes anyway" % (sp, s.get("mode")))
    s.setdefault("scenes", [])
    fdir = os.path.abspath(arg("--frames-dir", os.path.join(sdir, "frames"))); os.makedirs(fdir, exist_ok=True)
    if arg("--job") and not s.get("job"): s["job"] = arg("--job")
    if "--allow-mixed" not in sys.argv: s["mode"] = "pool"
    force = "--force" in sys.argv
    files, skipped = collect(inputs)
    by_path = {}
    for sc in s["scenes"]:
        if sc.get("path"):
            pp = sc["path"] if os.path.isabs(sc["path"]) else os.path.join(sdir, sc["path"])
            by_path[os.path.abspath(pp)] = sc
    used = {sc["id"] for sc in s["scenes"]}
    n_new = n_frames = n_updated = 0
    for i, f in enumerate(files, 1):
        try:
            if not has_video(f):
                skipped.append({"file": f, "reason": "no video stream (audio-only)"}); continue
            dur = duration(f)
        except Unreadable as e:
            skipped.append({"file": f, "reason": "unreadable or corrupt (%s)" % e}); continue
        except Exception as e:
            skipped.append({"file": f, "reason": "ffprobe failed: %s" % (str(e) or type(e).__name__)}); continue
        sc = by_path.get(f)
        if sc is None:
            k = i
            while "SM%02d" % k in used: k += 1
            sc = {"id": "SM%02d" % k, "slug": os.path.splitext(os.path.basename(f))[0],
                  "file": os.path.basename(f), "path": f, "tc_in": 0, "tc_out": round(dur, 2), "shots": []}
            fresh = True
        else:
            fresh = False
            sc["path"] = f; sc["tc_out"] = round(dur, 2)
        h8 = hashlib.sha1(f.encode("utf-8")).hexdigest()[:8]
        want = keyframes(rules, dur)
        want_ids = ["%s-%s" % (sc["id"], l) for l, _ in want]
        have = {sh.get("id"): sh for sh in sc["shots"]}
        new_shots, pulled, fb_fail = [], 0, []
        for (label, t), sid in zip(want, want_ids):
            old = have.get(sid)
            safe = re.sub(r"[^A-Za-z0-9_-]", "-", label)
            jpg = os.path.join(fdir, "%s_%s_%s_%d.jpg" % (sc["id"], safe, h8, int(round(t * 1000))))
            old_t = old.get("t_want", old.get("t")) if old else None   # t_want = asked-for time when a fallback pulled at 0
            same = (old is not None and old.get("src") == f and isinstance(old_t, (int, float))
                    and abs(old_t - t) < 1e-6 and old.get("frame") == jpg)
            ok_disk = os.path.exists(jpg) and os.path.getsize(jpg) > 0
            if same and ok_disk and not force:
                new_shots.append(old); continue
            ok, fb = pull(f, t, jpg)
            if not ok:
                fb_fail.append(t); continue
            pulled += 1
            sh = dict(old) if old else {"id": sid, "type": "", "desc": "", "verified": False,
                                         "semantic_check": "not_run"}
            if old and (not same or force):
                sh.pop("machine", None); sh.pop("macros", None)
            if old and not same:
                # the picture changed: the old verification and description no longer describe it
                sh["verified"] = False; sh["semantic_check"] = "not_run"; sh.pop("witness_flag", None)
                if (sh.get("desc") or "").strip():
                    sh["desc_stale"] = sh["desc"]; sh["desc"] = ""
            at = 0.0 if fb else t        # time actually pulled (the -ss 0 fallback pulls frame 0)
            sh.update({"id": sid, "label": label, "scene": sc["id"], "tc_in": at, "tc_out": at, "t": at,
                       "frame": jpg, "src": f, "file": os.path.basename(f)})
            if fb: sh["seek_fallback"] = True; sh["t_want"] = t
            else: sh.pop("seek_fallback", None); sh.pop("t_want", None)
            new_shots.append(sh)
        if not new_shots:
            skipped.append({"file": f, "reason": "no frame could be pulled (tried t=%s and -ss 0)" %
                            ",".join(str(x) for x in fb_fail)})
            continue
        if fb_fail:
            skipped.append({"file": f, "reason": "frame at %ss failed" % ",".join(str(x) for x in fb_fail)})
        if fresh:
            used.add(sc["id"]); s["scenes"].append(sc); by_path[f] = sc; n_new += 1
        elif pulled:
            n_updated += 1
        sc["shots"] = new_shots
        n_frames += pulled
    s["scenes"].sort(key=lambda x: x["id"])
    tmp = sp + ".tmp"
    with open(tmp, "w") as fh: json.dump(s, fh, indent=2)
    os.replace(tmp, sp)
    print(json.dumps({"state": sp, "clips": len(files), "scenes": len(s["scenes"]), "new_scenes": n_new,
                      "updated_scenes": n_updated,
                      "shots": sum(len(x["shots"]) for x in s["scenes"]), "frames_pulled": n_frames,
                      "skipped": skipped}))

if __name__ == "__main__":
    main()
