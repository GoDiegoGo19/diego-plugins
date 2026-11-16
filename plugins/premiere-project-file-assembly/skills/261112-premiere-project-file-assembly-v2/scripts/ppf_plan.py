#!/usr/bin/env python3
"""ppf_plan.py: objects.json -> plan.json (plain operations; see references/ops.md).

  ppf_plan.py objects.json --project NAME --out plan.json [--notes notes.txt] [--date YYMMDD]
              [--top 5] [--engine auto|minilm|tfidf] [--no-organize]

After the rule-based bins and string-outs, ppf_organize.py adds content sequences
(BROLL_<topic>, TAKES_<line>, SELECTS_<note>) as more ops.

Deterministic: same objects in, same plan out. The plan is a list of ops an LLM can
also write or edit by hand: add_bin, add_masterclip, add_sequence, place_clip.
"""
import argparse, datetime, os, sys
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ppf_common import load_json, save_json, rate_of, real_fps, tc_to_frames, conform_fps, conform_clips, seq_fps_of

TOP_BINS = ["00_CUTS", "01_SEQUENCES", "02_FOOTAGE", "03_AUDIO", "04_GRAPHICS", "05_STILLS"]
KIND_BIN = {"audio": "03_AUDIO", "graphic": "04_GRAPHICS", "still": "05_STILLS", "live": "05_STILLS"}
APPROX_MIN_S = 2.0     # wall-clock sync: clips overlapping by at least this many seconds (no timecode)


def fmt_of(clips, dominant=None):
    """Most common (fps, w, h) among clips; defaults 23.976 1920x1080. Each clip's rate is conformed
    to a standard rate BEFORE counting (iPhone VFR: 28.945 -> 29.97), with `dominant` = the shoot
    day's dominant standard rate, so a sequence never gets a raw measured rate (G1)."""
    c = Counter((seq_fps_of(x, dominant), x.get("width"), x.get("height"))
                for x in clips if x.get("fps"))
    if not c:
        return {"fps": 23.976, "width": 1920, "height": 1080}
    (fps, w, h), _ = c.most_common(1)[0]
    return {"fps": fps, "width": w or 1920, "height": h or 1080}


def tc_range_s(c):
    """(start_s, end_s) on the day's clock, or None when there is no usable timecode."""
    if not c.get("tc_start") or not c.get("fps") or not c.get("duration_s"):
        return None
    tb, _ = rate_of(c["fps"])
    fr = tc_to_frames(c["tc_start"], tb)
    if fr is None:
        return None
    start = fr / float(tb)          # nominal clock seconds (drop-frame drift ignored: see Gotchas)
    return start, start + c["duration_s"]


def sync_groups(clips):
    """Clips from DIFFERENT cards whose TC ranges overlap -> groups (union-find)."""
    rng = {c["clip_id"]: tc_range_s(c) for c in clips}
    ids = [c["clip_id"] for c in clips if rng[c["clip_id"]]]
    card = {c["clip_id"]: c["card"] for c in clips}
    parent = {i: i for i in ids}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for a_i, a in enumerate(ids):
        for b in ids[a_i + 1:]:
            if card[a] == card[b]:
                continue
            (s1, e1), (s2, e2) = rng[a], rng[b]
            if s1 < e2 and s2 < e1:
                parent[find(a)] = find(b)
    groups = {}
    for i in ids:
        groups.setdefault(find(i), []).append(i)
    out = []
    for g in groups.values():
        if len({card[i] for i in g}) >= 2:
            out.append(sorted(g, key=lambda i: rng[i][0]))
    return sorted(out, key=lambda g: rng[g[0]][0]), rng


def place(seq, c, track, at_s, in_s, out_s):
    """place_clip op; carries the clip's measured rate and VFR flag (flag only, no transcode)."""
    op = {"op": "place_clip", "seq": seq, "clip": c["clip_id"], "track": track, "at_s": at_s, "in_s": in_s, "out_s": out_s}
    if c.get("fps_measured") is not None:
        op["fps_measured"], op["vfr"] = c["fps_measured"], bool(c.get("vfr"))
    return op


