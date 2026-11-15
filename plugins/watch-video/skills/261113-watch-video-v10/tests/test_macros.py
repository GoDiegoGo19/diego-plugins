try:  # the scan skill sets temp outside home via p0_env; the watch skill has no _env
    import _env  # noqa: F401
except ImportError:
    pass
import json, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MACROS = os.path.join(HERE, "..", "scripts", "macros.py")
RULES = os.path.join(HERE, "..", "rules.json")

try:
    import cv2, numpy as np
except ImportError:
    cv2 = np = None
try:
    import webrtcvad  # noqa
except ImportError:
    webrtcvad = None
HAVE_FFMPEG = shutil.which("ffmpeg") is not None
ESPEAK = shutil.which("espeak-ng") or shutil.which("espeak")


def shot(sid, machine, **kw):
    d = {"id": sid, "tc_in": 0, "tc_out": 2, "machine": machine}
    d.update(kw)
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def run_state(self, scenes, extra=None, video=None, state_extra=None, rules=RULES):
        st = {"job": "t", "scenes": scenes}
        if video:
            st["video"] = {"path": video, "duration": 5}
        st.update(state_extra or {})
        sp = os.path.join(self.d, "WATCH_STATE.json")
        with open(sp, "w") as fh:
            json.dump(st, fh)
        cmd = [sys.executable, MACROS, "--state", sp, "--rules", rules] + (extra or [])
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.summary = json.loads(r.stdout.strip().splitlines()[-1])
        with open(sp) as fh:
            out = json.load(fh)
        return [sh for sc in out["scenes"] for sh in sc["shots"]]

    def one(self, machine, **kw):
        return self.run_state([{"id": "S1", "shots": [shot("1-1", machine, **kw)]}])[0]["macros"]


class TestM1(Base):
    def test_sizes(self):
        f = lambda h: {"faces": [{"x": .4, "y": .2, "w": .1, "h": h}], "detections": []}
        self.assertEqual(self.one(f(0.35))["M1"]["value"], "CU")
        self.assertEqual(self.one(f(0.15))["M1"]["value"], "MCU")
        ms = {"faces": [], "detections": [{"label": "person", "score": .9, "x": 0, "y": 0, "w": .3, "h": 0.6}]}
        self.assertEqual(self.one(ms)["M1"]["value"], "MS")
        ws = {"faces": [], "detections": [{"label": "person", "score": .9, "x": 0, "y": 0, "w": .1, "h": 0.2}]}
        self.assertEqual(self.one(ws)["M1"]["value"], "WS")

    def test_unresolved(self):
        m = self.one({"faces": [], "detections": [{"label": "car", "score": .9, "x": 0, "y": 0, "w": .5, "h": .5}]})["M1"]
        self.assertEqual(m["status"], "unresolved")
        self.assertIn("INSERT", m["why"])
        self.assertEqual(self.one({"caption": "v7 only"})["M1"]["status"], "unresolved")


class TestM5(Base):
    def test_light(self):
        self.assertEqual(self.one({"stats": {"brightness": 30}})["M5"]["value"], "night")
        self.assertEqual(self.one({"stats": {"brightness": 150, "warmth": 12}})["M5"]["value"], "day")

    def test_unresolved(self):
        self.assertEqual(self.one({"stats": {"brightness": 85}})["M5"]["status"], "unresolved")
        self.assertEqual(self.one({"stats": {}})["M5"]["status"], "unresolved")


class TestM6(Base):
    def box(self, text, y, h=0.05):
        return {"text": text, "conf": 90, "x": .1, "y": y, "w": .2, "h": h}

    def test_values(self):
        self.assertEqual(self.one({"ocr_boxes": [self.box("SCENE", .1), self.box("12", .1)]})["M6"]["value"], "slate")
        self.assertEqual(self.one({"ocr_boxes": [self.box("Jane", .80)]})["M6"]["value"], "lower_third")
        self.assertEqual(self.one({"ocr_boxes": [self.box("Welcome to the show", .3)]})["M6"]["value"], "card")
        self.assertEqual(self.one({"ocr_boxes": []})["M6"]["value"], "none")

    def test_unresolved(self):
        self.assertEqual(self.one({"ocr": ["some line"]})["M6"]["status"], "unresolved")
        # v11: a lone 2-letter fragment is OCR noise ("te Py | Be in," on a sofa, 261106 run), so no text
        self.assertEqual(self.one({"ocr_boxes": [self.box("ab", .3)]})["M6"]["value"], "none")


