#!/usr/bin/env python3
"""shot_ledger.py: Sexton's ledger row as the contract (P14). One row per shot; every field a closed vocabulary.

  shot_ledger.py build --measures measures.jsonl [--read read.jsonl] [--source source.jsonl] --out ledger.jsonl
  shot_ledger.py validate ledger.jsonl

Schema: wp/261114-read-shots-like-a-filmmaker-v1/inputs/editors_eye_ledger_and_reflection.md part 1 (references/ledger-row.md).
Each field is {"value", "origin": M|R|S|P, "trust": machine|machine~|person}. A free word, a missing origin or a bare
number in a vocabulary field fails validation, naming the field and the allowed list. Read fields are "unread" until
the read lane (P12) lands. Element tags and counts are never in the row (D010).
"""
import argparse
import json
import math
import os
import re
import sys

U = "unknown"
UNREAD = "unread"
# measures.jsonl trust words -> Sexton's trust column
TRUST_MAP = {"trusted": "machine", "approximate": "machine~", "person": "person"}

# name: (origin, trust, vocabulary list | regex string for "as listed" source facts)
SCHEMA = {
    # ---- S: source facts
    "shot_on": ("S", "machine", ["cinema", "prosumer", "DSLR-mirrorless", "phone", "drone"]),
    "native_res": ("S", "machine", r"^\d{3,5}x\d{3,5}$"),
    "fps": ("S", "machine", r"^\d{1,3}(\.\d{1,3})?$"),
    "bit_depth": ("S", "machine", ["8-bit", "10-bit", "12-bit", "16-bit"]),
    "transfer": ("S", "machine", ["Rec.709", "log", "HLG"]),
    "licence": ("S", "machine", ["comp", "licensed", "screener", "editorial-only"]),
    "source": ("S", "machine", r"^[^\n]{1,120}$"),
    "filmmaker": ("S", "machine", r"^[^\n]{1,120}$"),
    "collection": ("S", "machine", r"^[^\n]{1,120}$"),
    # ---- M: measured from pixels
    "levels": ("M", "machine", ["crushed blacks", "full range", "lifted (milky)", "clipped highlights", "protected highlights"]),
    "lighting_ratio": ("M", "machine~", ["1:1 flat", "2:1 soft", "4:1 modelled", "8:1+ hard chiaroscuro"]),
    "colour_temperature": ("M", "machine~", ["warm", "neutral", "cool", "mixed"]),
    "colour_cast": ("M", "machine~", ["none", "green", "magenta", "blue"]),
    "saturation": ("M", "machine", ["muted", "natural", "rich", "oversaturated"]),
    "fidelity": ("M", "machine", ["clean", "banding", "macroblocking", "aliasing"]),
    "clarity": ("M", "machine~", ["critical on subject", "shallow DOF (subject sharp)", "soft overall", "motion blur"]),
    "noise": ("M", "machine", ["clean", "fine grain", "noisy"]),
    "camera_move": ("M", "machine~", ["locked", "pan", "tilt", "push in", "pull out", "track", "handheld", "gimbal float",
                                     "crane-jib", "drone orbit", "drone fly-through", "zoom in", "zoom out", "truck left",
                                     "truck right", "pedestal up", "pedestal down"]),
    "move_speed": ("M", "machine", ["still", "slow", "medium", "fast"]),
    "subject_motion": ("M", "machine", ["still", "slow", "medium", "fast"]),
    "capture_speed": ("M", "machine", ["real time", "slow motion"]),
    "shot_size": ("M", "machine~", ["EWS", "WS", "MWS", "MS", "MCU", "CU", "ECU", "insert"]),
    "framing": ("M", "machine~", ["subject on thirds", "on phi point", "centred-symmetric", "off-balance"]),
    "headroom": ("M", "machine~", ["tight", "right", "excess"]),
    "lead_room": ("M", "machine~", ["right", "short"]),
    "horizon": ("M", "machine", ["level", "tilted", "dutch"]),
    # ---- R: read by the vision lane from the hero frame (evidence required when read)
    "lighting_style": ("R", "machine~", ["high key", "low key", "naturalistic", "motivated practical", "mixed", "flat broadcast"]),
    "quality_of_light": ("R", "machine~", ["hard", "soft", "backlit-rim", "silhouette", "top-light"]),
    "time_of_day": ("R", "machine~", ["dawn", "golden hour", "sunset", "day", "overcast", "blue hour", "night", "interior"]),
    "colour_design": ("R", "person", ["complementary (teal-orange)", "analogous warm", "analogous cool", "monochrome palette",
                                      "neutral", "accidental mixed"]),
    "depth": ("R", "machine~", ["layered (fg / mg / bg)", "two-layer", "flat against a wall"]),
    "mood": ("R", "person", ["serene", "tense", "intimate", "melancholy", "joyful", "clinical", "ominous", "triumphant", "neutral"]),
    "tone": ("R", "person", ["sincere", "documentary", "aspirational", "corporate", "playful", "dramatic", "ironic"]),
    "production_value": ("R", "person", ["high", "mid", "low"]),
    "pv_set": ("R", "person", ["dressed, specific", "generic", "bare seamless"]),
    "pv_props": ("R", "person", ["real, used, period-right", "new plastic", "none"]),
    "pv_performers": ("R", "person", ["natural behaviour", "posed for camera", "looking at lens"]),
    "pv_staging": ("R", "person", ["blocked in depth, everything in its place", "flat line-up"]),
    "believability": ("R", "person", ["believable", "generic stock", "staged-obvious"]),
    "cliche_markers": ("R", "machine~", ["none", "smile at lens", "arms folded at lens", "thumbs up", "handshake to camera",
                                         "pointing at screen", "laughing at nothing", "stethoscope draped", "clipboard nod",
                                         "high-five team", "lightbulb idea"]),
    # ---- P: pool fact (computed against the pool, never alone; D007)
    "novelty_in_pool": ("P", "machine", ["unique", "1 sibling", "common idea"]),
    # ---- the grammar (Sexton, editors_eye_shot_grammar.md sections 2-4): the slots the sentence is printed from.
    # ("vocab", key) = one word from references/vocab.json (a person extends it); ("words", n) = bounded free text, <= n words.
    "camera_angle": ("R", "machine~", ["eye level", "low", "high", "overhead", "dutch", "POV", "OTS"]),
    "subject": ("R", "machine~", ("vocab", "nouns")),
    "subject_mods": ("R", "machine~", ("words", 3)),
    "var_form": ("R", "machine~", ["solo", "couple", "family", "group", "crowd"]),
    "var_age": ("R", "machine~", ["child", "teen", "young adult", "middle-aged", "older"]),
    "var_casting": ("S", "person", ("words", 4)),
    "var_state": ("R", "machine~", ("words", 2)),
    "verb": ("R", "machine~", ("vocab", "verbs")),
    "manner": ("M", "machine~", ("words", 4)),
    "direction": ("M", "machine~", ["left to right", "right to left", "toward camera", "away from camera", "across", "static"]),
    "place": ("R", "machine~", ("words", 4)),
    "sun_warmth": ("M", "machine~", ["sunset-red", "warm", "neutral", "cool"]),
    "source_camera": ("S", "machine~", r"^[^\n]{1,40}$"),
    "source_codec": ("S", "machine", r"^[^\n]{1,40}$"),
    "source_quality_class": ("S", "machine~", ["cinema-A", "cinema-B", "prosumer", "drone", "DSLR", "phone"]),
    "arc_frames": ("M", "machine~", "frames"),
    "arc_head": ("M", "machine~", ("words", 4)),
    "arc_mid": ("M", "machine~", ("words", 4)),
    "arc_tail": ("M", "machine~", ("words", 4)),
    "arc_shape": ("M", "machine~", ["held", "push", "pull/reveal", "pass-through", "rack", "soft", "whip", "follow", "orbit",
                                   "enters/leaves"]),
    "arc_energy": ("M", "machine~", ["dynamic", "modest", "held"]),
    "sentence": ("M", "machine", r"^[^\n]{1,400}$"),
}
GRAMMAR = ["camera_angle", "subject", "subject_mods", "var_form", "var_age", "var_casting", "var_state", "verb", "manner",
           "direction", "place", "sun_warmth", "source_camera", "source_codec", "source_quality_class", "arc_frames", "arc_head",
           "arc_mid", "arc_tail", "arc_shape", "arc_energy", "sentence"]
