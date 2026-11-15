#!/usr/bin/env python3
"""The fabricator: the second machine (references/what-makes-a-good-shot.md, references/ascii-map.md).

The first machine (read_shots.py) watches each shot once and writes a ledger row. This one never looks at a pixel.
It reads those rows as text, builds one TOTEM POLE per shot (the row's closed words turned into sliders 0..1),
then answers an ask: parse it, filter hard, weigh the sliders by a template, rank, and say why in the row's own fields.

    reflect.py LEDGER.jsonl --ask "the 7 most dynamic elephant clips, cinematic high" --out ANSWER.md

Rules it enforces, all from Diego (261114):
  - nothing is good; everything is a slider, and the template says where the ask wants to sit on each
  - good is script-relative: "the good shot of the bird is really a byproduct of the script"
  - counts never climb the pole: a detector's boxes are for search, never a criterion or a reason
  - a hard filter removes a shot; everything aesthetic is a weight
  - a filter on a field the row does not know parks the shot in an unconfirmed tail, it never drops it
  - the parse is echoed before the answer, and every word it did not use is listed
  - if 3 pass, the answer says 3; it is never padded to the number asked for
"""
import argparse
import json
import os
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
TEMPLATES = SKILL / "templates"
U = "unresolved"

# ---------------------------------------------------------------- the sliders
# Each table maps a closed ledger value to a position on that slider. A value the row does not carry (unresolved,
# missing, unknown) contributes NOTHING: the slider returns None, the weight is dropped, and the rest are
# renormalised. Scoring an unknown as 0 would quietly punish a shot for what the machine could not see.
MOVE = {"locked": 0.0, "pan": 0.45, "tilt": 0.45, "truck left": 0.6, "truck right": 0.6, "pedestal up": 0.5,
        "pedestal down": 0.5, "push in": 0.8, "pull out": 0.8, "zoom in": 0.5, "zoom out": 0.5, "track": 0.85,
        "handheld": 0.6, "gimbal float": 0.7, "crane": 0.9, "drift": 0.25}
SPEED = {"still": 0.0, "slow": 0.35, "medium": 0.7, "fast": 1.0}
LEVELS = {"full range": 0.85, "protected highlights": 0.9, "crushed blacks": 0.7, "clipped highlights": 0.35,
          "lifted (milky)": 0.1}
RATIO = {"1:1 flat": 0.1, "2:1 soft": 0.45, "4:1 modelled": 0.8, "8:1+ hard chiaroscuro": 1.0}
FRAMING = {"on phi point": 0.9, "subject on thirds": 0.8, "centred-symmetric": 0.7, "off-balance": 0.3}
CLARITY = {"critical on subject": 1.0, "shallow DOF (subject sharp)": 0.95, "motion blur": 0.5, "soft overall": 0.15}
DEPTH = {"layered (fg / mg / bg)": 1.0, "two-layer": 0.55, "flat against a wall": 0.1}
STYLE = {"motivated practical": 1.0, "low key": 0.9, "naturalistic": 0.8, "mixed": 0.5, "high key": 0.5,
         "flat broadcast": 0.1}
LIGHTQ = {"backlit-rim": 0.95, "silhouette": 0.9, "soft": 0.6, "hard": 0.7, "top-light": 0.4}
BELIEVE = {"believable": 1.0, "generic stock": 0.35, "staged-obvious": 0.1}
PERFORMERS = {"natural behaviour": 1.0, "posed for camera": 0.35, "looking at lens": 0.1}
PV = {"high": 1.0, "mid": 0.5, "low": 0.1}
SHOT_ON = {"cinema": 1.0, "prosumer": 0.7, "drone": 0.6, "DSLR-mirrorless": 0.55, "phone": 0.25}
SAT = {"natural": 0.8, "rich": 0.9, "muted": 0.5, "oversaturated": 0.3}
COLOUR_DESIGN = {"complementary (teal-orange)": 0.85, "analogous warm": 0.8, "analogous cool": 0.8,
                 "monochrome palette": 0.75, "neutral / no design": 0.25}