class TestM8(Base):
    def test_dupes(self):
        sc = [{"id": "A", "shots": [shot("1-1", {"dhash": "ffff0000ffff0000"})]},
              {"id": "B", "shots": [shot("2-1", {"dhash": "ffff0000ffff0001"}),
                                    shot("2-2", {"dhash": "ffff0000ffff0000"})]},
              {"id": "C", "shots": [shot("3-1", {"dhash": "ffff0000ffff00ff"}),
                                    shot("3-2", {"dhash": "0000ffff0000ffff"})]}]
        r = dict((s["id"], s["macros"]["M8"]) for s in self.run_state(sc))
        self.assertEqual(r["1-1"]["value"], "none")
        self.assertEqual(r["2-1"]["value"], "dup"); self.assertEqual(r["2-1"]["of"], "1-1")
        self.assertEqual(r["2-2"]["value"], "dup")
        self.assertEqual(r["3-1"]["value"], "near"); self.assertEqual(r["3-1"]["of"], "2-1")  # nearest earlier shot from another clip
        self.assertEqual(r["3-2"]["value"], "none")
        self.assertTrue(all(v["status"] == "resolved" for v in r.values()))

    def test_same_clip_not_compared(self):
        sc = [{"id": "A", "shots": [shot("1-1", {"dhash": "ffff0000ffff0000"}),
                                    shot("1-2", {"dhash": "ffff0000ffff0000"})]}]
        self.assertEqual(self.run_state(sc)[1]["macros"]["M8"]["value"], "none")

    def test_unresolved(self):
        self.assertEqual(self.one({})["M8"]["status"], "unresolved")


@unittest.skipIf(cv2 is None, "cv2 missing")
class TestM2(Base):
    def img(self, name, val, noise=False):
        a = np.full((90, 160, 3), val, np.uint8)
        if noise:
            a = np.random.RandomState(1).randint(0, 255, (90, 160, 3)).astype(np.uint8)
        p = os.path.join(self.d, name)
        cv2.imwrite(p, a)
        return p

    def test_pool_pair(self):
        a, b = self.img("a.png", 100), self.img("b.png", 102)
        c, e = self.img("c.png", 100), self.img("e.png", 100, noise=True)
        sc = [{"id": "SM01", "shots": [shot("1-1", {}, file="x.mp4", frame=a), shot("1-2", {}, file="x.mp4", frame=b)]},
              {"id": "SM02", "shots": [shot("2-1", {}, file="y.mp4", frame=c), shot("2-2", {}, file="y.mp4", frame=e)]}]
        r = dict((s["id"], s["macros"]["M2"]["value"]) for s in self.run_state(sc))
        self.assertEqual(r, {"1-1": "static", "1-2": "static", "2-1": "moving", "2-2": "moving"})

    def test_unresolved_single_frame_no_video(self):
        m = self.one({}, frame=self.img("a.png", 100))["M2"]
        self.assertEqual(m["status"], "unresolved")

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg missing")
    def test_pull_second_frame_from_video(self):
        v = os.path.join(self.d, "static.mp4")
        subprocess.check_call(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=gray:s=160x90:d=3:r=10",
                               "-y", v])
        m = self.run_state([{"id": "S", "shots": [shot("1-1", {}, tc_in=0.5, tc_out=2.5)]}], extra=["--video", v])[0]["macros"]["M2"]
        self.assertEqual(m["value"], "static")