# a field that may legally carry a second origin: casting from a person (never R); dutch angle from the measured horizon
ALT_ORIGINS = {"var_casting": {"S", "P"}, "camera_angle": {"R", "M"},
               "time_of_day": {"R", "M"}, "quality_of_light": {"R", "M"}}   # measured by shot_light.py when no read exists
VOCAB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references", "vocab.json")
_VOCAB = None


def vocab(key):
    global _VOCAB
    if _VOCAB is None:
        with open(VOCAB_PATH) as f:
            _VOCAB = json.load(f)
    return _VOCAB.get(key, [])
ORIGINS = {"M", "R", "S", "P"}
TRUSTS = {"machine", "machine~", "person"}
COUNT_RE = re.compile(r"\b\d+\s+(people|persons|doctors|nurses|patients|elements|objects|faces|men|women|kids|children|birds|trees|cars)\b", re.I)


def allowed(name):
    spec = SCHEMA[name][2]
    extra = [U] + ([UNREAD] if SCHEMA[name][0] == "R" else [])
    if isinstance(spec, tuple) and spec[0] == "vocab":
        return vocab(spec[1]) + extra + ["(extend references/vocab.json '%s' to admit a word)" % spec[1]]
    if isinstance(spec, tuple):
        return "free text of at most %d words, no digits, or %s" % (spec[1], "/".join(extra))
    if spec == "frames":
        return "a list of 3-6 {pos, size, subject_state, focus}, or %s" % "/".join(extra)
    return (spec + extra) if isinstance(spec, list) else ("as listed, matching %s, or %s" % (spec, "/".join(extra)))


