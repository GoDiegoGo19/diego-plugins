"""261114: the fabricator (reflect.py). The bird test as code: three good shots, the brief picks."""
import importlib.util
import json
import pathlib
import sys
import tempfile

S = pathlib.Path(__file__).resolve().parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("rf", S / "reflect.py")
rf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rf)

fail = 0


def ck(c, m):
    global fail
    print(("ok   " if c else "FAIL ") + m)
    if not c:
        fail += 1


def R(sid, **kw):
    f = {"shot_id": sid}
    f.update(kw)
    return {"fields": f}


BASE = dict(subject="heron", shot_on="cinema", production_value="high", pv_set="dressed, specific",
            pv_performers="natural behaviour", believability="believable", cliche_markers="none",
            novelty_in_pool="unique", licence="licensed", noise="clean", fidelity="clean",
            colour_cast="none", horizon="level", saturation="natural", lighting_style="naturalistic")
SIL = R("P01", place="sky", shot_size="WS", camera_move="locked", move_speed="still", subject_motion="slow",
        levels="crushed blacks", lighting_ratio="8:1+ hard chiaroscuro", quality_of_light="silhouette",
        time_of_day="sunset", depth="two-layer", framing="subject on thirds", clarity="critical on subject",
        colour_design="analogous warm", arc_energy="held", **BASE)
DRA = R("P02", place="ridge", shot_size="MCU", camera_move="track", move_speed="fast", subject_motion="fast",
        levels="full range", lighting_ratio="4:1 modelled", quality_of_light="hard", time_of_day="day",
        depth="two-layer", framing="off-balance", clarity="motion blur",
        colour_design="neutral / no design", arc_energy="dynamic", **BASE)
REF = R("P03", place="still lake", shot_size="WS", camera_move="locked", move_speed="still", subject_motion="slow",
        levels="protected highlights", lighting_ratio="2:1 soft", quality_of_light="soft",
        time_of_day="golden hour", depth="layered (fg / mg / bg)", framing="centred-symmetric",
        clarity="critical on subject", colour_design="analogous cool", arc_energy="held", **BASE)
POOL = [SIL, DRA, REF]


def names(res):
    return [rf.sid(i["row"]) for i in res["ranked"]]


# --- the bird test: the same three shots, two briefs, two different winners
ck(names(rf.fabricate(POOL, "the best bird shots"))[0] == "P02",
   "default template (best = most dynamic) puts the drama shot first")
ck(names(rf.fabricate(POOL, template="reflection"))[0] == "P03",
   "a calm brief puts the reflection shot first: good is script-relative")
ck(names(rf.fabricate(POOL, template="silhouette"))[0] == "P01",
   "the silhouette profile puts the silhouette first")

# --- counts never climb the pole
many = R("P09", **dict(BASE, place="ridge", shot_size="WS", camera_move="locked", move_speed="still",
                       subject_motion="still", levels="lifted (milky)", lighting_ratio="1:1 flat",
                       depth="flat against a wall", framing="off-balance", clarity="soft overall",
                       quality_of_light="top-light", time_of_day="overcast", colour_design="neutral / no design",
                       arc_energy="held"))
many["fields"]["people_count"] = 47
many["fields"]["detections"] = ["bird"] * 47
res = rf.fabricate(POOL + [many], "the best bird shots")
ck(names(res)[-1] == "P09", "a row with 47 detections ranks last: a count is never a criterion")
ck(all("count" not in rf.reason(i, res["template"]).lower() for i in res["ranked"]),
   "no reason text mentions a count")

# --- unknown fields: dropped weight, renormalised, never a silent zero
thin = R("P10", subject="heron", camera_move="track", move_speed="fast", subject_motion="fast", licence="licensed")
res = rf.fabricate([DRA, thin], "best")
got = [i for i in res["ranked"] if rf.sid(i["row"]) == "P10"][0]
ck(got["missing"] and abs(sum(got["used"].values()) - 1.0) < 1e-6,
   "a row missing sliders renormalises the weights it does have (%s missing)" % ", ".join(got["missing"]))
ck(got["score"] > 0.5, "an energetic row is not punished to zero for what the machine could not see")

# --- a place the pool never heard of is still a filter, never a dropped word
p = rf.parse_ask("best shots in Thailand", POOL)
ck("thailand" in (p["musts"].get("place") or set()),
   "a place name absent from the pool still becomes a filter (it must not vanish into ignored)")
ck("thailand" not in p["ignored"], "and it is not listed as ignored, because it was used")

# a row that knows its place and does not match is out; a row that cannot confirm goes to the tail
noplace = R("P13", **dict(BASE, shot_size="WS", camera_move="locked", move_speed="still", subject_motion="slow",
                          levels="full range", lighting_ratio="4:1 modelled", depth="two-layer",
                          framing="subject on thirds", clarity="critical on subject", quality_of_light="soft",
                          time_of_day="day", colour_design="analogous warm", arc_energy="held"))
res = rf.fabricate(POOL + [noplace], "best shots in Thailand")
ck(any(rf.sid(i["row"]) == "P13" for i in res["unconfirmed"]),
   "a row whose place is unknown parks in the unconfirmed tail, it is never dropped")