SET = {"dressed, specific": 1.0, "generic": 0.4, "bare seamless": 0.2}

FLAGS = (("noise", {"noisy"}), ("fidelity", {"banding", "macroblocking", "aliasing"}),
         ("levels", {"clipped highlights", "lifted (milky)"}), ("clarity", {"soft overall"}),
         ("colour_cast", {"green", "magenta", "blue"}), ("horizon", {"tilted"}))


# What the first machine writes when it does not know. "unread" means no vision model has spoken for that slot
# and "unknown" means the measure could not settle it; both are the absence of a value, and neither is a value.
# 261114, first real ledger: these were being read as words, so a place filter compared "thailand" against "unread"
# and threw the whole pool out, and a reason cited "depth unread" as though that said something.
# "none" is NOT here: for colour_cast and cliche_markers it is a real, and good, answer.
BLANK = {"", "unresolved", "unknown", "unread", "n/a", "na", "absent", "pending", "todo"}


def val(row, field):
    """The row's value for one field, or None when the row does not know it.

    Reads the field from the row's `fields` block, then from the row itself, because the ledger keeps its
    identity (segment_id, clip_id, source) at the top level and its slots inside `fields`.
    """
    f = row.get("fields")
    v = f.get(field) if isinstance(f, dict) else None
    if v is None:
        v = row.get(field)
    if isinstance(v, dict):
        v = v.get("value")
    if isinstance(v, str) and v.strip().lower() in BLANK:
        return None
    if v in (None, ""):
        return None
    return v


def look(row, field, table):
    v = val(row, field)
    return table.get(v) if v is not None else None


def mean(xs):
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs)) if xs else None


def flags_of(row):
    return [f for f, bad in FLAGS if (val(row, f) in bad)]


def sliders(row):
    """One totem pole: every slider the row can answer, each 0..1. A slider the row cannot answer is absent.

    Not one of these counts anything. The detector's boxes live in the row for search and stop there.
    """
    energy = mean([look(row, "camera_move", MOVE), look(row, "move_speed", SPEED), look(row, "subject_motion", SPEED)])
    s = {
        "energy": energy,
        "purpose": mean([look(row, "framing", FRAMING), look(row, "clarity", CLARITY), look(row, "depth", DEPTH)]),
        "contrast": mean([look(row, "levels", LEVELS), look(row, "lighting_ratio", RATIO)]),
        "layers": look(row, "depth", DEPTH),
        "cinematic": mean([look(row, "lighting_style", STYLE), look(row, "clarity", CLARITY),
                           look(row, "colour_design", COLOUR_DESIGN), look(row, "camera_move", MOVE)]),
        "believable": mean([look(row, "believability", BELIEVE), look(row, "pv_performers", PERFORMERS),
                            1.0 if val(row, "cliche_markers") == "none" else (0.2 if val(row, "cliche_markers") else None)]),
        "production": mean([look(row, "production_value", PV), look(row, "shot_on", SHOT_ON), look(row, "pv_set", SET)]),
        "light": mean([look(row, "quality_of_light", LIGHTQ), look(row, "lighting_ratio", RATIO)]),
        "colour": mean([look(row, "saturation", SAT), look(row, "colour_design", COLOUR_DESIGN),
                        1.0 if val(row, "colour_cast") == "none" else None]),
        "clean": 1.0 if not flags_of(row) else max(0.0, 1.0 - 0.25 * len(flags_of(row))),
    }
    s["calm"] = None if energy is None else (1.0 - energy) * (1.0 if val(row, "horizon") == "level" else 0.85)
    return {k: round(v, 4) for k, v in s.items() if v is not None}


