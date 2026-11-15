#!/usr/bin/env python3
"""objects.py — shot objects + checks W1-W6 (contract: references/rules-and-objects.md §4-5).

Objects are DERIVED from WATCH_STATE.json and rebuildable. The ONLY thing this script writes into the
state file is `fill` (an atomic temp-file write); every other call only reads it.

Usage:
  objects.py build --state S.json [--rules rules.json]
  objects.py pending                   (every shot with a needs_scan key: machine / M1-M8 / desc)
  objects.py fill --state S.json --shot ID --macro M1 --value MCU [--by claude] [--why "..."]
  objects.py fill --state S.json --shot ID --desc "text" [--by claude]
  objects.py show <shot id | filename> (--objects O.json | --state S.json) [--json]
  objects.py summary [scene id]        (same source flags)
  objects.py unresolved                (same source flags)
  objects.py find [--size X] [--motion X] [--light X] [--text X] [--role X] [--tag X]
  objects.py check                     (exit 1 if any check fails)

With --state, calls read <state dir>/<state file stem>_objects.json, (re)building it first if it is missing,
belongs to another state_path, records a different state mtime, or is older than rules.json. A relative frame path is resolved against the STATE FILE's
directory, never the cwd.
"""
import sys, os, re, json, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from annotate_frames import diff_flag, pending_placeholder, is_pending  # W3 rule + PENDING marker, reused not copied

SCHEMA = "watch-objects/1"
SIMULANT_DEFAULT = ["manikin", "mannequin", "simulator", "patient", "infant", "baby", "classroom",
                    "clinic", "hospital", "turnout", "scba", "hazmat"]
MACROS = ["M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8"]
RANK = {"verified": 0, "unresolved": 1, "needs_scan": 2, "flagged": 3}   # flagged > needs_scan > unresolved > verified


def slug(s):
    return re.sub(r"[^A-Za-z0-9]+", "_", s or "").strip("_") or "job"


def objects_path(state_path):
    """<state dir>/<state file stem>_objects.json (two states in one folder never share an objects file)."""
    sp = os.path.abspath(state_path)
    return os.path.join(os.path.dirname(sp), "%s_objects.json" % os.path.splitext(os.path.basename(sp))[0])