def build_plan(objects, project, date, notes=None, organize=True, top=5, engine="auto"):
    clips = objects["clips"]
    dom, vfr_ids = conform_clips(clips)      # once per shoot: fps -> standard, fps_measured, vfr (in place)
    ops = []
    for b in TOP_BINS:
        ops.append({"op": "add_bin", "path": b})
    ops.append({"op": "add_bin", "path": "00_CUTS/%s_cut01" % date})
    days = sorted({c["day"] for c in clips})
    for d in days:
        ops.append({"op": "add_bin", "path": "01_SEQUENCES/%s" % d})
        ops.append({"op": "add_bin", "path": "02_FOOTAGE/%s" % d})
    # every object gets a master clip in a bin (proxies and sidecars included, so nothing is lost)
    made = {"02_FOOTAGE/%s" % d for d in days}
    for c in sorted(clips, key=lambda c: (c["day"], c["card_no"], c["rel_path"])):
        if c["kind"] in KIND_BIN:
            b = "%s/%s" % (KIND_BIN[c["kind"]], c["day"]) + ("/_live" if c["kind"] == "live" else "")
        else:
            b = "02_FOOTAGE/%s/%s_%s" % (c["day"], c["card_no"], c["card"])
            if c["role"] != "master":
                b += "/_%s" % c["role"]
            elif c.get("orientation") == "vertical":
                b += "/_vertical"
        for i in range(1, len(b.split("/")) + 1):
            p = "/".join(b.split("/")[:i])
            if p not in made and p not in TOP_BINS:
                ops.append({"op": "add_bin", "path": p}); made.add(p)
        ops.append({"op": "add_masterclip", "clip": c["clip_id"], "bin": b})
    usable = [c for c in clips if c["role"] == "master" and c.get("duration_s") and c["kind"] in ("interview", "broll")]
    vertical = [c for c in usable if c.get("orientation") == "vertical"]      # G4: own stringout, never mixed in
    usable = [c for c in usable if c.get("orientation") != "vertical"]
    skipped = [c["clip_id"] for c in clips if c["role"] == "master" and not c.get("duration_s")]
    for d in days:
        day = [c for c in usable if c["day"] == d]
        cards = sorted({(c["card_no"], c["card"]) for c in day})
        for no, card in cards:
            cc = sorted([c for c in day if c["card"] == card], key=lambda c: c["rel_path"])
            name = "%s_%s_%s_stringout" % (d, no, card)
            ops.append(dict({"op": "add_sequence", "name": name, "bin": "01_SEQUENCES/%s" % d, "role": "stringout"}, **fmt_of(cc, dom.get(d))))
            t = 0.0
            for c in cc:
                ops.append(place(name, c, "V1", round(t, 3), 0.0, c["duration_s"]))
                t += c["duration_s"]
        for kind, label in (("interview", "INTERVIEWS"), ("broll", "BROLL")):
            name = "%s_%s" % (d, label)
            kc = sorted([c for c in day if c["kind"] == kind], key=lambda c: (c["card_no"], c["rel_path"]))
            if not kc:
                # 261115, a stock pull: an INTERVIEWS sequence was built for 103 silent stock clips and held
                # nothing. An empty sequence is not a neutral extra; it is a thing the editor has to open to
                # discover it is empty. A sequence with no clips is not made.
                continue
            ops.append(dict({"op": "add_sequence", "name": name, "bin": "01_SEQUENCES/%s" % d, "role": label.lower()}, **fmt_of(kc, dom.get(d))))
            t = 0.0
            for c in kc:
                ops.append(place(name, c, "V1", round(t, 3), 0.0, c["duration_s"]))
                t += c["duration_s"]
        vd = sorted([c for c in vertical if c["day"] == d], key=lambda c: (c["card_no"], c["rel_path"]))
        if vd:
            name = "%s_VERTICAL_stringout" % d
            fps = Counter(seq_fps_of(c, dom.get(d)) for c in vd if c.get("fps")).most_common(1)
            ops.append({"op": "add_sequence", "name": name, "bin": "01_SEQUENCES/%s" % d, "role": "vertical",
                        "fps": dom.get(d) or (fps[0][0] if fps else 29.97), "width": 1080, "height": 1920})
            t = 0.0
            for c in vd:
                ops.append(place(name, c, "V1", round(t, 3), 0.0, c["duration_s"]))
                t += c["duration_s"]
        groups, rng = sync_groups(day)
        by_id = {c["clip_id"]: c for c in day}
        for n, g in enumerate(groups, 1):
            name = "%s_SYNC_%02d" % (d, n)
            ops.append(dict({"op": "add_sequence", "name": name, "bin": "01_SEQUENCES/%s" % d, "role": "sync",
                             "tc_origin_s": rng[g[0]][0]}, **fmt_of([by_id[i] for i in g], dom.get(d))))
            card_order = []
            for i in g:
                if by_id[i]["card"] not in card_order:
                    card_order.append(by_id[i]["card"])
            t0 = min(rng[i][0] for i in g)
            for i in g:
                ops.append(place(name, by_id[i], "V%d" % (card_order.index(by_id[i]["card"]) + 1),
                                 round(rng[i][0] - t0, 3), 0.0, by_id[i]["duration_s"]))
        tc_ids = {i for g in groups for i in g} | {c["clip_id"] for c in day if tc_range_s(c)}
        # Approximate sync lines clips up by wall clock so an editor can nudge them against the AUDIO.
        # With no audio there is nothing to check it against, so the sequence cannot be verified or used:
        # 261115, a stock pull with no audio and no timecode still got SYNC_approx groups. Silent clips are
        # not candidates for it. Real-timecode groups above are unaffected; those stand on the timecode.
        approx_in = [c for c in day if c["clip_id"] not in tc_ids and (c.get("audio_channels") or 0) > 0]
        for n, (g, wr) in enumerate(approx_groups(approx_in), 1):
            name = "%s_SYNC_approx_%d" % (d, n)       # never mixed with true-TC groups
            ops.append(dict({"op": "add_sequence", "name": name, "bin": "01_SEQUENCES/%s" % d, "role": "sync_approx",
                             "approx": True, "note": "wall-clock sync, check against audio"}, **fmt_of(g, dom.get(d))))
            lanes = []
            for c in g:
                k = c.get("device") or c.get("card_folder") or c["clip_id"]
                lanes += [k] if k not in lanes else []
            t0 = min(wr[c["clip_id"]][0] for c in g)
            for c in g:
                k = c.get("device") or c.get("card_folder") or c["clip_id"]
                ops.append(dict(place(name, c, "V%d" % (lanes.index(k) + 1), round(wr[c["clip_id"]][0] - t0, 3), 0.0,
                                      c["duration_s"]), approx=True))
    org = None
    if organize:                      # content layer (ppf_organize.py): topics, takes, selects; plain ops
        import ppf_organize
        day_fmt = lambda cl: fmt_of(cl, dom.get(Counter(x["day"] for x in cl).most_common(1)[0][0]) if cl else None)
        more, org = ppf_organize.organize(objects, ops, date, notes, top, engine, fmt_of=day_fmt)
        ops += more
    extra = iphone_report(objects, ops)
    if extra:
        rep = (org or {}).get("report") or "# Organize report\n"
        org = dict(org or {}, report=rep.rstrip("\n") + "\n\n" + extra)
    plan = {"made_by": "ppf_plan.py", "project": project, "date": date, "objects": objects.get("_path"),
            "notes": notes, "skipped_no_duration": skipped, "ops": ops,
            "fps_dominant": dom, "vfr_clips": vfr_list(clips, vfr_ids),
            "organize": {k: v for k, v in (org or {}).items() if k != "report"} if org and organize else None,
            "hdr_clips": [c["clip_id"] for c in clips if c.get("hdr")], "sidecars": objects.get("sidecars") or [],
            "selects_report": (org or {}).get("report")}
    return plan


