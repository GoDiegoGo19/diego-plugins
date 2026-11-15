"""framing: the bytes on disk <-> the XML text inside a .prproj.

In:  a path (or raw bytes) that is either a gzip stream or plain XML.
Out: the XML as `str`, plus a `Framing` record of how it was wrapped, so a
     write can wrap it the same way again.

What Premiere writes (facts from the vendored py-premiere, see SPEC.md):
a single gzip member whose 10-byte header is
    1f 8b | CM=08 (deflate) | FLG=00 | MTIME=00000000 | XFL=00 | OS=0a
i.e. no file name, no comment, no extra field, no header CRC, mtime zero,
OS byte 10 (older builds wrote 19). The body is a raw deflate stream at
zlib level 6 and the trailer is CRC32 + ISIZE, little-endian.

Our rules:
* A header read from a file is kept verbatim (including any optional
  FNAME/FCOMMENT/FEXTRA fields a CLI gzip may have added) and written back.
* A file that does not start with the gzip magic is plain XML and passes
  through unchanged; writing it back with `gzip=False` keeps it plain.
* Inflate stops at `max_size` bytes of output (default 2 GB) and raises
  `FramingError` instead of eating memory. Corrupt, truncated, CRC-mismatched
  and multi-member files also raise `FramingError`.
"""

from __future__ import annotations

import os
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "FramingError",
    "Framing",
    "PREMIERE_HEADER",
    "DEFAULT_MAX_SIZE",
    "unframe",
    "frame",
    "read_prproj",
    "read_prproj_framed",
    "write_prproj",
]

DEFAULT_MAX_SIZE = 2 * 1024**3  # 2 GB of inflated XML

_MAGIC = b"\x1f\x8b"
_CM_DEFLATE = 8
_OS_PREMIERE = 10
# FLG bits (RFC 1952 section 2.3.1)
_F_HCRC, _F_EXTRA, _F_NAME, _F_COMMENT = 0x02, 0x04, 0x08, 0x10

#: The header current Premiere builds write: no optional fields, mtime 0, OS 10.
PREMIERE_HEADER = _MAGIC + struct.pack("<BBIBB", _CM_DEFLATE, 0, 0, 0, _OS_PREMIERE)

_CHUNK = 1 << 20  # inflate in 1 MB output steps so the cap is enforced early


class FramingError(ValueError):
    """The bytes are not a .prproj we can unwrap (or are too big to)."""


@dataclass(frozen=True)
class Framing:
    """How a payload was wrapped. `header is None` means plain XML."""

    header: bytes | None = PREMIERE_HEADER

    @property
    def is_gzip(self) -> bool:
        return self.header is not None

    @property
    def mtime(self) -> int | None:
        return None if self.header is None else struct.unpack_from("<I", self.header, 4)[0]

    @property
    def os_byte(self) -> int | None:
        return None if self.header is None else self.header[9]

    @property
    def name(self) -> bytes | None:
        """The FNAME field, if the header has one (Premiere's never does)."""
        if self.header is None or not self.header[3] & _F_NAME:
            return None
        start = 10
        if self.header[3] & _F_EXTRA:
            start += 2 + struct.unpack_from("<H", self.header, 10)[0]
        return self.header[start : self.header.index(b"\x00", start)]


PLAIN = Framing(header=None)


def _measure_header(data: bytes) -> int:
    """Length of the gzip header at the front of `data` (fixed + optional parts)."""
    if len(data) < 10:
        raise FramingError("truncated gzip header")
    if data[2] != _CM_DEFLATE:
        raise FramingError(f"unsupported gzip compression method {data[2]}")
    flags = data[3]
    at = 10
    if flags & _F_EXTRA:
        if len(data) < at + 2:
            raise FramingError("truncated gzip header (FEXTRA)")
        at += 2 + struct.unpack_from("<H", data, at)[0]
    for bit in (_F_NAME, _F_COMMENT):
        if flags & bit:
            end = data.find(b"\x00", at)
            if end < 0:
                raise FramingError("truncated gzip header (unterminated string field)")
            at = end + 1
    if flags & _F_HCRC:
        at += 2
    if at > len(data):
        raise FramingError("truncated gzip header")
    return at