FRAME_STATES = {"in", "entering", "leaving", "absent", "part"}
FRAME_FOCUS = {"sharp", "soft", "racking", "unknown"}


def check_value(name, v):
    """One field's value against its spec; returns an error string or None."""
    spec = SCHEMA[name][2]
    if isinstance(spec, list):
        return None if v in spec else "%s: free word %r; allowed: %s" % (name, v, " / ".join(allowed(name)))
    if isinstance(spec, tuple) and spec[0] == "vocab":
        return None if v in vocab(spec[1]) else "%s: free word %r; allowed: %s" % (name, v, " / ".join(allowed(name)))
    if isinstance(spec, tuple):
        ok = isinstance(v, str) and 0 < len(v.split()) <= spec[1] and not re.search(r"\d", v)
        return None if ok else "%s: %r is not bounded text; allowed: %s" % (name, v, allowed(name))
    if spec == "frames":
        ok = isinstance(v, list) and 3 <= len(v) <= 6 and all(
            isinstance(x, dict) and isinstance(x.get("pos"), (int, float)) and x.get("size") in SCHEMA["shot_size"][2] + [U]
            and x.get("subject_state") in FRAME_STATES and x.get("focus") in FRAME_FOCUS for x in v)
        return None if ok else "%s: not a list of 3-6 arc frames; allowed: %s" % (name, allowed(name))
    return None if (isinstance(v, str) and re.match(spec, v)) else "%s: %r does not match; allowed: %s" % (name, v, allowed(name))


def F(name, value, evidence=None, origin=None):
    o, t, _ = SCHEMA[name]
    d = {"value": value, "origin": origin or o, "trust": t}
    if evidence:
        d["evidence"] = evidence
    return d


def val(m, k):
    v = (m.get(k) or {}).get("value", U)
    return v if isinstance(v, dict) else None


