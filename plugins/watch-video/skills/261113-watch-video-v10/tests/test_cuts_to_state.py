"""cuts_to_state.py (R1 + R2): shots land in the state in time order, filed by midpoint, cuts in frames."""
import json, os, shutil, subprocess, sys, tempfile, unittest
from fractions import Fraction

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
REPO = os.path.abspath(os.path.join(ROOT, "..", "..", "..", ".."))
CLIP = os.path.join(REPO, "skills", "261105-scan-and-index-hard-drives", "test_drive", "TEST_DRIVE_v1",
                    "260901_SHOOT_DAY_1", "FX6_A001", "XDROOT", "Sub", "A001C001_260901ABS03.MP4")
HAVE = os.path.isfile(CLIP) and shutil.which("ffprobe") and shutil.which("ffmpeg")


def load(p):
    with open(p) as f:
        return json.load(f)


def all_shots(st):
    return [(sc, sh) for sc in st["scenes"] for sh in sc["shots"]]


def check_filing(tc, st):
    pairs = all_shots(st)
    tcs = [sh["tc_in"] for _, sh in pairs]
    tc.assertEqual(tcs, sorted(tcs), "shots out of time order")
    starts = [sc["tc_in"] for sc in st["scenes"]]
    tc.assertEqual(starts, sorted(starts), "scenes out of time order")
    outside = [sh["id"] for sc, sh in pairs
               if not (sc["tc_in"] <= (sh["tc_in"] + sh["tc_out"]) / 2 <= sc["tc_out"])]
    tc.assertEqual(outside, [], "shots outside their scene span")


