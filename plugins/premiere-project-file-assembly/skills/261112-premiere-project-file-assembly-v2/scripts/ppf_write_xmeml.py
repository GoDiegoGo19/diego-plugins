#!/usr/bin/env python3
"""ppf_write_xmeml.py: plan.json + objects.json -> <project>_v<NNN>.xml (FCP7 xmeml v5).

  ppf_write_xmeml.py plan.json --out DIR [--objects objects.json] [--path-map /old=/Volumes/new]

Premiere imports this file as: bins, master clips, sequences with clips on tracks.
Never overwrites: picks the next free version number in DIR and saves the plan
next to it as <project>_v<NNN>.plan.json. Element shapes: references/xmeml-shape.md.
"""
import argparse, os, sys, uuid
import xml.etree.ElementTree as ET
from urllib.parse import quote
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ppf_common import load_json, save_json, rate_of, real_fps, next_version_path, short_id

NS_UUID = uuid.UUID("6f1d2b1e-6a51-4b8e-9d1f-2b6112abc000")   # fixed: same plan -> same uuids


def stable_uuid(*parts):
    return str(uuid.uuid5(NS_UUID, "|".join(parts)))


def sub(parent, tag, text=None, **attrs):
    e = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        e.text = str(text)
    return e


def rate_el(parent, fps, seq=False):
    tb, ntsc = rate_of(fps, seq)
    r = sub(parent, "rate"); sub(r, "timebase", tb); sub(r, "ntsc", "TRUE" if ntsc else "FALSE")
    return tb, ntsc


def pathurl(path, path_map):
    for old, new in path_map:
        if path.startswith(old):
            path = new + path[len(old):]
            break
    return "file://localhost" + quote(path, safe="/:")


def frames(seconds, fps):
    tb, ntsc = rate_of(fps)          # a clip's own rate (slo-mo stays 120); sequence fps are standard already
    return int(round(seconds * real_fps(tb, ntsc)))


