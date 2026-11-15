#!/usr/bin/env python3
"""ppf_write_prproj.py: ADAPTER A (tier 1). plan.json [+ base .prproj] -> <project>_v<NNN>.prproj.

  ppf_write_prproj.py plan.json --out DIR [--base USER.prproj] [--objects objects.json] [--multicam]

FIRST CHOICE: our own library `prwrite` (plugins/premiere-project-file-assembly/lib/prwrite,
stdlib only; build.py + probe.py). It is used whenever it imports and --multicam is not asked
for (prwrite does not build multicam clips yet). Set PPF_PRPROJ_ENGINE=py-premiere to force the
fallback. prwrite output runs here, unopened in Premiere.

FALLBACK: the py-premiere VENDORED in this plugin (vendor/py-premiere, py_premiere 0.1.0, MIT,
Pouf/py-premiere @ adb9e57, unpatched). Every call is quoted with file:line in
references/prproj-recipe.md. Order: bins -> import media per bin -> sequences -> clips -> save.
With --base the user's project is parsed and never written (save() goes to a new path and
refuses to overwrite); without --base a fresh project is made with py_premiere.new().
Media must be reachable: py-premiere reads each file's header on import, so run it where the
drive is mounted. Exit 2 with one line when py_premiere cannot be imported.
"""
import argparse, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.normpath(os.path.join(HERE, "../../../vendor/py-premiere"))
LIB = os.path.normpath(os.path.join(HERE, "../../../lib"))
sys.path.insert(0, HERE)
from ppf_common import load_json, save_json, next_version_path, rate_of, conform_fps

py_premiere = None


def load_prwrite():
    """Import our own prwrite from the plugin's lib/ (first choice). None when unavailable."""
    if os.environ.get("PPF_PRPROJ_ENGINE") == "py-premiere":
        return None
    if os.path.isdir(LIB) and LIB not in sys.path:
        sys.path.insert(0, LIB)
    try:
        import prwrite.build as build
        return build
    except ImportError:
        return None


def write_prwrite(plan, objects, base, out_dir):
    """plan.json ops -> .prproj with prwrite. Same ops and output naming as the fallback."""
    from prwrite import build, framing, parse, probe
    from prwrite.document import Doc
    from prwrite.model import Rate, Time
    clips = {c["clip_id"]: c for c in objects["clips"]}
    if base:
        doc = Doc.parse(framing.read_prproj(base))      # the user's file is read, never written
        p = parse.project_from_doc(doc)
    else:
        p, doc = build.new_project(plan["project"])
    bins, items, seqs = {"": p.root}, {}, {}
    log = {"placed": 0, "skipped": []}

    def ensure_bin(path):
        if path in bins:
            return bins[path]
        parent_path, _, name = path.rpartition("/")
        parent = ensure_bin(parent_path)
        b = next((c for c in parent.bins if c.name == name), None)
        if b is None:
            b = build.add_bin(doc, p, parent, name)
        bins[path] = b
        return b

    for op in plan["ops"]:
        if op["op"] == "add_bin":
            ensure_bin(op["path"])
    for op in plan["ops"]:
        if op["op"] != "add_masterclip":
            continue
        i = op["clip"]
        path = clips[i]["path"]
        if not os.path.exists(path):
            log["skipped"].append((i, "media missing")); continue
        try:
            items[i] = build.import_media(doc, p, ensure_bin(op["bin"]), path, probe.probe(path),
                                          project_dir=os.path.abspath(out_dir))
        except (NotImplementedError, ValueError, OSError) as e:
            log["skipped"].append((i, "import: %s: %s" % (type(e).__name__, e)))
    for op in plan["ops"]:
        if op["op"] == "add_sequence":
            rate = Rate.from_fps(str(conform_fps(op.get("fps") or 23.976)[0]))   # standard rate only: whole ticks (G1)
            n = 1 + max([int(o.get("track", "V1")[1:]) - 1 for o in plan["ops"]
                         if o["op"] == "place_clip" and o["seq"] == op["name"]] or [0])
            seqs[op["name"]] = build.add_sequence(doc, p, ensure_bin(op.get("bin", "01_SEQUENCES")),
                                                  op["name"], rate, (op.get("width") or 1920, op.get("height") or 1080),   # VERTICAL: 1080x1920
                                                  video_tracks=max(3, n), audio_tracks=max(3, n))
        elif op["op"] == "place_clip":
            s, mc = seqs.get(op["seq"]), items.get(op["clip"])
            if s is None or mc is None:
                log["skipped"].append((op["clip"], "not placed on %s (sequence or media missing)" % op["seq"])); continue
            out = Time.from_seconds(str(op["out_s"])) if op.get("out_s") is not None else mc.duration
            try:
                build.place(doc, s, int(op.get("track", "V1")[1:]) - 1, mc,
                            Time.from_seconds(str(op["at_s"])), Time.from_seconds(str(op.get("in_s") or 0)),
                            min(out, mc.duration))
                log["placed"] += 1
            except ValueError as e:
                log["skipped"].append((op["clip"], "place: %s" % e))
    path, ver = next_version_path(out_dir, plan["project"], ".prproj")
    if os.path.exists(path):
        raise SystemExit("ppf_write_prproj: %s exists; refusing to overwrite" % path)
    framing.write_prproj(path, doc.serialize(), gzip=True)   # .tmp then os.replace
    save_json(path[:-7] + ".plan.json", dict(plan, version=ver, prproj=os.path.basename(path), base=base,
                                             engine="prwrite"))
    q = parse.project_from_doc(Doc.parse(framing.read_prproj(path)))   # verify from the SAVED file
    names = {s.name for s in q.sequences}
    want = [o["name"] for o in plan["ops"] if o["op"] == "add_sequence"]
    miss = [w for w in want if w not in names]
    if log["skipped"]:                 # one line per reason, not one per file
        by = {}
        for i, why in log["skipped"]:
            by.setdefault(why.split(":")[0].strip(), []).append(os.path.basename(str(i)))
        for why, names in by.items():
            print("prproj: SKIP %d file(s), %s: %s" % (len(names), why, ", ".join(names[:6]) + (" ..." if len(names) > 6 else "")))
    print("prproj[prwrite]: wrote %s; sequences %d/%d on reparse; clips placed %d; media imported %d/%d"
          % (path, len(want) - len(miss), len(want), log["placed"], len(items), len(clips)))
    if miss:
        print("prproj: FAIL missing after reparse: %s" % ", ".join(miss)); sys.exit(1)
    return path


