"""watch_see.py on the scan skill's TEST_DRIVE_v1 (watch-video 1.1.0, 261113).

Needs onnxruntime + cv2 + PyAV + scenedetect + ruptures (the SEE engine) and the test drive in the repo
(skills/261105-scan-and-index-hard-drives/test_drive/TEST_DRIVE_v1, or $SEE_TEST_DRIVE). Skips cleanly otherwise.
"""
import csv, glob, importlib.util, json, os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEE = os.path.join(ROOT, "scripts", "watch_see.py")


def test_drive():
    if os.environ.get("SEE_TEST_DRIVE"):
        return os.environ["SEE_TEST_DRIVE"]
    up = ROOT
    for _ in range(6):
        up = os.path.dirname(up)
        hit = glob.glob(os.path.join(up, "skills", "*scan-and-index-hard-drives*", "test_drive", "TEST_DRIVE_v1"))
        if hit:
            return hit[0]
    return None


MISSING = [m for m in ("onnxruntime", "cv2", "av", "scenedetect", "ruptures", "tokenizers", "numpy")
           if importlib.util.find_spec(m) is None]
DRIVE = test_drive()
# The one known false positive: YOLOX-S reads the mandrill's face in GH5S_A/P1000002.MOV as a person
# (score >= vision.people_min_score). The test drive has no people; every other clip must say 0.
KNOWN_FALSE_PEOPLE = {"P1000002.MOV"}


@unittest.skipIf(MISSING, "SEE engine pieces missing: %s" % ", ".join(MISSING))
@unittest.skipIf(DRIVE is None, "TEST_DRIVE_v1 not found")
class SeeOnTestDrive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = tempfile.mkdtemp(prefix="see_")
        r = subprocess.run([sys.executable, SEE, DRIVE, "--out", cls.out, "--bracket", "quick", "--no-sheet"],
                           capture_output=True, text=True, timeout=600)
        cls.log = r.stdout + r.stderr
        cls.rc = r.returncode
        cls.w = json.load(open(os.path.join(cls.out, "watch.json"))) if r.returncode == 0 else {}

    def test_runs(self):
        self.assertEqual(self.rc, 0, self.log[-2000:])

    def test_segments_found(self):
        self.assertGreaterEqual(len(self.w["segments"]), len(self.w["clips"]))
        rows = list(csv.DictReader(open(os.path.join(self.out, "segments.csv"))))
        self.assertEqual(len(rows), len(self.w["segments"]))
        for s in self.w["segments"]:
            self.assertLessEqual(s["t_in"], s["hero_t"] + 1e-3)
            self.assertLess(s["t_in"], s["t_out"])

    def test_families(self):
        self.assertGreater(len(self.w["families"]), 0)
        self.assertTrue(all(s.get("family") for s in self.w["segments"]))
        self.assertTrue(os.path.isfile(os.path.join(self.out, "families.csv")))

    def test_hero_frames_written(self):
        for s in self.w["segments"]:
            p = os.path.join(self.out, s["hero"])
            self.assertTrue(os.path.isfile(p) and os.path.getsize(p) > 1000, s["segment_id"])

    def test_shared_ids(self):
        drive = os.path.basename(os.path.normpath(DRIVE))
        for c in self.w["clips"]:
            self.assertEqual(c["clip_id"], "%s/%s/%s" % (drive, c["card_column"], c["clip"]))
            self.assertEqual(c["drive"], drive)
        for s in self.w["segments"]:
            for k in ("drive", "card", "clip_id", "segment_id", "t_in", "t_out"):
                self.assertIn(k, s)
            self.assertTrue(s["segment_id"].startswith(s["clip_id"] + "#S"))
        ids = {c["clip_id"] for c in self.w["clips"]}
        self.assertIn("%s/260901_SHOOT_DAY_1/FX6_A001/A001C001_260901AB.MXF" % drive, ids)

    def test_no_proxies(self):
        self.assertFalse([c for c in self.w["clips"] if "/Sub/" in c["rel_path"] or "PROXY" in c["rel_path"]])

    def test_people_zero(self):
        seen = {s["clip"]: 0 for s in self.w["segments"]}
        for s in self.w["segments"]:
            seen[s["clip"]] = max(seen[s["clip"]], s["people"]["count"])
        self.assertEqual({k: v for k, v in seen.items() if v and k not in KNOWN_FALSE_PEOPLE}, {})

    def test_tags_honest(self):
        if self.w["engines"]["tags"] is None:
            self.assertIn("CLIP not installed", self.w["tags_note"])
            self.assertIn("CLIP not installed", self.log)
        self.assertTrue(os.path.isfile(os.path.join(self.out, "tags.csv")))
        self.assertTrue(self.w["tagline"])


if __name__ == "__main__":
    unittest.main()
