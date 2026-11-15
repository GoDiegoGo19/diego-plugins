"""prwrite.probe: the stream facts of one media file, without decoding it.

In:  a path to a movie (mp4/mov), audio (wav) or still (png/jpg).
Out: `probe(path) -> MediaInfo` (kind, duration, exact fps, frame size,
     audio channels and sample rate, codec fourcc, start timecode).

Order: `ffprobe` when `shutil.which("ffprobe")` finds it (it reads headers
only with the flags we pass); otherwise our own stdlib header readers:
  mp4/mov  ISO-BMFF boxes moov/mvhd, trak/tkhd, mdia/mdhd, hdlr, stsd, stts
           (we seek over every box we do not need, so `mdat` is never read);
  wav      RIFF `fmt ` chunk, and the `data` chunk's SIZE (never its bytes);
  png      IHDR; jpg  the first SOFn marker.
Audio samples are never read on either path.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
from dataclasses import dataclass
from fractions import Fraction

from .model import TICKS_PER_SECOND, Rate, Time

__all__ = ["MediaInfo", "ProbeError", "probe", "probe_ffprobe", "probe_headers"]

STILL_EXT = {".png", ".jpg", ".jpeg"}
AUDIO_EXT = {".wav", ".wave"}
MOVIE_EXT = {".mp4", ".mov", ".m4v"}


class ProbeError(ValueError):
    """The file is not one we can read stream facts from."""


@dataclass
class MediaInfo:
    """Stream facts. `fps` is exact (24000/1001), `duration` in seconds (exact)."""

    path: str
    kind: str = "video"  # "video" (movie, may carry audio), "audio", "still"
    duration: Fraction | None = None
    fps: Fraction | None = None
    width: int = 0
    height: int = 0
    audio_channels: int = 0
    sample_rate: int = 0
    codec: str = ""  # video fourcc ("avc1", "hvc1", "apch"); audio codec name for audio-only
    audio_codec: str = ""
    timecode: str = ""  # start timecode tag, "" when the file has none
    source: str = ""  # "ffprobe" or "headers"

    @property
    def has_video(self) -> bool:
        return self.kind in ("video", "still")

    @property
    def rate(self) -> Rate | None:
        return None if not self.fps else Rate.from_fps(self.fps)

    @property
    def duration_ticks(self) -> int:
        """Duration in ticks; for video, a whole number of frames when the rate allows."""
        if self.duration is None:
            return 0
        rate = self.rate
        if rate is not None:
            try:
                tpf = rate.ticks_per_frame
                return round(self.duration * rate.fps) * tpf
            except ValueError:
                pass
        return round(self.duration * TICKS_PER_SECOND)

    @property
    def start(self) -> Time:
        """The start timecode as a Time (0 when there is none or no rate)."""
        if not self.timecode or self.rate is None:
            return Time()
        try:
            return Time.from_timecode(self.timecode, self.rate)
        except ValueError:
            return Time()


def probe(path: str, *, use_ffprobe: bool | None = None) -> MediaInfo:
    """Path -> MediaInfo. ffprobe first when present, header readers otherwise."""
    if not os.path.isfile(path):
        raise ProbeError(f"no such file: {path}")
    if use_ffprobe is None:
        use_ffprobe = shutil.which("ffprobe") is not None
    if use_ffprobe:
        try:
            return probe_ffprobe(path)
        except ProbeError:
            pass  # fall through to the stdlib readers
    return probe_headers(path)


# ------------------------------------------------------------------ ffprobe

def _frac(text: str | None) -> Fraction | None:
    if not text or text in ("0/0", "N/A"):
        return None
    try:
        value = Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def probe_ffprobe(path: str) -> MediaInfo:
    cmd = ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path]
    try:
        out = subprocess.run(cmd, capture_output=True, check=True, timeout=60).stdout
        data = json.loads(out or b"{}")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ProbeError(f"ffprobe failed on {path}: {exc}") from None
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    ext = os.path.splitext(path)[1].lower()
    info = MediaInfo(path=path, source="ffprobe")
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None and audio is None:
        raise ProbeError(f"ffprobe found no video or audio stream in {path}")
    if video is not None:
        info.kind = "still" if ext in STILL_EXT else "video"
        info.width, info.height = int(video.get("width") or 0), int(video.get("height") or 0)
        info.codec = video.get("codec_tag_string") or ""
        if info.codec.startswith("[") or info.codec == "":
            info.codec = video.get("codec_name") or ""
        if info.kind == "video":
            info.fps = _frac(video.get("r_frame_rate")) or _frac(video.get("avg_frame_rate"))
    else:
        info.kind = "audio"
    if audio is not None:
        info.audio_channels = int(audio.get("channels") or 0)
        info.sample_rate = int(audio.get("sample_rate") or 0)
        info.audio_codec = audio.get("codec_name") or ""
    dur = (video or {}).get("duration") or fmt.get("duration") or (audio or {}).get("duration")
    if info.kind != "still" and dur not in (None, "N/A"):
        info.duration = Fraction(str(dur))
    for s in streams + [fmt]:
        tc = (s.get("tags") or {}).get("timecode")
        if tc:
            info.timecode = tc
            break
    return info


# ------------------------------------------------------------- stdlib headers

def probe_headers(path: str) -> MediaInfo:
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as fh:
        head = fh.read(16)
        fh.seek(0)
        if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
            return _wav(fh, path)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return _png(fh, path)
        if head[:2] == b"\xff\xd8":
            return _jpg(fh, path)
        if head[4:8] in (b"ftyp", b"moov", b"wide", b"free", b"mdat", b"skip") or ext in MOVIE_EXT:
            return _isobmff(fh, path)
    raise ProbeError(f"no header reader for {path}")


def _wav(fh, path) -> MediaInfo:
    size = os.fstat(fh.fileno()).st_size
    fh.seek(12)
    channels = rate = byte_rate = 0
    data_size = None
    while fh.tell() + 8 <= size:
        cid, csize = struct.unpack("<4sI", fh.read(8))
        if cid == b"fmt ":
            body = fh.read(min(csize, 40))
            _tag, channels, rate, byte_rate = struct.unpack_from("<HHII", body)
            fh.seek(csize - len(body) + (csize & 1), 1)
        elif cid == b"data":
            data_size = min(csize, size - fh.tell())
            break  # the samples themselves are never read
        else:
            fh.seek(csize + (csize & 1), 1)
    if not byte_rate:
        raise ProbeError(f"wav without a fmt chunk: {path}")
    dur = Fraction(data_size or 0, byte_rate)
    return MediaInfo(path=path, kind="audio", duration=dur, audio_channels=channels,
                     sample_rate=rate, audio_codec="pcm", source="headers")


def _png(fh, path) -> MediaInfo:
    data = fh.read(24)
    w, h = struct.unpack(">II", data[16:24])
    return MediaInfo(path=path, kind="still", width=w, height=h, codec="png", source="headers")


def _jpg(fh, path) -> MediaInfo:
    fh.seek(2)
    while True:
        b = fh.read(1)
        while b and b != b"\xff":
            b = fh.read(1)
        while b == b"\xff":
            b = fh.read(1)
        if not b:
            raise ProbeError(f"jpeg without a SOF marker: {path}")
        marker = b[0]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        (length,) = struct.unpack(">H", fh.read(2))
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            _p, h, w = struct.unpack(">BHH", fh.read(5))
            return MediaInfo(path=path, kind="still", width=w, height=h, codec="jpeg", source="headers")
        fh.seek(length - 2, 1)


def _boxes(fh, start: int, end: int):
    """Yield (type, body_start, body_end) for the boxes in [start, end), by seeking."""
    at = start
    while at + 8 <= end:
        fh.seek(at)
        size, kind = struct.unpack(">I4s", fh.read(8))
        body = at + 8
        if size == 1:
            (size,) = struct.unpack(">Q", fh.read(8))
            body = at + 16
        elif size == 0:
            size = end - at
        if size < 8:
            return
        yield kind, body, min(at + size, end)
        at += size


def _child(fh, start, end, *path):
    for kind, b, e in _boxes(fh, start, end):
        if kind == path[0]:
            return (b, e) if len(path) == 1 else _child(fh, b, e, *path[1:])
    return None


def _isobmff(fh, path) -> MediaInfo:
    size = os.fstat(fh.fileno()).st_size
    moov = _child(fh, 0, size, b"moov")
    if moov is None:
        raise ProbeError(f"no moov box in {path}")
    info = MediaInfo(path=path, kind="audio", source="headers")
    mvhd = _child(fh, *moov, b"mvhd")
    if mvhd:
        fh.seek(mvhd[0])
        version = fh.read(1)[0]
        fh.seek(mvhd[0] + (20 if version == 1 else 12))
        if version == 1:
            scale, dur = struct.unpack(">IQ", fh.read(12))
        else:
            scale, dur = struct.unpack(">II", fh.read(8))
        if scale:
            info.duration = Fraction(dur, scale)
    saw_video = False
    for kind, b, e in _boxes(fh, *moov):
        if kind != b"trak":
            continue
        mdia = _child(fh, b, e, b"mdia")
        if mdia is None:
            continue
        hdlr = _child(fh, *mdia, b"hdlr")
        fh.seek(hdlr[0] + 8) if hdlr else None
        handler = fh.read(4) if hdlr else b""
        mdhd = _child(fh, *mdia, b"mdhd")
        tscale = 0
        if mdhd:
            fh.seek(mdhd[0])
            v = fh.read(1)[0]
            fh.seek(mdhd[0] + (20 if v == 1 else 12))
            (tscale,) = struct.unpack(">I", fh.read(4))
        stsd = _child(fh, *mdia, b"minf", b"stbl", b"stsd")
        if handler == b"vide" and not saw_video:
            saw_video = True
            info.kind = "video"
            tkhd = _child(fh, b, e, b"tkhd")
            if tkhd:
                fh.seek(tkhd[1] - 8)
                w, h = struct.unpack(">II", fh.read(8))
                info.width, info.height = w >> 16, h >> 16
            if stsd:
                fh.seek(stsd[0] + 12)
                info.codec = fh.read(4).decode("latin-1")
                if not info.width:
                    fh.seek(stsd[0] + 8 + 8 + 24)
                    info.width, info.height = struct.unpack(">HH", fh.read(4))
            stts = _child(fh, *mdia, b"minf", b"stbl", b"stts")
            if stts and tscale:
                fh.seek(stts[0] + 4)
                (n,) = struct.unpack(">I", fh.read(4))
                if n:
                    _count, delta = struct.unpack(">II", fh.read(8))
                    if delta:
                        info.fps = Rate.from_fps(Fraction(tscale, delta)).fps
        elif handler == b"soun" and not info.audio_channels and stsd:
            fh.seek(stsd[0] + 12)
            info.audio_codec = fh.read(4).decode("latin-1")
            fh.seek(stsd[0] + 8 + 8 + 16)
            ch, _bits = struct.unpack(">HH", fh.read(4))
            fh.seek(stsd[0] + 8 + 8 + 24)
            (rate_fixed,) = struct.unpack(">I", fh.read(4))
            info.audio_channels, info.sample_rate = ch, rate_fixed >> 16
        elif handler == b"tmcd":
            pass  # the timecode sample lives in mdat; ffprobe reads it, we leave it ""
    if not saw_video and not info.audio_channels:
        raise ProbeError(f"no video or sound track in {path}")
    return info