# ---------------------------------------------------------------- measured words from shot_measure numbers
def words(rec):
    m = rec.get("measures", {})
    w = {}
    lv = val(m, "levels")
    if lv:
        if lv["pct_at_or_above_235"] > 1.0 or lv["p99_ire"] > 103:
            w["levels"] = ("clipped highlights", "p99 %.0f IRE, %.1f%% at 235" % (lv["p99_ire"], lv["pct_at_or_above_235"]))
        elif lv["pct_at_or_below_16"] > 2.0 or lv["p1_ire"] < -1:
            w["levels"] = ("crushed blacks", "p1 %.0f IRE, %.1f%% at 16" % (lv["p1_ire"], lv["pct_at_or_below_16"]))
        elif lv["p1_ire"] > 10:
            w["levels"] = ("lifted (milky)", "p1 %.0f IRE" % lv["p1_ire"])
        elif 85 <= lv["p99_ire"] <= 100:
            w["levels"] = ("protected highlights", "p99 %.0f IRE, no clip" % lv["p99_ire"])
        else:
            w["levels"] = ("full range", "p1 %.0f / p99 %.0f IRE" % (lv["p1_ire"], lv["p99_ire"]))
    lr = val(m, "lighting_ratio")
    if lr:
        s = lr["stops"]
        word = "1:1 flat" if s < 0.5 else "2:1 soft" if s < 1.5 else "4:1 modelled" if s < 2.5 else "8:1+ hard chiaroscuro"
        w["lighting_ratio"] = (word, "%.1f stops on the %s box" % (s, lr["ratio_source"]))
    ct = val(m, "cct")
    if ct:
        k = ct["kelvin"]
        lo, hi = sorted(ct.get("left_right_kelvin", [k, k]))
        if hi - lo > 2000 and lo < 4500 < hi:
            w["colour_temperature"] = ("mixed", "left/right %d K / %d K" % (lo, hi))
        else:
            w["colour_temperature"] = ("warm" if k < 4500 else "cool" if k > 6800 else "neutral", "%d K (estimate)" % k)
        w["colour_cast"] = ("blue" if k > 9500 else ct["tint"], "Duv %+.4f" % ct["duv"])
    sa = val(m, "saturation")
    if sa:
        s = sa["mean"]
        w["saturation"] = ("muted" if s < 0.15 else "natural" if s < 0.35 else "rich" if s < 0.55 else "oversaturated",
                           "mean S %.2f, M %.0f" % (s, sa["colourfulness"]))
    fd = val(m, "fidelity")
    if fd and isinstance(fd.get("blockiness_8x8"), float):
        b = fd["blockiness_8x8"]
        w["fidelity"] = ("macroblocking" if b > 1.5 else "clean", "8x8 edge ratio %.2f (probe)" % b)
    sh, sub = val(m, "sharpness"), val(m, "subject")
    if sh:
        g, mx = sh["laplacian_var"], sh["sharp_region"]["laplacian_var"]
        if mx < 50:
            w["clarity"] = ("soft overall", "max cell Laplacian var %.0f" % mx)
        elif sub and sub.get("box"):
            r_, c_ = sh["sharp_region"]["cell_row_col"]
            on = int(min(2, sub["y"] * 3)) == r_ and int(min(2, sub["x"] * 3)) == c_
            if on and g < 0.35 * mx:
                w["clarity"] = ("shallow DOF (subject sharp)", "subject cell %.0f vs frame %.0f" % (mx, g))
            elif on:
                w["clarity"] = ("critical on subject", "subject cell sharpest, %.0f" % mx)
    nz = val(m, "noise")
    if nz:
        s = nz["sigma"]
        w["noise"] = ("clean" if s < 1.5 else "fine grain" if s < 4 else "noisy", "sigma %.1f" % s)
    mo = val(m, "motion")
    if mo and "per second" in mo["unit"]:
        cam, sb = mo["camera"], mo["subject"]
        sp = cam["speed_pct_w"]
        bucket = lambda v: "still" if v < 1 else "slow" if v < 6 else "medium" if v < 20 else "fast"  # noqa: E731
        w["move_speed"] = (bucket(sp), "camera %.1f%% width/s" % sp)
        w["subject_motion"] = (bucket(sb["p90_residual_pct_w"]), "residual p90 %.1f%% width/s" % sb["p90_residual_pct_w"])
        jit = (val(m, "shake") or {}).get("jitter_pct_w")
        if sp < 1 and abs(cam["zoom_pct"]) < 3:
            w["camera_move"] = ("locked", "camera %.1f%% w/s" % sp)
        elif jit is not None and jit > 0.6:
            w["camera_move"] = ("handheld", "jitter %.2f%% w" % jit)
        elif abs(cam["zoom_pct"]) >= 3 and abs(cam["zoom_pct"]) > max(abs(cam["dx_pct_w"]), abs(cam["dy_pct_w"])):
            # a 2D scale cannot tell a dolly from a zoom: write the scale word only; shot_motion (by axis, D024) decides push vs zoom
            w["camera_move"] = ("zoom in" if cam["zoom_pct"] > 0 else "zoom out", "scale %+.1f%%/s (fallback; shot_motion overrides)" % cam["zoom_pct"])
        elif abs(cam["dx_pct_w"]) >= abs(cam["dy_pct_w"]):
            w["camera_move"] = ("pan", "dx %+.1f%% w/s" % cam["dx_pct_w"])
        else:
            w["camera_move"] = ("tilt", "dy %+.1f%% w/s" % cam["dy_pct_w"])
    fps = (val(m, "fidelity") or {}).get("fps")
    if isinstance(fps, (int, float)) and fps:
        w["capture_speed"] = ("slow motion" if fps >= 48 else "real time", "%.3f fps (assumes a 24/25/30 timeline)" % fps)
    if sub and sub.get("box"):
        h = sub["box"]["h"]
        if sub["subject_source"] == "face":
            size = "ECU" if h > 0.5 else "CU" if h > 0.3 else "MCU" if h > 0.18 else "MS" if h > 0.1 else "MWS" if h > 0.05 else "WS"
        else:
            size = "MS" if h > 0.9 else "MWS" if h > 0.6 else "WS" if h > 0.25 else "EWS"
        w["shot_size"] = (size, "%s box %.0f%% of frame height" % (sub["subject_source"], h * 100))
    fg = val(m, "framing_geometry")
    if fg:
        dt, dp, dc = fg["dist_thirds_pct_diag"], fg["dist_phi_pct_diag"], fg["dist_centre_pct_diag"]
        word = ("centred-symmetric" if dc < 5 else "subject on thirds" if dt < 5 and dt <= dp else
                "on phi point" if dp < 5 else "off-balance")
        w["framing"] = (word, "%s point: thirds %.1f%%, phi %.1f%%, centre %.1f%% of diagonal (geometry, not a score)"
                        % ((sub or {}).get("subject_source", "?"), dt, dp, dc))
    hr = val(m, "headroom")
    if hr:
        p = hr["pct_of_box_height"]
        lim = (10, 60) if hr["box"] == "face" else (5, 25)
        w["headroom"] = ("tight" if p < lim[0] else "excess" if p > lim[1] else "right", "%.0f%% of %s box" % (p, hr["box"]))
    hz = val(m, "horizon")
    if hz and isinstance(hz.get("tilt_deg"), float) and hz["confidence"] >= 0.3:
        t = hz["tilt_deg"]
        w["horizon"] = ("level" if abs(t) < 1 else "dutch" if abs(t) > 8 else "tilted",
                        "%+.1f deg, confidence %.2f, %d lines" % (t, hz["confidence"], hz["lines"]))
    return w


