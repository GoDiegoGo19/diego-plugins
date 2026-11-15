"""Tests for prwrite.model: Rate/Time tick math, timecode, JSON round trip.

No real .prproj exists in the repo; values here are computed by hand from
254016000000 ticks per second.
"""

import sys
import unittest
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prwrite.model import (  # noqa: E402
    TICKS_PER_SECOND, Bin, MasterClip, Media, Project, Rate, Sequence, Time, Track, TrackItem,
)


class RateTest(unittest.TestCase):
    def test_tick_rate(self):
        self.assertEqual(TICKS_PER_SECOND, 254016000000)

    def test_ntsc_rates_are_exact(self):
        for spoken in ("23.976", 23.976, 23.98, Fraction(24000, 1001)):
            self.assertEqual(Rate.from_fps(spoken), Rate(24000, 1001))
        self.assertEqual(Rate.from_fps(29.97), Rate(30000, 1001))
        self.assertEqual(Rate.from_fps("59.94"), Rate(60000, 1001))
        self.assertEqual(Rate.from_fps(25), Rate(25))

    def test_ticks_per_frame(self):
        self.assertEqual(Rate(24000, 1001).ticks_per_frame, 10594584000)
        self.assertEqual(Rate(30000, 1001).ticks_per_frame, 8475667200)
        self.assertEqual(Rate(25).ticks_per_frame, 10160640000)
        self.assertEqual(Rate.from_ticks_per_frame(8475667200), Rate(30000, 1001))
        self.assertEqual(Rate(24000, 1001).nominal, 24)
        self.assertTrue(Rate(30000, 1001).can_drop)
        self.assertFalse(Rate(24000, 1001).can_drop)


class TimeTest(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(Time.from_seconds(1).ticks, TICKS_PER_SECOND)
        self.assertEqual(Time.from_seconds("0.5").seconds, Fraction(1, 2))
        self.assertEqual(Time(TICKS_PER_SECOND * 3).seconds, 3)

    def test_frames_23976(self):
        r = Rate.from_fps(23.976)
        t = Time.from_frames(24, r)
        self.assertEqual(t.seconds, Fraction(1001, 1000))
        self.assertEqual(t.to_frames(r), 24)
        self.assertEqual(Time(t.ticks - 1).to_frames(r), 23)  # floor

    def test_timecode_ndf_23976(self):
        r = Rate(24000, 1001)
        t = Time.from_timecode("01:00:00:00", r)
        self.assertEqual(t.to_frames(r), 86400)
        self.assertEqual(t.to_timecode(r), "01:00:00:00")
        self.assertEqual(Time.from_timecode("00:00:10:12", r).to_frames(r), 252)

    def test_drop_frame_2997(self):
        r = Rate(30000, 1001)
        cases = {
            "00:00:59;29": 1799,
            "00:01:00;02": 1800,     # ;00 and ;01 are dropped at minute 1
            "00:10:00;00": 17982,    # no drop at the tenth minute
            "01:00:00;00": 107892,
        }
        for tc, frames in cases.items():
            t = Time.from_timecode(tc, r)
            self.assertEqual(t.to_frames(r), frames, tc)
            self.assertEqual(t.to_timecode(r, drop=True), tc)
        # One hour of DF timecode is 3600.0 real seconds within a frame.
        secs = Time.from_timecode("01:00:00;00", r).seconds
        self.assertLess(abs(secs - 3600), Fraction(1001, 30000))
        with self.assertRaises(ValueError):
            Time.from_timecode("00:01:00;00", r)
        with self.assertRaises(ValueError):
            Time.from_timecode("00:00:01;00", Rate(25))

    def test_drop_frame_every_frame_round_trips(self):
        r = Rate(30000, 1001)
        for f in range(0, 40000, 7):
            tc = Time.from_frames(f, r).to_timecode(r, drop=True)
            self.assertEqual(Time.from_timecode(tc, r).to_frames(r), f)


class JsonTest(unittest.TestCase):
    def test_round_trip(self):
        r = Rate(24000, 1001)
        p = Project(name="p.prproj", oid="1", extras={"root_version": "3"})
        p.root.bins.append(Bin(name="Interviews", uid="b1", clips=["m1"],
                               bins=[Bin(name="Day 1", uid="b2")]))
        p.media["u-media"] = Media(path="/x/a.mov", uid="u-media")
        p.clips["m1"] = MasterClip(name="a.mov", uid="m1", item_uid="i1",
                                   media_uid="u-media", duration=Time(5 * TICKS_PER_SECOND))
        item = TrackItem(name="a.mov", oid="40", start=Time(0), end=Time.from_frames(48, r),
                         in_=Time.from_frames(10, r), out=Time.from_frames(58, r), master_uid="m1")
        p.sequences.append(Sequence(name="Rough", uid="s1", rate=r, frame_size=(1920, 1080),
                                    zero_point=Time.from_timecode("01:00:00:00", r),
                                    video_tracks=[Track(uid="t1", items=[item])],
                                    audio_tracks=[Track(kind="Audio", uid="t2")]))
        text = p.to_json()
        back = Project.from_json(text)
        self.assertEqual(back, p)
        self.assertEqual(back.to_json(), text)
        self.assertEqual(back.sequences[0].video_tracks[0].items[0].duration.to_frames(r), 48)


if __name__ == "__main__":
    unittest.main()
