"""Shared fixture for owner D's tests: a small hand-built model.Project.

There is no real .prproj in the repo; this Project is made from prwrite.model
dataclasses directly (owner B's model.py), not parsed from a file.
"""
import os
import sys

LIB = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from prwrite.model import Bin, MasterClip, Media, Project, Rate, Sequence, Time, Track, TrackItem  # noqa: E402

R2398 = Rate(24000, 1001)


def f(n, rate=R2398):
    return Time.from_frames(n, rate)


def small_project():
    mov = Media(path="/Volumes/DRIVE/day 1/A001.MOV", uid="11111111-1111-1111-1111-111111111111",
                extras={"rate": [24000, 1001], "width": 3840, "height": 2160, "audio_channels": 2,
                        "timecode_start": "12:00:00:00"})
    wav = Media(path="/Volumes/DRIVE/day 1/ZOOM0001.WAV", uid="m-wav",
                extras={"rate": [24000, 1001], "audio_channels": 2, "has_video": False})
    a001 = MasterClip(name="A001.MOV", uid="mc-a001", media_uid=mov.uid, duration=f(480))
    zoom = MasterClip(name="ZOOM0001.WAV", uid="mc-zoom", media_uid=wav.uid, duration=f(960))
    seq_mc = MasterClip(name="ASSEMBLY", uid="mc-seq", sequence_uid="seq-1")
    v1 = Track(kind="Video", index=0, items=[
        TrackItem(name="A001.MOV", start=f(0), end=f(100), in_=f(10), out=f(110), master_uid="mc-a001"),
        TrackItem(name="A001.MOV", start=f(100), end=f(200), in_=f(300), out=f(400), master_uid="mc-a001"),
    ])
    a1 = Track(kind="Audio", index=0, items=[
        TrackItem(start=f(0), end=f(100), in_=f(10), out=f(110), master_uid="mc-a001"),
        TrackItem(start=f(100), end=f(200), in_=f(300), out=f(400), master_uid="mc-a001"),
    ])
    a2 = Track(kind="Audio", index=1, items=[
        TrackItem(start=f(0), end=f(100), in_=f(10), out=f(110), master_uid="mc-a001"),
    ])
    a3 = Track(kind="Audio", index=2, items=[
        TrackItem(start=f(0), end=f(200), in_=f(0), out=f(200), master_uid="mc-zoom"),
    ])
    seq = Sequence(name="ASSEMBLY", uid="seq-1", rate=R2398, frame_size=(1920, 1080),
                   video_tracks=[v1], audio_tracks=[a1, a2, a3])
    root = Bin(kind="RootProjectItem", bins=[
        Bin(name="01_SEQUENCES", clips=["mc-seq"]),
        Bin(name="02_FOOTAGE", bins=[Bin(name="DAY_1", clips=["mc-a001", "mc-zoom"])]),
    ])
    return Project(name="TEST_DRIVE", root=root, media={mov.uid: mov, wav.uid: wav},
                   clips={c.uid: c for c in (a001, zoom, seq_mc)}, sequences=[seq])