def num(v):
    """Timecode to seconds. Accepts numbers, numeric strings, [HH:]MM:SS[.f]. None if unreadable."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            parts = [float(p) for p in v.strip().split(":")]
        except ValueError:
            return None
        t = 0.0
        for p in parts:
            t = t * 60 + p
        return t
    return None


def worst(statuses):
    w = "verified"
    for s in statuses:
        if RANK[s] > RANK[w]:
            w = s
    return w


def resolve_frame(fp, base):
    if not fp:
        return None
    return fp if os.path.isabs(fp) else os.path.join(base, fp)


def default_rules_path():
    return os.path.join(HERE, "..", "rules.json")


def load_rules(rules_path):
    if rules_path and os.path.exists(rules_path):
        try:
            with open(rules_path) as f:
                r = json.load(f)
            return r if isinstance(r, dict) else {}
        except ValueError:
            return {}
    return {}


def build(state_path, rules_path=None):
    base = os.path.dirname(os.path.abspath(state_path))
    state_mtime = os.path.getmtime(state_path)
    with open(state_path) as f:
        st = json.load(f)
    rules = load_rules(rules_path or default_rules_path())
    rules_version = rules.get("version")
    words = rules.get("W5_simulant_words")
    if not isinstance(words, list) or not words:
        words = SIMULANT_DEFAULT
    simulant = re.compile("|".join(re.escape(str(w)) for w in words), re.I)
    pool = st.get("mode") == "pool"
    checks, shots, scenes = [], {}, []

    def rec(check, target, ok, note=""):
        checks.append({"check": check, "target": target, "result": "pass" if ok else "fail", "note": note})

    for sc in st.get("scenes", []):
        sc_failed, order = [], []
        prev_out, prev_in = None, None
        need_file = pool or bool(sc.get("file"))
        if not sc.get("shots"):
            rec("W1", sc.get("id"), False, "scene has no shots")
            sc_failed.append("NO FRAME")
        for sh in sc.get("shots", []):
            sid = sh.get("id")
            failed = []
            # W1
            fp = resolve_frame(sh.get("frame"), base)
            ok = bool(fp and os.path.exists(fp))
            rec("W1", sid, ok, "" if ok else "no frame on disk: %s" % sh.get("frame"))
            if not ok:
                failed.append("NO FRAME")
            # W2 (list order = appearance order)
            tin, tout = num(sh.get("tc_in")), num(sh.get("tc_out"))
            ok = True
            note = ""
            if tin is not None:
                if prev_in is not None and tin < prev_in:
                    ok, note = False, "tc_in %s before previous tc_in %s" % (tin, prev_in)
                elif prev_out is not None and tin < prev_out:
                    ok, note = False, "tc_in %s before previous tc_out %s" % (tin, prev_out)
                prev_in = tin
                prev_out = tout if tout is not None else tin
            rec("W2", sid, ok, note)
            if not ok:
                failed.append("TC OVERLAP")
                if "TC OVERLAP" not in sc_failed:
                    sc_failed.append("TC OVERLAP")
            # W3
            # The disagree rule compares the machine to a description; before the watch writes one there is nothing to disagree with.
            real_machine = isinstance(sh.get("machine"), dict) and sh["machine"] and not is_pending(sh["machine"])
            ok = not (sh.get("witness_flag") or (real_machine and (sh.get("desc") or "").strip() and diff_flag(sh.get("desc", ""), sh["machine"])))
            rec("W3", sid, ok, "" if ok else "witness disagrees with description")
            if not ok:
                failed.append("WITNESS FLAG")
            # W4
            if need_file:
                ok = bool(sh.get("file") or sc.get("file"))
                rec("W4", sid, ok, "" if ok else "cell names no source file")
                if not ok:
                    failed.append("NO FILENAME")
            # W5
            if sh.get("witness_flag") or simulant.search(sh.get("desc") or ""):
                ok = sh.get("semantic_check", "not_run") not in (None, "not_run")
                rec("W5", sid, ok, "" if ok else "simulant-risk shot, semantic_check not run")
                if not ok:
                    failed.append("SEMANTIC NOT RUN")
            # W6: every shot has `machine` and all of M1-M8. A PENDING placeholder counts as present (but the
            # shot becomes needs_scan below). Anything else missing stays "unresolved" (not flagged) so Claude looks.
            machine = sh.get("machine")
            macros = sh.get("macros") if isinstance(sh.get("macros"), dict) else {}
            absent = [k for k in MACROS if not isinstance(macros.get(k), dict)]
            if not isinstance(machine, dict) or not machine:
                why6 = "no witness"
            elif machine.get("error"):
                why6 = "no witness (machine error: %s)" % str(machine.get("error"))[:60]
            elif not macros:
                why6 = "no macros"
            elif absent:
                why6 = "no macros (missing %s)" % ",".join(absent)
            else:
                why6 = None
            rec("W6", sid, why6 is None, "" if why6 is None else "NOT ANALYSED: " + why6)
            # needs_scan: a scanner must look (machine / macro placeholders, description stale since the frame changed)
            pend, pend_why = [], {}
            if is_pending(machine):
                pend.append("machine"); pend_why["machine"] = machine.get("why") or "no witness data"
            for k in MACROS:
                if is_pending(macros.get(k)):
                    pend.append(k); pend_why[k] = macros[k].get("why") or "no witness data"
            if sh.get("desc_stale"):
                pend.append("desc"); pend_why["desc"] = "frame changed since description"
            unres = sorted(k for k, v in macros.items() if isinstance(v, dict) and v.get("status") == "unresolved")
            if why6:
                unres = [why6] + unres
            status = "flagged" if failed else ("needs_scan" if pend else ("unresolved" if unres else "verified"))
            shots[sid] = {
                "id": sid, "scene": sc.get("id"), "type": sh.get("type"),
                "tc_in": sh.get("tc_in"), "tc_out": sh.get("tc_out"),
                "file": sh.get("file") or sc.get("file"), "frame": sh.get("frame"),
                "frame_abs": fp,
                "desc": sh.get("desc"), "macros": macros,
                "clip_s": (num(sc.get("tc_out")) - num(sc.get("tc_in")) if sc.get("file") and num(sc.get("tc_out")) is not None and num(sc.get("tc_in")) is not None else None),
                "counts": (sh.get("machine") or {}).get("counts"),
                "ocr": (sh.get("machine") or {}).get("ocr"),
                "status": status, "failed": failed, "unresolved": unres,
                "needs_scan": pend, "needs_scan_why": pend_why,
            }
            order.append(sid)
        sstat = worst([shots[i]["status"] for i in order] + (["flagged"] if sc_failed else []))
        sobj = {"id": sc.get("id"), "slug": sc.get("slug"), "tc_in": sc.get("tc_in"),
                "tc_out": sc.get("tc_out"), "shots": order, "status": sstat, "failed": sc_failed}
        if sc.get("file"):
            sobj["file"] = sc["file"]
        scenes.append(sobj)

    video = dict(st.get("video") or {})
    video["status"] = worst([s["status"] for s in scenes])
    if not shots:
        video["status"] = worst([video["status"], "unresolved"])
        video["note"] = "no shots"
    obj = {"schema": SCHEMA, "job": st.get("job"),
           "state_path": os.path.abspath(state_path), "state_mtime": state_mtime,
           "built_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "rules_version": rules_version, "video": video, "scenes": scenes,
           "shots": shots, "checks": checks}
    out = objects_path(state_path)
    with open(out, "w") as f:
        json.dump(obj, f, indent=2)
    return out, obj


def fresh_objects(state_path, rules_path=None):
    """Path of an up-to-date objects file for this state, rebuilding when missing / foreign / older."""
    op = objects_path(state_path)
    ok = False
    if os.path.exists(op):
        try:
            with open(op) as f:
                o = json.load(f)
            rp = rules_path or default_rules_path()
            ok = (o.get("state_path") == os.path.abspath(state_path)
                  and o.get("state_mtime") == os.path.getmtime(state_path)
                  and not (os.path.exists(rp) and os.path.getmtime(rp) > os.path.getmtime(op)))
        except (ValueError, AttributeError):
            ok = False
    if not ok:
        op, _ = build(state_path, rules_path)
    return op


# ---------- fill: the one write into the state ----------

def fill(state_path, shot_id, macro=None, value=None, by="claude", why=None, desc=None, rules_path=None):
    """Write a judgment into the state (atomic). Returns (message dict, rc)."""
    with open(state_path) as f:
        st = json.load(f)
    hit = None
    for sc in st.get("scenes", []):
        for sh in sc.get("shots", []):
            if sh.get("id") == shot_id:
                hit = hit or sh
    if hit is None:
        return {"error": "no shot %r in %s" % (shot_id, state_path)}, 1
    if macro is None and desc is None:
        return {"error": "fill needs --macro M1..M8 with --value, or --desc"}, 2
    if macro is not None:
        if macro not in MACROS:
            return {"error": "unknown macro %r (allowed: %s)" % (macro, ",".join(MACROS))}, 1
        if value is None:
            return {"error": "fill --macro needs --value"}, 2
    if macro is not None:
        v = value
        if isinstance(v, str) and v.strip()[:1] in ("[", "{"):
            try:
                v = json.loads(v)
            except ValueError:
                pass
        macros = hit.get("macros") if isinstance(hit.get("macros"), dict) else {}
        for k in MACROS:   # a complete record: anything not yet scanned stays an explicit placeholder
            if not isinstance(macros.get(k), dict):
                macros[k] = pending_placeholder("not yet scanned")
        macros[macro] = {"value": v, "status": "resolved", "why": why or "filled by %s" % by, "filled_by": by}
        hit["macros"] = macros
    if desc is not None:
        hit["desc"] = desc
        hit["desc_filled_by"] = by
        hit.pop("desc_stale", None)
    tmp = state_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=2)
    os.replace(tmp, state_path)
    op, obj = build(state_path, rules_path)
    return {"filled": shot_id, "macro": macro, "desc": desc is not None,
            "status": obj["shots"][shot_id]["status"], "objects": op}, 0


# ---------- calls ----------

def mval(sh, k):
    m = (sh.get("macros") or {}).get(k)
    if not isinstance(m, dict) or m.get("status") != "resolved":
        return None
    return m.get("value")


def mstat(sh, k):
    m = (sh.get("macros") or {}).get(k)
    return m.get("status") if isinstance(m, dict) else None


def chip(sh, k, name=None):
    """Text for a macro: value if resolved, '?' if unresolved, 'PENDING' if needs_scan, None if not_run/absent."""
    s = mstat(sh, k)
    if s == "resolved":
        return str(mval(sh, k))
    if s == "unresolved":
        return "?"
    if s == "needs_scan":
        return "PENDING"
    return None


def tags_of(sh):
    v = mval(sh, "M7")
    out = []
    for t in (v if isinstance(v, list) else ([v] if v else [])):
        if isinstance(t, dict):
            t = t.get("word") or t.get("tag") or t.get("label")
        elif isinstance(t, (list, tuple)) and t:
            t = t[0]
        if t:
            out.append(str(t).lower())
    return out


def fmt_dur(sh):
    a, b = num(sh.get("tc_in")), num(sh.get("tc_out"))
    if a is None or b is None or b <= a:
        c = sh.get("clip_s")
        return ("clip %.1fs" % c) if isinstance(c, (int, float)) and c > 0 else None
    return "%.1fs" % (b - a)


def line(sh):
    p = [sh["id"]]
    if sh.get("file"):
        p.append(os.path.basename(sh["file"]))
    d = fmt_dur(sh)
    if d:
        p.append(d)
    size = chip(sh, "M1") or sh.get("type")
    for c in (size, chip(sh, "M2"), chip(sh, "M5")):
        if c:
            p.append(c)
    t = chip(sh, "M6")
    if t:
        p.append("text:" + t)
    m3 = (sh.get("macros") or {}).get("M3")
    if isinstance(m3, dict) and m3.get("status") == "resolved":
        pct = m3.get("pct")
        p.append("speech %d%%" % round(pct) if isinstance(pct, (int, float)) else str(m3.get("value")))
    elif isinstance(m3, dict) and m3.get("status") == "unresolved":
        p.append("speech ?")
    elif isinstance(m3, dict) and m3.get("status") == "needs_scan":
        p.append("speech PENDING")
    r = chip(sh, "M4")
    if r:
        p.append("role:" + r)
    m8 = mval(sh, "M8")
    m8d = (sh.get("macros") or {}).get("M8") or {}
    if m8 in ("dup", "near") and isinstance(m8d, dict) and m8d.get("of"):
        m8 = ("dup of %s" if m8 == "dup" else "near %s") % m8d["of"]
    elif m8 == "none":
        m8 = None
    if m8:
        if isinstance(m8, dict):
            m8 = " ".join("%s %s" % (k.replace("_", " "), v) for k, v in m8.items() if v)
        p.append(str(m8).replace("_", " "))
    tg = tags_of(sh)
    if tg:
        p.append("tags:" + ",".join(tg))
    p.append(sh["status"])
    if sh["failed"]:
        p.append("FAIL " + "/".join(sh["failed"]))
    if sh["unresolved"]:
        p.append("? " + ",".join(sh["unresolved"]))
    if sh.get("needs_scan"):
        p.append("PENDING " + ",".join(sh["needs_scan"]))
    return " · ".join(p)


def cmd_show(obj, key, js):
    hits = [s for s in obj["shots"].values()
            if s["id"] == key or (s.get("file") and (s["file"] == key or os.path.basename(s["file"]) == key))]
    if not hits:
        return "no shot matches %r" % key, 1
    return (json.dumps(hits, indent=2) if js else "\n".join(line(s) for s in hits)), 0


def tally(shots):
    cats = {"size": "M1", "motion": "M2", "light": "M5", "text": "M6", "role": "M4"}
    out = {}
    for name, k in cats.items():
        c = {}
        for s in shots:
            v = mval(s, k)
            if v is None and name == "size":
                v = s.get("type")
            key = str(v) if v is not None else {"unresolved": "?", "needs_scan": "PENDING"}.get(mstat(s, k))
            if key:
                c[key] = c.get(key, 0) + 1
        out[name] = c
    return out


def cmd_summary(obj, scene, js):
    if scene:
        scs = [s for s in obj["scenes"] if s["id"] == scene]
        if not scs:
            return "no scene %r" % scene, 1
    else:
        scs = obj["scenes"]
    shots = [obj["shots"][i] for sc in scs for i in sc["shots"]]
    flags = [{"shot": s["id"], "failed": s["failed"]} for s in shots if s["failed"]]
    flags += [{"scene": sc["id"], "failed": sc["failed"]} for sc in scs if sc["failed"]]
    st = {}
    for s in shots:
        st[s["status"]] = st.get(s["status"], 0) + 1
    # an empty scope is never "verified": there is nothing to have verified
    status = worst([s["status"] for s in scs]) if shots else "unresolved"
    res = {"scope": scene or "video", "scenes": len(scs), "shots": len(shots),
           "status": status, "by_status": st, "needs_scan": st.get("needs_scan", 0),
           "counts": tally(shots), "flags": flags}
    if not shots:
        res["note"] = "no shots"
    if js:
        return json.dumps(res, indent=2), 0
    ls = ["%s · %d scenes · %d shots · %s" % (res["scope"], res["scenes"], res["shots"], res["status"]),
          "status: " + ", ".join("%s %d" % kv for kv in sorted(st.items())),
          "needs_scan: %d" % res["needs_scan"]]
    for k, c in res["counts"].items():
        if c:
            ls.append("%s: %s" % (k, ", ".join("%s %d" % kv for kv in sorted(c.items()))))
    for f in flags:
        ls.append("flag %s: %s" % (f.get("shot") or f.get("scene"), "/".join(f["failed"])))
    return "\n".join(ls), 0


def cmd_unresolved(obj, js):
    rows = []
    for s in obj["shots"].values():
        if s["status"] in ("verified", "needs_scan"):   # needs_scan belongs to `pending`: a scanner, not a judgment
            continue
        why = list(s["failed"]) + [m if m.startswith("no ") else "unresolved " + m for m in s["unresolved"]]
        rows.append({"id": s["id"], "frame": s.get("frame_abs") or s.get("frame"), "status": s["status"], "why": why})
    if js:
        return json.dumps(rows, indent=2), 0
    return "\n".join("%s · %s · %s · %s" % (r["id"], r["status"], r["frame"], "; ".join(r["why"])) for r in rows) or "none", 0


def cmd_pending(obj, js):
    rows = []
    for s in obj["shots"].values():
        if s.get("needs_scan"):
            rows.append({"id": s["id"], "frame": s.get("frame_abs") or s.get("frame"), "status": s["status"],
                         "pending": s["needs_scan"], "why": s.get("needs_scan_why", {})})
    if js:
        return json.dumps(rows, indent=2), 0
    return "\n".join("%s · %s · %s · pending: %s" % (r["id"], r["status"], r["frame"], ",".join(r["pending"]))
                     for r in rows) or "none", 0


def cmd_find(obj, a, js):
    want = [("M1", a.get("--size")), ("M2", a.get("--motion")), ("M5", a.get("--light")),
            ("M6", a.get("--text")), ("M4", a.get("--role"))]
    ids = []
    for s in obj["shots"].values():
        ok = True
        for k, w in want:
            if w is None:
                continue
            v = mval(s, k)
            if k == "M1" and v is None:
                v = s.get("type")
            if str(v).lower() != w.lower():
                ok = False
        if a.get("--tag") and a["--tag"].lower() not in tags_of(s):
            ok = False
        if ok:
            ids.append(s["id"])
    return (json.dumps(ids) if js else "\n".join(ids) or "none"), 0


def cmd_check(obj, js):
    if js:
        return json.dumps(obj["checks"], indent=2), 0
    ls = ["%s · %s · %s%s" % (c["check"], c["target"], c["result"], (" · " + c["note"]) if c["note"] else "")
          for c in obj["checks"]]
    bad = sum(1 for c in obj["checks"] if c["result"] == "fail")
    ls.append("%d checks · %d fail" % (len(obj["checks"]), bad))
    return "\n".join(ls), (1 if bad else 0)


def parse(argv):
    opts, pos, i = {}, [], 0
    flags = {"--json"}
    while i < len(argv):
        t = argv[i]
        if t in flags:
            opts[t] = True
        elif t.startswith("--"):
            opts[t] = argv[i + 1] if i + 1 < len(argv) else ""
            i += 1
        else:
            pos.append(t)
        i += 1
    return opts, pos


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = sys.argv[1]
    a, pos = parse(sys.argv[2:])
    if cmd == "build":
        if not a.get("--state"):
            print("build needs --state")
            return 2
        out, obj = build(a["--state"], a.get("--rules"))
        print(json.dumps({"objects": out, "status": obj["video"]["status"], "shots": len(obj["shots"]),
                          "checks_failed": sum(1 for c in obj["checks"] if c["result"] == "fail")}))
        return 0
    if cmd == "fill":
        if not a.get("--state") or not a.get("--shot"):
            print("fill needs --state and --shot")
            return 2
        msg, rc = fill(a["--state"], a["--shot"], a.get("--macro"), a.get("--value"), a.get("--by", "claude"),
                       a.get("--why"), a.get("--desc"), a.get("--rules"))
        print(json.dumps(msg))
        return rc
    op = a.get("--objects")
    if not op and a.get("--state"):
        op = fresh_objects(a["--state"], a.get("--rules"))
    if not op or not os.path.exists(op):
        print("need --objects <path> or --state <path>")
        return 2
    with open(op) as f:
        obj = json.load(f)
    js = bool(a.get("--json"))
    if cmd == "show":
        if not pos:
            print("show needs a shot id or filename")
            return 2
        out, rc = cmd_show(obj, pos[0], js)
    elif cmd == "summary":
        out, rc = cmd_summary(obj, pos[0] if pos else None, js)
    elif cmd == "unresolved":
        out, rc = cmd_unresolved(obj, js)
    elif cmd == "pending":
        out, rc = cmd_pending(obj, js)
    elif cmd == "find":
        out, rc = cmd_find(obj, a, js)
    elif cmd == "check":
        out, rc = cmd_check(obj, js)
    else:
        print("unknown command %r" % cmd)
        return 2
    print(out)
    return rc


if __name__ == "__main__":
    sys.exit(main())
