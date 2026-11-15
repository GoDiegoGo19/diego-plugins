"""prwrite.model: plain project data, no XML.

In:  values (from `parse.project_from_doc`, from `build`, or from JSON).
Out: dataclasses `Project`, `Bin`, `Media`, `MasterClip`, `Sequence`,
     `Track`, `TrackItem`, plus the value types `Rate` and `Time`;
     `Project.to_json()` -> str and `Project.from_json(str)` -> Project.

Time is integer ticks, 254016000000 per second (fact: vendored
py_premiere/models/time.py:10). Rates are exact fractions (23.976 is
24000/1001), so tick <-> frame <-> timecode math never touches floats.

Every object carries `oid` (its ObjectID in the document, "" if it has
none), `uid` (its ObjectUID, "" if Premiere keys it by ObjectID) and
`extras`, a JSON-safe dict for anything not modeled, so a round trip
through the model drops nothing we read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, is_dataclass
from fractions import Fraction
from typing import Any

TICKS_PER_SECOND = 254016000000

# Common video rates written the way people say them -> exact fractions.
_NAMED_RATES = {
    "23.976": Fraction(24000, 1001),
    "23.98": Fraction(24000, 1001),
    "29.97": Fraction(30000, 1001),
    "47.952": Fraction(48000, 1001),
    "59.94": Fraction(60000, 1001),
    "119.88": Fraction(120000, 1001),
}


@dataclass(frozen=True)
class Rate:
    """A frame rate as an exact fraction `num/den` frames per second."""

    num: int
    den: int = 1

    def __post_init__(self) -> None:
        if self.num <= 0 or self.den <= 0:
            raise ValueError("rate must be positive")
        g = Fraction(self.num, self.den)
        object.__setattr__(self, "num", g.numerator)
        object.__setattr__(self, "den", g.denominator)

    @classmethod
    def from_fps(cls, fps: int | float | str | Fraction) -> "Rate":
        """24, 25, "23.976", 29.97, Fraction(30000, 1001) ... -> Rate."""
        if isinstance(fps, Fraction):
            return cls(fps.numerator, fps.denominator)
        key = f"{fps}".rstrip("0").rstrip(".") if isinstance(fps, (float, str)) else None
        if key in _NAMED_RATES:
            f = _NAMED_RATES[key]
            return cls(f.numerator, f.denominator)
        f = Fraction(str(fps))
        # A float that is within 0.01 of an NTSC rate means that NTSC rate.
        for named in _NAMED_RATES.values():
            if abs(f - named) < Fraction(1, 100):
                return cls(named.numerator, named.denominator)
        return cls(f.numerator, f.denominator)

    @classmethod
    def from_ticks_per_frame(cls, tpf: int) -> "Rate":
        """Premiere stores a rate as ticks per frame (FrameRate element)."""
        if tpf <= 0:
            raise ValueError("ticks per frame must be positive")
        f = Fraction(TICKS_PER_SECOND, tpf)
        return cls(f.numerator, f.denominator)

    @property
    def fps(self) -> Fraction:
        return Fraction(self.num, self.den)

    @property
    def ticks_per_frame(self) -> int:
        tpf = Fraction(TICKS_PER_SECOND) / self.fps
        if tpf.denominator != 1:
            raise ValueError(f"{self} has no whole tick count per frame")
        return tpf.numerator

    @property
    def nominal(self) -> int:
        """The integer rate timecode counts in (29.97 -> 30, 23.976 -> 24)."""
        return round(self.fps)

    @property
    def can_drop(self) -> bool:
        """Drop-frame exists only for the 29.97/59.94 family."""
        return self.den != 1 and self.nominal % 30 == 0

    def __str__(self) -> str:
        return str(self.num) if self.den == 1 else f"{self.num}/{self.den}"

    def to_json(self) -> list[int]:
        return [self.num, self.den]

    @classmethod
    def from_json(cls, value: Any) -> "Rate":
        return cls(int(value[0]), int(value[1]))


def _dropped_per_minute(rate: Rate) -> int:
    if not rate.can_drop:
        raise ValueError(f"drop-frame timecode needs a 29.97/59.94 rate, not {rate}")
    return rate.nominal // 15  # 2 at 29.97, 4 at 59.94


@dataclass(frozen=True, order=True)
class Time:
    """A point or span in ticks (int). Exact; seconds come back as Fraction."""

    ticks: int = 0

    @classmethod
    def from_seconds(cls, seconds: int | float | str | Fraction) -> "Time":
        """Rounded to the nearest tick (half to even)."""
        return cls(round(Fraction(str(seconds)) * TICKS_PER_SECOND))

    @property
    def seconds(self) -> Fraction:
        return Fraction(self.ticks, TICKS_PER_SECOND)

    @classmethod
    def from_frames(cls, frames: int, rate: Rate) -> "Time":
        return cls(frames * rate.ticks_per_frame)

    def to_frames(self, rate: Rate) -> int:
        """The frame this time falls in (floor)."""
        return self.ticks // rate.ticks_per_frame

    @classmethod
    def from_timecode(cls, tc: str, rate: Rate) -> "Time":
        """`HH:MM:SS:FF`; a `;` (or `.`) anywhere marks drop-frame."""
        drop = ";" in tc or "." in tc
        parts = tc.replace(";", ":").replace(".", ":").split(":")
        if len(parts) != 4 or not all(p.isdigit() for p in parts):
            raise ValueError(f"not a HH:MM:SS:FF timecode: {tc!r}")
        h, m, s, f = (int(p) for p in parts)
        n = rate.nominal
        if m >= 60 or s >= 60 or f >= n:
            raise ValueError(f"timecode field out of range: {tc!r}")
        frames = (h * 3600 + m * 60 + s) * n + f
        if drop:
            d = _dropped_per_minute(rate)
            if s == 0 and m % 10 and f < d:
                raise ValueError(f"{tc!r} names a dropped frame number")
            minutes = 60 * h + m
            frames -= d * (minutes - minutes // 10)
        return cls.from_frames(frames, rate)

    def to_timecode(self, rate: Rate, drop: bool = False) -> str:
        frames = self.to_frames(rate)
        if frames < 0:
            raise ValueError("negative times have no timecode")
        n = rate.nominal
        sep = ":"
        if drop:
            d = _dropped_per_minute(rate)
            per_min = 60 * n - d
            per_10 = 10 * 60 * n - 9 * d
            tens, rem = divmod(frames, per_10)
            frames += 9 * d * tens
            if rem > d:
                frames += d * ((rem - d) // per_min)
            sep = ";"
        f = frames % n
        total_s = frames // n
        return f"{total_s // 3600:02d}:{total_s // 60 % 60:02d}:{total_s % 60:02d}{sep}{f:02d}"

    def __add__(self, other: "Time") -> "Time":
        return Time(self.ticks + other.ticks)

    def __sub__(self, other: "Time") -> "Time":
        return Time(self.ticks - other.ticks)


# ---------------------------------------------------------------- objects


@dataclass
class Media:
    """A media file (Premiere `Media`, keyed by ObjectUID).

    Stream facts (from `probe`, or read back from the document) are real
    fields; unknown stays at the default (None / 0 / ""). `audio_channels`
    None = unknown, 0 = no audio. Older JSON with these keys in `extras`
    still loads, and export reads either place (fields first)."""

    path: str = ""
    oid: str = ""
    uid: str = ""
    rate: Rate | None = None
    duration: Time | None = None
    width: int = 0
    height: int = 0
    audio_channels: int | None = None
    sample_rate: int = 0
    has_video: bool | None = None
    timecode_start: str = ""
    extras: dict = field(default_factory=dict)


@dataclass
class MasterClip:
    """A clip in the project panel: `ClipProjectItem` (item_uid) and its
    `MasterClip` (uid). `media_uid` names the Media it plays, "" for
    generators and sequence clips; `sequence_uid` is set for a sequence's
    own master clip. `duration` is the source's OriginalDuration."""

    name: str = ""
    oid: str = ""
    uid: str = ""
    item_uid: str = ""
    media_uid: str = ""
    sequence_uid: str = ""
    duration: Time | None = None
    extras: dict = field(default_factory=dict)


