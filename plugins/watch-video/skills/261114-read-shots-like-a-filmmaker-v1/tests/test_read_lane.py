"""Tests for read_lane.py (P12, the vision lane). Run from the skill folder: python3 -I -B tests/test_read_lane.py -v"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))
import read_lane as RL  # noqa: E402

LOW = {"measures": {"levels": {"value": {"p50_ire": 70.0}, "trust": "trusted", "rule": "x"},
                    "cct": {"value": {"kelvin": 3200}, "trust": "approximate", "rule": "x"}}}


class FakeLogits:
    """Injected model: score() returns fixed per-label log-probs; no generate() (no evidence)."""
    path = "logits"

    def __init__(self, table):
        self.table = table

    def score(self, frame, prompt, labels):
        return [self.table.get(l, -9.0) for l in labels]


class FakeText:
    path = "text"

    def __init__(self, text):
        self.text = text

    def generate(self, frame, prompt, n=30):
        return self.text


class T(unittest.TestCase):
    def test_no_model_all_unresolved_exit_0(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, HOME=d)
            env.pop("WATCH_MODELS_DIR", None)
            out = os.path.join(d, "read.jsonl")
            m = os.path.join(d, "m.jsonl")
            Path(m).write_text(json.dumps({"segment_id": "X#S01", "source": "f.jpg", "measures": LOW["measures"]}) + "\n")
            slots = ["lighting_style", "time_of_day", "subject", "verb", "mood"]
            r = subprocess.run([sys.executable, "-I", "-B", str(SKILL / "scripts/read_lane.py"), "f.jpg", "--measured", m,
                                "--out", out, "--model", os.path.join(d, "nope"), "--slots"] + slots,
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            row = json.loads(Path(out).read_text())
            self.assertEqual(sorted(row["fields"]), sorted(slots))
            for v in row["fields"].values():
                self.assertEqual(v["value"], "unresolved")
                self.assertTrue(v["note"].startswith("model not installed"))
                self.assertIn(v["trust"], ("person", "approximate"))
        res = RL.read("f.jpg", LOW, ["mood"])
        self.assertEqual(res["mood"]["note"], "model not installed")
        # 261114: mood is Diego's slot, so the lane never claims person trust for it. It is a
        # suggestion (approximate) carrying awaits_person until he confirms or overwrites it.
        self.assertEqual(res["mood"]["trust"], "approximate")
        self.assertTrue(res["mood"]["awaits_person"])

    def test_contradiction_low_key_vs_p50_70(self):
        f = RL.facts(LOW)
        self.assertIsNotNone(RL.contradiction("low key, deep shadows", f))
        self.assertIsNone(RL.contradiction("high key, bright", f))
        res = RL.read("f.jpg", LOW, ["lighting_style"], model=FakeLogits({"low key": -0.1, "high key": -3.0}))
        self.assertEqual(res["lighting_style"]["value"], "unresolved")
        self.assertIn("low key", res["lighting_style"]["note"])
        self.assertIn("70", res["lighting_style"]["note"])
        # the rest of the table
        self.assertTrue(RL.contradiction("golden light", {"cct": 7000}))
        self.assertTrue(RL.contradiction("static frame", {"camera_move": "pan"}))
        self.assertFalse(RL.contradiction("static frame", {"camera_move": "locked"}))
        self.assertTrue(RL.contradiction("night", {"p50": 50}))
        self.assertTrue(RL.contradiction("high key", {"p50": 20}))

    def test_label_matching_text_path(self):
        labs = RL.labels_for("time_of_day")
        self.assertEqual(RL.match_label("...this looks like golden hour light...", labs), "golden hour")
        self.assertIsNone(RL.match_label("sunrise-ish glow", labs))
        res = RL.read("f.jpg", LOW, ["time_of_day"], model=FakeText("...this looks like golden hour light..."))
        self.assertEqual(res["time_of_day"]["value"], "golden hour")
        self.assertTrue(res["time_of_day"]["rule"].startswith("text"))
        res = RL.read("f.jpg", LOW, ["time_of_day"], model=FakeText("sunrise-ish glow"))
        self.assertEqual(res["time_of_day"]["value"], "unresolved")
        self.assertIn("sunrise-ish glow", res["time_of_day"]["note"])

    def test_var_casting_never_asked(self):
        self.assertNotIn("var_casting", RL.READ_SLOTS)
        with self.assertRaises(ValueError):
            RL.read("f.jpg", LOW, ["mood", "var_casting"])
        with self.assertRaises(ValueError):
            RL.read("f.jpg", LOW, ["var_casting"], model=FakeLogits({}))

    def test_fake_logits_top_label_and_margin(self):
        m = FakeLogits({"intimate": -0.2, "serene": -1.5})
        res = RL.read("f.jpg", LOW, ["mood"], model=m)["mood"]
        self.assertEqual(res["value"], "intimate")
        self.assertAlmostEqual(res["margin"], 1.3, places=3)
        self.assertTrue(res["rule"].startswith("logits"))
        m = FakeLogits({"intimate": -0.2, "serene": -0.3})
        res = RL.read("f.jpg", LOW, ["mood"], model=m)["mood"]
        self.assertEqual(res["value"], "unresolved")
        self.assertIn("low margin", res["note"])

    def test_measured_facts_in_prompt(self):
        p = RL.prompt_for("lighting_style", RL.facts(LOW), RL.labels_for("lighting_style"))
        self.assertIn("p50 70.0 IRE", p)
        self.assertIn("low key", p)

    def test_hosted_refuses_without_yes(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([sys.executable, "-I", "-B", str(SKILL / "scripts/read_lane.py"), "f.jpg", "--out",
                                os.path.join(d, "o.jsonl"), "--lane", "hosted"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            self.assertIn("--yes-paid", r.stderr)
            self.assertFalse(os.path.exists(os.path.join(d, "o.jsonl")))


if __name__ == "__main__":
    unittest.main()
