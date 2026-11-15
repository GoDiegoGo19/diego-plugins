"""Tests for prwrite.framing (owner A).

There is no real .prproj in the repo, so every fixture here is bytes we make:
Premiere-style gzip (our own `frame`), stdlib-gzip files (optional header
fields), plain XML, and deliberately broken streams.
"""

import gzip as stdgzip
import io
import os
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # .../lib

from prwrite import framing as F  # noqa: E402
from prwrite import read_prproj, write_prproj  # noqa: E402

XML = '<?xml version="1.0" encoding="UTF-8" ?>\n<PremiereData Version="3">\n\t<X>é&#10;</X>\n</PremiereData>\n\n'


class FramingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_premiere_header_bytes(self):
        self.assertEqual(F.PREMIERE_HEADER, bytes.fromhex("1f8b08000000000000" + "0a"))
        fr = F.Framing()
        self.assertEqual((fr.mtime, fr.os_byte, fr.name), (0, 10, None))

    def test_gzip_round_trip_is_byte_identical(self):
        path = self.dir / "a.prproj"
        write_prproj(path, XML)
        raw = path.read_bytes()
        self.assertTrue(raw.startswith(F.PREMIERE_HEADER))
        text, fr = F.read_prproj_framed(path)
        self.assertEqual(text, XML)
        self.assertEqual(fr.header, F.PREMIERE_HEADER)
        write_prproj(self.dir / "b.prproj", text, framing=fr)
        self.assertEqual((self.dir / "b.prproj").read_bytes(), raw)
        # stdlib gzip agrees it is a valid gzip file
        self.assertEqual(stdgzip.decompress(raw).decode(), XML)
        self.assertFalse((self.dir / "a.prproj.tmp").exists())

    def test_foreign_header_with_name_and_mtime_is_preserved(self):
        buf = io.BytesIO()
        with stdgzip.GzipFile(filename="proj.xml", mode="wb", fileobj=buf, mtime=1234567) as g:
            g.write(XML.encode())
        payload, fr = F.unframe(buf.getvalue())
        self.assertEqual(payload.decode(), XML)
        self.assertEqual((fr.mtime, fr.name), (1234567, b"proj.xml"))
        again = F.frame(payload, fr)
        self.assertTrue(again.startswith(fr.header))
        self.assertEqual(F.unframe(again), (payload, fr))

    def test_plain_xml_passthrough(self):
        path = self.dir / "plain.prproj"
        path.write_bytes(XML.encode())
        text, fr = F.read_prproj_framed(path)
        self.assertEqual((text, fr.is_gzip), (XML, False))
        write_prproj(self.dir / "plain2.prproj", text, framing=fr)
        self.assertEqual((self.dir / "plain2.prproj").read_bytes(), XML.encode())
        write_prproj(self.dir / "plain3.prproj", text, gzip=False)
        self.assertEqual(read_prproj(self.dir / "plain3.prproj"), XML)

    def test_size_cap(self):
        bomb = F.frame(b"<a>" + b" " * 5_000_000 + b"</a>")
        with self.assertRaises(F.FramingError):
            F.unframe(bomb, max_size=1_000_000)
        self.assertEqual(len(F.unframe(bomb, max_size=6_000_000)[0]), 5_000_007)
        with self.assertRaises(F.FramingError):
            F.unframe(b"<a/>" * 10, max_size=8)

    def test_broken_streams(self):
        good = F.frame(XML.encode())
        bad_crc = good[:-8] + struct.pack("<II", 0, len(XML.encode()))
        cases = {
            "truncated header": good[:6],
            "truncated body": good[:-12],
            "bad crc": bad_crc,
            "two members": good + good,
            "garbage deflate": F.PREMIERE_HEADER + b"\xff\xff\xff\xff" + b"\0" * 8,
            "bad method": good[:2] + b"\x07" + good[3:],
        }
        for label, data in cases.items():
            with self.subTest(label), self.assertRaises(F.FramingError):
                F.unframe(data)

    def test_level6_raw_deflate(self):
        body = F.frame(XML.encode())[10:-8]
        expect = zlib.compressobj(6, zlib.DEFLATED, -15)
        self.assertEqual(body, expect.compress(XML.encode()) + expect.flush())

    def test_framing_error_is_value_error(self):
        self.assertTrue(issubclass(F.FramingError, ValueError))


if __name__ == "__main__":
    unittest.main()
