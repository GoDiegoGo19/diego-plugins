"""Tests for prwrite.parse.project_from_doc.

There is no real .prproj in the repo. The fixture below is hand-written,
minimal Premiere-style XML using the element paths recorded in SPEC.md
"Shared facts" (ObjectID/ObjectRef for clips, sources, groups, items;
ObjectUID/ObjectURef for panel items, master clips, media, sequences,
tracks). If owner A's `prwrite.document.Doc` is importable it is used;
otherwise a tiny local shim `_doc_from_text` (root + find over a flat
ObjectID/ObjectUID index) stands in for it.
"""

import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prwrite.model import Project, Rate, Time  # noqa: E402
from prwrite.parse import project_from_doc  # noqa: E402

try:  # owner A's module, when present
    from prwrite.document import Doc as _Doc  # noqa: E402
except ImportError:  # pragma: no cover - depends on build order
    _Doc = None


class _ShimDoc:
    def __init__(self, text):
        self.root = ET.fromstring(text)
        self.index = {}
        for el in self.root:
            for key in ("ObjectID", "ObjectUID"):
                if el.get(key):
                    self.index[el.get(key)] = el

    def find(self, key):
        return self.index.get(key)


def _doc_from_text(text):
    return _Doc.parse(text) if _Doc is not None else _ShimDoc(text)


TPF = 10594584000  # ticks per frame at 23.976
S = 254016000000