def wall_range_s(c):
    """(start_s, end_s) on the wall clock from the QuickTime creation date (to the second), or None."""
    if c.get("date_source") != "creation_time" or not c.get("shot_at") or not c.get("duration_s"):
        return None
    try:
        t = datetime.datetime.fromisoformat(c["shot_at"]).timestamp()
    except (TypeError, ValueError):
        return None
    return t, t + c["duration_s"]


def approx_groups(clips):
    """No timecode, creation_time to the second: clips whose wall-clock ranges overlap by >= 2 s,
    grouped (union-find) -> [(clips sorted by start, {clip_id: range})]. Approximate: phones' clocks drift."""
    wr = {c["clip_id"]: wall_range_s(c) for c in clips}
    cs = [c for c in clips if wr[c["clip_id"]]]
    parent = {c["clip_id"]: c["clip_id"] for c in cs}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for k, a in enumerate(cs):
        for b in cs[k + 1:]:
            (s1, e1), (s2, e2) = wr[a["clip_id"]], wr[b["clip_id"]]
            if min(e1, e2) - max(s1, s2) >= APPROX_MIN_S:
                parent[find(a["clip_id"])] = find(b["clip_id"])
    groups = {}
    for c in cs:
        groups.setdefault(find(c["clip_id"]), []).append(c)
    out = [sorted(g, key=lambda c: (wr[c["clip_id"]][0], c["rel_path"])) for g in groups.values() if len(g) >= 2]
    return [(g, wr) for g in sorted(out, key=lambda g: wr[g[0]["clip_id"]][0])]