# ---------------------------------------------------------------- the templates
def load_template(name, seen=None):
    """A template file, with its inherits chain already folded in."""
    f = TEMPLATES / ("%s.json" % name)
    if not f.exists():
        raise SystemExit("reflect: no template %r in %s (have: %s)"
                         % (name, TEMPLATES, ", ".join(sorted(p.stem for p in TEMPLATES.glob("*.json")))))
    t = json.loads(f.read_text())
    seen = (seen or set()) | {name}
    parent = t.get("inherits")
    if parent and parent not in ("none", None) and parent not in seen:
        base = load_template(parent, seen)
        base.update({k: v for k, v in t.items() if v not in (None, "none")})
        base["hard"] = list(dict.fromkeys(list(base.get("hard") or []) + list(t.get("hard") or [])))
        return base
    return t


def weights_of(t):
    return {w["slider"]: float(w["weight"]) for w in t["weights"]}


def all_templates():
    out = {}
    for p in sorted(TEMPLATES.glob("*.json")):
        try:
            out[p.stem] = json.loads(p.read_text())
        except Exception:  # noqa: BLE001  a broken template must not hide the working ones
            pass
    return out


# ---------------------------------------------------------------- the ask
SLIDER_WORDS = ("energy", "purpose", "contrast", "layers", "cinematic", "believable", "production", "calm",
                "clean", "light", "colour", "color")
STOP = {"the", "a", "an", "find", "me", "show", "get", "give", "clips", "clip", "shots", "shot", "of", "with",
        "and", "for", "best", "most", "top", "please", "footage", "ones", "that", "are", "is", "in", "at", "on",
        "by", "up", "to", "from", "excluded", "exclude", "no", "not", "only", "high", "low", "group", "grouped"}


def parse_ask(text, rows):
    """Plain words -> {template, count, musts, must_nots, overrides, group_by, ignored}. Nothing is dropped silently."""
    t = (text or "").strip()
    low = " " + t.lower() + " "
    used = []
    p = {"template": None, "count": None, "musts": {}, "must_nots": {}, "overrides": {}, "group_by": None}

    m = re.search(r"\b(\d{1,3})\b", low)
    if m:
        p["count"] = int(m.group(1))
        used.append(m.group(1))

    for name, tpl in sorted(all_templates().items(), key=lambda kv: -max(len(s) for s in (kv[1].get("says") or [kv[0]]))):
        for phrase in sorted((tpl.get("says") or [name]), key=len, reverse=True):
            if (" " + phrase.lower() + " ") in low or low.strip().endswith(" " + phrase.lower()):
                p["template"] = name
                used += phrase.lower().split()
                break
        if p["template"]:
            break
    if not p["template"]:
        p["template"] = "best"

    for s in SLIDER_WORDS:
        for word, mult in (("high", 2.0), ("up", 2.0), ("low", 0.5), ("down", 0.5)):
            if re.search(r"\b%s\s+%s\b" % (s, word), low) or re.search(r"\b%s\s+%s\b" % (word, s), low):
                p["overrides"][("colour" if s == "color" else s)] = mult
                used += [s, word]

    m = re.search(r"\bby (place|time of day|time|source|source class|subject)\b", low)
    if m:
        p["group_by"] = {"time": "time_of_day", "time of day": "time_of_day", "source": "shot_on",
                         "source class": "shot_on", "place": "place", "subject": "subject"}[m.group(1)]
        used += ["by"] + m.group(1).split()

    # nouns and places against what the pool actually carries, so a filter is never invented
    known = {"subject": set(), "place": set(), "time_of_day": set(), "shot_on": set()}
    for r in rows:
        for f in known:
            v = val(r, f)
            if v:
                known[f].add(str(v).lower())
    neg = bool(re.search(r"\b(no|not|without|excluded|exclude|excluding)\b", low))
    for field, vals in known.items():
        for v in sorted(vals, key=len, reverse=True):
            if (" " + v + " ") in low:
                where = p["must_nots"] if (neg and re.search(r"\b(no|not|without|excluded|exclude|excluding)\b.{0,30}%s|%s.{0,30}\b(excluded|exclude)\b" % (re.escape(v), re.escape(v)), low)) else p["musts"]
                where.setdefault(field, set()).add(v)
                used += v.split()
    for word, field, v in (("phone", "shot_on", "phone"), ("drone", "shot_on", "drone"), ("cinema", "shot_on", "cinema")):
        if re.search(r"\b(no|not|without|excluding)\s+%s\b|\b%s\b[^.]{0,20}\bexclud" % (word, word), low):
            p["must_nots"].setdefault(field, set()).add(v)
            used.append(word)

    # A place the pool has never heard of is still a filter. 261114: "in Thailand" used to fall through to the
    # ignored list, which silently deleted the ask's hardest constraint. Now it becomes a place filter, and every
    # row that cannot confirm its place lands in the unconfirmed tail instead of being ranked as if nothing was asked.
    if "place" not in p["musts"] and "place" not in p["must_nots"]:
        m = re.search(r"\b(?:in|at)\s+((?:[A-Z][\w-]+)(?:\s+[A-Z][\w-]+)?)", t)
        if m:
            p["musts"].setdefault("place", set()).add(m.group(1).lower())
            used += m.group(1).lower().split() + ["in", "at"]

    words = [w for w in re.findall(r"[a-z]+", t.lower()) if w not in STOP]
    p["ignored"] = sorted({w for w in words if w not in set(used)})
    return p


