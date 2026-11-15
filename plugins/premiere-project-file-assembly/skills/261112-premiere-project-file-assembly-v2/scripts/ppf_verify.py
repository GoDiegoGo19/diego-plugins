#!/usr/bin/env python3
"""ppf_verify.py: check a written xmeml against its plan and objects. Prints PASS/FAIL lines.

  ppf_verify.py PROJECT_vNNN.xml [--plan PROJECT_vNNN.plan.json] [--objects objects.json] [--no-rosetta]

Checks: every clip in objects.json is a master clip; sequence count and names match the
plan; clip count per sequence matches the plan; every pathurl resolves to a file; no
duplicate ids. Also runs Rosetta v7's read_xml.py on the file and reports its exit and
trust line. Rosetta reads ONE top sequence (it is built for sequence-level XML), so it
is reported, not used as the gate; the gate is xml.etree below.
"""
import argparse, os, subprocess, sys
import xml.etree.ElementTree as ET
from collections import Counter
from urllib.parse import unquote, urlparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ppf_common import load_json

HERE = os.path.dirname(os.path.abspath(__file__))
ROSETTA = os.path.normpath(os.path.join(HERE, "../../../../read-premiere-xml-rosetta/skills/read-premiere-xml-rosetta/scripts/read_xml.py"))


def verify(xml_path, plan, objects, rosetta=True, workdir=None):
    res = []
    ok = lambda name, cond, detail="": res.append(("PASS" if cond else "FAIL", name, detail))
    root = ET.parse(xml_path).getroot()
    ok("xmeml version 5", root.tag == "xmeml" and root.get("version") == "5")
    masters = {c.get("id"): c for c in root.iter("clip")}
    comments = {c.findtext("comments/mastercomment1") for c in masters.values()}
    want_ids = [c["clip_id"] for c in objects["clips"]]
    miss = [i for i in want_ids if i not in comments]
    ok("every object is a master clip", not miss, "%d/%d%s" % (len(want_ids) - len(miss), len(want_ids), (" missing " + ", ".join(miss[:3])) if miss else ""))
    seqs = {s.findtext("name"): s for s in root.iter("sequence")}
    plan_seqs = [o["name"] for o in plan["ops"] if o["op"] == "add_sequence"]
    ok("sequence count matches plan", len(seqs) == len(plan_seqs), "%d xml / %d plan" % (len(seqs), len(plan_seqs)))
    ok("sequence names match plan", set(seqs) == set(plan_seqs), ", ".join(sorted(set(plan_seqs) ^ set(seqs))))
    want_n = Counter(o["seq"] for o in plan["ops"] if o["op"] == "place_clip")
    bad = [n for n in plan_seqs if len(seqs.get(n, ET.Element("x")).findall("media/video/track/clipitem")) != want_n.get(n, 0)]
    ok("video clip count per sequence matches plan", not bad, ", ".join(bad))
    urls = [u.text for u in root.iter("pathurl")]
    missing = [u for u in urls if not os.path.exists(unquote(urlparse(u).path))]
    ok("every pathurl resolves to a file", not missing, "%d/%d%s" % (len(urls) - len(missing), len(urls), (" e.g. " + missing[0]) if missing else ""))
    ids = Counter(e.get("id") for e in root.iter() if e.get("id") and e.tag != "file")
    dups = [i for i, n in ids.items() if n > 1]
    ok("no duplicate ids", not dups, ", ".join(dups[:5]))
    files = Counter(f.get("id") for f in root.iter("file") if len(f))
    ok("each file defined in full exactly once", all(n == 1 for n in files.values()) and len(files) == len(want_ids), "%d defined" % len(files))
    if rosetta and os.path.exists(ROSETTA):
        wd = workdir or os.path.splitext(xml_path)[0] + "_rosetta"
        r = subprocess.run([sys.executable, ROSETTA, xml_path, "--workdir", wd], capture_output=True, text=True)
        tail = [l.strip() for l in r.stdout.splitlines() if "refusals" in l or "no_null" in l]
        res.append(("INFO", "Rosetta v7 read_xml.py exit %d (reads one top sequence; not the gate)" % r.returncode, "; ".join(tail)))
    elif rosetta:
        res.append(("INFO", "Rosetta read_xml.py not found at %s" % ROSETTA, ""))
    for s, n, d in res:
        print("%s  %s%s" % (s, n, ("  [" + d + "]") if d else ""))
    return all(s != "FAIL" for s, _, _ in res), res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xml"); ap.add_argument("--plan"); ap.add_argument("--objects"); ap.add_argument("--no-rosetta", action="store_true")
    a = ap.parse_args(argv)
    plan = load_json(a.plan or a.xml[:-4] + ".plan.json")
    good, _ = verify(a.xml, plan, load_json(a.objects or plan["objects"]), not a.no_rosetta)
    print("VERIFY", "PASS" if good else "FAIL")
    sys.exit(0 if good else 1)


if __name__ == "__main__":
    main()