def load_lib():
    """Import the vendored py_premiere (or an installed one). Returns the module or exits 2."""
    global py_premiere
    if py_premiere is not None:
        return py_premiere
    if os.path.isdir(VENDOR):
        sys.path.insert(0, VENDOR)
    try:
        import py_premiere as lib
        from py_premiere.models import media_import
    except ImportError as e:
        print("ppf_write_prproj: py_premiere cannot be imported (%s); vendored copy expected at %s; nothing written" % (e, VENDOR))
        sys.exit(2)
    # Engine v2 gotcha: TIMECODE_FORMATS knows only 25/29.97/30 (media_import.py:70); add
    # 23.976 and 24 so importing those movies does not raise. In-memory only; the file is untouched.
    media_import.TIMECODE_FORMATS.setdefault((10594584000, False), 110)
    media_import.TIMECODE_FORMATS.setdefault((10584000000, False), 100)
    py_premiere = lib
    return lib


# sequence_builder.py:212 FORMATS names that exist at adb9e57 (no 24/30/60 progressive presets there)
PRESET_BY_FPS = {23.976: "1080p23976", 24.0: "1080p23976", 25.0: "1080p25", 29.97: "1080p2997",
                 30.0: "1080p2997", 50.0: "1080p50", 59.94: "1080p5994", 60.0: "1080p5994"}


def T(seconds):
    from py_premiere.models.time import Time               # time.py:85 Time.from_seconds
    return Time.from_seconds(float(seconds))


def child(node, name):
    for c in node.children:                                 # project_item.py:822 children
        if c.name == name:                                  # project_item.py:572 name
            return c
    return None


def ensure_bin(p, path, cache):
    if path in cache:
        return cache[path]
    parent_path, _, name = path.rpartition("/")
    parent = ensure_bin(p, parent_path, cache) if parent_path else p.root_item   # project.py:240
    b = child(parent, name)
    if b is None:                       # `is None`, never `not b`: an empty bin is falsy (engine v2 gotcha)
        b = parent.add_bin(name)        # project_item.py:603 add_bin(name) -> ProjectItem
    cache[path] = b
    return b