def echo(p):
    bits = []
    for f, vs in sorted(p["musts"].items()):
        bits.append("%s = %s" % (f, " / ".join(sorted(vs))))
    for f, vs in sorted(p["must_nots"].items()):
        bits.append("no %s %s" % (f, " / ".join(sorted(vs))))
    tpl = p["template"]
    if p["overrides"]:
        tpl += " (" + ", ".join("%s x%s" % (k, ("2" if v == 2.0 else "0.5")) for k, v in sorted(p["overrides"].items())) + ")"
    bits.append(tpl)
    if p["group_by"]:
        bits.append("by " + p["group_by"])
    if p["count"]:
        bits.append("up to %d" % p["count"])
    return " · ".join(bits)


# ---------------------------------------------------------------- hard filters
FAULT = {"fidelity": {"macroblocking"}, "levels": set()}


def hard_fail(row, t, p):
    """(reason, unconfirmed) -> reason is why the shot is out; unconfirmed means the row simply does not know."""
    for f, vs in (p["musts"] or {}).items():
        v = val(row, f)
        if v is None:
            return ("%s unknown" % f, True)
        if str(v).lower() not in vs:
            return ("%s is %s" % (f, v), False)
    for f, vs in (p["must_nots"] or {}).items():
        v = val(row, f)
        if v is not None and str(v).lower() in vs:
            return ("%s is %s" % (f, v), False)
    for rule in (t.get("hard") or []):
        r = rule.lower()
        if "size" in r:
            want = re.findall(r"\b(EWS|WS|MWS|MS|MCU|CU|ECU|insert)\b", rule)
            v = val(row, "shot_size")
            if want and v is None:
                return ("shot_size unknown", True)
            if want and v not in want:
                return ("shot_size %s, template wants %s" % (v, "/".join(want)), False)
        if "arc_energy != held" in r or "not held" in r:
            if val(row, "arc_energy") == "held":
                return ("arc is held", False)
        if "licence" in r and val(row, "licence") == "editorial-only":
            return ("licence editorial-only", False)
        if "clean" in r and flags_of(row):
            return ("flags: %s" % ", ".join(flags_of(row)), False)
        if "staged-obvious" in r and val(row, "believability") == "staged-obvious":
            return ("believability staged-obvious", False)
        if "locked / slow" in r or "still / slow" in r:
            if val(row, "move_speed") not in (None, "still", "slow"):
                return ("move_speed %s" % val(row, "move_speed"), False)
    return (None, False)


# ---------------------------------------------------------------- the score
def score(pole, w, overrides=None):
    """(score 0..1, used weights, missing sliders). Unknown sliders drop out and the rest renormalise."""
    w = dict(w)
    for k, mult in (overrides or {}).items():
        if k in w:
            w[k] = w[k] * mult
    have = {k: v for k, v in w.items() if k in pole}
    missing = sorted(set(w) - set(have))
    tot = sum(have.values())
    if not tot:
        return (None, {}, missing)
    return (round(sum(pole[k] * v for k, v in have.items()) / tot, 4),
            {k: round(v / tot, 4) for k, v in have.items()}, missing)