@unittest.skipUnless(HAVE, "TEST_DRIVE_v1 clip or ffprobe/ffmpeg missing")
class CutsToState(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import cuts_to_state, detect_cuts
        cls.c2s, cls.dc = cuts_to_state, detect_cuts
        cls.tmp = tempfile.mkdtemp()
        cls.cuts = detect_cuts.detect_cuts(CLIP)
        # a SEE file whose segments leave gaps and start late: the R2 trap
        see = {"segments": [{"clip": os.path.basename(CLIP), "t_in": 0.5, "t_out": 1.0, "tags": {}},
                            {"clip": os.path.basename(CLIP), "t_in": 6.0, "t_out": 6.5,
                             "tags": {"content": {"value": "late seg"}}},
                            {"clip": "OTHER.MP4", "t_in": 0.0, "t_out": 99.0, "tags": {}}]}
        cls.see = os.path.join(cls.tmp, "watch.json")
        with open(cls.see, "w") as f:
            json.dump(see, f)
        cls.job = os.path.join(cls.tmp, "job")
        sp, cls.summary = cuts_to_state.build(cls.job, CLIP, see=cls.see, title="Test Clip")
        cls.st = load(sp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_fps_is_the_files(self):
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=r_frame_rate", "-of", "csv=p=0", CLIP], capture_output=True, text=True).stdout
        self.assertEqual(Fraction(self.st["cuts"]["fps_rational"]), Fraction(out.strip().strip(",")))
        self.assertEqual(self.st["cuts"]["fps_rational"], "24000/1001")

    def test_cuts_frames_and_seconds_agree(self):
        c = self.st["cuts"]
        fps = Fraction(c["fps_rational"])
        self.assertTrue(c["cut_frames"], "the test clip has cuts")
        self.assertEqual(len(c["cut_frames"]), len(c["cut_times"]))
        for f, t in zip(c["cut_frames"], c["cut_times"]):
            self.assertIsInstance(f, int)
            self.assertLess(abs(t - float(f / fps)), 0.5 / float(fps))
        for _, sh in all_shots(self.st):
            self.assertLess(abs(sh["tc_in"] - float(sh["frame_in"] / fps)), 0.5 / float(fps))
            self.assertEqual(sh["frames"], sh["frame_out"] - sh["frame_in"])

    def test_shots_in_order_and_inside_scenes(self):
        self.assertEqual(self.summary["shots"], len(self.st["cuts"]["cut_frames"]) + 1)
        self.assertEqual(len(all_shots(self.st)), self.summary["shots"])
        self.assertEqual(self.summary["scene_source"], "SEE")
        check_filing(self, self.st)

    def test_state_fields(self):
        self.assertEqual(self.st["job"], "Test Clip")
        for _, sh in all_shots(self.st):
            for k in ("snd_in", "snd_off", "snd_beat"):
                self.assertIsInstance(sh[k], str)
            self.assertFalse(sh["verified"])
            self.assertTrue(os.path.isfile(os.path.join(self.job, sh["frame"])), sh["frame"])
        self.assertEqual(sorted(os.listdir(self.job)), ["WATCH_STATE.json", "frames"])  # no side file

    def test_one_frame_shot_is_kept(self):
        fake = dict(self.cuts)
        a = self.cuts["cut_frames"][0]
        fake["cut_frames"] = sorted(set(self.cuts["cut_frames"]) | {a + 1})  # a synthetic 1-frame shot at a..a+1
        job = os.path.join(self.tmp, "one_frame")
        sp, summ = self.c2s.build(job, CLIP, see=self.see, cuts=fake)
        st = load(sp)
        shots = [sh for _, sh in all_shots(st)]
        self.assertEqual(len(shots), len(fake["cut_frames"]) + 1)
        one = [sh for sh in shots if sh["frames"] == 1]
        self.assertEqual(len(one), 1)
        self.assertEqual(one[0]["frame_in"], a)
        self.assertLess(one[0]["tc_out"] - one[0]["tc_in"], 0.05)  # the old glue's 0.05 s floor would drop it
        self.assertTrue(os.path.isfile(os.path.join(job, one[0]["frame"])))
        check_filing(self, st)

    def test_no_see_means_one_scene(self):
        sp, summ = self.c2s.build(os.path.join(self.tmp, "nosee"), CLIP, cuts=self.cuts, frames=False)
        st = load(sp)
        self.assertEqual(len(st["scenes"]), 1)
        check_filing(self, st)


class Cli(unittest.TestCase):
    def test_help_exits_zero(self):
        for s in ("detect_cuts.py", "cuts_to_state.py", "watch_state.py"):
            r = subprocess.run([sys.executable, os.path.join(SCRIPTS, s), "--help"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, (s, r.stderr[-300:]))
            self.assertIn("usage", r.stdout.lower(), s)

    def test_file_shots_gap_shot_goes_to_earlier_scene(self):
        import cuts_to_state
        scenes = [{"id": "A", "tc_in": 0.0, "tc_out": 1.0}, {"id": "B", "tc_in": 5.0, "tc_out": 6.0}]
        shots = [{"id": "s3", "tc_in": 5.0, "tc_out": 6.0}, {"id": "s2", "tc_in": 2.0, "tc_out": 3.0},
                 {"id": "s1", "tc_in": 0.0, "tc_out": 2.0}]
        out = cuts_to_state.file_shots(scenes, shots)
        self.assertEqual([[s["id"] for s in sc["shots"]] for sc in out], [["s1", "s2"], ["s3"]])
        self.assertEqual(out[0]["tc_out"], 3.0)


@unittest.skipUnless(HAVE, "TEST_DRIVE_v1 clip or ffprobe missing")
class BulkAddShots(unittest.TestCase):
    def test_add_shots_one_call(self):
        d = tempfile.mkdtemp()
        try:
            ws = os.path.join(SCRIPTS, "watch_state.py")
            sp = os.path.join(d, "S.json")
            run = lambda *a: subprocess.run([sys.executable, ws, *a, "--state", sp], capture_output=True, text=True)  # noqa: E731
            self.assertEqual(run("init", "--video", CLIP, "--job", "T").returncode, 0)
            run("add-scene", "--id", "SC01"); run("add-scene", "--id", "SC02")
            f = os.path.join(d, "shots.json")
            with open(f, "w") as h:
                json.dump([{"id": "101-1", "scene": "SC01"}, {"id": "101-2", "scene": "SC02"}], h)
            r = run("add-shots", "--json", f)
            self.assertEqual(r.returncode, 0, r.stderr)
            st = load(sp)
            self.assertEqual([[x["id"] for x in sc["shots"]] for sc in st["scenes"]], [["101-1"], ["101-2"]])
            with open(f, "w") as h:
                json.dump([{"id": "101-3", "scene": "SC09"}], h)
            self.assertNotEqual(run("add-shots", "--json", f).returncode, 0)
            self.assertEqual(sum(len(sc["shots"]) for sc in load(sp)["scenes"]), 2)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