def unframe(data: bytes, *, max_size: int = DEFAULT_MAX_SIZE) -> tuple[bytes, Framing]:
    """Raw file bytes -> (XML payload bytes, Framing). Plain XML passes through."""
    if not data.startswith(_MAGIC):
        if len(data) > max_size:
            raise FramingError(f"plain XML is {len(data)} bytes, over the {max_size}-byte cap")
        return data, PLAIN
    head = _measure_header(data)
    inflater = zlib.decompressobj(-zlib.MAX_WBITS)
    pieces: list[bytes] = []
    total = 0
    pending = data[head:]
    try:
        while True:
            room = max_size - total
            out = inflater.decompress(pending, min(_CHUNK, room + 1))
            total += len(out)
            if total > max_size:
                raise FramingError(f"inflated payload exceeds the {max_size}-byte cap")
            pieces.append(out)
            pending = inflater.unconsumed_tail
            if inflater.eof or (not pending and not out):
                break
    except zlib.error as exc:
        raise FramingError(f"corrupt deflate stream: {exc}") from None
    if not inflater.eof:
        raise FramingError("truncated deflate stream")
    payload = b"".join(pieces)
    trailer = inflater.unused_data
    if len(trailer) < 8:
        raise FramingError("truncated gzip trailer")
    if len(trailer) > 8:
        raise FramingError("more than one gzip member; Premiere writes exactly one")
    crc, isize = struct.unpack("<II", trailer)
    if crc != zlib.crc32(payload) or isize != len(payload) & 0xFFFFFFFF:
        raise FramingError("gzip CRC or length does not match the payload")
    return payload, Framing(header=bytes(data[:head]))


def frame(payload: bytes, framing: Framing = Framing()) -> bytes:
    """XML payload bytes -> file bytes, wrapped the way `framing` says."""
    if framing.header is None:
        return payload
    deflater = zlib.compressobj(6, zlib.DEFLATED, -zlib.MAX_WBITS)
    body = deflater.compress(payload) + deflater.flush()
    return framing.header + body + struct.pack("<II", zlib.crc32(payload), len(payload) & 0xFFFFFFFF)


def read_prproj_framed(path: str | os.PathLike, *, max_size: int = DEFAULT_MAX_SIZE) -> tuple[str, Framing]:
    """Path -> (XML text, Framing). Text is decoded as UTF-8 (Premiere's encoding)."""
    raw = Path(path).read_bytes()
    payload, framing = unframe(raw, max_size=max_size)
    try:
        return payload.decode("utf-8"), framing
    except UnicodeDecodeError as exc:
        raise FramingError(f"payload is not UTF-8: {exc}") from None


def read_prproj(path: str | os.PathLike, *, max_size: int = DEFAULT_MAX_SIZE) -> str:
    """Path -> XML text (gzip or plain, detected from the magic bytes)."""
    return read_prproj_framed(path, max_size=max_size)[0]


def write_prproj(
    path: str | os.PathLike,
    text: str,
    *,
    gzip: bool = True,
    framing: Framing | None = None,
) -> Path:
    """XML text -> file at `path`. Returns the path written.

    `framing` (from `read_prproj_framed`) wins when given, so a file read and
    written back keeps its exact header. Otherwise `gzip=True` uses Premiere's
    header and `gzip=False` writes plain XML. The write goes to `<path>.tmp`
    first and is moved into place, so a crash never leaves half a project.
    Overwrite policy (never overwrite, versions) belongs to the caller.
    """
    target = Path(path)
    if framing is None:
        framing = Framing() if gzip else PLAIN
    data = frame(text.encode("utf-8"), framing)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)
    return target