TIE_ORDER = ("mood", "believability", "novelty_in_pool", "licence")


def tie_key(item):
    row, pole = item["row"], item["pole"]
    nov = {"unique": 0, "1 sibling": 1, "common idea": 2}.get(val(row, "novelty_in_pool"), 3)
    return (-(pole.get("believable") or 0), nov, len(flags_of(row)), str(val(row, "shot_id") or ""))


def reason(item, t, top=2):
    """Why this shot sits here, in the row's own words. Cites the template's must_cite fields. Never a count."""
    row, used = item["row"], item["used"]
    best = sorted(used.items(), key=lambda kv: -(kv[1] * item["pole"].get(kv[0], 0)))[:top]
    cited = []
    for f in (t.get("must_cite") or [])[:5]:
        for cand in (f, f.replace("_or_", "/").split("/")[0], "lighting_ratio" if "ratio" in f else f,
                     "framing" if "framing" in f else f, "depth" if "depth" in f else f):
            v = val(row, cand)
            if v is not None:
                cited.append("%s %s" % (cand, v))
                break
    on = ", ".join("%s %.2f" % (k, item["pole"].get(k, 0)) for k, _ in best)
    return "leads on %s; %s" % (on, "; ".join(dict.fromkeys(cited)) or "no cited field in the row")


# ---------------------------------------------------------------- the run
def fabricate(rows, ask=None, template=None, count=None, shuffle_check=True):
    p = parse_ask(ask or "", rows)
    if template:
        p["template"] = template
    if count:
        p["count"] = count
    t = load_template(p["template"])
    w = weights_of(t)
    cap = bool((t.get("switches") or {}).get("cap_staged_obvious", True))

    kept, out, unconfirmed = [], [], []
    for row in rows:
        why, unknown = hard_fail(row, t, p)
        if why:
            (unconfirmed if unknown else out).append({"row": row, "why": why})
            continue
        pole = sliders(row)
        sc, used, missing = score(pole, w, p["overrides"])
        if sc is None:
            unconfirmed.append({"row": row, "why": "no slider the template weighs"})
            continue
        kept.append({"row": row, "pole": pole, "score": sc, "used": used, "missing": missing,
                     "capped": cap and val(row, "believability") == "staged-obvious"})

    def order(items, seed=None):
        it = list(items)
        if seed is not None:
            random.Random(seed).shuffle(it)
        return sorted(it, key=lambda i: (1 if i["capped"] else 0, -i["score"]) + tie_key(i))

    ranked = order(kept)
    unstable = False
    if shuffle_check and len(ranked) > 1:
        unstable = [i["row"].get("shot_id") for i in order(kept, 1)] != [i["row"].get("shot_id") for i in ranked]
    for a, b in zip(ranked, ranked[1:]):
        if abs(a["score"] - b["score"]) < 1e-9 and tie_key(a) == tie_key(b):
            a["tie"] = b["tie"] = True
    return {"parse": p, "echo": echo(p), "template": t, "ranked": ranked, "out": out,
            "unconfirmed": unconfirmed, "unstable": unstable, "asked_for": p["count"]}


def group(res, field):
    g = {}
    for i in res["ranked"]:
        g.setdefault(val(i["row"], field) or "unknown", []).append(i)
    return dict(sorted(g.items(), key=lambda kv: (-len(kv[1]), str(kv[0]))))


def sid(row):
    """A name for this shot a person can find again: the ledger's own id, else the file it came from."""
    for f in ("shot_id", "segment_id", "clip_id"):
        v = val(row, f)
        if v:
            return str(v)
    src = val(row, "source")
    return Path(str(src)).name if src else "?"