@unittest.skipUnless(HAVE_FFMPEG and webrtcvad is not None, "ffmpeg or webrtcvad missing")
class TestM3(Base):
    # NOTE: a pure 440 Hz sine reads 100% 'speech' to webrtcvad, so white noise is the negative case.
    def make(self, name, lavfi):
        p = os.path.join(self.d, name)
        subprocess.check_call(["ffmpeg", "-nostdin", "-v", "error"] + lavfi + ["-y", p])
        return p

    def test_noise_is_low_speech(self):
        v = self.make("tone.wav", ["-f", "lavfi", "-i", "anoisesrc=d=3:c=white:a=0.1"])
        m = self.run_state([{"id": "S", "shots": [shot("1-1", {}, path=v)]}])[0]["macros"]["M3"]
        self.assertEqual(m["status"], "resolved")
        self.assertEqual(m["value"], "no_speech")
        self.assertLess(m["pct"], 40)

    def test_no_audio_is_not_run(self):
        v = self.make("silent.mp4", ["-f", "lavfi", "-i", "color=black:s=64x64:d=1:r=5"])
        m = self.run_state([{"id": "S", "shots": [shot("1-1", {}, path=v)]}])[0]["macros"]["M3"]
        self.assertEqual(m["status"], "not_run")
        self.assertIn("audio", m["why"])

    def test_missing_source_is_not_run(self):
        self.assertEqual(self.one({})["M3"]["status"], "not_run")

    @unittest.skipUnless(ESPEAK, "espeak not installed")
    def test_real_speech(self):
        w = os.path.join(self.d, "s.wav")
        subprocess.check_call([ESPEAK, "-w", w, "Hello, this is a test of the speech detector. " * 4])
        m = self.run_state([{"id": "S", "shots": [shot("1-1", {}, path=w)]}])[0]["macros"]["M3"]
        self.assertEqual(m["value"], "speech")


