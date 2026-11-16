#!/usr/bin/env python3
"""ppf.py: the one front door. Conversation -> ops -> plan -> writer -> verify -> new version.

  ppf.py build  <media_folder | scan_clips.csv> --project NAME --out DIR [--media-root R]
                [--to prproj|xmeml] [--base BASE.prproj] [--notes notes.txt] [--date YYMMDD]
                [--top 5] [--engine auto|minilm|tfidf] [--no-organize]
--notes: one note per line -> <YYMMDD>_SELECTS_<note> sequences + NAME_vNNN_selects_report.md.
  ppf.py revise <PROJECT_vNNN.plan.json> --ops ops.json [--to prproj|xmeml] [--base X.prproj] [--out DIR]
  ppf.py status [--out DIR]

--to prproj (default, tier 1) uses the py-premiere vendored in this plugin; --base is the
user's project (read, never written), omitted = a fresh project. Import failure exits 2. --to xmeml is tier 3 (Premiere import duplicates media).
Every write is a new <NAME>_v<NNN> file; nothing is overwritten.
"""
import argparse, copy, glob, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ppf_ingest, ppf_plan
from ppf_common import load_json, save_json

OPS = {"add_bin": ["path"], "add_masterclip": ["clip", "bin"], "add_sequence": ["name"],
       "place_clip": ["seq", "clip"], "append_clips": ["seq", "clips"], "remove_sequence": ["name"],
       "rename_sequence": ["name", "to"], "remove_clip": ["seq", "clip"]}


def write_out(plan, objects, out, to, base, multicam=False):
    if to == "prproj":
        import ppf_write_prproj
        return ppf_write_prproj.write(plan, objects, base, out, multicam), None
    import ppf_write_xmeml, ppf_verify
    path, plan_path = ppf_write_xmeml.write(plan, objects, out)
    print("write: %s" % path)
    good, _ = ppf_verify.verify(path, load_json(plan_path), objects)
    print("VERIFY", "PASS" if good else "FAIL")
    return path, good


def apply_ops(plan, objects, ops):
    """Conversation operations (references/ops.md) -> a new plan. Refuses unknown ops and unknown ids."""
    plan = copy.deepcopy(plan); P = plan["ops"]
    clips = {c["clip_id"]: c for c in objects["clips"]}
    seq_names = lambda: [o["name"] for o in P if o["op"] == "add_sequence"]
    for op in ops:
        k = op.get("op")
        if k not in OPS:
            sys.exit("ppf revise: unknown op %r (known: %s)" % (k, ", ".join(OPS)))
        for f in OPS[k]:
            if f not in op:
                sys.exit("ppf revise: %s needs %r" % (k, f))
        for cid in ([op["clip"]] if "clip" in op else []) + op.get("clips", []):
            if cid not in clips:
                sys.exit("ppf revise: unknown clip id %r" % cid)
        if k in ("place_clip", "append_clips", "remove_clip", ) and op["seq"] not in seq_names():
            sys.exit("ppf revise: no sequence named %r" % op["seq"])
        if k == "add_sequence":
            if op["name"] in seq_names():
                sys.exit("ppf revise: sequence %r exists" % op["name"])
            seq_op = {kk: v for kk, v in op.items() if kk != "clips"}
            P.append(dict({"bin": "01_SEQUENCES", "fps": 23.976, "width": 1920, "height": 1080}, **seq_op))
            if op.get("clips"):          # add_sequence may carry clips: place them back to back on V1
                end = 0.0
                for cid in op["clips"]:
                    d = clips[cid]["duration_s"] or 0
                    P.append({"op": "place_clip", "seq": op["name"], "clip": cid, "track": op.get("track", "V1"),
                              "at_s": round(end, 3), "in_s": 0.0, "out_s": d})
                    end += d
        elif k == "append_clips":
            end = max([o["at_s"] + o["out_s"] - o.get("in_s", 0) for o in P if o["op"] == "place_clip" and o["seq"] == op["seq"]] or [0])
            for cid in op["clips"]:
                d = clips[cid]["duration_s"] or 0
                P.append({"op": "place_clip", "seq": op["seq"], "clip": cid, "track": op.get("track", "V1"),
                          "at_s": round(end, 3), "in_s": 0.0, "out_s": d})
                end += d
        elif k == "remove_sequence":
            P[:] = [o for o in P if not (o["op"] == "add_sequence" and o["name"] == op["name"]) and not (o["op"] == "place_clip" and o["seq"] == op["name"])]
        elif k == "rename_sequence":
            for o in P:
                if o["op"] == "add_sequence" and o["name"] == op["name"]:
                    o["name"] = op["to"]
                if o["op"] == "place_clip" and o["seq"] == op["name"]:
                    o["seq"] = op["to"]
        elif k == "remove_clip":
            P[:] = [o for o in P if not (o["op"] == "place_clip" and o["seq"] == op["seq"] and o["clip"] == op["clip"])]
        else:
            P.append(dict(op))
    plan.setdefault("revisions", []).append(ops)
    plan.pop("version", None); plan.pop("xml", None); plan.pop("prproj", None)
    return plan


