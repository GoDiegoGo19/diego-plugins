#!/usr/bin/env python3
"""test_shared_ids.py · ppf_ingest's clip_ids against the shared-ID schema (SHARED_IDS.md, scripts/shared_ids.py).

    python3 tests/test_shared_ids.py -v

1. ppf_ingest on the TEST_DRIVE_v1 folder: every clip_id valid, card part == card_of(rel_path), unique.
2. ppf_ingest on the scan's P6 clips CSV sample: same checks, and every clip_id is the one folder ingest makes
   for the same file (one file, one clip_id, whichever way print was fed).
A failure is a divergence to fix, not a reason to loosen the schema. Needs ffprobe for the folder ingest.
"""
import contextlib, glob, io, os, shutil, subprocess, sys, tempfile, unittest
sys.dont_write_bytecode = True        # no __pycache__ inside the plugin (size gate)
HERE = os.path.dirname(os.path.abspath(__file__))
SK = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SK, "scripts"))
import ppf_ingest  # noqa: E402
import shared_ids as sid  # noqa: E402

REPO = os.path.normpath(os.path.join(SK, "../../../.."))
DRIVE = os.path.join(REPO, "skills/261105-scan-and-index-hard-drives/test_drive/TEST_DRIVE_v1")
P6 = glob.glob(os.path.join(REPO, "reports/261108-2200_scan-v16_review/P6_DATASETS/*_datasets_clips_P6-*.csv"))
NAME = "TEST_DRIVE_v1"


def quiet(fn, *a):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a)


def problems(clips):
    out = []
    for c in clips:
        if not sid.validate(c["clip_id"], "clip"):
            out.append(("invalid", c["clip_id"]))
        elif sid.parse_clip_id(c["clip_id"])["card"] != sid.card_of(c["rel_path"]):
            out.append(("card != card_of(rel_path)", c["clip_id"], sid.card_of(c["rel_path"])))
    return out


@unittest.skipIf(not os.path.isdir(DRIVE) or not shutil.which("ffprobe"), "TEST_DRIVE_v1 or ffprobe missing")
class FolderIngest(unittest.TestCase):
    longMessage = False

    @classmethod
    def setUpClass(cls):
        cls.clips = quiet(ppf_ingest.from_folder, DRIVE, NAME)

    def test_clip_ids_follow_schema(self):
        bad = problems(self.clips)
        self.assertFalse(bad, "%d bad, e.g. %s" % (len(bad), bad[:3]))

    def test_clip_ids_unique(self):
        ids = [c["clip_id"] for c in self.clips]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        self.assertFalse(dup, dup[:4])

    def test_card_field_is_the_card_in_the_id(self):
        """D5: `card` on an objects.json clip is the short card name, not the card its clip_id carries."""
        bad = [(c["clip_id"], c["card"]) for c in self.clips if sid.validate(c["clip_id"], "clip")
               and c["card"] != sid.parse_clip_id(c["clip_id"])["card"]]
        self.assertFalse(bad, "%d clips, e.g. %s" % (len(bad), bad[:2]))


@unittest.skipIf(not P6 or not os.path.isdir(DRIVE), "P6 clips CSV sample or TEST_DRIVE_v1 missing")
class ScanIngest(unittest.TestCase):
    longMessage = False

    @classmethod
    def setUpClass(cls):
        cls.clips = quiet(ppf_ingest.from_scan, P6[0], DRIVE, NAME)
        cls.folder = {c["rel_path"]: c["clip_id"] for c in quiet(ppf_ingest.from_folder, DRIVE, NAME)} if shutil.which("ffprobe") else {}

    def test_clip_ids_follow_schema(self):
        bad = problems(self.clips)
        self.assertFalse(bad, "%d bad, e.g. %s" % (len(bad), bad[:3]))

    def test_same_file_same_clip_id(self):
        """Every clip_id from the P6 CSV is the one ppf_ingest makes from the folder for the same file."""
        if not self.folder:
            self.skipTest("ffprobe missing")
        bad = [(c["clip_id"], self.folder.get(c["rel_path"])) for c in self.clips if self.folder.get(c["rel_path"]) != c["clip_id"]]
        self.assertFalse(bad, "%d differ, e.g. %s" % (len(bad), bad[:2]))

    def test_start_timecode(self):
        """D6: tc_start is a timecode or empty, never the scan's PENDING marker."""
        bad = [(c["clip"], c["tc_start"]) for c in self.clips if c.get("tc_start") and not sid.validate(c["tc_start"], "timecode")]
        self.assertFalse(bad, "%d bad, e.g. %s" % (len(bad), bad[:2]))


@unittest.skipIf(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), "ffmpeg/ffprobe missing")
class OtherLayouts(unittest.TestCase):
    """Layouts TEST_DRIVE_v1 does not have: a day folder below the top (D8), a clip loose in a day folder (D7)."""
    longMessage = False

    def test_card_follows_card_of(self):
        root = os.path.join(tempfile.mkdtemp(prefix="ids_"), "D")
        for rel in ("PROJ/260901_DAY/CAMA/a.mp4", "260902_DAY2/b.mp4", "CARD_X/c.mp4", "d.mp4"):
            os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=64x36:d=1",
                            "-y", os.path.join(root, rel)], check=True)
        bad = problems(quiet(ppf_ingest.from_folder, root, "D"))
        self.assertFalse(bad, "%d bad: %s" % (len(bad), bad))


if __name__ == "__main__":
    unittest.main()