def write(plan, objects, base, out_dir, multicam=False):
    if not multicam and load_prwrite() is not None:
        return write_prwrite(plan, objects, base, out_dir)
    lib = load_lib()
    clips = {c["clip_id"]: c for c in objects["clips"]}
    app = lib.parse(base) if base else lib.new()            # __init__.py:29 parse(path) / :49 new()
    p = app.project                                         # application.py:18 project
    bins, items, seqs, log = {}, {}, {}, {"placed": 0, "skipped": [], "multicams": 0}
    pending = {}
    for op in plan["ops"]:
        if op["op"] == "add_bin":
            ensure_bin(p, op["path"], bins)
        elif op["op"] == "add_masterclip":
            pending.setdefault(op["bin"], []).append(op["clip"])
    for b, ids in pending.items():
        target = ensure_bin(p, b, bins)
        for i in ids:                   # one file per call: an unsupported codec skips one clip, not the bin
            path = clips[i]["path"]
            if not os.path.exists(path):
                log["skipped"].append((i, "media missing")); continue
            try:
                got = p.import_files([path], target)        # project.py:306 import_files(paths, target_bin) -> list
            except (NotImplementedError, ValueError, KeyError, OSError) as e:
                log["skipped"].append((i, "import: %s: %s" % (type(e).__name__, e))); continue
            if got:
                items[i] = got[0]
    groups = {}
    for op in plan["ops"]:
        if op["op"] == "add_sequence":
            tracks = 1 + max([int(o.get("track", "V1")[1:]) - 1 for o in plan["ops"]
                              if o["op"] == "place_clip" and o["seq"] == op["name"]] or [0])
            preset = PRESET_BY_FPS.get(conform_fps(op.get("fps") or 23.976)[0], "1080p23976")
            s = p.add_sequence(op["name"], preset=preset, video_tracks=max(3, tracks))   # project.py:602
            if s.project_item is not None:                  # sequence.py:388 project_item
                s.project_item.move_to(ensure_bin(p, op.get("bin", "01_SEQUENCES"), bins))  # project_item.py:687
            seqs[op["name"]] = s
        elif op["op"] == "place_clip":
            s, it = seqs.get(op["seq"]), items.get(op["clip"])
            if s is None or it is None:
                log["skipped"].append((op["clip"], "not placed on %s (sequence or media missing)" % op["seq"])); continue
            n = int(op.get("track", "V1")[1:]) - 1
            c = clips[op["clip"]]
            halves = [s.video_tracks[n]]                    # sequence.py:630 video_tracks
            if c.get("audio_channels") and len(s.audio_tracks) > n:
                halves.append(s.audio_tracks[n])           # sequence.py:635 audio_tracks; link not written [D]
            for tr in halves:
                ti = tr.add_clip(it, start=T(op["at_s"]))   # track.py:842 add_clip(project_item, start=Time)
                full = c.get("duration_s") or 0
                if op.get("in_s") or (op.get("out_s") is not None and op["out_s"] < full - 0.001):
                    ti.in_point = T(op.get("in_s", 0))      # track_item.py:595 in_point setter
                    ti.out_point = T(op["out_s"])           # track_item.py:605 out_point setter
            log["placed"] += 1
            if "_SYNC_" in op["seq"]:
                groups.setdefault(op["seq"], []).append((op["at_s"], it))
    if multicam:
        # project.py:1024 create_multicam_clip(items, name): syncs at clip STARTS and needs exactly
        # one audio angle; only build it when every angle in the TC group starts at offset 0.
        for name, g in groups.items():
            if len(g) >= 2 and all(at == 0 for at, _ in g):
                try:
                    mc = p.create_multicam_clip([it for _, it in g], name=name + "_MC")
                    mc.move_to(ensure_bin(p, "01_SEQUENCES", bins)); log["multicams"] += 1
                except ValueError as e:
                    log["skipped"].append((name, "multicam: %s" % e))
            else:
                log["skipped"].append((name, "multicam not built: angles start at different TC (sync sequence keeps the offsets)"))
    path, ver = next_version_path(out_dir, plan["project"], ".prproj")
    p.save(path)                                            # project.py:1642 save(path): refuses overwrite, atomic
    save_json(path[:-7] + ".plan.json", dict(plan, version=ver, prproj=os.path.basename(path), base=base))
    q = lib.parse(path).project                             # verify from the SAVED file
    names = {s.name for s in q.sequences}                   # project.py:261 sequences
    want = [o["name"] for o in plan["ops"] if o["op"] == "add_sequence"]
    miss = [w for w in want if w not in names]
    if log["skipped"]:                 # one line per reason, not one per file
        by = {}
        for i, why in log["skipped"]:
            by.setdefault(why.split(":")[0].strip(), []).append(os.path.basename(str(i)))
        for why, names in by.items():
            print("prproj: SKIP %d file(s), %s: %s" % (len(names), why, ", ".join(names[:6]) + (" ..." if len(names) > 6 else "")))
    print("prproj: wrote %s; sequences %d/%d on reparse; clips placed %d; media imported %d/%d; multicams %d"
          % (path, len(want) - len(miss), len(want), log["placed"], len(items), len(clips), log["multicams"]))
    if miss:
        print("prproj: FAIL missing after reparse: %s" % ", ".join(miss)); sys.exit(1)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan"); ap.add_argument("--out", required=True)
    ap.add_argument("--base", help="a .prproj Premiere wrote (the user's project); omitted = py_premiere.new()")
    ap.add_argument("--objects"); ap.add_argument("--multicam", action="store_true")
    a = ap.parse_args(argv)
    plan = load_json(a.plan)
    return write(plan, load_json(a.objects or plan["objects"]), a.base, a.out, a.multicam)


if __name__ == "__main__":
    main()
