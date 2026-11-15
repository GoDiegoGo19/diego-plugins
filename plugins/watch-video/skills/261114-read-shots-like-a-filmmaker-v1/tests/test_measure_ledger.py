"""Tests for shot_measure.py and shot_ledger.py (P10 + P14). Run: python3 -I -B -m unittest tests/test_measure_ledger.py -v
(from the skill folder) or python3 -I -B tests/test_measure_ledger.py."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))
import shot_ledger as L  # noqa: E402
import shot_measure as SM  # noqa: E402

REPO = SKILL.parents[3]
TD = REPO / "skills/261105-scan-and-index-hard-drives/test_drive/TEST_DRIVE_v1"
CLIPS = ["260901_SHOOT_DAY_1/FX6_A001/XDROOT/Clip/A001C001_260901AB.MXF",
         "260901_SHOOT_DAY_1/AERIAL_DJI/DCIM/100MEDIA/DJI_0001.MP4",
         "260901_SHOOT_DAY_1/GH5S_A/DCIM/100_PANA/P1000001.MOV"]
SHIP = ["levels", "contrast", "lighting_ratio", "cct", "saturation", "palette", "sharpness", "noise", "clipping",
        "motion", "shake", "subject", "framing_geometry", "headroom", "horizon", "fidelity"]
TRUST = {"trusted", "approximate", "person"}


def run_measure(args):
    out = os.path.join(TMP.name, "m_%d.jsonl" % len(os.listdir(TMP.name)))
    assert SM.main(args + ["--out", out]) == 0
    with open(out) as f:
        return [json.loads(l) for l in f]


def setUpModule():
    global TMP, REAL
    TMP = tempfile.TemporaryDirectory()
    # three TEST_DRIVE_v1 frames pulled with ffmpeg (plus a neighbour each, so motion has a pair)
    REAL = []
    for i, c in enumerate(CLIPS):
        d = os.path.join(TMP.name, "real%d" % i)
        os.makedirs(d)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "3", "-i", str(TD / c), "-frames:v", "2",
                        os.path.join(d, "f%02d.png")], check=True)
        REAL.append(d)


def tearDownModule():
    TMP.cleanup()


class Measure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = []
        for d in REAL:
            cls.rows += run_measure([d, "--fps", "23.976", "--assume-transfer", "rec709", "--drive", "TEST_DRIVE_v1"])[:1]
        cls.video = run_measure([str(TD / CLIPS[0]), "--root", str(TD)])

    def test_every_ship_field_with_trust(self):
        self.assertEqual(len(self.rows), 3)
        for r in self.rows:
            self.assertEqual(r["transfer"], "Rec.709")
            for k in SHIP:
                self.assertIn(k, r["measures"], k)
                self.assertIn(r["measures"][k]["trust"], TRUST, k)
                self.assertTrue(r["measures"][k]["rule"], k)

    def test_plausible_ranges(self):
        for r in self.rows:
            m = {k: v["value"] for k, v in r["measures"].items()}
            lv = m["levels"]
            self.assertTrue(-8 <= lv["p1_ire"] <= lv["p50_ire"] <= lv["p99_ire"] <= 110)
            self.assertTrue(0 <= m["contrast"]["stops_p95_p5"] <= 16)
            self.assertTrue(1000 <= m["cct"]["kelvin"] <= 25000)
            self.assertEqual(m["cct"]["label"], "cast_estimate")
            self.assertTrue(0 <= m["saturation"]["mean"] <= 1 and m["saturation"]["colourfulness"] >= 0)
            self.assertEqual(len(m["palette"]["lab"]), 5)
            self.assertAlmostEqual(sum(c["share"] for c in m["palette"]["lab"]), 1.0, places=2)
            self.assertGreaterEqual(m["sharpness"]["sharp_region"]["laplacian_var"], m["sharpness"]["laplacian_var"] * 0.99)
            self.assertTrue(0 <= m["noise"]["sigma"] < 50)
            self.assertIn(m["subject"]["subject_source"], ("face", "person", "saliency"))
            self.assertTrue(0 <= m["subject"]["x"] <= 1 and 0 <= m["subject"]["y"] <= 1)
            self.assertTrue(0 <= m["framing_geometry"]["dist_thirds_pct_diag"] <= 50)
            self.assertIn("camera", m["motion"])
            self.assertTrue(m["lighting_ratio"] == "unknown" or m["lighting_ratio"]["ratio_source"] in ("face", "person"))
            self.assertTrue(r["segment_id"].endswith("#S01"))

    def test_video_ids_and_untagged_transfer(self):
        r = self.video[0]
        self.assertEqual(r["clip_id"], "TEST_DRIVE_v1/260901_SHOOT_DAY_1/FX6_A001/A001C001_260901AB.MXF")
        self.assertEqual(r["segment_id"], r["clip_id"] + "#S01")
        self.assertEqual(r["transfer"], "unknown")                   # untagged stream: honest unknown
        self.assertEqual(r["measures"]["levels"]["value"], "unknown")
        self.assertIn("fps", r["measures"]["fidelity"]["value"])

    def _synthetic(self, img, transfer="rec709"):
        d = tempfile.mkdtemp(dir=TMP.name)
        cv2.imwrite(os.path.join(d, "a.png"), img)
        return run_measure([d, "--assume-transfer", transfer])[0]["measures"]

    def test_dark_frame_reads_low_key(self):
        rng = np.random.default_rng(1)
        img = np.clip(rng.normal(28, 6, (360, 640, 3)), 0, 255).astype(np.uint8)
        img[150:200, 300:360] = 120                                   # one small practical
        lv = self._synthetic(img)["levels"]["value"]
        self.assertEqual(lv["key"], "low")
        self.assertLess(lv["p50_ire"], 15)

    def test_flat_grey_reads_near_zero_contrast(self):
        m = self._synthetic(np.full((360, 640, 3), 128, np.uint8))
        self.assertLess(m["contrast"]["value"]["stops_p95_p5"], 0.05)
        self.assertLess(m["contrast"]["value"]["rms"], 0.01)

    def test_log_writes_unknown_levels(self):
        m = self._synthetic(np.full((360, 640, 3), 128, np.uint8), "log")
        for k in ("levels", "contrast", "cct", "lighting_ratio"):
            self.assertEqual(m[k]["value"], "unknown", k)
            self.assertIn("log", m[k]["note"])

    def test_tilted_horizon(self):
        img = np.full((360, 640, 3), 200, np.uint8)
        M = cv2.getRotationMatrix2D((320, 180), 5, 1.0)
        base = np.full((360, 640, 3), 200, np.uint8)
        base[180:] = 60
        img = cv2.warpAffine(base, M, (640, 360), borderMode=cv2.BORDER_REPLICATE)
        hz = self._synthetic(img)["horizon"]["value"]
        self.assertAlmostEqual(hz["tilt_deg"], 5, delta=1.0)


class Ledger(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rows = []
        for d in REAL:
            rows += run_measure([d, "--fps", "23.976", "--assume-transfer", "rec709", "--drive", "TEST_DRIVE_v1"])[:1]
        cls.meas = os.path.join(TMP.name, "meas_real.jsonl")
        with open(cls.meas, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        cls.ledger = os.path.join(TMP.name, "ledger.jsonl")
        assert L.main(["build", "--measures", cls.meas, "--out", cls.ledger]) == 0
        cls.rows = [json.loads(l) for l in open(cls.ledger)]

    def test_build_validate_pass(self):
        self.assertEqual(L.main(["validate", self.ledger]), 0)
        for r in self.rows:
            self.assertEqual(set(r["fields"]), set(L.SCHEMA))
            for name, d in r["fields"].items():
                self.assertIn(d["origin"], "MRSP")
            self.assertEqual(r["fields"]["mood"]["value"], "unread")
            self.assertNotEqual(r["fields"]["levels"]["value"], "unknown")

    def test_free_word_fails_naming_field(self):
        bad = copy.deepcopy(self.rows[0])
        bad["fields"]["saturation"]["value"] = "punchy"
        errs = L.check_row(bad)
        self.assertTrue(any(e.startswith("saturation: free word 'punchy'") and "muted / natural / rich / oversaturated" in e
                            for e in errs), errs)
        p = os.path.join(TMP.name, "bad.jsonl")
        with open(p, "w") as f:
            f.write(json.dumps(bad) + "\n")
        self.assertEqual(L.main(["validate", p]), 1)

    def test_missing_origin_and_count_fail(self):
        bad = copy.deepcopy(self.rows[0])
        del bad["fields"]["levels"]["origin"]
        bad["fields"]["noise"]["value"] = 3
        errs = L.check_row(bad)
        self.assertTrue(any(e.startswith("levels: missing origin") for e in errs))
        self.assertTrue(any(e.startswith("noise: a number") for e in errs))

    def test_novelty_typical_vs_outlier(self):
        base = json.loads(open(self.meas).readline())
        pool = []
        rng = np.random.default_rng(3)
        for i in range(8):
            r = copy.deepcopy(base)
            m = r["measures"]
            m["levels"]["value"]["p50_ire"] += float(rng.normal(0, 2))
            m["saturation"]["value"]["mean"] += float(rng.normal(0, 0.01))
            m["cct"]["value"]["kelvin"] += int(rng.normal(0, 100))
            r["segment_id"] = base["clip_id"] + "#S%02d" % (i + 1)
            pool.append(r)
        out = copy.deepcopy(pool[0])
        out["segment_id"] = base["clip_id"] + "#S09"
        mo = out["measures"]
        mo["levels"]["value"]["p50_ire"] = 5.0
        mo["saturation"]["value"]["mean"] = 0.9
        mo["cct"]["value"]["kelvin"] = 2500
        mo["contrast"]["value"]["stops_p95_p5"] = 0.1
        pool.append(out)
        nov = L.novelty([L.measured_vector(r) for r in pool])
        self.assertEqual(nov[-1][0], "unique")
        self.assertIn(nov[1][0], ("common idea", "1 sibling"))
        self.assertNotEqual(nov[1][0], nov[-1][0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
