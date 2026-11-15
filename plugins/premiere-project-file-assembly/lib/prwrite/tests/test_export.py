"""Owner D: export_xmeml. Fixture is a hand-built Project (no real .prproj in the repo)."""
import unittest
import xml.etree.ElementTree as ET

from _xfixture import R2398, f, small_project

from prwrite.cli import validate
from prwrite.export_xmeml import frames, pathurl, project_to_xmeml
from prwrite.model import Rate, Time


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.text = project_to_xmeml(small_project())
        self.root = ET.fromstring(self.text.split("\n", 2)[2])

    def test_header_and_version(self):          # PC-1.4
        self.assertTrue(self.text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'))
        self.assertEqual(self.root.tag, "xmeml")
        self.assertEqual(self.root.get("version"), "5")

    def test_own_validate_is_clean(self):
        self.assertEqual(validate(self.text), [])

    def test_deterministic(self):
        self.assertEqual(self.text, project_to_xmeml(small_project()))

    def test_files_declared_once(self):         # FL-9
        full = [x.get("id") for x in self.root.iter("file") if len(x)]
        self.assertEqual(len(full), len(set(full)))
        self.assertEqual(len(full), 2)
        refs = [x for x in self.root.iter("file") if not len(x)]
        self.assertGreater(len(refs), 0)

    def test_bins_and_items(self):
        names = [b.findtext("name") for b in self.root.iter("bin")]
        self.assertEqual(names, ["01_SEQUENCES", "02_FOOTAGE", "DAY_1"])
        self.assertEqual(len(list(self.root.iter("sequence"))), 1)
        self.assertEqual(sorted(c.findtext("name") for c in self.root.iter("clip")), ["A001.MOV", "ZOOM0001.WAV"])
        seq = self.root.find(".//sequence")
        self.assertEqual(len(seq.findall("./media/video/track")), 1)
        self.assertEqual(len(seq.findall("./media/audio/track")), 3)
        self.assertEqual(seq.findtext("duration"), "200")
        self.assertEqual(seq.findtext("rate/timebase"), "24")
        self.assertEqual(seq.findtext("rate/ntsc"), "TRUE")

    def test_frames_and_links(self):            # RD-K3, OR-106, FL-7
        seq = self.root.find(".//sequence")
        v = seq.findall("./media/video/track/clipitem")
        self.assertEqual([(c.findtext("start"), c.findtext("end"), c.findtext("in"), c.findtext("out")) for c in v],
                         [("0", "100", "10", "110"), ("100", "200", "300", "400")])
        self.assertEqual(len(v[0].findall("link")), 3)      # V1 + A1 + A2
        self.assertEqual(len(v[1].findall("link")), 2)      # V1 + A1
        a = seq.findall("./media/audio/track/clipitem")
        self.assertEqual([c.findtext("sourcetrack/trackindex") for c in a], ["1", "1", "2", "1"])
        self.assertTrue(all(c.find("sourcetrack/mediatype").text == "audio" for c in a))
        self.assertEqual(a[3].findall("link"), [])          # WAV alone: no links

    def test_file_shape(self):                  # OR-182, OR-868
        mov = next(x for x in self.root.iter("file") if len(x) and x.findtext("name") == "A001.MOV")
        self.assertEqual(mov.findtext("pathurl"), "file://localhost/Volumes/DRIVE/day%201/A001.MOV")
        self.assertEqual(mov.findtext("duration"), "480")
        self.assertEqual(mov.findtext("timecode/string"), "12:00:00:00")
        self.assertEqual(mov.findtext("timecode/frame"), str(12 * 3600 * 24))
        self.assertEqual(mov.findtext("media/video/samplecharacteristics/width"), "3840")
        self.assertEqual(mov.findtext("media/audio/channelcount"), "2")
        wav = next(x for x in self.root.iter("file") if len(x) and x.findtext("name") == "ZOOM0001.WAV")
        self.assertIsNone(wav.find("media/video"))

    def test_ntsc_frames_from_ticks(self):      # FL-3 / RD-K2
        self.assertEqual(frames(Time.from_seconds(419.5), Rate(30000, 1001)), 12572)   # not 12585
        self.assertEqual(frames(f(129801, Rate(30000, 1001)), Rate(30000, 1001)), 129801)

    def test_pathurl(self):
        self.assertEqual(pathurl("/a b/c#d.mov"), "file://localhost/a%20b/c%23d.mov")

    def test_validate_catches_duplicate_file(self):   # FL-9
        root = ET.fromstring(self.text.split("\n", 2)[2])
        first = next(x for x in root.iter("file") if len(x))
        later = [x for x in root.iter("file") if not len(x) and x.get("id") == first.get("id")][0]
        for ch in first:
            later.append(ch)
        codes = {x["code"] for x in validate(ET.tostring(root, encoding="unicode"))}
        self.assertIn("FILE_DUPLICATE_DECLARATION", codes)

    def test_validate_catches_bad_link_rate_url(self):
        bad = (self.text.replace("<linkclipref>clipitem-", "<linkclipref>nowhere-", 1)
                        .replace("file://localhost/Volumes", "http://x/Volumes", 1))
        codes = {x["code"] for x in validate(bad)}
        self.assertIn("LINK_DANGLING", codes)
        self.assertIn("PATHURL", codes)

    def test_validate_catches_rate_mismatch(self):    # FL-3
        root = ET.fromstring(self.text.split("\n", 2)[2])
        ci = root.find(".//sequence/media/video/track/clipitem")
        ci.find("rate/ntsc").text = "FALSE"
        codes = {x["code"] for x in validate(ET.tostring(root, encoding="unicode"))}
        self.assertIn("RATE_MISMATCH", codes)


if __name__ == "__main__":
    unittest.main()