class TestM4(Base):
    def clip(self, sid, tc_out, faces, frames=None, **kw):
        mc = {"faces": [{"x": .4, "y": .2, "w": .1, "h": .2}] * faces}
        return {"id": sid, "tc_in": 0, "tc_out": tc_out, "shots": [shot(sid + "-1", mc, file=sid + ".mp4", **kw)]}

    def test_unresolved_without_signals(self):
        # no frames, no audio -> M2 and M3 cannot be run, so role cannot be settled
        r = self.run_state([self.clip("C1", 30, 1)])[0]["macros"]
        self.assertEqual(r["M4"]["status"], "unresolved")

    @unittest.skipUnless(HAVE_FFMPEG and webrtcvad is not None, "ffmpeg or webrtcvad missing")
    def test_broll(self):
        v = os.path.join(self.d, "t.wav")
        subprocess.check_call(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=3:c=white:a=0.1", "-y", v])
        sc = self.clip("C1", 6, 0, path=v)
        r = self.run_state([sc])[0]["macros"]
        self.assertEqual(r["M3"]["value"], "no_speech")
        self.assertEqual(r["M4"]["value"], "broll")

    def test_not_broll_when_long(self):
        r = self.run_state([self.clip("C1", 60, 0)])[0]["macros"]
        self.assertEqual(r["M4"]["status"], "unresolved")


class TestM4UsesKeptValues(Base):
    """Gemini review 261107: in a cut, M4 was judged on a fresh M2/M3 recompute that was then
    discarded in favour of the kept (hand-filled) values. M4 must use what the shot carries."""
    def test_cut_m4_uses_filled_m2_m3(self):
        filled = {"M2": {"value": "static", "status": "resolved", "why": "filled", "filled_by": "claude"},
                  "M3": {"value": "no_speech", "status": "resolved", "why": "filled", "filled_by": "claude"}}
        sh = shot("1-1", {"faces": []}, tc_in=0, tc_out=6, macros=filled)
        r = self.run_state([{"id": "S1", "shots": [sh]}])[0]["macros"]
        self.assertEqual(r["M2"]["value"], "static")          # the fill stays
        self.assertEqual(r["M4"]["value"], "broll", r["M4"])  # and the role is judged on it


class TestM7(Base):
    def test_not_run_without_clip(self):
        try:
            import open_clip  # noqa
            self.skipTest("open_clip installed")
        except ImportError:
            pass
        try:
            import clip  # noqa
            self.skipTest("clip installed")
        except ImportError:
            pass
        m = self.one({})["M7"]
        self.assertEqual(m["status"], "not_run")
        self.assertIn("CLIP", m["why"])


class TestDriver(Base):
    def test_skip_existing_and_force(self):
        sc = [{"id": "S", "shots": [shot("1-1", {"stats": {"brightness": 10}}, macros={"M5": {"value": "x", "status": "resolved", "why": ""}})]}]
        out = self.run_state(sc)
        self.assertEqual(self.summary["shots_processed"], 1)          # the record is incomplete: missing keys are written
        self.assertEqual(out[0]["macros"]["M5"]["value"], "x")        # ...but the existing macro is never clobbered
        self.assertEqual(sorted(out[0]["macros"]), ["M%d" % i for i in range(1, 9)])
        out = self.run_state(sc, extra=["--force"])
        self.assertEqual(self.summary["shots_processed"], 1)
        self.assertEqual(out[0]["macros"]["M5"]["value"], "night")

    def test_shot_without_machine_gets_pending_placeholders(self):
        out = self.run_state([{"id": "S", "shots": [{"id": "1-1"}]}])
        self.assertEqual(sorted(out[0]["macros"]), ["M%d" % i for i in range(1, 9)])
        for m in out[0]["macros"].values():
            self.assertEqual((m["value"], m["status"], m["why"], m["scan_by"]),
                             ("PENDING", "needs_scan", "no witness data", "any"))
        self.assertEqual(self.summary["pending_shots"], 1)


class TestFixRound(Base):
    def box(self, text, y, h=0.05, x=.1):
        return {"text": text, "conf": 90, "x": x, "y": y, "w": .2, "h": h}

    def m6(self, boxes):
        return self.one({"ocr_boxes": boxes})["M6"]

    def test_spread_stat_card_is_not_lower_third(self):
        boxes = [self.box("91%", .25), self.box("of nurses", .33), self.box("report burnout", .42),
                 self.box("in the first", .55), self.box("year of work", .66), self.box("Source: survey", .72)]
        m = self.m6(boxes)
        self.assertNotEqual(m["value"], "lower_third")
        self.assertTrue(m["value"] == "card" or m["status"] == "unresolved", m)

    def test_lower_third_needs_most_chars_low_and_nothing_high(self):
        self.assertEqual(self.m6([self.box("JANE DOE", .80), self.box("Fire Captain", .86)])["value"], "lower_third")
        m = self.m6([self.box("WATERMARK", .1), self.box("JANE DOE", .80), self.box("Fire Captain", .86)])
        self.assertNotEqual(m["value"], "lower_third")

    def test_burned_in_timecode(self):
        m = self.m6([self.box("01:02:03:04", .9)])
        self.assertEqual((m["value"], m["status"]), ("timecode", "resolved"))
        self.assertEqual(self.m6([self.box("01:02:03;04", .1)])["value"], "timecode")
        m = self.m6([self.box("01:02:03:04", .9), self.box("Jane", .8)])
        self.assertEqual(m["value"], "lower_third")   # timecode excluded from the other rules
        self.assertEqual(m["also"], "timecode")

    def test_rock_and_roll_lower_third_not_slate(self):
        m = self.m6([self.box("ROCK", .8), self.box("AND", .8), self.box("ROLL", .8)])
        self.assertEqual(m["value"], "lower_third")

    def test_slate_words(self):
        self.assertEqual(self.m6([self.box("TAKE", .1)])["value"], "slate")
        self.assertEqual(self.m6([self.box("roll", .1), self.box("camera", .2)])["value"], "slate")
        self.assertNotEqual(self.m6([self.box("SC", .1), self.box("TK", .1)])["value"], "slate")

    def test_two_flat_frames_not_dup(self):
        flat = {"stats": {"busyness": 2, "brightness": 90}}
        sc = [{"id": "A", "shots": [shot("1-1", dict(flat, dhash="0000000000000000"))]},
              {"id": "B", "shots": [shot("2-1", dict(flat, dhash="0000000000000000"))]},
              {"id": "C", "shots": [shot("3-1", {"dhash": "ffffffffffffffff"})]},   # hash alone: all ones
              {"id": "D", "shots": [shot("4-1", {"dhash": "ffff0000ffff0000"})]}]
        r = dict((x["id"], x["macros"]["M8"]) for x in self.run_state(sc))
        for k in ("1-1", "2-1", "3-1"):
            self.assertEqual(r[k]["status"], "unresolved", r[k])
            self.assertIn("flat", r[k]["why"])
        self.assertNotEqual(r["2-1"]["value"], "dup")
        self.assertEqual(r["4-1"]["value"], "none")

    def test_black_frame_m5_unresolved(self):
        m = self.one({"stats": {"brightness": 2, "busyness": 1}})["M5"]
        self.assertEqual(m["status"], "unresolved")
        self.assertIn("flat", m["why"])

    def test_rules_missing_sections_still_runs(self):
        rp = os.path.join(self.d, "bare.json")
        with open(rp, "w") as fh:
            json.dump({"version": 1}, fh)
        out = self.run_state([{"id": "S", "shots": [shot("1-1", {"stats": {"brightness": 30}, "ocr_boxes": [],
                                                              "dhash": "ffff0000ffff0000",
                                                              "faces": [{"x": .4, "y": .2, "w": .1, "h": .35}]})]}],
                             rules=rp)
        m = out[0]["macros"]
        self.assertEqual(m["M1"]["value"], "CU")
        self.assertEqual(m["M5"]["value"], "night")
        self.assertEqual(m["M6"]["value"], "none")
        rp2 = os.path.join(self.d, "partial.json")
        with open(rp2, "w") as fh:
            json.dump({"version": 1, "M6_text": {"card_min_chars": 3}}, fh)
        out = self.run_state([{"id": "S", "shots": [shot("1-1", {"ocr_boxes": [self.box("abcd", .3)]})]}], rules=rp2)
        self.assertEqual(out[0]["macros"]["M6"]["value"], "card")

    def test_relative_paths_resolve_against_state_dir(self):
        if cv2 is None:
            self.skipTest("cv2 missing")
        for n, v in (("a.png", 100), ("b.png", 100)):
            cv2.imwrite(os.path.join(self.d, n), np.full((90, 160, 3), v, np.uint8))
        cwd = os.getcwd()
        os.chdir(tempfile.gettempdir())
        try:
            out = self.run_state([{"id": "S", "shots": [shot("1-1", {}, file="x.mp4", frame="a.png"),
                                                        shot("1-2", {}, file="x.mp4", frame="b.png")]}])
        finally:
            os.chdir(cwd)
        self.assertEqual(out[0]["macros"]["M2"]["value"], "static")


class TestInProcess(Base):
    """Drive macros.main / m2_for in-process with the media calls stubbed."""

    def setUp(self):
        Base.setUp(self)
        sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
        import macros
        self.mod = macros

    def test_m2_sample_window_inside_shot(self):
        pulled = []
        orig_pull, orig_diff = self.mod.pull_frame, self.mod.mean_diff
        self.mod.pull_frame = lambda src, t, out: pulled.append(t) or True
        self.mod.mean_diff = lambda a, b: 0.0
        vid = os.path.join(self.d, "v.mp4")
        open(vid, "wb").close()
        r = {"static_max_diff": 6.0}
        try:
            for tin, tout in ((10.0, 10.6), (10.0, 12.0), (3.0, 3.7)):
                del pulled[:]
                m = self.mod.m2_for([({}, {"id": "x", "tc_in": tin, "tc_out": tout})], lambda sc, sh: None, vid, r, self.d, self.d)
                self.assertEqual(m["status"], "resolved")
                self.assertEqual(len(pulled), 2)
                for t in pulled:
                    self.assertGreaterEqual(t, tin + 0.05 - 1e-9)
                    self.assertLessEqual(t, tout - 0.05 + 1e-9)
                self.assertAlmostEqual(sum(pulled) / 2, (tin + tout) / 2, places=6)
            del pulled[:]
            m = self.mod.m2_for([({}, {"id": "x", "tc_in": 10.0, "tc_out": 10.5})], lambda sc, sh: None, vid, r, self.d, self.d)
            self.assertEqual(m["status"], "unresolved")
            self.assertEqual(pulled, [])
        finally:
            self.mod.pull_frame, self.mod.mean_diff = orig_pull, orig_diff

    def test_single_video_m4_is_per_shot(self):
        face = {"faces": [{"x": .4, "y": .2, "w": .1, "h": .2}]}
        sc = [{"id": "S1", "shots": [shot("1-1", face, tc_in=0, tc_out=30),
                                     shot("1-2", {"faces": []}, tc_in=30, tc_out=36),
                                     shot("1-3", {"faces": []}, tc_in=36, tc_out=40)]}]
        sp = os.path.join(self.d, "WATCH_STATE.json")
        vid = os.path.join(self.d, "v.mp4")
        open(vid, "wb").close()
        with open(sp, "w") as fh:
            json.dump({"job": "t", "video": {"path": "v.mp4", "duration": 40}, "scenes": sc}, fh)
        mod = self.mod
        saved = (mod.m3_for, mod.m2_for)
        # whole video is speech-heavy; shot 1-3 is the only silent slice; every shot reads static
        mod.m3_for = lambda src, r, t0=None, t1=None: mod.mk("no_speech" if t0 == 36 else "speech", "resolved", "stub")
        mod.m2_for = lambda *a, **k: mod.mk("static", "resolved", "stub")
        try:
            self.assertEqual(mod.main(["--state", sp, "--rules", RULES]), 0)
        finally:
            mod.m3_for, mod.m2_for = saved
        with open(sp) as fh:
            shots = json.load(fh)["scenes"][0]["shots"]
        roles = dict((s["id"], s["macros"]["M4"]) for s in shots)
        self.assertEqual(roles["1-1"]["value"], "sot")
        self.assertEqual(roles["1-2"]["status"], "unresolved")      # insert, no faces, speech-heavy: NOT sot
        self.assertEqual(roles["1-3"]["value"], "broll")            # silent, no faces, short
        self.assertNotEqual(roles["1-2"]["value"], "sot")

    def test_m4_broll_needs_moving_or_no_faces(self):
        r = {"sot_min_s": 20, "broll_max_s": 12}
        ns = self.mod.mk("no_speech", "resolved", "")
        st = self.mod.mk("static", "resolved", "")
        self.assertEqual(self.mod.m4_for(1, st, ns, r, 5)["status"], "unresolved")   # a face, static, silent
        self.assertEqual(self.mod.m4_for(0, st, ns, r, 5)["value"], "broll")
        self.assertEqual(self.mod.m4_for(1, self.mod.mk("moving", "resolved", ""), ns, r, 5)["value"], "broll")


class TestPendingAndRules(Base):
    """Diego's PENDING rule + N3/N4 + the '.' timecode separator."""
    def box(self, text, y, h=0.05, x=.1):
        return {"text": text, "conf": 90, "x": x, "y": y, "w": .2, "h": h}

    def rules_with(self, **sections):
        with open(RULES) as fh:
            r = json.load(fh)
        for k, v in sections.items():
            r[k].update(v)
        rp = os.path.join(self.d, "r.json")
        with open(rp, "w") as fh:
            json.dump(r, fh)
        return rp

    def test_placeholder_replaced_when_machine_arrives(self):
        sc = [{"id": "S", "shots": [{"id": "1-1", "tc_in": 0, "tc_out": 2,
                                     "machine": {"value": "PENDING", "status": "needs_scan", "why": "frame missing"}}]}]
        out = self.run_state(sc)
        self.assertEqual({m["status"] for m in out[0]["macros"].values()}, {"needs_scan"})
        self.assertEqual(self.summary["counts"]["M1"]["needs_scan"], 1)
        # rerun with the placeholder still there: nothing to do
        self.run_state(sc)
        # a real machine arrives: placeholders are recomputed, not kept
        sc[0]["shots"][0]["machine"] = {"stats": {"brightness": 10, "busyness": 80}}
        sc[0]["shots"][0]["macros"] = out[0]["macros"]
        out = self.run_state(sc)
        self.assertEqual(out[0]["macros"]["M5"]["value"], "night")
        self.assertNotIn("needs_scan", {m["status"] for m in out[0]["macros"].values()})

    def test_fill_judgment_survives_macros_rerun_unless_force(self):
        filled = {"value": "ECU", "status": "resolved", "why": "looked", "filled_by": "claude"}
        sc = [{"id": "S", "shots": [shot("1-1", {"stats": {"brightness": 10, "busyness": 80}}, macros={"M5": filled})]}]
        out = self.run_state(sc)
        self.assertEqual(out[0]["macros"]["M5"], filled)
        out = self.run_state(sc, extra=["--force"])
        self.assertEqual(out[0]["macros"]["M5"]["value"], "night")

    def test_m2_unresolved_when_a_frame_used_seek_fell_back(self):
        fa, fb = os.path.join(self.d, "a.jpg"), os.path.join(self.d, "b.jpg")
        for f in (fa, fb):
            with open(f, "wb") as fh:
                fh.write(b"\xff\xd8\xff")
        sc = [{"id": "S", "file": "a.mov", "shots": [shot("1-1", {}, frame=fa, file="a.mov"),
                                                      shot("1-2", {}, frame=fb, file="a.mov", seek_fallback=True)]}]
        out = self.run_state(sc)
        for sh in out:
            self.assertEqual(sh["macros"]["M2"]["status"], "unresolved")
            self.assertIn("fallback", sh["macros"]["M2"]["why"])

    def test_m6_thresholds_come_from_rules(self):
        boxes = [self.box("JANE DOE", .30), self.box("Fire Captain of the Year", .86)]
        run = lambda rules: self.run_state([{"id": "S", "shots": [shot("1-1", {"ocr_boxes": boxes})]}], rules=rules)[0]["macros"]["M6"]
        self.assertEqual(run(RULES)["status"], "unresolved")                       # a box above y 0.45
        self.assertEqual(run(self.rules_with(M6_text={"above_y": 0.2}))["value"], "lower_third")
        self.assertEqual(run(self.rules_with(M6_text={"lower_frac": 0.99, "above_y": 0.2}))["status"], "unresolved")
        card = [self.box("a long enough card text", .30), self.box("more words here", .55), self.box("x", .9)]
        run2 = lambda rules: self.run_state([{"id": "S", "shots": [shot("1-1", {"ocr_boxes": card})]}], rules=rules)[0]["macros"]["M6"]
        self.assertEqual(run2(RULES)["value"], "card")
        self.assertEqual(run2(self.rules_with(M6_text={"card_max_lower_frac": 0.01}))["status"], "unresolved")

    def test_rules_json_carries_new_keys(self):
        with open(RULES) as fh:
            r = json.load(fh)
        self.assertEqual((r["M6_text"]["lower_frac"], r["M6_text"]["above_y"], r["M6_text"]["card_max_lower_frac"]),
                         (0.70, 0.45, 0.5))
        self.assertEqual((r["M2_motion"]["min_shot_s"], r["M2_motion"]["sample_offset_s"], r["M2_motion"]["edge_pad_s"]),
                         (0.6, 0.25, 0.05))

    def test_m2_min_shot_s_from_rules(self):
        import sys as _s
        _s.path.insert(0, os.path.join(HERE, "..", "scripts"))
        import macros
        vid = os.path.join(self.d, "v.mp4")
        open(vid, "wb").close()
        r = {"static_max_diff": 6.0, "min_shot_s": 2.0}
        m = macros.m2_for([({}, {"id": "x", "tc_in": 0, "tc_out": 1.5})], lambda sc, sh: None, vid, r, self.d, self.d)
        self.assertEqual(m["status"], "unresolved")
        self.assertIn("shorter than 2.0", m["why"])

    def test_timecode_dot_separator(self):
        m = self.one({"ocr_boxes": [self.box("01:02:03.04", .9)]})["M6"]
        self.assertEqual((m["value"], m["status"]), ("timecode", "resolved"))


if __name__ == "__main__":
    unittest.main()
