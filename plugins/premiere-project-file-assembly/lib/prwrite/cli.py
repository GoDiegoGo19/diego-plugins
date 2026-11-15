"""prwrite.cli: `python3 -m prwrite inspect|compare|validate|peek FILE ...`

In:  a .prproj (gzip or plain) or an FCP7 .xml (xmeml) path on the command line.
Out: a plain-text report on stdout (`--json` for JSON) and an exit code.

  inspect FILE     bins, master clips, sequences and counts. .prproj goes through
                   framing -> document -> parse (owners A/B) into a model.Project;
                   .xml is read with xml.etree.
  compare A B      object counts per kind, and names added/removed from A to B.
  validate FILE    xmeml rules from Rosetta v7 (see `validate`): exit 0 clean, 3 failed,
                   4 could not read. The codes follow state-schema.md R9's grammar
                   (0 PASS / 3 FAIL / 4 REFUSED; 1 stays an escaped exception).
  peek FILE        gunzip if needed + counts of the root's direct children by tag.
                   Stdlib only; never builds the model; streams, so big files are fine.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from urllib.parse import unquote, urlparse

EXIT_PASS, EXIT_FAIL, EXIT_REFUSED = 0, 3, 4   # state-schema.md R9

GZIP_MAGIC = b"\x1f\x8b"


# ------------------------------------------------------------------ reading

def _open(path):
    """Binary stream, transparently gunzipped (peek: no framing module needed)."""
    with open(path, "rb") as fh:
        magic = fh.read(2)
    return gzip.open(path, "rb") if magic == GZIP_MAGIC else open(path, "rb")


def _is_xmeml(path) -> bool:
    with _open(path) as fh:
        head = fh.read(4096).decode("utf-8", "replace")
    return "<xmeml" in head


def _load_project(path):
    """.prproj -> model.Project via owners A/B. Raises ImportError until they land."""
    from .document import Doc
    from .framing import read_prproj
    from .parse import project_from_doc
    return project_from_doc(Doc.parse(read_prproj(path)))


# ------------------------------------------------------------------ peek

def peek(path) -> dict:
    """Root tag/attributes and counts of its direct children by tag, streaming."""
    counts: Counter = Counter()
    depth = 0
    root_tag, root_attrs = None, {}
    with _open(path) as fh:
        for ev, el in ET.iterparse(fh, events=("start", "end")):
            if ev == "start":
                depth += 1
                if depth == 1:
                    root_tag, root_attrs = el.tag, dict(el.attrib)
                elif depth == 2:
                    counts[el.tag] += 1
            else:
                depth -= 1
                if depth == 1:
                    el.clear()          # keep memory flat on 600-clip projects
    return {"file": path, "root": root_tag, "attrs": root_attrs,
            "children": sum(counts.values()), "by_tag": dict(sorted(counts.items()))}


# ------------------------------------------------------------------ summaries

def _xmeml_summary(path) -> dict:
    with _open(path) as fh:
        root = ET.parse(fh).getroot()
    bins, clips, seqs = [], [], []

    def walk(children, trail):
        for el in children:
            name = el.findtext("name") or ""
            if el.tag == "bin":
                bins.append("/".join(trail + [name]))
                walk(el.find("children") if el.find("children") is not None else [], trail + [name])
            elif el.tag == "clip":
                clips.append(name)
            elif el.tag == "sequence":
                seqs.append(name)

    proj = root.find("project")
    if proj is not None:
        walk(proj.find("children") if proj.find("children") is not None else [], [])
    for el in root:                        # xmeml may also carry top-level clips/sequences
        if el.tag in ("clip", "sequence", "bin"):
            walk([el], [])
    seq_items = {s.findtext("name") or "": len(s.findall("./media//track/clipitem"))
                 for s in root.iter("sequence")}
    return {"format": "xmeml", "version": root.get("version"),
            "name": (proj.findtext("name") if proj is not None else None),
            "bins": bins, "clips": clips, "sequences": seqs,
            "files": len({f.get("id") for f in root.iter("file")}),
            "sequence_clipitems": seq_items}


def _project_summary(p) -> dict:
    bins = ["/".join(path) for path, _ in p.root.walk() if path]
    clips = [c.name for c in p.clips.values() if not c.sequence_uid]
    seq_items = {s.name: sum(len(t.items) for t in s.video_tracks + s.audio_tracks)
                 for s in p.sequences}
    return {"format": "prproj", "name": p.name, "bins": bins, "clips": clips,
            "sequences": [s.name for s in p.sequences], "files": len(p.media),
            "sequence_clipitems": seq_items}


def summarize(path) -> dict:
    """Name lists per kind for an xmeml or .prproj file."""
    if _is_xmeml(path):
        return _xmeml_summary(path)
    try:
        return _project_summary(_load_project(path))
    except ImportError as exc:
        raise SystemExit("prwrite: .prproj needs framing/document/parse (not importable: %s); "
                         "try `peek`" % exc)


def compare(a, b) -> dict:
    sa, sb = summarize(a), summarize(b)
    out = {"a": a, "b": b, "kinds": {}}
    for kind in ("bins", "clips", "sequences"):
        ca, cb = Counter(sa[kind]), Counter(sb[kind])
        out["kinds"][kind] = {"a": len(sa[kind]), "b": len(sb[kind]),
                              "added": sorted((cb - ca).elements()),
                              "removed": sorted((ca - cb).elements())}
    out["kinds"]["files"] = {"a": sa["files"], "b": sb["files"]}
    return out


# ------------------------------------------------------------------ validate

_TC = re.compile(r"^\d\d[:;]\d\d[:;]\d\d[:;.]\d\d$")


def _finding(code, where, detail, level="error"):
    return {"code": code, "level": level, "where": where, "detail": detail}


def _rate_key(el):
    r = el.find("rate")
    if r is None:
        return None
    return (r.findtext("timebase"), (r.findtext("ntsc") or "FALSE").upper())


def _check_pathurl(url) -> str | None:
    """None if well formed, else why not."""
    if not url:
        return "empty"
    if any(c in url for c in " \t\n\"<>"):
        return "unencoded whitespace or reserved character"
    u = urlparse(url)
    if u.scheme != "file":
        return "scheme is %r, not file" % u.scheme
    if u.netloc not in ("", "localhost"):
        return "host is %r (expected localhost or empty)" % u.netloc
    if not unquote(u.path).startswith("/"):
        return "path is not absolute"
    return None


def validate(source) -> list[dict]:
    """xmeml text, bytes or a path -> findings (empty list = clean).

    Rules (Rosetta v7 ids):
      PC-1.4   root is <xmeml version="5">.
      FL-9 / PC-4R  every <file id> declared in full exactly once, at its first occurrence;
               every bare <file id="x"/> resolves to it; a clipitem without a file is flagged.
      PC-4R    clip, sequence and clipitem ids are unique; masterclipid names a <clip>.
      OR-182   a full file has name or pathurl; pathurl is a well-formed file:// URL.
      OR-868   timecode has string, or frame and displayformat; string looks like HH:MM:SS:FF.
      OR-106   link has linkclipref (resolving to a clipitem) or mediatype+trackindex+clipindex.
      FL-3     every rate has an integer timebase > 0 and ntsc TRUE/FALSE; sequence clipitems
               share the sequence rate; a full file's rate matches its master clip's rate.
      RD-K3    start <= end, in <= out (-1 allowed for start/end: transition-owned edges).
    """
    if isinstance(source, bytes):
        text = source.decode("utf-8")
    elif isinstance(source, str) and source.lstrip().startswith("<"):
        text = source
    else:
        with _open(source) as fh:
            text = fh.read().decode("utf-8")
    root = ET.fromstring(text)
    out: list[dict] = []
    if root.tag != "xmeml":
        return [_finding("ROOT", "/", "root is <%s>, not <xmeml>" % root.tag)]
    if root.get("version") != "5":
        out.append(_finding("VERSION", "/xmeml", "version=%r, expected '5' (PC-1.4)" % root.get("version")))

    parent = {c: p for p in root.iter() for c in p}

    def where(el):
        trail = []
        while el is not None:
            trail.append(el.tag + ("[@id=%s]" % el.get("id") if el.get("id") else ""))
            el = parent.get(el)
        return "/" + "/".join(reversed(trail))

    # files: declare once, refs resolve (FL-9, PC-4R)
    full, seen_ids = {}, set()
    for f in root.iter("file"):
        fid = f.get("id")
        if not fid:
            out.append(_finding("FILE_NO_ID", where(f), "file without id"))
            continue
        if len(f):
            if fid in full:
                out.append(_finding("FILE_DUPLICATE_DECLARATION", where(f),
                                    "file %s declared in full again; first at %s (FL-9)" % (fid, where(full[fid]))))
            else:
                full[fid] = f
        elif fid not in full:
            out.append(_finding("FILE_REF_BEFORE_DECLARATION" if fid in {x.get("id") for x in root.iter("file") if len(x)}
                                else "FILE_DANGLING", where(f), "bare file %s has no earlier full declaration (FL-9)" % fid))
        seen_ids.add(fid)
    for fid, f in full.items():
        if f.find("name") is None and f.find("pathurl") is None:
            out.append(_finding("FILE_NAME_OR_PATHURL", where(f), "neither name nor pathurl (OR-182)"))
        url = f.findtext("pathurl")
        if f.find("pathurl") is not None:
            why = _check_pathurl(url)
            if why:
                out.append(_finding("PATHURL", where(f), "%s: %r" % (why, url)))

    # ids unique (PC-4R); clips may be referenced by bare <clip id/> in principle: count full ones
    for tag in ("clip", "sequence", "clipitem"):
        c = Counter(e.get("id") for e in root.iter(tag) if e.get("id") and len(e))
        for i, n in c.items():
            if n > 1:
                out.append(_finding("ID_DUPLICATE", tag, "%s id %s registered %d times (PC-4R)" % (tag, i, n)))
    clip_ids = {e.get("id") for e in root.iter("clip")}
    clipitem_ids = {e.get("id") for e in root.iter("clipitem")}

    # rates (FL-3)
    for r in root.iter("rate"):
        tb, ntsc = r.findtext("timebase"), (r.findtext("ntsc") or "").upper()
        if not (tb and tb.isdigit() and int(tb) > 0):
            out.append(_finding("RATE_TIMEBASE", where(r), "timebase %r" % tb))
        if ntsc not in ("TRUE", "FALSE"):
            out.append(_finding("RATE_NTSC", where(r), "ntsc %r" % ntsc))
    for s in root.iter("sequence"):
        sk = _rate_key(s)
        for ci in s.findall("./media//track/clipitem"):
            ck = _rate_key(ci)
            if ck is not None and sk is not None and ck != sk:
                out.append(_finding("RATE_MISMATCH", where(ci), "clipitem rate %s != sequence rate %s" % (ck, sk)))
    for clip in root.iter("clip"):
        mk = _rate_key(clip)
        for f in clip.iter("file"):
            if len(f) and mk is not None and _rate_key(f) not in (None, mk):
                out.append(_finding("RATE_MISMATCH", where(f), "file rate %s != master clip rate %s" % (_rate_key(f), mk)))

    # clipitems
    for ci in root.iter("clipitem"):
        files = ci.findall("file")
        if len(files) != 1:
            out.append(_finding("CLIPITEM_FILE", where(ci), "%d <file> children (want 1)" % len(files)))
        mid = ci.findtext("masterclipid")
        if mid and mid not in clip_ids:
            out.append(_finding("MASTERCLIP_DANGLING", where(ci), "masterclipid %s names no <clip>" % mid))
        nums = {}
        for tag in ("start", "end", "in", "out"):
            v = ci.findtext(tag)
            if v is None:
                continue
            try:
                nums[tag] = int(v)
            except ValueError:
                out.append(_finding("FRAMES", where(ci), "%s=%r is not whole frames" % (tag, v)))
        if nums.get("start", -1) >= 0 and nums.get("end", -1) >= 0 and nums["end"] < nums["start"]:
            out.append(_finding("SPAN", where(ci), "end %d < start %d" % (nums["end"], nums["start"])))
        if "in" in nums and "out" in nums and nums["out"] < nums["in"]:
            out.append(_finding("SPAN", where(ci), "out %d < in %d" % (nums["out"], nums["in"])))
        for ln in ci.findall("link"):
            ref = ln.findtext("linkclipref")
            triple = all(ln.find(t) is not None for t in ("mediatype", "trackindex", "clipindex"))
            if ref is None and not triple:
                out.append(_finding("LINK_FORM", where(ln), "neither linkclipref nor mediatype+trackindex+clipindex (OR-106)"))
            if ref is not None and ref not in clipitem_ids:
                out.append(_finding("LINK_DANGLING", where(ln), "linkclipref %s names no clipitem" % ref))

    # timecode (OR-868)
    for tc in root.iter("timecode"):
        s = tc.findtext("string")
        if s is None and (tc.find("frame") is None or tc.find("displayformat") is None):
            out.append(_finding("TIMECODE_FORM", where(tc), "no string and not frame+displayformat (OR-868)"))
        if s is not None and not _TC.match(s):
            out.append(_finding("TIMECODE_STRING", where(tc), "string %r is not HH:MM:SS:FF" % s))
    return out


# ------------------------------------------------------------------ output

def _print_summary(s):
    print("%s  %s%s" % (s["format"], s.get("name") or "", "  (version %s)" % s["version"] if s.get("version") else ""))
    print("bins %d  clips %d  sequences %d  files %d" % (len(s["bins"]), len(s["clips"]), len(s["sequences"]), s["files"]))
    for b in s["bins"]:
        print("  bin   %s" % b)
    for c in s["clips"]:
        print("  clip  %s" % c)
    for q in s["sequences"]:
        print("  seq   %s  (%d clip items)" % (q, s["sequence_clipitems"].get(q, 0)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m prwrite", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="JSON on stdout")
    sp = ap.add_subparsers(dest="cmd", required=True)
    for name in ("inspect", "validate", "peek"):
        sp.add_parser(name).add_argument("file")
    c = sp.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    a = ap.parse_args(argv)

    if a.cmd == "peek":
        try:
            r = peek(a.file)
        except (OSError, ET.ParseError, EOFError) as exc:
            print("peek: cannot read %s: %s" % (a.file, exc), file=sys.stderr)
            return EXIT_REFUSED
        if a.json:
            print(json.dumps(r, indent=1))
        else:
            print("%s  <%s %s>  %d children" % (r["file"], r["root"],
                  " ".join('%s="%s"' % kv for kv in r["attrs"].items()), r["children"]))
            for tag, n in r["by_tag"].items():
                print("  %6d  %s" % (n, tag))
        return EXIT_PASS

    if a.cmd == "inspect":
        s = summarize(a.file)
        print(json.dumps(s, indent=1)) if a.json else _print_summary(s)
        return EXIT_PASS

    if a.cmd == "compare":
        r = compare(a.a, a.b)
        if a.json:
            print(json.dumps(r, indent=1))
        else:
            for kind, d in r["kinds"].items():
                print("%-10s %5d -> %-5d" % (kind, d["a"], d["b"]))
                for n in d.get("added", []):
                    print("  + %s" % n)
                for n in d.get("removed", []):
                    print("  - %s" % n)
        return EXIT_PASS

    # validate
    try:
        findings = validate(a.file)
    except (OSError, ET.ParseError, UnicodeDecodeError, EOFError) as exc:
        print("validate: REFUSED, cannot read %s: %s" % (a.file, exc), file=sys.stderr)
        return EXIT_REFUSED
    if a.json:
        print(json.dumps({"file": a.file, "result": "FAIL" if findings else "PASS", "findings": findings}, indent=1))
    else:
        for f in findings:
            print("%s %s  %s  %s" % (f["level"].upper(), f["code"], f["where"], f["detail"]))
        print("validate: %s (%d findings)" % ("FAIL" if findings else "PASS", len(findings)))
    return EXIT_FAIL if findings else EXIT_PASS
