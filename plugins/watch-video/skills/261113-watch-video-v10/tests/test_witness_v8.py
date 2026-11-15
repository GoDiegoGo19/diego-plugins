try:  # the scan skill sets temp outside home via p0_env; the watch skill has no _env
    import _env  # noqa: F401
except ImportError:
    pass
#!/usr/bin/env python3
"""Witness v8 test: pool_frames.py + annotate_frames.py on generated clips.
Run: python3 -m unittest tests/test_witness_v8.py -v   (from the skill root)"""
import json, os, shutil, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
FONT = os.path.join(ROOT, "fonts", "Montserrat-Bold.ttf")

def _missing():
    m = []
    try:
        import cv2, numpy  # noqa
        if not (hasattr(cv2, "CascadeClassifier") and hasattr(cv2.dnn, "readNetFromCaffe")):
            m.append("OpenCV 4.x (this cv2 %s lacks Caffe/Haar; pip install 'opencv-python-headless<5')" % cv2.__version__)
    except Exception: m.append("cv2/numpy")
    for b in ("tesseract", "ffmpeg", "ffprobe"):
        if not shutil.which(b): m.append(b)
    return m

MISSING = _missing()

@unittest.skipIf(MISSING, "missing dependency: %s" % ", ".join(MISSING))
class WitnessV8(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="witness_v8_")
        clips = os.path.join(cls.tmp, "clips"); os.makedirs(clips)
        def ff(*a):
            subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error"] + list(a), check=True)
        dt = ("drawtext=fontfile=%s:text='JANE DOE REPORTER':fontsize=48:fontcolor=white:"
              "box=1:boxcolor=black@0.8:boxborderw=12:x=60:y=h-120" % FONT)
        ff("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=25:d=14", "-vf", dt, "-pix_fmt", "yuv420p",
           os.path.join(clips, "A_lower_third.mp4"))
        ff("-f", "lavfi", "-i", "color=c=0x303030:s=1280x720:r=25:d=6", "-pix_fmt", "yuv420p",
           os.path.join(clips, "B_static.mp4"))
        ff("-f", "lavfi", "-i", "mandelbrot=s=640x360:r=25", "-t", "8", "-pix_fmt", "yuv420p",
           os.path.join(clips, "C_mandelbrot.mov"))
        cls.state = os.path.join(cls.tmp, "WATCH_STATE.json")
        cls.frames = os.path.join(cls.tmp, "frames")
        cmd = [sys.executable, os.path.join(SCRIPTS, "pool_frames.py"), clips, "--state", cls.state,
               "--frames-dir", cls.frames, "--job", "T"]
        cls.pool_out = json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)
        cls.pool_out2 = json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)
        subprocess.run([sys.executable, os.path.join(SCRIPTS, "annotate_frames.py"), "--state", cls.state],
                       capture_output=True, text=True, check=True)
        with open(cls.state) as fh: cls.s = json.load(fh)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def shots(self):
        return {sh["id"]: sh for sc in self.s["scenes"] for sh in sc["shots"]}

    def test_pool_structure(self):
        self.assertEqual(self.s["mode"], "pool"); self.assertIsNone(self.s["video"])
        self.assertEqual([sc["id"] for sc in self.s["scenes"]], ["SM01", "SM02", "SM03"])
        self.assertEqual(sorted(self.shots()), ["SM01-HEAD", "SM01-MID", "SM01-TAIL", "SM02-HEAD", "SM02-MID",
                                                "SM03-HEAD", "SM03-MID"])  # tail only for >= 12 s
        self.assertEqual(self.pool_out["skipped"], [])

    def test_idempotent(self):
        self.assertEqual(self.pool_out2["new_scenes"], 0)
        self.assertEqual(self.pool_out2["scenes"], 3); self.assertEqual(self.pool_out2["shots"], 7)

    def test_v8_keys(self):
        for sid, sh in self.shots().items():
            m = sh["machine"]
            for k in ("caption", "regions", "ocr", "tags", "counts", "stats", "witness",
                      "detections", "faces", "ocr_boxes", "dhash", "size"):
                self.assertIn(k, m, "%s missing %s" % (sid, k))
            for k in ("brightness", "busyness", "warmth", "saturation"):
                self.assertIn(k, m["stats"])
            self.assertRegex(m["dhash"], r"^[0-9a-f]{16}$")
            self.assertEqual(m["size"], [640, 360])

    def test_lower_third_ocr_box(self):
        boxes = self.shots()["SM01-HEAD"]["machine"]["ocr_boxes"]
        words = {b["text"].upper(): b for b in boxes}
        self.assertTrue({"JANE", "DOE"} & set(words), "lower third not read: %r" % boxes)
        for w in ("JANE", "DOE", "REPORTER"):
            if w in words:
                self.assertGreater(words[w]["y"], 0.6)
                self.assertTrue(0 <= words[w]["x"] <= 1 and 0 < words[w]["w"] < 1)
        self.assertTrue(any("JANE" in l.upper() or "REPORTER" in l.upper() for l in self.shots()["SM01-HEAD"]["machine"]["ocr"]))

    def test_stats_and_hash_sensible(self):
        sh = self.shots()
        st = sh["SM02-HEAD"]["machine"]["stats"]
        self.assertLess(st["saturation"], 5); self.assertAlmostEqual(st["warmth"], 0, delta=3)
        self.assertEqual(sh["SM02-HEAD"]["machine"]["dhash"], sh["SM02-MID"]["machine"]["dhash"])  # static clip
        self.assertGreater(sh["SM01-HEAD"]["machine"]["stats"]["saturation"], 30)
        self.assertNotEqual(sh["SM01-HEAD"]["machine"]["dhash"], sh["SM02-HEAD"]["machine"]["dhash"])

