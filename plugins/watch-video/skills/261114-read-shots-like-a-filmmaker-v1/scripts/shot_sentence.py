#!/usr/bin/env python3
"""shot_sentence.py: Sexton's shot sentence, printed by code from the ledger slots (never written by a model).

  shot_sentence.py ledger.jsonl [--md sentences.md]     # one line per shot, then the pool grouped by subject + place

Template (wp/261114-read-shots-like-a-filmmaker-v1/inputs/editors_eye_shot_grammar.md section 1):
  [size] [angle] [move, speed] on [subject: variation + noun] [verb] [manner] [direction] [place], [time of day] ([light]),
  [source]; [arc shape]: [head] -> [mid] -> [tail]
A slot that is unknown or unread is dropped from the sentence; its gap stays in the row. Five shots then differ only where
their slots differ, so an editor can compare them word for word (section 5: variation is a diff).
"""
import argparse
import json
import sys

U, UNREAD = "unknown", "unread"
SIZE = {"EWS": "Extreme wide", "WS": "Wide", "MWS": "Medium wide", "MS": "Medium", "MCU": "Medium close-up",
        "CU": "Close-up", "ECU": "Extreme close-up", "insert": "Insert"}
ANGLE = {"eye level": "eye level", "low": "low angle", "high": "high angle", "overhead": "overhead", "dutch": "dutch angle",
         "POV": "POV", "OTS": "over the shoulder"}
MOVE = {"locked": "locked off", "push in": "push in", "pull out": "pull out", "drone orbit": "drone orbit",
        "drone fly-through": "drone fly-through", "gimbal float": "gimbal float", "crane-jib": "crane"}
AGE = {"young adult": "young", "middle-aged": "middle-aged", "older": "older", "child": "child", "teen": "teenage"}
DOUBLE = {"run", "sit", "swim", "shop", "jog", "hug", "nod", "clap", "skip", "stop", "cut", "put", "get", "dig", "spin", "wave_"}
# the slots compared inside a pool group (the variation diff), in sentence order
DIFF_SLOTS = ["shot_size", "camera_angle", "camera_move", "move_speed", "var_form", "var_age", "var_casting", "var_state",
              "subject_mods", "verb", "manner", "direction", "place", "time_of_day", "quality_of_light", "sun_warmth",
              "source_quality_class", "source_camera", "arc_shape", "arc_energy"]


def v(f, k):
    x = (f.get(k) or {}).get("value", U)
    return None if x in (U, UNREAD, None, "") else x


def ing(lemma):
    """walk -> walking, run -> running, embrace -> embracing, lie -> lying."""
    if lemma.endswith("ie"):
        return lemma[:-2] + "ying"
    if lemma.endswith("e") and not lemma.endswith("ee"):
        return lemma[:-1] + "ing"
    if lemma in DOUBLE:
        return lemma + lemma[-1] + "ing"
    return lemma + "ing"


def subject_phrase(f):
    noun = v(f, "subject")
    if not noun:
        return None
    form = v(f, "var_form")
    mods = v(f, "subject_mods")
    words = [AGE.get(v(f, "var_age"), v(f, "var_age")), v(f, "var_casting")]
    if form and form not in ("solo", noun):
        words.append(form + "'s")          # "older couple's clasped hands"
    if mods and len(mods.split()) == 1:
        words.append(mods)                 # one-word modifier before the noun
    words.append(noun)
    if mods and len(mods.split()) > 1:
        words.append(mods)                 # "couple small in frame"
    if v(f, "var_state"):
        words.append(v(f, "var_state"))
    return " ".join(w for w in words if w)