def render(res, ask):
    t = res["template"]
    n = res["asked_for"]
    rk = res["ranked"][:n] if n else res["ranked"]
    L = ["# The fabricator · %s" % (ask or ("template " + t["name"])), "",
         "**Parse.** %s" % res["echo"],
         "**Ignored words.** %s" % (", ".join(res["parse"]["ignored"]) or "none"),
         "**Template.** `%s` — %s" % (t["name"], " · ".join("%s %.2f" % (x["slider"], x["weight"]) for x in t["weights"])), ""]
    if n and len(res["ranked"]) < n:
        L += ["**%d found, not %d.** The answer is never padded." % (len(res["ranked"]), n), ""]
    if res["unstable"]:
        L += ["**UNSTABLE.** Two shuffled runs disagreed on the order. Treat the ranking as a tie.", ""]
    if res["parse"]["group_by"]:
        for k, items in group(res, res["parse"]["group_by"]).items():
            L += ["## %s · %s (%d)" % (res["parse"]["group_by"], k, len(items)), ""]
            L += ["%d. **%s** %.3f — %s" % (j, sid(i["row"]), i["score"], reason(i, t)) for j, i in enumerate(items[:n or 5], 1)]
            L += [""]
    else:
        L += ["## Ranked", ""]
        for j, i in enumerate(rk, 1):
            tag = " · TIE" if i.get("tie") else ""
            tag += " · capped (staged-obvious)" if i.get("capped") else ""
            L.append("%d. **%s** %.3f%s" % (j, sid(i["row"]), i["score"], tag))
            L.append("   - %s" % reason(i, t))
            if i["missing"]:
                L.append("   - the row could not answer: %s (weights renormalised over the rest)" % ", ".join(i["missing"]))
        L.append("")
    if res["unconfirmed"]:
        L += ["## Unconfirmed (the row does not know a field the ask filters on; not dropped)", ""]
        L += ["- **%s** — %s" % (sid(i["row"]), i["why"]) for i in res["unconfirmed"]] + [""]
    if res["out"]:
        L += ["## Filtered out (%d)" % len(res["out"]), ""]
        L += ["- **%s** — %s" % (sid(i["row"]), i["why"]) for i in res["out"]] + [""]
    L += ["---", "Counts never climb the pole: a detector's boxes are in the row for search, never a reason here.",
          "Sliders are positions, not verdicts. The template is a file a person edits: `templates/%s.json`." % t["name"]]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="the fabricator: rank a pool of ledger rows against an ask")
    ap.add_argument("ledger", nargs="?", help="ledger.jsonl from read_shots.py (one row per shot)")
    ap.add_argument("--ask", help='plain words, e.g. "the 7 most dynamic elephant clips, cinematic high"')
    ap.add_argument("--template", help="force a template by name (else the ask picks it, else best)")
    ap.add_argument("--count", type=int, help="how many to return")
    ap.add_argument("--out", help="write the answer here (markdown); default stdout")
    ap.add_argument("--json-out", help="also write the ranking as jsonl")
    ap.add_argument("--list-templates", action="store_true")
    a = ap.parse_args(argv)
    if a.list_templates:
        for n, t in sorted(all_templates().items()):
            print("%-14s %s" % (n, ", ".join(t.get("says") or [])))
        return 0
    if not a.ledger:
        ap.error("a ledger.jsonl is required (or --list-templates)")
    rows = [json.loads(x) for x in Path(a.ledger).read_text().splitlines() if x.strip()]
    res = fabricate(rows, a.ask, a.template, a.count)
    md = render(res, a.ask)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        Path(a.out).write_text(md)
        print("reflect: %d of %d shot(s) ranked on template %s -> %s"
              % (len(res["ranked"]), len(rows), res["template"]["name"], a.out))
    else:
        print(md)
    if a.json_out:
        with open(a.json_out, "w") as f:
            for i in res["ranked"]:
                f.write(json.dumps({"shot": sid(i["row"]), "score": i["score"], "pole": i["pole"],
                                    "weights_used": i["used"], "missing": i["missing"],
                                    "capped": i["capped"], "template": res["template"]["name"]}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