def measured_vector(rec):
    """Numbers only, for novelty against the pool (never shown to the ranker)."""
    m = rec.get("measures", {})
    g = lambda k, *p: _dig(val(m, k), p)  # noqa: E731
    return [g("levels", "p50_ire"), g("contrast", "stops_p95_p5"), g("cct", "kelvin"), g("saturation", "mean"),
            g("saturation", "colourfulness"), g("sharpness", "laplacian_var"), g("noise", "sigma"),
            g("motion", "camera", "speed_pct_w"), g("motion", "subject", "p90_residual_pct_w"),
            g("subject", "x"), g("subject", "y"), g("palette", "hue_spread_deg")]


def _dig(d, path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return float(d) if isinstance(d, (int, float)) else None


def novelty(vectors):
    """Distance of each row's measured vector from the pool mean, in pooled standard units, bucketed."""
    n = len(vectors)
    out = [(U, "pool under 3 rows")] * n
    if n < 3:
        return out
    dims = len(vectors[0])
    cols = []
    for j in range(dims):
        xs = [v[j] for v in vectors if v[j] is not None]
        if len(xs) < 3:
            cols.append(None)
            continue
        if j in (5,):  # sharpness is heavy-tailed
            xs = [math.log1p(x) for x in xs]
        mu = sum(xs) / len(xs)
        sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / len(xs)) or 1.0
        cols.append((mu, sd))
    res = []
    for v in vectors:
        zs = []
        for j, c in enumerate(cols):
            if c and v[j] is not None:
                x = math.log1p(v[j]) if j == 5 else v[j]
                zs.append(((x - c[0]) / c[1]) ** 2)
        if not zs:
            res.append((U, "no measured numbers"))
            continue
        d = math.sqrt(sum(zs) / len(zs))
        word = "unique" if d >= 1.5 else "1 sibling" if d >= 0.9 else "common idea"
        res.append((word, "distance %.2f from pool mean over %d measures, %d rows" % (d, len(zs), n)))
    return res


def load(path):
    rows = []
    if path:
        with open(path) as f:
            rows = [json.loads(l) for l in f if l.strip()]
    return {r["segment_id"]: r for r in rows if "segment_id" in r}