def iphone_report(objects, ops):
    """Markdown for the report: HDR clips, wall-clock sync groups, vertical stringouts, sidecars, Live Photos."""
    clips = objects["clips"]
    rep = []
    hdr = [c for c in clips if c.get("hdr")]
    if hdr:
        rep += ["## HDR clips (set sequence color to Rec.709 or work in HLG)", "",
                "Flag only: nothing was converted.", "", "| clip | hdr | color space |", "|---|---|---|"]
        rep += ["| `%s` | %s | %s |" % (c["clip_id"], c["hdr"], c.get("color_space") or "") for c in hdr] + [""]
    ap = [o for o in ops if o["op"] == "add_sequence" and o.get("approx")]
    if ap:
        rep += ["## Approximate sync (wall-clock sync, check against audio)", "",
                "No timecode: clips are offset by their creation_time difference (to the second).", ""]
        rep += ["- `%s`: %s" % (o["name"], ", ".join("`%s`" % p["clip"] for p in ops
                                                      if p["op"] == "place_clip" and p["seq"] == o["name"])) for o in ap] + [""]
    vs = [o["name"] for o in ops if o["op"] == "add_sequence" and o.get("role") == "vertical"]
    if vs:
        rep += ["## Vertical clips", "", "In `02_FOOTAGE/<day>/<NN>_<CARD>/_vertical` and %s (1080x1920); "
                "not in the horizontal stringouts or BROLL." % ", ".join("`%s`" % v for v in vs), ""]
    live = [c["clip_id"] for c in clips if c.get("kind") == "live"]
    if live:
        rep += ["## Live Photo clips", "", "In `05_STILLS/<day>/_live`, not in sequences: " + ", ".join("`%s`" % x for x in live), ""]
    sc = objects.get("sidecars") or []
    if sc:
        rep += ["## Sidecars (ignored)", "", "%d .AAE/.THM/.XMP files were not imported: " % len(sc)
                + ", ".join("`%s`" % x for x in sc[:40]) + (" ..." if len(sc) > 40 else ""), ""]
    return "\n".join(rep)


def vfr_list(clips, ids):
    """[{clip, fps_measured, fps}] for clips flagged VFR: candidates for a CFR transcode before the online."""
    by = {c["clip_id"]: c for c in clips}
    return [{"clip": i, "fps_measured": by[i]["fps_measured"], "fps": by[i]["fps"]} for i in ids]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("objects"); ap.add_argument("--project", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--notes", help="text file of notes or the prompt; stored in the plan, matched by ppf_match.py")
    ap.add_argument("--date", default=datetime.date.today().strftime("%y%m%d"), help="project date for 00_CUTS (YYMMDD)")
    ap.add_argument("--top", type=int, default=5); ap.add_argument("--engine", default="auto", choices=["auto", "minilm", "tfidf"])
    ap.add_argument("--no-organize", action="store_true")
    a = ap.parse_args(argv)
    objects = load_json(a.objects); objects["_path"] = os.path.abspath(a.objects)
    notes = open(a.notes, encoding="utf-8").read() if a.notes else None
    plan = build_plan(objects, a.project, a.date, notes, not a.no_organize, a.top, a.engine)
    save_json(a.out, plan)
    n = Counter(o["op"] for o in plan["ops"])
    print("plan: %s -> %s (%s)" % (a.objects, a.out, ", ".join("%s=%d" % kv for kv in sorted(n.items()))))
    if plan["vfr_clips"]:
        print("plan: %d VFR clips conformed to a standard rate (flagged, not transcoded): see plan[\"vfr_clips\"]" % len(plan["vfr_clips"]))
    return plan


if __name__ == "__main__":
    main()
