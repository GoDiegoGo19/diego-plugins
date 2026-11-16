"""Tests for the grammar (shot_ledger SCHEMA + shot_sentence), the arc sampler (shot_arc) and the front door (read_shots).
Run: python3 -I -B tests/test_grammar_arc.py (from anywhere)."""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))
os.environ.setdefault("READ_SHOTS_NO_VISION", "1")   # the grammar and the front door, not the model
os.environ.setdefault("WATCH_NO_AUTO_INSTALL", "1")  # and never a download from a test
import read_shots as R  # noqa: E402
import shot_arc as A  # noqa: E402
import shot_ledger as L  # noqa: E402
import shot_sentence as S  # noqa: E402

REPO = SKILL.parents[3]
TD = REPO / "skills/261105-scan-and-index-hard-drives/test_drive/TEST_DRIVE_v1"
FX6 = TD / "260901_SHOOT_DAY_1/FX6_A001/XDROOT/Clip/A001C001_260901AB.MXF"
SHAPES = L.SCHEMA["arc_shape"][2]


def blank(sid):
    f = {k: L.F(k, L.UNREAD if o == "R" else L.U) for k, (o, t, spec) in L.SCHEMA.items()}
    return {"segment_id": sid, "clip_id": sid.split("#")[0], "fields": f}


def row(sid, **slots):
    r = blank(sid)
    for k, v in slots.items():
        r["fields"][k] = L.F(k, v)
    return L.finish(r)


# Sexton's five sunset couples (editors_eye_shot_grammar.md section 1), as slot values
COUPLES = [
    dict(shot_size="WS", camera_angle="eye level", camera_move="locked", move_speed="still", subject="couple", var_form="couple",
         var_age="young adult", verb="walk", manner="slowly hand in hand", direction="left to right", place="along the waterline",
         time_of_day="sunset", quality_of_light="silhouette", source_camera="RED", source_quality_class="cinema-B",
         source_codec="R3D raw", arc_shape="pass-through", arc_head="WS entering sharp", arc_mid="WS subject sharp",
         arc_tail="frame empty sharp", arc_energy="modest"),
    dict(shot_size="MS", camera_angle="eye level", camera_move="handheld", move_speed="fast", subject="couple", var_form="couple",
         var_age="young adult", verb="run", manner="fast laughing", direction="away from camera", place="into the surf",
         time_of_day="sunset", quality_of_light="hard", source_camera="Sony FX", source_quality_class="prosumer",
         source_codec="XAVC 10-bit", arc_shape="follow", arc_head="MS subject sharp", arc_mid="MWS subject sharp",
         arc_tail="WS subject sharp", arc_energy="dynamic"),
    dict(shot_size="EWS", camera_angle="overhead", camera_move="drone orbit", move_speed="slow", subject="couple", var_form="couple",
         subject_mods="small in frame", verb="embrace", manner="still", direction="static", place="on the waterline",
         time_of_day="sunset", quality_of_light="hard", source_camera="DJI", source_quality_class="drone", source_codec="H.264 8-bit",
         arc_shape="orbit", arc_head="EWS subject sharp", arc_mid="EWS subject sharp", arc_tail="WS subject sharp", arc_energy="modest"),
    dict(shot_size="CU", camera_angle="low", camera_move="push in", move_speed="slow", subject="hands", var_form="couple",
         var_age="older", subject_mods="clasped", verb="walk", manner="slowly", direction="toward camera", place="on wet sand",
         time_of_day="golden hour", quality_of_light="soft", source_camera="ARRI", source_quality_class="cinema-A",
         source_codec="ARRIRAW log", arc_shape="push", arc_head="CU part sharp", arc_mid="ECU part sharp", arc_tail="CU subject soft",
         arc_energy="modest"),
    dict(shot_size="CU", camera_angle="eye level", camera_move="handheld", move_speed="slow", subject="couple", var_form="couple",
         var_state="silhouetted", verb="sway", manner="slowly", direction="static", place="against the sea", time_of_day="sunset",
         quality_of_light="backlit-rim", source_camera="Canon", source_quality_class="DSLR", source_codec="H.264 8-bit",
         arc_shape="soft", arc_head="CU subject soft", arc_mid="CU subject soft", arc_tail="CU subject sharp", arc_energy="modest"),
]


