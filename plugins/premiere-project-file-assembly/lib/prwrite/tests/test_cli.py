"""Owner D: cli (peek, validate, inspect, compare). Files are made in the test; no real .prproj exists."""
import contextlib
import gzip
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from _xfixture import LIB, small_project

from prwrite import cli
from prwrite.export_xmeml import project_to_xmeml

PRPROJ_LIKE = b"""<?xml version="1.0" encoding="UTF-8" ?>
<PremiereData Version="3">
\t<Project ObjectRef="1"/>
\t<Project ObjectID="1" ClassID="62ad66dd" Version="1"/>
\t<RootProjectItem ObjectUID="a" ClassID="1c307a89" Version="1"/>
\t<BinProjectItem ObjectUID="b" ClassID="dbfd6e4b" Version="1"/>
\t<BinProjectItem ObjectUID="c" ClassID="dbfd6e4b" Version="1"/>
</PremiereData>
"""


def run(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(list(argv))
    return code, buf.getvalue()


class CliTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.gz = os.path.join(self.dir, "t.prproj")
        with gzip.open(self.gz, "wb") as fh:
            fh.write(PRPROJ_LIKE)
        self.xml = os.path.join(self.dir, "a.xml")
        with open(self.xml, "w", encoding="utf-8") as fh:
            fh.write(project_to_xmeml(small_project()))

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_peek_gzip(self):
        r = cli.peek(self.gz)
        self.assertEqual(r["root"], "PremiereData")
        self.assertEqual(r["attrs"], {"Version": "3"})
        self.assertEqual(r["by_tag"], {"BinProjectItem": 2, "Project": 2, "RootProjectItem": 1})
        code, out = run("peek", self.gz)
        self.assertEqual(code, 0)
        self.assertIn("2  BinProjectItem", out)

    def test_peek_plain_xmeml(self):
        self.assertEqual(cli.peek(self.xml)["by_tag"], {"project": 1})

    def test_validate_cli(self):
        code, out = run("validate", self.xml)
        self.assertEqual(code, 0, out)
        bad = os.path.join(self.dir, "bad.xml")
        with open(bad, "w") as fh:
            fh.write('<xmeml version="4"><project><children/></project></xmeml>')
        self.assertEqual(run("validate", bad)[0], 3)                 # R9 FAIL
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run("validate", os.path.join(self.dir, "missing.xml"))[0], 4)   # R9 REFUSED

    def test_inspect_xml(self):
        code, out = run("inspect", self.xml)
        self.assertEqual(code, 0)
        self.assertIn("bins 3  clips 2  sequences 1  files 2", out)
        self.assertIn("seq   ASSEMBLY  (6 clip items)", out)

    def test_compare(self):
        p = small_project()
        p.sequences[0].name = "ASSEMBLY_v2"
        b = os.path.join(self.dir, "b.xml")
        with open(b, "w") as fh:
            fh.write(project_to_xmeml(p))
        r = cli.compare(self.xml, b)
        self.assertEqual(r["kinds"]["sequences"]["added"], ["ASSEMBLY_v2"])
        self.assertEqual(r["kinds"]["sequences"]["removed"], ["ASSEMBLY"])
        self.assertEqual(r["kinds"]["clips"]["added"], [])

    def test_python_dash_m(self):
        out = subprocess.run([sys.executable, "-m", "prwrite", "peek", self.gz], cwd=LIB,   # -I would drop cwd from sys.path
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("PremiereData", out.stdout)


if __name__ == "__main__":
    unittest.main()