@dataclass
class Bin:
    """A bin (or the root item). `clips` holds MasterClip.uid keys in panel
    order; nested bins in `bins`. `kind` is the element tag (RootProjectItem,
    BinProjectItem, SmartBinProjectItem)."""

    name: str = ""
    oid: str = ""
    uid: str = ""
    kind: str = "BinProjectItem"
    bins: list["Bin"] = field(default_factory=list)
    clips: list[str] = field(default_factory=list)
    extras: dict = field(default_factory=dict)

    def walk(self):
        """Yield (path, bin) depth first; path is a tuple of bin names."""
        stack = [((), self)]
        while stack:
            path, b = stack.pop()
            yield path, b
            for child in reversed(b.bins):
                stack.append((path + (child.name,), child))


@dataclass
class TrackItem:
    """A clip on a track. `start`/`end` are on the sequence timeline;
    `in_`/`out` are in the source (raw stored ticks, before speed).
    `master_uid` names the MasterClip, `clip_oid` the source clip object."""

    name: str = ""
    oid: str = ""
    uid: str = ""
    start: Time = field(default_factory=Time)
    end: Time = field(default_factory=Time)
    in_: Time = field(default_factory=Time)
    out: Time = field(default_factory=Time)
    master_uid: str = ""
    clip_oid: str = ""
    subclip_oid: str = ""
    extras: dict = field(default_factory=dict)

    @property
    def duration(self) -> Time:
        return self.end - self.start