class Grammar(unittest.TestCase):
    def test_five_sunset_couples_differ(self):
        rows = [row("POOL/LOOSE/sunset_%d.mov#S01" % i, **c) for i, c in enumerate(COUPLES)]
        L.mark_indistinct(rows)
        for r in rows:
            self.assertEqual(L.check_row(r), [], r["segment_id"])
            self.assertFalse(r["indistinct"])
            print("  " + r["row_text"])
        sents = [r["fields"]["sentence"]["value"] for r in rows]
        self.assertEqual(len(set(sents)), 5)
        for i in range(5):
            for j in range(i + 1, 5):
                diff = [k for k in S.DIFF_SLOTS if rows[i]["fields"][k]["value"] != rows[j]["fields"][k]["value"]]
                self.assertGreaterEqual(len(diff), 2, (i, j, diff))
        self.assertTrue(sents[0].startswith("Wide, eye level, locked off on young couple walking slowly hand in hand left to right"))
        self.assertIn("older couple's clasped hands", sents[3])
        self.assertIn("; pass-through: WS entering sharp → WS subject sharp → frame empty sharp", sents[0])
        md = S.markdown(rows)
        self.assertIn("### couple · waterline (2 shots)", md)
        self.assertIn("differ on:", md)

    def test_identical_rows_indistinct(self):
        a = row("POOL/LOOSE/a.mov#S01", **COUPLES[0])
        b = row("POOL/LOOSE/b.mov#S01", **COUPLES[0])
        c = row("POOL/LOOSE/c.mov#S01", **COUPLES[1])
        L.mark_indistinct([a, b, c])
        self.assertTrue(a["indistinct"] and b["indistinct"])
        self.assertFalse(c["indistinct"])

    def test_free_verb_fails(self):
        r = row("POOL/LOOSE/a.mov#S01", **COUPLES[0])
        r["fields"]["verb"] = L.F("verb", "saunter")
        errs = L.check_row(r)
        self.assertTrue(any(e.startswith("verb: free word 'saunter'") and "vocab.json" in e for e in errs), errs)

    def test_bounded_and_casting_rules(self):
        r = row("POOL/LOOSE/a.mov#S01", **COUPLES[0])
        r["fields"]["place"] = L.F("place", "along the long empty waterline")          # 5 words
        r["fields"]["var_casting"] = L.F("var_casting", "young", origin="R")             # a model may not set casting
        errs = L.check_row(r)
        self.assertTrue(any(e.startswith("place:") for e in errs), errs)
        self.assertTrue(any(e.startswith("var_casting: origin R") for e in errs), errs)
        r["fields"]["var_casting"] = L.F("var_casting", "Black", origin="P")
        r["fields"]["place"] = L.F("place", "along the waterline")
        self.assertEqual(L.check_row(r), [])

    def test_empty_slots_omitted(self):
        r = row("POOL/LOOSE/a.mov#S01", shot_size="WS", arc_shape="held")
        self.assertEqual(r["fields"]["sentence"]["value"], "Wide; held")


class Arc(unittest.TestCase):
    def test_fx6_cuts_and_arc(self):
        shots, _ = R.shots_of(FX6)
        fps = 24000 / 1001
        bounds = [round(t * fps) for _, t, _ in shots[1:]]
        self.assertEqual(bounds, [48, 168])
        _, t_in, t_out = shots[1]
        res = A.arc(FX6, t_in, t_out, None, "rec709")
        self.assertEqual(res["status"], "seen")
        g = res["grammar"]
        self.assertIn(g["arc_shape"]["value"], SHAPES)
        self.assertIn(g["arc_energy"]["value"], L.SCHEMA["arc_energy"][2])
        self.assertTrue(3 <= len(g["arc_frames"]["value"]) <= 5, g["arc_frames"])
        self.assertIsNone(L.check_value("arc_frames", g["arc_frames"]["value"]))
        print("  arc:", g["arc_shape"]["value"], g["arc_head"]["value"], "->", g["arc_tail"]["value"], g["arc_shape"].get("evidence"))


class FrontDoor(unittest.TestCase):
    def test_end_to_end_and_never_overwrite(self):
        with tempfile.TemporaryDirectory() as out:
            self.assertEqual(R.main([str(TD), "--out", out, "--assume-transfer", "rec709"]), 0)
            self.assertEqual(L.validate(os.path.join(out, "ledger.jsonl")), 0)
            rows = [json.loads(l) for l in Path(out, "ledger.jsonl").read_text().splitlines()]
            md = Path(out, "sentences.md").read_text()
            lines = [l for l in md.splitlines() if l.startswith("- `")]
            self.assertEqual(len(lines), len(rows))
            self.assertGreaterEqual(len(rows), 10)
            fx6 = [r for r in rows if r["clip_id"].endswith("A001C001_260901AB.MXF")]
            self.assertEqual(len(fx6), 3)
            summ = json.loads(Path(out, "read_shots.json").read_text())
            self.assertTrue(summ["valid"])
            self.assertIn("subject", summ["unread_pending_vision_lane"])
            for r in rows:
                self.assertIn(r["fields"]["arc_shape"]["value"], SHAPES)
                self.assertEqual(r["fields"]["source_camera"]["origin"], "S")
            self.assertEqual(R.main([str(FX6), "--out", out, "--assume-transfer", "rec709"]), 0)
            self.assertTrue(os.path.exists(os.path.join(out, "ledger_v002.jsonl")))
            self.assertTrue(os.path.exists(os.path.join(out, "sentences_v002.md")))
            self.assertEqual(len(Path(out, "ledger.jsonl").read_text().splitlines()), len(rows))   # first run untouched


if __name__ == "__main__":
    unittest.main(verbosity=2)