FIXTURE = f"""<?xml version="1.0" encoding="UTF-8" ?>
<PremiereData Version="3">
	<Project ObjectRef="1"/>
	<Project ObjectID="1" ClassID="p" Version="40">
		<RootProjectItem ObjectURef="root"/>
	</Project>
	<RootProjectItem ObjectUID="root" ClassID="r" Version="1">
		<ProjectItem Version="1"><Name>demo.prproj</Name></ProjectItem>
		<ProjectItemContainer Version="1"><Items Version="1">
			<Item Index="0" ObjectURef="bin-a"/>
			<Item Index="1" ObjectURef="item-seq"/>
		</Items></ProjectItemContainer>
	</RootProjectItem>
	<BinProjectItem ObjectUID="bin-a" ClassID="dbfd6653-24da-480e-a35e-ba45e9504e4b" Version="1">
		<ProjectItem Version="1"><Name>Interviews</Name></ProjectItem>
		<ProjectItemContainer Version="1"><Items Version="1">
			<Item Index="0" ObjectURef="bin-b"/>
		</Items></ProjectItemContainer>
	</BinProjectItem>
	<BinProjectItem ObjectUID="bin-b" ClassID="dbfd6653-24da-480e-a35e-ba45e9504e4b" Version="1">
		<ProjectItem Version="1"><Name>Day 1</Name></ProjectItem>
		<ProjectItemContainer Version="1"><Items Version="1">
			<Item Index="0" ObjectURef="item-a"/>
		</Items></ProjectItemContainer>
	</BinProjectItem>
	<ClipProjectItem ObjectUID="item-a" ClassID="c" Version="1">
		<ProjectItem Version="1"><Name>stale name</Name></ProjectItem>
		<MasterClip ObjectURef="master-a"/>
	</ClipProjectItem>
	<MasterClip ObjectUID="master-a" ClassID="m" Version="11">
		<Clips Version="1"><Clip Index="0" ObjectRef="10"/></Clips>
		<Name>A001.mov</Name>
	</MasterClip>
	<VideoClip ObjectID="10" ClassID="vc" Version="11">
		<Clip Version="18"><Source ObjectRef="11"/><ClipID>x</ClipID></Clip>
	</VideoClip>
	<VideoMediaSource ObjectID="11" ClassID="vms" Version="2">
		<MediaSource Version="4"><OriginalDuration>{240 * TPF}</OriginalDuration><Media ObjectURef="media-a"/></MediaSource>
		<OriginalDuration>{240 * TPF}</OriginalDuration>
	</VideoMediaSource>
	<Media ObjectUID="media-a" ClassID="md" Version="30">
		<FilePath>/Volumes/Drive/A001.mov</FilePath>
	</Media>
	<ClipProjectItem ObjectUID="item-seq" ClassID="c" Version="1">
		<ProjectItem Version="1"><Name>Rough</Name></ProjectItem>
	</ClipProjectItem>
	<Sequence ObjectUID="seq-1" ClassID="s" Version="11">
		<Node Version="1"><Properties Version="1">
			<MZ.ZeroPoint>{86400 * TPF}</MZ.ZeroPoint>
			<MZ.Sequence.VideoTimeDisplayFormat>110</MZ.Sequence.VideoTimeDisplayFormat>
		</Properties></Node>
		<TrackGroups Version="1">
			<TrackGroup Version="1" Index="0"><First>228cda18-3625-4d2d-951e-348879e4ed93</First><Second ObjectRef="20"/></TrackGroup>
			<TrackGroup Version="1" Index="1"><First>80b8e3d5-6dca-4195-aefb-cb5f407ab009</First><Second ObjectRef="21"/></TrackGroup>
		</TrackGroups>
		<Name>Rough</Name>
	</Sequence>
	<VideoTrackGroup ObjectID="20" ClassID="vtg" Version="4">
		<TrackGroup Version="1">
			<Tracks Version="1"><Track Index="0" ObjectURef="vt-1"/><Track Index="1" ObjectURef="vt-2"/></Tracks>
			<FrameRate>{TPF}</FrameRate>
		</TrackGroup>
		<FrameRect>0,0,1920,1080</FrameRect>
	</VideoTrackGroup>
	<AudioTrackGroup ObjectID="21" ClassID="atg" Version="4">
		<TrackGroup Version="1"><Tracks Version="1"/><FrameRate>5292000</FrameRate></TrackGroup>
		<NumAdaptiveChannels>2</NumAdaptiveChannels>
	</AudioTrackGroup>
	<VideoClipTrack ObjectUID="vt-1" ClassID="vct" Version="3">
		<ClipTrack Version="1">
			<Track Version="1"><ID>1</ID><Index>0</Index></Track>
			<ClipItems Version="3"><TrackItems Version="1">
				<TrackItem Index="0" ObjectRef="30"/>
				<TrackItem Index="1" ObjectRef="31"/>
			</TrackItems></ClipItems>
		</ClipTrack>
	</VideoClipTrack>
	<VideoClipTrack ObjectUID="vt-2" ClassID="vct" Version="3">
		<ClipTrack Version="1">
			<Track Version="1"><ID>2</ID><Index>1</Index></Track>
			<ClipItems Version="3"><TrackItems Version="1">
				<TrackItem Index="0" ObjectRef="32"/>
			</TrackItems></ClipItems>
		</ClipTrack>
	</VideoClipTrack>
	<VideoClipTrackItem ObjectID="30" ClassID="vti" Version="8">
		<ClipTrackItem Version="8"><TrackItem Version="4"><End>{48 * TPF}</End></TrackItem><SubClip ObjectRef="40"/></ClipTrackItem>
	</VideoClipTrackItem>
	<VideoClipTrackItem ObjectID="31" ClassID="vti" Version="8">
		<ClipTrackItem Version="8"><TrackItem Version="4"><Start>{48 * TPF}</Start><End>{96 * TPF}</End></TrackItem><SubClip ObjectRef="41"/></ClipTrackItem>
	</VideoClipTrackItem>
	<VideoClipTrackItem ObjectID="32" ClassID="vti" Version="8">
		<ClipTrackItem Version="8"><TrackItem Version="4"><Start>{24 * TPF}</Start><End>{72 * TPF}</End></TrackItem><SubClip ObjectRef="42"/></ClipTrackItem>
	</VideoClipTrackItem>
	<SubClip ObjectID="40" ClassID="sc" Version="6"><Clip ObjectRef="50"/><MasterClip ObjectURef="master-a"/><Name>A001.mov</Name></SubClip>
	<SubClip ObjectID="41" ClassID="sc" Version="6"><Clip ObjectRef="51"/><MasterClip ObjectURef="master-a"/><Name>A001.mov</Name></SubClip>
	<SubClip ObjectID="42" ClassID="sc" Version="6"><Clip ObjectRef="52"/><MasterClip ObjectURef="master-a"/><Name>A001 b-roll</Name></SubClip>
	<VideoClip ObjectID="50" ClassID="vc" Version="11"><Clip Version="18"><Source ObjectRef="11"/><InPoint>0</InPoint><OutPoint>{48 * TPF}</OutPoint></Clip></VideoClip>
	<VideoClip ObjectID="51" ClassID="vc" Version="11"><Clip Version="18"><Source ObjectRef="11"/><InPoint>{100 * TPF}</InPoint><OutPoint>{148 * TPF}</OutPoint></Clip></VideoClip>
	<VideoClip ObjectID="52" ClassID="vc" Version="11"><Clip Version="18"><Source ObjectRef="11"/><InPoint>{200 * TPF}</InPoint><OutPoint>{240 * TPF}</OutPoint><PlaybackSpeed>1.2</PlaybackSpeed></Clip></VideoClip>
</PremiereData>
"""


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.p = project_from_doc(_doc_from_text(FIXTURE))

    def test_bins_tree(self):
        root = self.p.root
        self.assertEqual((root.name, root.kind, root.uid), ("demo.prproj", "RootProjectItem", "root"))
        self.assertEqual([b.name for b in root.bins], ["Interviews"])
        self.assertEqual([b.name for b in root.bins[0].bins], ["Day 1"])
        self.assertEqual(root.bins[0].bins[0].clips, ["master-a"])
        paths = [path for path, _ in root.walk()]
        self.assertEqual(paths, [(), ("Interviews",), ("Interviews", "Day 1")])

    def test_master_clip_and_media(self):
        mc = self.p.clips["master-a"]
        self.assertEqual(mc.name, "A001.mov")  # MasterClip/Name wins over the stale ProjectItem/Name
        self.assertEqual(mc.item_uid, "item-a")
        self.assertEqual(self.p.media[mc.media_uid].path, "/Volumes/Drive/A001.mov")
        self.assertEqual(mc.duration, Time(240 * TPF))
        # The sequence's panel item has no MasterClip here: kept, not dropped.
        self.assertEqual(self.p.root.extras["unmodeled_items"][0]["uid"], "item-seq")

    def test_sequence_tracks_items(self):
        (s,) = self.p.sequences
        self.assertEqual((s.name, s.uid), ("Rough", "seq-1"))
        self.assertEqual(s.rate, Rate(24000, 1001))
        self.assertEqual(s.frame_size, (1920, 1080))
        self.assertEqual(s.zero_point.to_timecode(s.rate), "01:00:00:00")
        self.assertEqual(s.extras["properties"]["MZ.Sequence.VideoTimeDisplayFormat"], "110")
        self.assertEqual([t.uid for t in s.video_tracks], ["vt-1", "vt-2"])
        self.assertEqual([t.index for t in s.video_tracks], [0, 1])
        self.assertEqual(s.audio_tracks, [])
        v1, v2 = s.video_tracks
        self.assertEqual([i.oid for i in v1.items], ["30", "31"])
        a, b = v1.items
        self.assertEqual(a.start, Time(0))  # elided Start reads as zero
        self.assertEqual(a.end.to_frames(s.rate), 48)
        self.assertEqual((b.in_.to_frames(s.rate), b.out.to_frames(s.rate)), (100, 148))
        (c,) = v2.items
        self.assertEqual((c.name, c.master_uid, c.extras["playback_speed"]), ("A001 b-roll", "master-a", "1.2"))
        self.assertEqual(s.end.to_frames(s.rate), 96)

    def test_json_round_trip_of_parsed(self):
        self.assertEqual(Project.from_json(self.p.to_json()), self.p)


if __name__ == "__main__":
    unittest.main()
