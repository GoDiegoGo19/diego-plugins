"""test_shared_ids.py · watch_see.py output against the shared-ID schema (SHARED_IDS.md, scripts/shared_ids.py).

    python3 -m unittest test_shared_ids -v        (from this tests/ folder)

With the SEE engine installed it runs watch_see.py --bracket quick on TEST_DRIVE_v1 and checks that watch.json;
without it, it checks the fixture tests/fixtures/watch_TEST_DRIVE_v1_quick.json (a saved quick run).
The cross check (every watch clip_id is one print's ppf_ingest makes for the same file) runs when the print
plugin sits beside this one. A failure is a divergence to fix, not a reason to loosen the schema.
"""
import glob, importlib.util, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "scripts"))
import shared_ids as sid  # noqa: E402

FIXTURE = HERE / "fixtures" / "watch_TEST_DRIVE_v1_quick.json"
MISSING = [m for m in ("onnxruntime", "cv2", "av", "scenedetect", "ruptures", "tokenizers", "numpy")
           if importlib.util.find_spec(m) is None]


def find_up(pattern):
    up = ROOT
    for _ in range(7):
        up = up.parent
        hit = sorted(glob.glob(str(up / pattern)))
        if hit:
            return hit[-1]          # the newest version folder
    return None


DRIVE = os.environ.get("SEE_TEST_DRIVE") or find_up("skills/*scan-and-index-hard-drives*/test_drive/TEST_DRIVE_v1")
PPF_SCRIPTS = os.environ.get("PPF_SCRIPTS") or find_up("plugins/premiere-project-file-assembly/skills/*premiere-project-file-assembly-v*/scripts/ppf_ingest.py")
PPF_SCRIPTS = PPF_SCRIPTS and os.path.dirname(PPF_SCRIPTS) if PPF_SCRIPTS and PPF_SCRIPTS.endswith(".py") else PPF_SCRIPTS


class WatchAgainstSharedIds(unittest.TestCase):
    longMessage = False

    @classmethod
    def setUpClass(cls):
        if not MISSING and DRIVE:
            out = tempfile.mkdtemp(prefix="see_ids_")
            r = subprocess.run([sys.executable, str(ROOT / "scripts" / "watch_see.py"), DRIVE, "--out", out,
                                "--bracket", "quick", "--no-sheet"], capture_output=True, text=True, timeout=600)
            assert r.returncode == 0, (r.stdout + r.stderr)[-2000:]
            with open(os.path.join(out, "watch.json")) as f:
                cls.w, cls.source = json.load(f), "live watch_see.py run"
        else:
            with open(FIXTURE) as f:
                cls.w, cls.source = json.load(f), "fixture %s" % FIXTURE.name
        print("\n  [print ids from %s]" % PPF_SCRIPTS)
        print("  [watch ids from %s: %d clips, %d segments]" % (cls.source, len(cls.w["clips"]), len(cls.w["segments"])))

    def test_drive(self):
        self.assertTrue(sid.validate(self.w["drive"], "drive"), self.w["drive"])

    def test_clip_ids(self):
        wrong = [c["clip_id"] for c in self.w["clips"] if not sid.validate(c["clip_id"], "clip")]
        self.assertFalse(wrong, wrong[:4])

    def test_clip_id_parts_follow_the_file(self):
        """drive part == drive; card part == card_of(rel_path); file part == the file name."""
        wrong = []
        for c in self.w["clips"]:
            p = sid.parse_clip_id(c["clip_id"])
            if (p["drive"], p["card"], p["file"]) != (self.w["drive"], sid.card_of(c["rel_path"]), c["clip"]):
                wrong.append(c["clip_id"])
        self.assertFalse(wrong, wrong[:4])

    def test_clip_ids_unique(self):
        ids = [c["clip_id"] for c in self.w["clips"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_card_field_is_the_card_in_the_id(self):
        """D5: a record's `card` field must be the card the clip_id carries, or a consumer rebuilds a wrong ID."""
        wrong = [(c["clip_id"], c["card"]) for c in self.w["clips"] if c["card"] != sid.parse_clip_id(c["clip_id"])["card"]]
        self.assertFalse(wrong, "%d records, e.g. %s" % (len(wrong), wrong[:2]))

    def test_segment_ids(self):
        wrong = [s["segment_id"] for s in self.w["segments"] if not sid.validate(s["segment_id"], "segment")
                 or sid.parse_segment_id(s["segment_id"])["clip_id"] != s["clip_id"]
                 or sid.parse_segment_id(s["segment_id"])["n"] != s["n"]]
        self.assertFalse(wrong, wrong[:4])

    def test_segment_times(self):
        wrong = [(s["segment_id"], s["t_in"], s["t_out"], s["hero_t"]) for s in self.w["segments"]
                 if not all(sid.validate(s[k], "time") for k in ("t_in", "t_out", "hero_t"))
                 or not s["t_in"] < s["t_out"] or not s["t_in"] - 1e-3 <= s["hero_t"] <= s["t_out"] + 1e-3]
        self.assertFalse(wrong, wrong[:4])

    @unittest.skipIf(not (PPF_SCRIPTS and DRIVE), "print plugin or TEST_DRIVE_v1 not beside this plugin")
    def test_print_knows_every_watch_clip_id(self):
        """Sexton's case: a watch-side clip_id that print's objects.json does not know."""
        sys.path.insert(0, PPF_SCRIPTS)
        import ppf_ingest
        known = {c["clip_id"] for c in ppf_ingest.from_folder(DRIVE, self.w["drive"])}
        unknown = [c["clip_id"] for c in self.w["clips"] if c["clip_id"] not in known]
        self.assertFalse(unknown, unknown[:4])


if __name__ == "__main__":
    unittest.main()