def cmd_build(a):
    os.makedirs(a.out, exist_ok=True)
    if getattr(a, "version", None):
        # 261115: version numbers are per project NAME, so a second project restarts at v001 and a build that
        # must sit beside an earlier one had to be renumbered by hand. --version says which number to write.
        os.environ["PPF_FORCE_VERSION"] = str(a.version)
    obj_path = os.path.join(a.out, "%s_objects.json" % a.project)
    args = [a.source, "--out", obj_path] + (["--media-root", a.media_root] if a.media_root else [])
    objects = ppf_ingest.main(args); objects["_path"] = os.path.abspath(obj_path)
    if getattr(a, "by_folder", False):
        clips = objects.get("clips") or objects.get("objects") or []
        ppf_ingest.by_folder(clips, os.path.basename(os.path.abspath(a.source)) or "FOLDER")
        objects["organized_by"] = "folder"
        with open(obj_path, "w", encoding="utf-8") as f:
            json.dump(objects, f, indent=1)
        print("ingest: --by-folder, bins mirror the source tree: %s"
              % ", ".join(sorted({"%s/%s" % (c["day"], c["card"]) for c in clips})[:6]))
    notes = open(a.notes, encoding="utf-8").read() if a.notes else None
    plan = ppf_plan.build_plan(objects, a.project, a.date, notes, not a.no_organize, a.top, a.engine)
    res = write_out(plan, objects, a.out, a.to, a.base, a.multicam)
    if plan.get("selects_report") and res and res[0]:
        rp = os.path.splitext(str(res[0]))[0] + "_selects_report.md"     # NAME_vNNN_selects_report.md, never overwritten
        with open(rp, "x", encoding="utf-8") as f:
            f.write(plan["selects_report"] + "\n")
        print("selects report: %s" % rp)
    return res


def cmd_revise(a):
    plan = load_json(a.plan); objects = load_json(plan["objects"])
    new = apply_ops(plan, objects, load_json(a.ops))
    return write_out(new, objects, a.out or os.path.dirname(os.path.abspath(a.plan)), a.to, a.base, a.multicam)


def cmd_status(a):
    for f in sorted(glob.glob(os.path.join(a.out, "*_v[0-9][0-9][0-9].plan.json"))):
        p = load_json(f)
        n = sum(1 for o in p["ops"] if o["op"] == "add_sequence")
        c = sum(1 for o in p["ops"] if o["op"] == "place_clip")
        print("%s  v%03d  %s  sequences=%d placed=%d revisions=%d" % (p["project"], p.get("version", 0),
              p.get("xml") or p.get("prproj"), n, c, len(p.get("revisions", []))))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    import datetime
    b = sp.add_parser("build"); b.add_argument("source"); b.add_argument("--project", required=True)
    b.add_argument("--out", required=True); b.add_argument("--media-root"); b.add_argument("--notes")
    b.add_argument("--date", default=datetime.date.today().strftime("%y%m%d"))
    b.add_argument("--top", type=int, default=5, help="selects per note (with --notes)")
    b.add_argument("--engine", default="auto", choices=["auto", "minilm", "tfidf"], help="organize engine (auto: MiniLM, else TF-IDF)")
    b.add_argument("--no-organize", action="store_true", help="skip the content layer (topics, takes, selects)")
    b.add_argument("--by-folder", action="store_true",
                   help="bins mirror the source folders instead of the shoot date (stock, or anything already sorted)")
    b.add_argument("--version", type=int, help="force this version number instead of the next free one")
    r = sp.add_parser("revise"); r.add_argument("plan"); r.add_argument("--ops", required=True); r.add_argument("--out")
    for x in (b, r):
        x.add_argument("--to", choices=["prproj", "xmeml"], default="prproj"); x.add_argument("--base", help="user's .prproj (prproj only; omitted = fresh project)")
        x.add_argument("--multicam", action="store_true", help="prproj: also build a multicam clip for SYNC groups whose angles start together")
    s = sp.add_parser("status"); s.add_argument("--out", default=".")
    a = ap.parse_args(argv)
    return {"build": cmd_build, "revise": cmd_revise, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    from ppf_common import maybe_reexec
    maybe_reexec()
    main()