def render(f):
    """f: the row's fields dict. Returns the sentence (empty slots omitted cleanly)."""
    head = []
    if v(f, "shot_size"):
        head.append(SIZE.get(v(f, "shot_size"), v(f, "shot_size")))
    if v(f, "camera_angle"):
        head.append(ANGLE.get(v(f, "camera_angle"), v(f, "camera_angle")))
    mv = v(f, "camera_move")
    if mv:
        sp = v(f, "move_speed")
        head.append(MOVE.get(mv, mv) if mv == "locked" or not sp or sp == "still" else "%s %s" % (sp, MOVE.get(mv, mv)))
    s = ", ".join(head)
    who, verb = subject_phrase(f), (ing(v(f, "verb")) if v(f, "verb") else None)
    # manner and direction describe an action: printed only once a subject or a verb is read
    act = [who, verb] + ([v(f, "manner"), v(f, "direction")] if (who or verb) else []) + [v(f, "place")]
    act = " ".join(a for a in act if a)
    if act:
        s = (s + (" on " if who else ", ") + act) if s else act
    when = v(f, "time_of_day")
    light = v(f, "quality_of_light")
    if when or light:
        s += ", " + (when or "") + (" (%s light)" % light if light and when else ("%s light" % light if light else ""))
    cam, cls, codec = v(f, "source_camera"), v(f, "source_quality_class"), v(f, "source_codec")
    if cam or cls:
        inner = ", ".join(x for x in (cls, codec) if x)
        s += ", shot on " + (cam or "unlisted camera") + (" (%s)" % inner if inner else "")
    shape = v(f, "arc_shape")
    arc = [x for x in (v(f, "arc_head"), v(f, "arc_mid"), v(f, "arc_tail")) if x]
    if shape or arc:
        s += "; " + (shape or "arc") + (": " + " → ".join(arc) if arc else "")
    s = s.strip(" ,;")
    return (s[0].upper() + s[1:]) if s else "unread shot"


def group_key(f):
    place = v(f, "place")
    return "%s · %s" % (v(f, "subject") or UNREAD, place.split()[-1] if place else UNREAD)


def pool_diff(rows):
    """{group key: (same: [(slot, value)], differ: [(slot, [values])], rows)}; Sexton section 5, computed, never invented."""
    groups = {}
    for r in rows:
        groups.setdefault(group_key(r["fields"]), []).append(r)
    out = {}
    for k, rs in groups.items():
        same, differ = [], []
        for slot in DIFF_SLOTS:
            vals = [v(r["fields"], slot) or "?" for r in rs]
            uniq = list(dict.fromkeys(vals))
            if len(uniq) == 1 and uniq[0] != "?":
                same.append((slot, uniq[0]))
            elif len(uniq) > 1:
                differ.append((slot, vals))
        out[k] = (same, differ, rs)
    return out


def tally(vals):
    """In row order for a small group (word for word, Sexton's five); counted when the group is large."""
    if len(vals) <= 6:
        return " / ".join(vals)
    c = {}
    for x in vals:
        c[x] = c.get(x, 0) + 1
    return " / ".join("%s x%d" % (k, n) if n > 1 else k for k, n in sorted(c.items(), key=lambda kv: -kv[1]))


def markdown(rows, title="Shots"):
    L = ["# %s" % title, "", "One line per shot, printed by code from the ledger slots (shot_sentence.py). "
         "Unread slots are dropped until the vision lane fills them.", ""]
    for r in rows:
        flag = "  **(indistinct: ask the vision lane for state, manner or place)**" if r.get("indistinct") else ""
        L.append("- `%s` %s%s" % (r["segment_id"].split("/")[-1], r["fields"]["sentence"]["value"], flag))
    L += ["", "## The pool, by subject + place (variation as a diff)", ""]
    for k, (same, differ, rs) in pool_diff(rows).items():
        L.append("### %s (%d shot%s)" % (k, len(rs), "" if len(rs) == 1 else "s"))
        L.append("- same: " + (", ".join("%s (%s)" % s for s in same) or "nothing known in common yet"))
        if len(rs) > 1:
            L.append("- differ on: " + ("; ".join("%s (%s)" % (s, tally(vals)) for s, vals in differ) or "nothing (indistinct)"))
        L.append("")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ledger")
    ap.add_argument("--md", help="write sentences.md here")
    a = ap.parse_args(argv)
    rows = [json.loads(l) for l in open(a.ledger) if l.strip()]
    for r in rows:
        print(render(r["fields"]))
    if a.md:
        with open(a.md, "w") as f:
            f.write(markdown(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