ck(all("unknown" in i["why"] for i in res["unconfirmed"]), "the tail names the field it could not confirm")
ck({rf.sid(i["row"]) for i in res["out"]} == {"P01", "P02", "P03"},
   "rows that do know their place, and it is not Thailand, are filtered out with a reason")

# --- never padded
res = rf.fabricate(POOL, "the 7 best bird shots")
ck(res["asked_for"] == 7 and len(res["ranked"]) == 3, "asked for 7, found 3, and says 3")
ck("3 found, not 7" in rf.render(res, "the 7 best bird shots"), "the answer states it is not padded")

# --- hard faults and licence
ed = R("P11", **dict(BASE, licence="editorial-only", place="sky", shot_size="WS", camera_move="locked",
                     move_speed="still", subject_motion="slow", levels="full range",
                     lighting_ratio="4:1 modelled", depth="two-layer", framing="subject on thirds",
                     clarity="critical on subject", quality_of_light="soft", time_of_day="day",
                     colour_design="analogous warm", arc_energy="held"))
res = rf.fabricate(POOL + [ed], "best")
ck(any("editorial-only" in i["why"] for i in res["out"]), "editorial-only licence is a hard filter, with the reason")

# --- the staged-obvious cap holds a shot down, it does not drop it
st = R("P12", **dict(BASE, believability="staged-obvious", place="ridge", shot_size="MCU", camera_move="track",
                     move_speed="fast", subject_motion="fast", levels="full range",
                     lighting_ratio="4:1 modelled", depth="layered (fg / mg / bg)", framing="on phi point",
                     clarity="critical on subject", quality_of_light="hard", time_of_day="day",
                     colour_design="complementary (teal-orange)", arc_energy="dynamic"))
res = rf.fabricate([SIL, st], "best")
ck(rf.sid(res["ranked"][-1]["row"]) == "P12" and res["ranked"][-1]["capped"],
   "a staged-obvious shot is capped to the bottom, still present, still scored")

# --- the parse is echoed and nothing is dropped silently
p = rf.parse_ask("find the 7 most dynamic heron clips, cinematic high", POOL)
ck(p["template"] == "best" and p["count"] == 7 and p["overrides"].get("cinematic") == 2.0,
   "the ask parses to template best, count 7, cinematic doubled")
ck("heron" in (p["musts"].get("subject") or set()), "a noun the pool carries becomes a subject filter")
ck(isinstance(p["ignored"], list), "every unused word is listed: %s" % (p["ignored"] or "none"))

# --- overrides actually move the ranking
a = rf.fabricate(POOL, "best bird shots")
b = rf.fabricate(POOL, "best bird shots, calm high, energy low")
ck(names(a) != names(b), "moving two sliders in the ask changes the order (%s -> %s)" % (names(a), names(b)))

# --- templates inherit
t = rf.load_template("reflection")
ck(t["name"] == "reflection" and any(x["slider"] == "calm" for x in t["weights"]),
   "a profile template loads with its own weights")

# --- deterministic
ck(names(rf.fabricate(POOL, "best")) == names(rf.fabricate(POOL, "best")), "the same pool and ask rank the same way")
ck(rf.fabricate(POOL, "best")["unstable"] is False, "a clear ranking is not flagged unstable")

# --- a written answer renders
md = rf.render(rf.fabricate(POOL, "the best bird shots"), "the best bird shots")
ck("**Parse.**" in md and "Ignored words" in md and "templates/best.json" in md,
   "the answer echoes the parse, lists ignored words, and names the editable template file")


# ---- 261114, from the first run on a real ledger: three bugs that only a real row exposes
real = {"segment_id": "A_dark_push.mp4#S01", "source": "/pool/A_dark_push.mp4",
        "fields": {"levels": "protected highlights", "lighting_ratio": "unknown", "depth": "unread",
                   "place": "unread", "camera_move": "locked", "move_speed": "still",
                   "subject_motion": "still", "framing": "centred-symmetric", "believability": "unread"}}
ck(rf.sid(real) == "A_dark_push.mp4#S01",
   "a shot is named by the ledger's segment_id, which lives at the top level, not inside fields")
ck(rf.val(real, "depth") is None and rf.val(real, "lighting_ratio") is None,
   "unread and unknown are the absence of a value, not values")
ck(rf.val({"fields": {"colour_cast": "none"}}, "colour_cast") == "none",
   "none stays a real answer: for colour cast and cliches it is the good one")
res = rf.fabricate([real], "best shots in Thailand")
ck(not res["out"] and any(rf.sid(i["row"]) == "A_dark_push.mp4#S01" for i in res["unconfirmed"]),
   "a pool whose place is unread parks in the unconfirmed tail; it is not thrown out")
res = rf.fabricate([real], "best")
ck("unread" not in rf.reason(res["ranked"][0], res["template"]),
   "a reason never cites an unread slot as though it said something")
print(("%d failed" % fail) if fail else "0 failed")
sys.exit(1 if fail else 0)