def build(a):
    meas = [json.loads(l) for l in open(a.measures) if l.strip()]
    reads, srcs = load(a.read), load(a.source)
    nov = novelty([measured_vector(r) for r in meas])
    out = []
    for rec, (nw, ne) in zip(meas, nov):
        sid = rec["segment_id"]
        fields = {}
        w = words(rec)
        for name, (o, t, spec) in SCHEMA.items():
            if o == "M":
                v, ev = w.get(name, (U, None))
                if name == "lead_room":
                    ev = "probe: facing direction not built this round"
                fields[name] = F(name, v, ev)
            elif o == "R":
                rd = (reads.get(sid) or {}).get(name)
                if isinstance(rd, dict):
                    fields[name] = F(name, rd.get("value", U), rd.get("evidence"))
                else:
                    fields[name] = F(name, rd if isinstance(rd, str) else UNREAD)
            elif o == "P":
                fields[name] = F(name, nw, ne)
        gm = rec.get("grammar") or {}   # M and S slot values made by shot_arc.py / read_shots.py: {name: {value, evidence[, origin]}}
        rd_all = reads.get(sid) or {}
        for name in GRAMMAR:
            o = SCHEMA[name][0]
            g = rd_all.get(name) if o == "R" or name == "var_casting" else None
            g = g if g is not None else gm.get(name)
            if isinstance(g, dict) and g.get("value") not in (None, ""):
                org = g.get("origin")
                if name == "var_casting" and org not in ("S", "P"):
                    org = "S"   # casting is never inferred by a model (Sexton section 2)
                fields[name] = F(name, g["value"], g.get("evidence"), org)
            elif isinstance(g, str) and g:
                fields[name] = F(name, g)
            else:
                fields[name] = F(name, UNREAD if o == "R" else U)
        for name, g in gm.items():   # the other lanes (shot_motion, shot_light): a dedicated M lane wins; an R slot only when unread
            if name in GRAMMAR or name not in SCHEMA or not isinstance(g, dict) or g.get("value") in (None, U, UNREAD, ""):
                continue
            if check_value(name, g["value"]) is None and (SCHEMA[name][0] == "M" or fields[name]["value"] == UNREAD):
                fields[name] = F(name, g["value"], g.get("evidence"), g.get("origin") or SCHEMA[name][0])
        if fields["camera_angle"]["value"] == UNREAD and fields["horizon"]["value"] == "dutch":
            fields["camera_angle"] = F("camera_angle", "dutch", fields["horizon"].get("evidence"), "M")
        prb = (rec.get("measures", {}).get("fidelity") or {}).get("value") or {}
        sfacts = dict(srcs.get(sid) or {})
        if isinstance(prb, dict):
            sfacts.setdefault("native_res", "%sx%s" % (prb["width"], prb["height"]) if prb.get("width") else U)
            sfacts.setdefault("fps", ("%g" % prb["fps"]) if prb.get("fps") else U)
            sfacts.setdefault("bit_depth", "%d-bit" % prb["bit_depth"] if prb.get("bit_depth") else U)
        tr = rec.get("transfer", U)
        sfacts.setdefault("transfer", tr)
        for name, (o, t, spec) in SCHEMA.items():
            if o == "S" and name not in GRAMMAR:
                v = sfacts.get(name, U)
                fields[name] = F(name, v, rec.get("transfer_source") if name == "transfer" else None)
        row = {"segment_id": sid, "clip_id": rec.get("clip_id", sid.split("#")[0]), "fields": fields}
        row.update({k: rec[k] for k in ("t_in", "t_out", "hero_t") if k in rec})   # SHARED_IDS time rule: times ride on the row
        finish(row)
        out.append(row)
    mark_indistinct(out)
    with open(a.out, "w") as f:
        for row in out:
            f.write(json.dumps(row) + "\n")
    print("shot_ledger build: %d row(s) -> %s" % (len(out), a.out))
    return 0


# the quality facts after the sentence, in Sexton's fixed order (section 4); production value carries its four evidence slots
FACTS = ["levels", "lighting_ratio", "saturation", "clarity", "production_value", "believability", "novelty_in_pool", "licence"]
FACT_WORD = {"novelty_in_pool": {"unique": "unique in pool", "1 sibling": "1 sibling in pool", "common idea": "common idea in pool"}}