class Writer:
    def __init__(self, plan, objects, path_map):
        self.plan, self.path_map = plan, path_map
        self.clips = {c["clip_id"]: c for c in objects["clips"]}
        self.root = ET.Element("xmeml", version="5")
        proj = sub(self.root, "project"); sub(proj, "name", plan["project"])
        self.children = sub(proj, "children")
        self.bins = {"": self.children}
        self.seqs = {}
        self.clipitem_n = 0

    def ids(self, clip_id):
        k = self.clips[clip_id]["xml_key"] if clip_id in self.clips else short_id(clip_id)
        return "masterclip-" + k, "file-" + k

    # ---- bins
    def add_bin(self, path):
        if path in self.bins:
            return self.bins[path]
        parent_path, _, name = path.rpartition("/")
        parent = self.add_bin(parent_path) if parent_path else self.children
        b = sub(parent, "bin"); sub(b, "uuid", stable_uuid("bin", path)); sub(b, "name", name)
        self.bins[path] = sub(b, "children")
        return self.bins[path]

    # ---- files
    def file_el(self, parent, clip_id):
        c = self.clips[clip_id]
        _, fid = self.ids(clip_id)
        f = sub(parent, "file", id=fid)    # written in full here; finish() keeps only the first in document order
        fps = c.get("fps") or 25
        sub(f, "name", c["clip"]); sub(f, "pathurl", pathurl(c["path"], self.path_map))
        tb, _ = rate_el(f, fps)
        sub(f, "duration", frames(c.get("duration_s") or 0, fps))
        tc = sub(f, "timecode"); rate_el(tc, fps)
        sub(tc, "string", c.get("tc_start") or "00:00:00:00")
        from ppf_common import tc_to_frames
        sub(tc, "frame", tc_to_frames(c.get("tc_start") or "00:00:00:00", tb) or 0)
        sub(tc, "displayformat", "DF" if ";" in (c.get("tc_start") or "") else "NDF")
        m = sub(f, "media")
        if c["kind"] not in ("audio",):
            v = sub(m, "video"); sc = sub(v, "samplecharacteristics"); rate_el(sc, fps)
            sub(sc, "width", c.get("width") or 1920); sub(sc, "height", c.get("height") or 1080)
            sub(sc, "pixelaspectratio", "square")
        if c.get("audio_channels"):
            a = sub(m, "audio"); sc = sub(a, "samplecharacteristics")
            sub(sc, "depth", 16); sub(sc, "samplerate", 48000)
            sub(a, "channelcount", c["audio_channels"])
        return f

    # ---- master clips
    def add_masterclip(self, clip_id, bin_path):
        c = self.clips[clip_id]
        mid, _ = self.ids(clip_id)
        fps = c.get("fps") or 25
        dur = frames(c.get("duration_s") or 0, fps)
        el = sub(self.add_bin(bin_path), "clip", id=mid)
        sub(el, "uuid", stable_uuid("clip", clip_id)); sub(el, "masterclipid", mid)
        sub(el, "ismasterclip", "TRUE"); sub(el, "duration", dur); rate_el(el, fps)
        sub(el, "name", c["clip"])
        lab = sub(el, "labels"); sub(lab, "label2", {"interview": "Iris", "broll": "Forest"}.get(c["kind"], "Lavender"))
        sub(sub(el, "comments"), "mastercomment1", clip_id)     # traceable to objects.json
        media = sub(el, "media")
        kinds = ([] if c["kind"] == "audio" else ["video"]) + (["audio"] * min(c.get("audio_channels") or 0, 2))
        for i, mt in enumerate(kinds):
            tr = sub(sub(media, mt) if media.find(mt) is None else media.find(mt), "track")
            ci = sub(tr, "clipitem", id="%s-%s%d" % (mid, mt[0], i))
            sub(ci, "masterclipid", mid); sub(ci, "name", c["clip"]); rate_el(ci, fps)
            sub(ci, "start", 0); sub(ci, "end", dur); sub(ci, "in", 0); sub(ci, "out", dur)
            self.file_el(ci, clip_id)
            if mt == "audio":
                st = sub(ci, "sourcetrack"); sub(st, "mediatype", "audio"); sub(st, "trackindex", i if kinds[0] == "video" else i + 1)

    # ---- sequences
    def add_sequence(self, op):
        name = op["name"]
        el = sub(self.add_bin(op.get("bin", "01_SEQUENCES")), "sequence", id="sequence-" + short_id("seq|" + name))
        sub(el, "uuid", stable_uuid("seq", name)); sub(el, "name", name)
        dur = sub(el, "duration", 0); rate_el(el, op.get("fps", 23.976), True)     # sequence: always standard (G8)
        tc = sub(el, "timecode"); rate_el(tc, op.get("fps", 23.976), True)
        sub(tc, "string", "00:00:00:00"); sub(tc, "frame", 0); sub(tc, "displayformat", "NDF")
        media = sub(el, "media"); video = sub(media, "video")
        fmt = sub(video, "format"); sc = sub(fmt, "samplecharacteristics"); rate_el(sc, op.get("fps", 23.976), True)
        sub(sc, "width", op.get("width", 1920)); sub(sc, "height", op.get("height", 1080))
        sub(sc, "pixelaspectratio", "square")
        audio = sub(media, "audio")
        self.seqs[name] = {"el": el, "fps": op.get("fps", 23.976), "dur": dur, "video": video, "audio": audio,
                           "vt": {}, "at": {}, "end": 0}

    def track(self, s, kind, n):
        key = "vt" if kind == "video" else "at"
        while len(s[key]) < n:
            s[key][len(s[key]) + 1] = sub(s[kind], "track")
        return s[key][n]

    def place_clip(self, op):
        s = self.seqs[op["seq"]]; c = self.clips[op["clip"]]
        mid, _ = self.ids(op["clip"])
        fps = s["fps"]
        start = frames(op["at_s"], fps); fin = frames(op.get("in_s", 0), fps)
        fout = frames(op.get("out_s", c.get("duration_s") or 0), fps)
        end = start + (fout - fin)
        vno = int(op.get("track", "V1")[1:])
        nch = min(c.get("audio_channels") or 0, 2)
        items = [("video", vno, 0)] + [("audio", (vno - 1) * 2 + k + 1, k + 1) for k in range(nch)]
        ids = []
        for mt, tno, _ in items:
            self.clipitem_n += 1
            ids.append("clipitem-%d" % self.clipitem_n)
        made = []
        for (mt, tno, src), cid in zip(items, ids):
            tr = self.track(s, mt, tno)
            ci = sub(tr, "clipitem", id=cid)
            sub(ci, "masterclipid", mid); sub(ci, "name", c["clip"]); sub(ci, "enabled", "TRUE")
            sub(ci, "duration", frames(c.get("duration_s") or 0, fps)); rate_el(ci, fps, True)   # placed by seconds at the sequence rate
            sub(ci, "start", start); sub(ci, "end", end); sub(ci, "in", fin); sub(ci, "out", fout)
            self.file_el(ci, op["clip"])
            if mt == "audio":
                st = sub(ci, "sourcetrack"); sub(st, "mediatype", "audio"); sub(st, "trackindex", src)
            made.append((ci, mt, tno, len(list(tr))))
        if len(made) > 1:                      # link video + its audio so they move together
            for ci, _, _, _ in made:
                for (_, lmt, ltno, lidx), lid in zip(made, ids):
                    ln = sub(ci, "link"); sub(ln, "linkclipref", lid)
                    sub(ln, "mediatype", lmt); sub(ln, "trackindex", ltno); sub(ln, "clipindex", lidx)
        s["end"] = max(s["end"], end); s["dur"].text = str(s["end"])

    def run(self):
        for op in self.plan["ops"]:
            k = op["op"]
            if k == "add_bin":
                self.add_bin(op["path"])
            elif k == "add_masterclip":
                self.add_masterclip(op["clip"], op["bin"])
            elif k == "add_sequence":
                self.add_sequence(op)
            elif k == "place_clip":
                self.place_clip(op)
            else:
                raise SystemExit("ppf_write_xmeml: unknown op %r (apply revise ops first)" % k)
        return self.finish()

    def finish(self):
        """FCP7 rule: a file is defined in full once, at its FIRST occurrence in document order;
        every later <file id="..."/> is a bare reference."""
        seen = set()
        for f in self.root.iter("file"):
            if f.get("id") in seen:
                for ch in list(f):
                    f.remove(ch)
            seen.add(f.get("id"))
        return self.root


def write(plan, objects, out_dir, path_map=()):
    root = Writer(plan, objects, list(path_map)).run()
    ET.indent(root)
    path, n = next_version_path(out_dir, plan["project"], ".xml")
    body = ET.tostring(root, encoding="unicode")
    with open(path, "x", encoding="utf-8") as fh:          # "x": refuse to overwrite, ever
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + body + "\n")
    plan_path = path[:-4] + ".plan.json"
    plan = dict(plan, version=n, xml=os.path.basename(path))
    save_json(plan_path, plan)
    return path, plan_path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan"); ap.add_argument("--out", required=True)
    ap.add_argument("--objects", help="objects.json (default: the path recorded in the plan)")
    ap.add_argument("--path-map", action="append", default=[], help="OLD=NEW path prefix swap, e.g. /home/x/DRIVE=/Volumes/DRIVE")
    a = ap.parse_args(argv)
    plan = load_json(a.plan)
    objects = load_json(a.objects or plan["objects"])
    pm = [tuple(x.split("=", 1)) for x in a.path_map]
    path, plan_path = write(plan, objects, a.out, pm)
    print("write: %s (+ %s)" % (path, os.path.basename(plan_path)))
    return path


if __name__ == "__main__":
    main()
