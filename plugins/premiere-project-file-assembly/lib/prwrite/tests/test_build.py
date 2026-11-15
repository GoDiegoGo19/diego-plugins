"""build + probe: a fresh project from TEST_DRIVE_v1 media, saved, read back, exported.

There is no real Premiere-written .prproj in the repo; every fixture here is made by
our own build.new_project(). "Runs here, unopened in Premiere."
"""

import gzip
import os
import subprocess
import sys
import tempfile
import unittest

from _xfixture import LIB  # noqa: F401  (puts lib/ on sys.path)
from prwrite import build, cli, framing, parse, probe
from prwrite.document import Doc
from prwrite.export_xmeml import project_to_xmeml
from prwrite.model import Rate, Time


REPO = os.path.abspath(os.path.join(LIB, "..", "..", ".."))
DRIVE = os.path.join(REPO, "skills", "261105-scan-and-index-hard-drives", "test_drive", "TEST_DRIVE_v1")
MOV = os.path.join(DRIVE, "260901_SHOOT_DAY_1", "GH5S_A", "DCIM", "100_PANA", "P1000001.MOV")
MP4 = os.path.join(DRIVE, "260901_SHOOT_DAY_1", "AERIAL_DJI", "DCIM", "100MEDIA", "DJI_0001.MP4")
R = Rate(24000, 1001)
F = R.ticks_per_frame


@unittest.skipUnless(os.path.exists(MOV) and os.path.exists(MP4), "TEST_DRIVE_v1 media not present")
class BuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = self.tmp.name
        self.project, self.doc = build.new_project("TD")
        p, d = self.project, self.doc
        self.media_bin = build.add_bin(d, p, p.root, "01_MEDIA")
        self.sub_bin = build.add_bin(d, p, self.media_bin, "GH5S_A")
        self.seq_bin = build.add_bin(d, p, p.root, "02_SEQUENCES")
        self.mov = build.import_media(d, p, self.sub_bin, MOV, probe.probe(MOV))
        self.mp4 = build.import_media(d, p, self.media_bin, MP4, probe.probe(MP4))
        self.seq = build.add_sequence(d, p, self.seq_bin, "TD_SEQ", R, (1920, 1080),
                                      Time.from_timecode("01:00:00:00", R))
        # three clips from two media on V1 (and A1 where the media has audio)
        build.place(d, self.seq, 0, self.mov, Time(0), Time(0), Time(48 * F))
        build.place(d, self.seq, 0, self.mp4, Time(48 * F), Time(24 * F), Time(72 * F))
        build.place(d, self.seq, 0, self.mov, Time(96 * F), Time(100 * F), Time(130 * F))

    def tearDown(self):
        self.tmp.cleanup()

    def test_probe_both_paths_agree(self):
        a, b = probe.probe(MOV, use_ffprobe=None), probe.probe_headers(MOV)
        self.assertEqual((a.fps, a.width, a.height, a.audio_channels, a.sample_rate),
                         (b.fps, b.width, b.height, b.audio_channels, b.sample_rate))
        self.assertEqual(b.fps, R.fps)

    def test_save_gzip_and_read_back(self):
        path = build.save_version(self.doc, os.path.join(self.out, "TD"))
        self.assertTrue(str(path).endswith("TD_v001.prproj"))
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(2), b"\x1f\x8b")
        with open(path, "rb") as fh:
            gzip.decompress(fh.read())  # a valid gzip stream
        doc = Doc.parse(framing.read_prproj(path))
        self.assertIsNotNone(doc.find("1"))
        q = parse.project_from_doc(doc)
        bins = ["/".join(pth) for pth, _ in q.root.walk() if pth]
        self.assertEqual(bins, ["01_MEDIA", "01_MEDIA/GH5S_A", "02_SEQUENCES"])
        names = sorted(c.name for c in q.clips.values())
        self.assertEqual(names, ["DJI_0001.MP4", "P1000001.MOV", "TD_SEQ"])
        self.assertEqual([s.name for s in q.sequences], ["TD_SEQ"])
        s = q.sequences[0]
        self.assertEqual(s.rate, R)
        self.assertEqual(s.frame_size, (1920, 1080))
        self.assertEqual(s.zero_point, Time.from_timecode("01:00:00:00", R))
        self.assertEqual((len(s.video_tracks), len(s.audio_tracks)), (3, 3))
        got = [(i.start.ticks, i.end.ticks, i.in_.ticks, i.out.ticks) for i in s.video_tracks[0].items]
        self.assertEqual(got, [(0, 48 * F, 0, 48 * F), (48 * F, 96 * F, 24 * F, 72 * F),
                               (96 * F, 126 * F, 100 * F, 130 * F)])
        self.assertEqual(len(s.audio_tracks[0].items), 2)  # the MP4 has no audio
        seq_mc = next(c for c in q.clips.values() if c.sequence_uid)
        self.assertEqual(seq_mc.sequence_uid, s.uid)
        # every reference resolves: new_project starts from Premiere's own blank project (261113),
        # so the old skeleton's dangling refs to 12-15 are gone. Zero is the only acceptable answer.
        dangling = {k for k in doc.index.sites if doc.find(k) is None}
        self.assertEqual(dangling, set())
        self.assertGreaterEqual(len([e for e in doc.root if e.get("ObjectID") or e.get("ObjectUID")]), 39)

    def test_second_save_is_v002_and_never_overwrites(self):
        a = build.save_version(self.doc, os.path.join(self.out, "TD"))
        b = build.save_version(self.doc, os.path.join(self.out, "TD"))
        self.assertTrue(str(b).endswith("TD_v002.prproj"))
        self.assertNotEqual(a, b)
        self.assertFalse(any(f.endswith(".tmp") for f in os.listdir(self.out)))

    def test_export_xmeml_validates(self):
        xml = project_to_xmeml(self.project)
        self.assertEqual(cli.validate(xml), [])

    def test_cli_inspect_prints_counts(self):
        path = build.save_version(self.doc, os.path.join(self.out, "TD"))
        env = dict(os.environ, PYTHONPATH=LIB)
        r = subprocess.run([sys.executable, "-B", "-m", "prwrite", "inspect", str(path)],
                           capture_output=True, text=True, env=env, cwd=self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("TD_SEQ", r.stdout)
        p = subprocess.run([sys.executable, "-B", "-m", "prwrite", "peek", str(path)],
                           capture_output=True, text=True, env=env, cwd=self.out)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Sequence", p.stdout)

    def test_rates_presets_and_whole_tick(self):
        p, d = build.new_project("R")
        build.add_sequence(d, p, p.root, "S24", Rate(24), (1920, 1080))
        with self.assertRaises(build.BuildError):
            build.add_sequence(d, p, p.root, "S13", Rate(13), (1920, 1080))  # 254016000000 / 13 is not whole


if __name__ == "__main__":
    unittest.main()
