"""Tests for scripts/prproj_extract.py on a generated fake project (tests/fixtures/make_fake_prproj.py).

  cd <skill> && python3 -I -B -m unittest discover -s tests -p 'test_prproj_extract.py' -v

Everything is written to a temp folder; nothing lands in the plugin (size gate).
"""
import contextlib, hashlib, io, os, re, sys, tempfile, unittest
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
sys.path.insert(0, os.path.join(HERE, "fixtures"))
import prproj_extract as px
import make_fake_prproj as fake


def rb(path):
    with open(path, "rb") as f:
        return f.read()


def run_check(path):
    buf = io.StringIO()
    ok = px.check(path, log=lambda s: buf.write(s + "\n"))
    return ok, buf.getvalue()


class ExtractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.src = os.path.join(cls.tmp.name, "fake.prproj")
        cls.info = fake.write(cls.src)
        cls.src_sha = hashlib.sha256(rb(cls.src)).hexdigest()
        quiet = lambda s: None
        cls.out = px.extract(cls.src, "SEQ A", os.path.join(cls.tmp.name, "out"), stamp="261113-0000", log=quiet)
        cls.skip = px.extract(cls.src, "SEQ A", os.path.join(cls.tmp.name, "skip"), stamp="261113-0000",
                              skip_restore=True, log=quiet)
        cls.p = px.Project(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_source_checks_pass_and_is_untouched(self):
        ok, txt = run_check(self.src)
        self.assertTrue(ok, txt)
        self.assertEqual(hashlib.sha256(rb(self.src)).hexdigest(), self.src_sha)

    def test_output_name_and_header(self):
        self.assertEqual(os.path.basename(self.out), "fake__seq-a_261113-0000.prproj")
        self.assertEqual(rb(self.out)[:10], bytes.fromhex("1f8b080000000000000a"))
        self.assertTrue(self.p.buf.endswith(b"</PremiereData>\n\n"))

    def test_extract_then_check_passes(self):
        ok, txt = run_check(self.out)
        self.assertTrue(ok, txt)
        self.assertTrue(txt.strip().splitlines()[-1].startswith("PASS objects="), txt)

    def test_three_sequences_none_of_b(self):
        names = sorted(o.name for o in self.p.objs if o.tag == b"Sequence")
        self.assertEqual(names, ["NEST 1", "NEST 2", "SEQ A"])
        self.assertNotIn(self.info["seqs"]["SEQ B"]["uid"].encode(), self.p.buf)

    def test_bins_no_empty_contiguous(self):
        bins = {o.key: i for i, o in enumerate(self.p.objs) if o.tag in px.BIN_TAGS}
        self.assertNotIn(b"@" + self.info["bins"]["b_only"].encode(), bins)
        for k, i in bins.items():
            idx = [int(x) for x in re.findall(rb'<Item Index="(\d+)"', self.p.blob(i))]
            self.assertTrue(idx, "empty bin %r" % k)
            self.assertEqual(idx, list(range(len(idx))), k)
        root = self.p.blob(bins[b"@" + self.info["bins"]["root"].encode()])
        self.assertEqual(len(re.findall(rb"<Item ", root)), 2)
        seqbin = self.p.blob(bins[b"@" + self.info["bins"]["seq"].encode()])
        self.assertEqual(len(re.findall(rb"<Item ", seqbin)), 3)   # A, N1, N2 (B dropped, renumbered)

    def test_settings_and_project_kept(self):
        for n in range(1, 40):
            if n != 2:
                self.assertIn(b"#%d" % n, self.p.by_key, n)

    def test_open_sequence_list_trimmed(self):
        got = px.OPENSEQ_RE.findall(self.p.buf)
        self.assertEqual([(g[1], g[2]) for g in got], [(b"1", self.info["seqs"]["SEQ A"]["uid"].encode())])

    def test_payloads_restored_into_first_kept_element(self):
        for h, data in ((self.info["H1"], self.info["DATA1"]), (self.info["H2"], self.info["DATA2"])):
            lines = [l for l in self.p.buf.split(b"\n") if b'BinaryHash="%s"' % h.encode() in l]
            self.assertGreaterEqual(len(lines), 2)
            self.assertFalse(lines[0].endswith(b"/>"), lines[0])
            self.assertTrue(lines[0].endswith(data.encode()), lines[0])
            self.assertTrue(all(l.endswith(b"/>") for l in lines[1:]))
        # H2 first appears under a different tag name than its source copy
        self.assertIn(b'<SecondaryFilterData Encoding="base64" BinaryHash="%s">' % self.info["H2"].encode(), self.p.buf)

    def test_skip_restore_fails_naming_orphans(self):
        ok, txt = run_check(self.skip)
        self.assertFalse(ok)
        last = txt.strip().splitlines()[-1]
        self.assertTrue(last.startswith("FAIL 1 "), txt)
        line1 = [l for l in txt.splitlines() if l.startswith("1 ")][0]
        self.assertIn("FAIL", line1)
        self.assertIn("ORPHANED payloads", line1)
        self.assertIn(self.info["H1"], line1)
        self.assertIn(self.info["H2"], line1)

    def test_env_switch_skips_restore(self):
        os.environ["PRPROJ_EXTRACT_SKIP_RESTORE"] = "1"
        try:
            out = px.extract(self.src, "SEQ A", os.path.join(self.tmp.name, "env"), stamp="261113-0001",
                             log=lambda s: None)
        finally:
            del os.environ["PRPROJ_EXTRACT_SKIP_RESTORE"]
        self.assertFalse(run_check(out)[0])

    def test_missing_settings_gives_nine_dangling(self):
        miss = os.path.join(self.tmp.name, "miss.prproj")
        fake.write(miss, drop_settings=range(3, 12))
        ok, txt = run_check(miss)
        self.assertFalse(ok)
        self.assertIn("dangling top-level refs: 9 ", txt)

    def test_escaped_fake_refs_not_counted(self):
        src = px.Project(self.src)
        keys = {k for o in src.objs for k, _ in o.refs}
        self.assertNotIn(b"#999", keys)
        self.assertNotIn(b"#998", keys)
        self.assertIn(b'ObjectRef="999"', src.buf)       # the text is there; it is just not a ref
        self.assertIn(b'ObjectRef="999"', self.p.buf)    # kept byte-for-byte in SEQ A

    def test_local_ids_are_scoped(self):
        src = px.Project(self.src)
        proj = src.objs[src.by_key[b"#1"]]
        targets = [k for k, _ in proj.refs]
        self.assertEqual(len(targets), 38)               # RootProjectItem + 37 settings, no local 1..20
        self.assertEqual(sorted(targets[1:], key=lambda k: int(k[1:])), [b"#%d" % n for n in range(3, 40)])

    def test_refuses_overwrite(self):
        with self.assertRaises(FileExistsError):
            px.extract(self.src, "SEQ A", os.path.join(self.tmp.name, "out"), stamp="261113-0000", log=lambda s: None)
        self.assertEqual(len(os.listdir(os.path.join(self.tmp.name, "out"))), 1)

    def test_unknown_sequence_refused(self):
        with self.assertRaises(px.PrprojError):
            px.extract(self.src, "NOPE", os.path.join(self.tmp.name, "x"), stamp="261113-0002", log=lambda s: None)


if __name__ == "__main__":
    unittest.main()