def _ff(*a):
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error"] + list(a), check=True)

def _pool(state, inputs, *extra, **kw):
    cmd = [sys.executable, os.path.join(SCRIPTS, "pool_frames.py")] + list(inputs) + ["--state", state] + list(extra)
    return subprocess.run(cmd, capture_output=True, text=True, **kw)

def _annotate(state, *extra, **kw):
    cmd = [sys.executable, os.path.join(SCRIPTS, "annotate_frames.py"), "--state", state] + list(extra)
    return subprocess.run(cmd, capture_output=True, text=True, **kw)

def _load(p):
    with open(p) as fh: return json.load(fh)

@unittest.skipIf(MISSING, "missing dependency: %s" % ", ".join(MISSING))
class FixRound(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="witness_fix_")
        self.clips = os.path.join(self.tmp, "clips"); os.makedirs(self.clips)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def clip(self, name, color="red", d=4, *extra):
        p = os.path.join(self.clips, name)
        _ff("-f", "lavfi", "-i", "color=c=%s:s=320x180:r=25:d=%s" % (color, d), "-pix_fmt", "yuv420p", *(list(extra) + [p]))
        return p

    def rules(self, points, tail=None):
        p = os.path.join(self.tmp, "rules.json")
        kf = {"points": points, "tail_min_s": 12}
        if tail: kf["tail"] = tail
        with open(p, "w") as fh: json.dump({"keyframes": kf}, fh)
        return p

    def test_corrupt_state_aborts_unchanged(self):
        self.clip("a.mp4")
        st = os.path.join(self.tmp, "S.json")
        junk = b'{"scenes": [ truncated'
        with open(st, "wb") as fh: fh.write(junk)
        r = _pool(st, [self.clips])
        self.assertEqual(r.returncode, 2); self.assertIn("does not parse", r.stderr)
        with open(st, "rb") as fh: self.assertEqual(fh.read(), junk)

    def test_non_pool_state_refused_unless_allowed(self):
        self.clip("a.mp4")
        st = os.path.join(self.tmp, "S.json")
        with open(st, "w") as fh: json.dump({"mode": "video", "scenes": []}, fh)
        with open(st, "rb") as fh: before = fh.read()
        r = _pool(st, [self.clips])
        self.assertEqual(r.returncode, 2); self.assertEqual(open(st, "rb").read(), before)
        self.assertEqual(_pool(st, [self.clips], "--allow-mixed").returncode, 0)
        self.assertEqual(_load(st)["mode"], "video")

    def test_two_states_one_folder_do_not_share_frames(self):
        a = self.clip("a.mp4", "red"); b = self.clip("b.mp4", "blue")
        s1 = os.path.join(self.tmp, "S1.json"); s2 = os.path.join(self.tmp, "S2.json")
        self.assertEqual(_pool(s1, [a]).returncode, 0); self.assertEqual(_pool(s2, [b]).returncode, 0)
        f1 = {sh["frame"] for sc in _load(s1)["scenes"] for sh in sc["shots"]}
        f2 = {sh["frame"] for sc in _load(s2)["scenes"] for sh in sc["shots"]}
        self.assertTrue(f1 and f2 and not (f1 & f2))
        for f in f1 | f2:
            self.assertTrue(os.path.isabs(f)); self.assertTrue(os.path.getsize(f) > 0)
            self.assertEqual(os.path.dirname(f), os.path.join(self.tmp, "frames"))

    def test_keyframe_change_repulls_and_keeps_desc(self):
        self.clip("a.mp4", "red", 10)
        st = os.path.join(self.tmp, "S.json")
        r1 = self.rules([["HEAD", 0.2], ["MID", 0.6]])
        self.assertEqual(_pool(st, [self.clips], "--rules", r1).returncode, 0)
        s = _load(st); sh = s["scenes"][0]["shots"][0]
        self.assertEqual(sh["id"], "SM01-HEAD"); old_frame = sh["frame"]
        sh["desc"] = "keep me"; sh["machine"] = {"x": 1}; sh["macros"] = {"M1": {}}
        s["scenes"][0]["shots"][1]["machine"] = {"y": 2}
        with open(st, "w") as fh: json.dump(s, fh)
        out = json.loads(_pool(st, [self.clips], "--rules", r1).stdout)   # unchanged rules: no pull
        self.assertEqual(out["frames_pulled"], 0)
        r2 = self.rules([["HEAD", 0.3], ["MID", 0.6]])
        out = json.loads(_pool(st, [self.clips], "--rules", r2).stdout)
        self.assertEqual(out["frames_pulled"], 1)
        shots = {x["id"]: x for x in _load(st)["scenes"][0]["shots"]}
        h = shots["SM01-HEAD"]
        self.assertNotEqual(h["frame"], old_frame); self.assertAlmostEqual(h["t"], 3.0)
        # N1: the picture changed, so the old description leaves the live field (text kept as desc_stale)
        self.assertEqual(h["desc"], ""); self.assertEqual(h["desc_stale"], "keep me")
        self.assertFalse(h["verified"]); self.assertEqual(h["semantic_check"], "not_run")
        self.assertNotIn("machine", h); self.assertNotIn("macros", h)
        self.assertEqual(shots["SM01-MID"]["machine"], {"y": 2})
        # label set change: MID dropped, TAIL-like new label added
        r3 = self.rules([["HEAD", 0.3], ["END", 0.9]])
        _pool(st, [self.clips], "--rules", r3)
        shots = {x["id"]: x for x in _load(st)["scenes"][0]["shots"]}
        self.assertEqual(sorted(shots), ["SM01-END", "SM01-HEAD"]); self.assertEqual(shots["SM01-HEAD"]["desc_stale"], "keep me")
        # --force re-pulls everything
        out = json.loads(_pool(st, [self.clips], "--rules", r3, "--force").stdout)
        self.assertEqual(out["frames_pulled"], 2)

    def test_duplicate_labels_rejected(self):
        self.clip("a.mp4")
        st = os.path.join(self.tmp, "S.json")
        r = _pool(st, [self.clips], "--rules", self.rules([["HEAD", 0.1], ["head", 0.5]]))
        self.assertEqual(r.returncode, 2); self.assertIn("duplicate", r.stderr)
        self.assertFalse(os.path.exists(st))

    def test_one_frame_clip_uses_ss0_fallback(self):
        p = os.path.join(self.clips, "one.mp4")
        _ff("-f", "lavfi", "-i", "color=c=green:s=320x180:r=25", "-frames:v", "1", "-pix_fmt", "yuv420p", p)
        st = os.path.join(self.tmp, "S.json")
        out = json.loads(_pool(st, [p]).stdout)
        self.assertEqual(out["skipped"], [], out)
        shots = _load(st)["scenes"][0]["shots"]
        self.assertTrue(shots)
        for sh in shots: self.assertGreater(os.path.getsize(sh["frame"]), 0)

    def test_audio_only_skipped_no_scene(self):
        p = os.path.join(self.clips, "snd.mov")
        _ff("-f", "lavfi", "-i", "sine=d=2", p)
        st = os.path.join(self.tmp, "S.json")
        out = json.loads(_pool(st, [p]).stdout)
        self.assertEqual(out["scenes"], 0); self.assertEqual(len(out["skipped"]), 1)
        self.assertIn("no video", out["skipped"][0]["reason"])

    def test_zero_byte_and_missing_frame_reported_not_crash(self):
        self.clip("a.mp4")
        st = os.path.join(self.tmp, "S.json")
        _pool(st, [self.clips])
        s = _load(st); shots = s["scenes"][0]["shots"]
        open(shots[0]["frame"], "wb").close()          # 0-byte
        shots[1]["frame"] = os.path.join(self.tmp, "nope.jpg")
        with open(st, "w") as fh: json.dump(s, fh)
        r = _annotate(st)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["annotated"], 0)
        self.assertEqual({m["shot"] for m in out["missing_frames"]}, {shots[0]["id"], shots[1]["id"]})
        # no silent blank: both shots carry the PENDING placeholder and stay in missing_frames
        for sh in _load(st)["scenes"][0]["shots"]:
            self.assertEqual((sh["machine"]["value"], sh["machine"]["status"]), ("PENDING", "needs_scan"))
            self.assertTrue(sh["machine"]["why"])

    def test_relative_frames_resolve_from_state_dir(self):
        self.clip("a.mp4")
        st = os.path.join(self.tmp, "S.json")
        _pool(st, [self.clips])
        s = _load(st)
        for sh in s["scenes"][0]["shots"]: sh["frame"] = os.path.relpath(sh["frame"], self.tmp)
        with open(st, "w") as fh: json.dump(s, fh)
        other = tempfile.mkdtemp(prefix="elsewhere_"); self.addCleanup(shutil.rmtree, other, True)
        r = _annotate(st, cwd=other)
        out = json.loads(r.stdout)
        self.assertEqual(out["missing_frames"], []); self.assertEqual(out["annotated"], len(s["scenes"][0]["shots"]))

    def test_n1_unchanged_rerun_keeps_desc_and_verification(self):
        self.clip("a.mp4", "red", 10)
        st = os.path.join(self.tmp, "S.json")
        _pool(st, [self.clips])
        s = _load(st); sh = s["scenes"][0]["shots"][0]
        sh["desc"] = "keep"; sh["verified"] = True; sh["semantic_check"] = "passed"
        with open(st, "w") as fh: json.dump(s, fh)
        _pool(st, [self.clips])
        sh = _load(st)["scenes"][0]["shots"][0]
        self.assertEqual((sh["desc"], sh["verified"], sh["semantic_check"]), ("keep", True, "passed"))
        self.assertNotIn("desc_stale", sh)

    def test_n3_fallback_records_time_pulled_and_does_not_repull(self):
        p = os.path.join(self.clips, "one.mp4")
        _ff("-f", "lavfi", "-i", "color=c=green:s=320x180:r=25", "-frames:v", "1", "-pix_fmt", "yuv420p", p)
        st = os.path.join(self.tmp, "S.json")
        r1 = self.rules([["HEAD", 0.9], ["MID", 0.95]])
        _pool(st, [p], "--rules", r1)
        shots = _load(st)["scenes"][0]["shots"]
        fb = [sh for sh in shots if sh.get("seek_fallback")]
        if not fb: self.skipTest("this ffmpeg seeks past a one-frame clip without falling back")
        for sh in fb:
            self.assertEqual((sh["t"], sh["tc_in"], sh["tc_out"]), (0.0, 0.0, 0.0))
            self.assertIn("t_want", sh)
        out = json.loads(_pool(st, [p], "--rules", r1).stdout)
        self.assertEqual(out["frames_pulled"], 0)             # the asked-for time is remembered: no endless re-pull

    def test_n6_zero_byte_and_corrupt_video_are_not_audio_only(self):
        z = os.path.join(self.clips, "zero.mp4"); open(z, "wb").close()
        c = os.path.join(self.clips, "junk.mov")
        with open(c, "wb") as fh: fh.write(b"this is not a movie" * 50)
        st = os.path.join(self.tmp, "S.json")
        out = json.loads(_pool(st, [z, c]).stdout)
        self.assertEqual(out["scenes"], 0)
        self.assertEqual(len(out["skipped"]), 2)
        for sk in out["skipped"]:
            self.assertIn("unreadable or corrupt", sk["reason"]); self.assertNotIn("audio", sk["reason"])

    def test_sidecar_unreadable_image_recorded_not_crash(self):
        d = os.path.join(self.tmp, "frames"); os.makedirs(d)
        with open(os.path.join(d, "bad.jpg"), "wb") as fh: fh.write(b"not a jpeg")
        open(os.path.join(d, "zero.png"), "wb").close()
        out_json = os.path.join(self.tmp, "ann.json")
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "annotate_frames.py"), "--frames", d, "--out", out_json],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        summ = json.loads(r.stdout)
        self.assertEqual(summ["annotated"], 0)
        self.assertEqual({u["frame"] for u in summ["unreadable"]}, {"bad.jpg", "zero.png"})
        ann = _load(out_json)
        for f in ("bad.jpg", "zero.png"):
            self.assertEqual((ann[f]["value"], ann[f]["status"]), ("PENDING", "needs_scan"))

    def test_diff_sets_and_clears_witness_flag(self):
        st = os.path.join(self.tmp, "S.json")
        machine = {"caption": "x", "counts": {"persons": 5, "faces": 0}, "ocr": []}
        sh = lambda i, desc, **kw: dict({"id": i, "tc_in": 0, "tc_out": 1, "desc": desc, "machine": machine}, **kw)
        with open(st, "w") as fh:
            json.dump({"job": "J", "scenes": [{"id": "S", "shots": [
                sh("fires", "one man at a desk"),
                sh("cleared", "five people, a crowd of workers", witness_flag=True),
                sh("blank", "", witness_flag=True),
                {"id": "pend", "desc": "x", "witness_flag": True,
                 "machine": {"value": "PENDING", "status": "needs_scan", "why": "w"}}]}]}, fh)
        r = _annotate(st, "--diff")
        self.assertEqual(r.returncode, 0, r.stderr)
        by = {x["id"]: x for x in _load(st)["scenes"][0]["shots"]}
        self.assertTrue(by["fires"]["witness_flag"])
        self.assertNotIn("witness_flag", by["cleared"])       # rule no longer fires: cleared
        self.assertNotIn("witness_flag", by["blank"])         # nothing to disagree with
        self.assertTrue(by["pend"]["witness_flag"])           # no data to judge by: left alone
        self.assertEqual(json.loads(r.stdout)["flagged"], 1)

    def test_missing_frame_placeholder_keeps_real_machine(self):
        st = os.path.join(self.tmp, "S.json")
        with open(st, "w") as fh:
            json.dump({"job": "J", "scenes": [{"id": "S", "shots": [
                {"id": "a", "frame": os.path.join(self.tmp, "gone.jpg"), "machine": {"caption": "real"}}]}]}, fh)
        out = json.loads(_annotate(st, "--force").stdout)
        self.assertEqual(len(out["missing_frames"]), 1)
        self.assertEqual(_load(st)["scenes"][0]["shots"][0]["machine"], {"caption": "real"})

if __name__ == "__main__":
    unittest.main()
