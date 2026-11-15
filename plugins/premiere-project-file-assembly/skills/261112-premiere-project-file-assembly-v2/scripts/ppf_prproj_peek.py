#!/usr/bin/env python3
"""ppf_prproj_peek.py: look inside a .prproj with the standard library only.

  ppf_prproj_peek.py FILE.prproj [--names]

A .prproj is gzip-compressed XML: one <PremiereData> root holding a flat list of
objects linked by ObjectID / ObjectRef / ObjectUID. Prints the gzip OS byte, the
root, counts of top-level element names, how many Sequence and bin items it holds,
and (with --names) every <Name> under a Sequence or a bin. Never writes.
"""
import argparse, collections, gzip, sys
import xml.etree.ElementTree as ET


def peek(path, names=False):
    raw = open(path, "rb").read()
    if raw.startswith(b"version https://git-lfs"):
        print("file: %s is a Git LFS pointer stub (%d bytes), not a project; fetch the real file" % (path, len(raw)))
        return collections.Counter()
    gz = raw[:2] == b"\x1f\x8b"
    print("file: %s (%d bytes, gzip=%s, os_byte=%s)" % (path, len(raw), gz, raw[9] if gz else "-"))
    xml = gzip.decompress(raw) if gz else raw
    root = ET.fromstring(xml)
    print("root: <%s> version=%s, %d top-level elements, xml %d bytes" % (root.tag, root.get("Version"), len(root), len(xml)))
    c = collections.Counter(e.tag for e in root)
    for tag, n in c.most_common():
        print("  %6d  %s" % (n, tag))
    if names:
        for e in root:
            if e.tag in ("Sequence", "BinProjectItem", "ClipProjectItem"):
                nm = e.find(".//Name")
                print("  %-16s %s" % (e.tag, nm.text if nm is not None else ""))
    return c


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prproj"); ap.add_argument("--names", action="store_true")
    a = ap.parse_args()
    peek(a.prproj, a.names)