def row_text(row):
    """The one line the ranker reads: the code-rendered sentence + the quality facts in a fixed order; no tags, no counts."""
    f = row["fields"]
    parts = [f["sentence"]["value"]] if f.get("sentence", {}).get("value") not in (None, U) else []
    for k in FACTS:
        v = f[k]["value"]
        if v in (U, UNREAD):
            continue
        if k == "production_value":
            pv = [f[p]["value"] for p in ("pv_set", "pv_props", "pv_performers", "pv_staging") if f[p]["value"] not in (U, UNREAD)]
            v = "PV %s" % v + (" (%s)" % ", ".join(pv) if pv else "")
        parts.append(FACT_WORD.get(k, {}).get(v, v))
    return " · ".join(str(p) for p in parts)


def finish(row):
    """Render the sentence from the slots (code, never a model) and the row_text under it."""
    if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import shot_sentence  # noqa: E402  (same folder; imported late so either file can be run alone)
    row["fields"]["sentence"] = F("sentence", shot_sentence.render(row["fields"]), "rendered by shot_sentence.py from the slots")
    row["row_text"] = row_text(row)
    return row


def mark_indistinct(rows):
    """Two rows in one pool with the same sentence: the grammar is too coarse here; flag both (Sexton section 5)."""
    seen = {}
    for r in rows:
        seen.setdefault(r["fields"]["sentence"]["value"], []).append(r)
    for group in seen.values():
        for r in group:
            r["indistinct"] = len(group) > 1
    return rows


def check_row(row):
    errs = []
    sid = row.get("segment_id")
    if not sid or not re.search(r"#S\d{2,}$", str(sid)):
        errs.append("segment_id: missing or not <clip_id>#S<nn> (%r)" % sid)
    fields = row.get("fields")
    if not isinstance(fields, dict):
        return errs + ["fields: missing"]
    for name in SCHEMA:
        if name not in fields:
            errs.append("%s: missing field" % name)
    for name, d in fields.items():
        if name not in SCHEMA:
            errs.append("%s: not a field of the row (allowed fields: %s)" % (name, ", ".join(SCHEMA)))
            continue
        if not isinstance(d, dict):
            errs.append("%s: must be {value, origin, trust}" % name)
            continue
        o, t, spec = SCHEMA[name]
        if d.get("origin") not in ORIGINS:
            errs.append("%s: missing origin (M/R/S/P)" % name)
        elif d["origin"] not in ALT_ORIGINS.get(name, {o}):
            errs.append("%s: origin %s, schema says %s" % (name, d["origin"], "/".join(sorted(ALT_ORIGINS.get(name, {o})))))
        if d.get("trust") not in TRUSTS:
            errs.append("%s: trust %r not in machine/machine~/person" % (name, d.get("trust")))
        v = d.get("value")
        if isinstance(v, bool) or isinstance(v, (int, float)):
            errs.append("%s: a number (%r) is not a value; allowed: %s" % (name, v, allowed(name)))
            continue
        if v in (U,) or (v == UNREAD and o == "R"):
            continue
        e = check_value(name, v)
        if e:
            errs.append(e)
        if isinstance(v, list):
            v = json.dumps(v)
        ev = d.get("evidence") or ""
        if COUNT_RE.search(str(v)) or COUNT_RE.search(ev):
            errs.append("%s: a count of people or objects is used as a value or evidence" % name)
    if COUNT_RE.search(str(row.get("row_text", ""))):
        errs.append("row_text: a count of people or objects is in the text")
    return errs


def validate(path):
    n = bad = 0
    for i, line in enumerate(open(path), 1):
        if not line.strip():
            continue
        n += 1
        errs = check_row(json.loads(line))
        if errs:
            bad += 1
            for e in errs:
                print("FAIL line %d: %s" % (i, e))
    print("shot_ledger validate: %d row(s), %d valid, %d failed -> %s" % (n, n - bad, bad, "PASS" if n and not bad else "FAIL"))
    return 0 if n and not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build")
    b.add_argument("--measures", required=True)
    b.add_argument("--read")
    b.add_argument("--source")
    b.add_argument("--out", required=True)
    v = sp.add_parser("validate")
    v.add_argument("ledger")
    a = ap.parse_args(argv)
    return build(a) if a.cmd == "build" else validate(a.ledger)


if __name__ == "__main__":
    sys.exit(main())