@dataclass
class Track:
    """A video or audio clip track (keyed by ObjectUID). `kind` is
    "Video" or "Audio"; `index` is its position, `track_id` Premiere's ID."""

    kind: str = "Video"
    oid: str = ""
    uid: str = ""
    index: int = 0
    track_id: int = 0
    items: list[TrackItem] = field(default_factory=list)
    extras: dict = field(default_factory=dict)


@dataclass
class Sequence:
    """A timeline. `rate` from the video group FrameRate; `zero_point` is the
    starting timecode (MZ.ZeroPoint); `frame_size` from FrameRect."""

    name: str = ""
    oid: str = ""
    uid: str = ""
    rate: Rate | None = None
    frame_size: tuple[int, int] | None = None
    zero_point: Time = field(default_factory=Time)
    video_tracks: list[Track] = field(default_factory=list)
    audio_tracks: list[Track] = field(default_factory=list)
    extras: dict = field(default_factory=dict)

    @property
    def end(self) -> Time:
        return max((i.end for t in self.video_tracks + self.audio_tracks for i in t.items),
                   default=Time())


@dataclass
class Project:
    """The whole project: the root bin tree, master clips and media by uid,
    and sequences in document order."""

    name: str = ""
    oid: str = ""
    uid: str = ""
    root: Bin = field(default_factory=lambda: Bin(kind="RootProjectItem"))
    media: dict[str, Media] = field(default_factory=dict)
    clips: dict[str, MasterClip] = field(default_factory=dict)
    sequences: list[Sequence] = field(default_factory=list)
    extras: dict = field(default_factory=dict)

    def to_json(self, indent: int | None = 1) -> str:
        return json.dumps(_dump(self), indent=indent, sort_keys=False)

    @classmethod
    def from_json(cls, text: str) -> "Project":
        return _load(cls, json.loads(text))


# ------------------------------------------------------- json (dataclasses)

_TYPES = {c.__name__: c for c in (Media, MasterClip, Bin, TrackItem, Track, Sequence, Project)}


def _dump(value: Any) -> Any:
    if isinstance(value, Time):
        return value.ticks
    if isinstance(value, Rate):
        return value.to_json()
    if is_dataclass(value):
        return {f.name: _dump(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (list, tuple)):
        return [_dump(v) for v in value]
    if isinstance(value, dict):
        return {k: _dump(v) for k, v in value.items()}
    return value


def _load(cls: type, data: dict) -> Any:
    kw = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        v = data[f.name]
        t = f.type if isinstance(f.type, str) else str(f.type)
        if v is None:
            kw[f.name] = None
        elif t.startswith("Time"):
            kw[f.name] = Time(int(v))
        elif t.startswith("Rate"):
            kw[f.name] = Rate.from_json(v)
        elif t.startswith("tuple"):
            kw[f.name] = tuple(v)
        elif t == "Bin":
            kw[f.name] = _load(Bin, v)
        elif t.startswith("list[") and t[5:-1].strip("'\"") in _TYPES:
            kw[f.name] = [_load(_TYPES[t[5:-1].strip("'\"")], x) for x in v]
        elif t.startswith("dict[str, ") and t[10:-1] in _TYPES:
            kw[f.name] = {k: _load(_TYPES[t[10:-1]], x) for k, x in v.items()}
        else:
            kw[f.name] = v
    return cls(**kw)
