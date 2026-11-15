try:  # the scan skill sets temp outside home via p0_env; the watch skill has no _env
    import _env  # noqa: F401
except ImportError:
    pass
import json, os, subprocess, sys, tempfile, unittest
try:
    import jinja2
except ImportError:
    jinja2 = None

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "objects.py")


def run(*args):
    p = subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr


MACRO_KEYS = ["M%d" % i for i in range(1, 9)]


def M(value, status="resolved", **kw):
    d = {"value": value, "status": status, "why": "t"}
    d.update(kw)
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.sp = os.path.join(self.d, "WATCH_STATE.json")

    def tearDown(self):
        self.tmp.cleanup()

    def frame(self, name, make=True):
        p = os.path.join(self.d, name)
        if make:
            with open(p, "wb") as f:
                f.write(b"\xff\xd8\xff")
        return p

    def write(self, st):
        # Contract W6: a complete record has all of M1-M8. Fixtures that list only some macros are padded with
        # not_run entries (present, omitted from chips/tallies), so each test still exercises just the macros it names.
        for sc in st.get("scenes", []):
            for sh in sc.get("shots", []):
                if sh.get("macros") and isinstance(sh.get("machine"), dict):
                    for k in MACRO_KEYS:
                        sh["macros"].setdefault(k, {"value": None, "status": "not_run", "why": "fixture pad"})
        with open(self.sp, "w") as f:
            json.dump(st, f, indent=2)
        with open(self.sp, "rb") as f:
            self.before = f.read()

    def objs(self):
        rc, out, err = run("build", "--state", self.sp)
        self.assertEqual(rc, 0, err)
        with open(os.path.join(self.d, "WATCH_STATE_objects.json")) as f:
            return json.load(f)


class PoolTests(Base):
    def setUp(self):
        Base.setUp(self)
        sot = {"M1": M("MCU"), "M2": M("static"), "M3": M("speech", pct=72), "M4": M("sot"),
               "M5": M("day"), "M6": M("none"), "M7": M([{"word": "drone", "score": .5}]), "M8": M(None)}
        sh = lambda i, fr, mac, **kw: dict({"id": i, "type": "MCU", "tc_in": 1.0, "tc_out": 1.0,
                                             "desc": "a woman smiles", "frame": fr, "macros": mac,
                                             "machine": {"caption": "x"}}, **kw)
        st = {"job": "Test Pool", "mode": "pool", "video": {"duration": 30}, "scenes": [
            {"id": "SM01", "slug": "A", "file": "A001C003.mov", "tc_in": 0, "tc_out": 6.2, "shots": [
                sh("SM01-M", self.frame("a1.jpg"), sot, tc_in=0, tc_out=6.2),
                sh("SM01-H", self.frame("a2.jpg"), {"M1": M("MCU"), "M2": M(None, "unresolved")}, tc_in=6.2, tc_out=7.0)]},
            {"id": "SM02", "slug": "B", "file": "B002.mov", "tc_in": 0, "tc_out": 3, "shots": [
                sh("SM02-H", self.frame("b1.jpg"), {"M1": M("WS"), "M2": M("moving"), "M5": M("night"),
                                                    "M6": M("lower_third"), "M7": M(["drone"])}),
                sh("SM02-M", self.frame("nope.jpg", make=False), {"M1": M("WS")}),
                sh("SM02-T", self.frame("b3.jpg"), {"M1": M("CU")},
                   desc="infant simulator in a classroom")]}]}
        self.write(st)
        self.o = self.objs()

    def test_statuses(self):
        s = self.o["shots"]
        self.assertEqual(s["SM01-M"]["status"], "verified")
        self.assertEqual(s["SM01-H"]["status"], "unresolved")
        self.assertEqual(s["SM01-H"]["unresolved"], ["M2"])
        self.assertEqual(s["SM02-H"]["status"], "verified")
        self.assertEqual(s["SM02-M"]["failed"], ["NO FRAME"])
        self.assertEqual(s["SM02-T"]["failed"], ["SEMANTIC NOT RUN"])
        sc = {x["id"]: x["status"] for x in self.o["scenes"]}
        self.assertEqual(sc, {"SM01": "unresolved", "SM02": "flagged"})
        self.assertEqual(self.o["video"]["status"], "flagged")
        self.assertEqual(self.o["schema"], "watch-objects/1")
        rj = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rules.json")
        want = json.load(open(rj)).get("version") if os.path.exists(rj) else None
        self.assertEqual(self.o["rules_version"], want)

    def test_state_untouched(self):
        with open(self.sp, "rb") as f:
            self.assertEqual(f.read(), self.before)

    def test_w4_missing_file_in_pool(self):
        st = json.loads(self.before)
        del st["scenes"][1]["file"]
        self.write(st)
        o = self.objs()
        self.assertIn("NO FILENAME", o["shots"]["SM02-H"]["failed"])

    def test_show_text_and_json(self):
        rc, out, _ = run("show", "SM01-M", "--state", self.sp)
        self.assertEqual(out, "SM01-M · A001C003.mov · 6.2s · MCU · static · day · text:none · speech 72% · "
                              "role:sot · tags:drone · verified")
        rc, out, _ = run("show", "A001C003.mov", "--state", self.sp)
        self.assertEqual(len(out.splitlines()), 2)
        rc, out, _ = run("show", "SM02-M", "--state", self.sp, "--json")
        self.assertEqual(json.loads(out)[0]["failed"], ["NO FRAME"])
        self.assertEqual(run("show", "zzz", "--state", self.sp)[0], 1)

    def test_summary(self):
        rc, out, _ = run("summary", "--state", self.sp)
        self.assertIn("2 scenes · 5 shots · flagged", out)
        self.assertIn("flag SM02-M: NO FRAME", out)
        self.assertIn("motion: ? 1, moving 1, static 1", out)
        rc, out, _ = run("summary", "SM01", "--state", self.sp, "--json")
        j = json.loads(out)
        self.assertEqual((j["shots"], j["status"]), (2, "unresolved"))
        self.assertEqual(j["counts"]["motion"], {"static": 1, "?": 1})

    def test_unresolved(self):
        rc, out, _ = run("unresolved", "--state", self.sp)
        ids = [l.split(" · ")[0] for l in out.splitlines()]
        self.assertEqual(sorted(ids), ["SM01-H", "SM02-M", "SM02-T"])
        self.assertIn("unresolved M2", out)
        rc, out, _ = run("unresolved", "--state", self.sp, "--json")
        self.assertEqual(len(json.loads(out)), 3)

    def test_find(self):
        f = lambda *a: run("find", "--state", self.sp, *a)[1].splitlines()
        self.assertEqual(f("--size", "mcu", "--motion", "static"), ["SM01-M"])
        self.assertEqual(f("--light", "night"), ["SM02-H"])
        self.assertEqual(f("--text", "lower_third"), ["SM02-H"])
        self.assertEqual(f("--role", "sot"), ["SM01-M"])
        self.assertEqual(sorted(f("--tag", "drone")), ["SM01-M", "SM02-H"])
        self.assertEqual(f("--size", "CU"), ["SM02-T"])
        self.assertEqual(f("--size", "ECU"), ["none"])
        rc, out, _ = run("find", "--state", self.sp, "--light", "night", "--json")
        self.assertEqual(json.loads(out), ["SM02-H"])

    def test_check(self):
        rc, out, _ = run("check", "--state", self.sp)
        self.assertEqual(rc, 1)
        self.assertIn("W1 · SM02-M · fail", out)
        self.assertIn("W5 · SM02-T · fail", out)
        self.assertIn("W1 · SM01-M · pass", out)
        rc, out, _ = run("check", "--state", self.sp, "--json")
        self.assertEqual(sum(1 for c in json.loads(out) if c["result"] == "fail"), 2)

    def test_objects_flag(self):
        op = os.path.join(self.d, "WATCH_STATE_objects.json")
        rc, out, _ = run("find", "--objects", op, "--role", "sot")
        self.assertEqual(out, "SM01-M")

    def test_semantic_passed_clears_w5(self):
        st = json.loads(self.before)
        st["scenes"][1]["shots"][2]["semantic_check"] = "passed"
        self.write(st)
        self.assertEqual(self.objs()["shots"]["SM02-T"]["status"], "verified")


class NormalModeTests(Base):
    def test_tc_overlap_and_ordering(self):
        sh = lambda i, a, b: {"id": i, "type": "WS", "tc_in": a, "tc_out": b, "desc": "wide",
                              "frame": self.frame(i + ".jpg"), "macros": {"M1": M("WS")},
                              "machine": {"caption": "x"}}
        self.write({"job": "Test Pool", "mode": "grid-first", "scenes": [
            {"id": "SC01", "slug": "X", "tc_in": 0, "tc_out": 9, "shots": [
                sh("1", 0, 3), sh("2", 3, 5), sh("3", 4, 6), sh("4", 6, 8)]}]})
        o = self.objs()
        self.assertEqual(o["shots"]["3"]["failed"], ["TC OVERLAP"])
        self.assertEqual(o["shots"]["2"]["status"], "verified")
        self.assertEqual(o["shots"]["4"]["status"], "verified")
        self.assertEqual(o["scenes"][0]["failed"], ["TC OVERLAP"])
        self.assertEqual(o["scenes"][0]["status"], "flagged")
        # no file needed in normal mode (W4 skipped)
        self.assertFalse([c for c in o["checks"] if c["check"] == "W4"])

    def test_witness_flag_and_rules_version(self):
        rules = os.path.join(self.d, "rules.json")
        with open(rules, "w") as f:
            json.dump({"version": "8.0.1"}, f)
        self.write({"job": "Test Pool", "scenes": [{"id": "S", "slug": "s", "shots": [
            {"id": "1", "tc_in": 0, "tc_out": 1, "desc": "a man", "frame": self.frame("w.jpg"),
             "witness_flag": True, "semantic_check": "adjudicated", "macros": {"M1": M("WS")},
             "machine": {"caption": "x"}}]}]})
        rc, out, err = run("build", "--state", self.sp, "--rules", rules)
        self.assertEqual(rc, 0, err)
        with open(os.path.join(self.d, "WATCH_STATE_objects.json")) as f:
            o = json.load(f)
        self.assertEqual(o["rules_version"], "8.0.1")
        self.assertEqual(o["shots"]["1"]["failed"], ["WITNESS FLAG"])
        with open(self.sp, "rb") as f:
            self.assertEqual(f.read(), self.before)


class FixRoundTests(Base):
    def one_shot_state(self, shot, **sc):
        sh = {"id": "1", "tc_in": 0, "tc_out": 1, "frame": self.frame("f.jpg"), "desc": "wide"}
        sh.update(shot)
        scene = {"id": "S", "slug": "s", "shots": [sh]}
        scene.update(sc)
        self.write({"job": "Test Pool", "scenes": [scene]})
        return self.objs()

    def test_unannotated_shot_is_unresolved_not_verified(self):
        o = self.one_shot_state({})
        s = o["shots"]["1"]
        self.assertEqual(s["status"], "unresolved")
        self.assertIn("no witness", s["unresolved"])
        self.assertTrue([c for c in o["checks"] if c["check"] == "W6" and c["result"] == "fail"])
        self.assertEqual(o["video"]["status"], "unresolved")
        rc, out, _ = run("unresolved", "--state", self.sp)
        self.assertTrue(out.startswith("1 ·"), out)
        self.assertIn("no witness", out)

    def test_machine_without_macros_and_machine_error(self):
        s = self.one_shot_state({"machine": {"caption": "x"}})["shots"]["1"]
        self.assertEqual((s["status"], s["unresolved"]), ("unresolved", ["no macros"]))
        s = self.one_shot_state({"machine": {"error": "boom"}, "macros": {"M1": M("WS")}})["shots"]["1"]
        self.assertEqual(s["status"], "unresolved")
        self.assertTrue(s["unresolved"][0].startswith("no witness"))

    def test_analysed_shot_verified(self):
        o = self.one_shot_state({"machine": {"caption": "x"}, "macros": {"M1": M("WS")}})
        self.assertEqual(o["shots"]["1"]["status"], "verified")
        self.assertEqual(o["video"]["status"], "verified")

    def test_zero_shot_scene_flagged_and_zero_shot_state(self):
        self.write({"job": "Test Pool", "scenes": [{"id": "S", "slug": "s", "shots": []}]})
        o = self.objs()
        self.assertEqual(o["scenes"][0]["status"], "flagged")
        self.assertEqual(o["scenes"][0]["failed"], ["NO FRAME"])
        self.assertNotEqual(o["video"]["status"], "verified")
        self.write({"job": "Test Pool", "scenes": []})
        o = self.objs()
        self.assertEqual(o["video"]["status"], "unresolved")
        self.assertEqual(o["video"]["note"], "no shots")

    def test_relative_frame_resolved_against_state_dir(self):
        self.frame("rel.jpg")
        cwd = os.getcwd()
        os.chdir(tempfile.gettempdir())
        try:
            o = self.one_shot_state({"frame": "rel.jpg", "machine": {"caption": "x"}, "macros": {"M1": M("WS")}})
        finally:
            os.chdir(cwd)
        self.assertEqual(o["shots"]["1"]["failed"], [])

    def test_simulant_words_from_rules(self):
        rules = os.path.join(self.d, "rules.json")
        with open(rules, "w") as f:
            json.dump({"version": 1, "W5_simulant_words": ["zeppelin"]}, f)
        st = {"job": "Test Pool", "scenes": [{"id": "S", "slug": "s", "shots": [
            {"id": "1", "tc_in": 0, "tc_out": 1, "frame": self.frame("f.jpg"), "desc": "a zeppelin and a patient",
             "machine": {"caption": "x"}, "macros": {"M1": M("WS")}}]}]}
        self.write(st)
        run("build", "--state", self.sp, "--rules", rules)
        with open(os.path.join(self.d, "WATCH_STATE_objects.json")) as f:
            o = json.load(f)
        self.assertEqual(o["shots"]["1"]["failed"], ["SEMANTIC NOT RUN"])
        st["scenes"][0]["shots"][0]["desc"] = "just a patient"
        self.write(st)
        run("build", "--state", self.sp, "--rules", rules)
        with open(os.path.join(self.d, "WATCH_STATE_objects.json")) as f:
            self.assertEqual(json.load(f)["shots"]["1"]["failed"], [])

    def test_objects_rebuilt_when_state_newer(self):
        self.one_shot_state({"machine": {"caption": "x"}, "macros": {"M1": M("WS")}})
        op = os.path.join(self.d, "WATCH_STATE_objects.json")
        self.assertEqual(run("summary", "--state", self.sp)[1].splitlines()[0].split(" · ")[3], "verified")
        with open(self.sp) as f:
            st = json.load(f)
        st["scenes"][0]["shots"][0]["macros"] = {"M1": M(None, "unresolved")}
        with open(self.sp, "w") as f:
            json.dump(st, f)
        future = os.path.getmtime(op) + 50
        os.utime(self.sp, (future, future))
        self.assertEqual(run("summary", "--state", self.sp)[1].splitlines()[0].split(" · ")[3], "unresolved")


HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
RULES = os.path.join(HERE, "..", "rules.json")
PENDING = {"value": "PENDING", "status": "needs_scan", "why": "no witness data", "scan_by": "any"}


def script(name, *args):
    p = subprocess.run([sys.executable, os.path.join(SCRIPTS, name)] + list(args), capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr


class PendingChainTests(Base):
    """A shot whose frame is unusable travels annotate -> macros -> objects pending -> fill, never blank."""
    def setUp(self):
        Base.setUp(self)
        full = dict((k, M("x")) for k in MACRO_KEYS)
        self.zero = self.frame("zero.jpg")
        open(self.zero, "wb").close()                       # 0-byte: exists (W1 passes) but unusable
        self.write({"job": "J", "mode": "pool", "scenes": [{"id": "SM01", "slug": "a", "file": "a.mov", "shots": [
            {"id": "SM01-GONE", "tc_in": 1, "tc_out": 1, "frame": os.path.join(self.d, "gone.jpg"), "file": "a.mov"},
            {"id": "SM01-ZERO", "tc_in": 2, "tc_out": 2, "frame": self.zero, "file": "a.mov"},
            {"id": "SM01-OK", "tc_in": 3, "tc_out": 3, "frame": self.frame("ok.jpg"), "file": "a.mov",
             "machine": {"caption": "x"}, "macros": full}]}]})

    def load(self):
        with open(self.sp) as f:
            return json.load(f)

    def test_chain_pending_then_fill(self):
        rc, out, err = script("annotate_frames.py", "--state", self.sp)
        self.assertEqual(rc, 0, err)
        self.assertEqual({m["shot"] for m in json.loads(out)["missing_frames"]}, {"SM01-GONE", "SM01-ZERO"})
        shots = dict((x["id"], x) for x in self.load()["scenes"][0]["shots"])
        for i in ("SM01-GONE", "SM01-ZERO"):
            self.assertEqual((shots[i]["machine"]["value"], shots[i]["machine"]["status"]), ("PENDING", "needs_scan"))
        rc, out, err = script("macros.py", "--state", self.sp)
        self.assertEqual(rc, 0, err)
        shots = dict((x["id"], x) for x in self.load()["scenes"][0]["shots"])
        for i in ("SM01-GONE", "SM01-ZERO"):
            self.assertEqual(sorted(shots[i]["macros"]), MACRO_KEYS)
            self.assertEqual({m["status"] for m in shots[i]["macros"].values()}, {"needs_scan"})
        self.assertEqual(shots["SM01-OK"]["macros"]["M1"]["value"], "x")        # untouched
        # pending lists both, with frame path and the pending keys; the finished shot is absent
        rc, out, _ = run("pending", "--state", self.sp, "--json")
        rows = dict((r["id"], r) for r in json.loads(out))
        self.assertEqual(sorted(rows), ["SM01-GONE", "SM01-ZERO"])
        self.assertEqual(rows["SM01-ZERO"]["pending"], ["machine"] + MACRO_KEYS)
        self.assertEqual(rows["SM01-ZERO"]["frame"], self.zero)
        self.assertEqual(rows["SM01-ZERO"]["status"], "needs_scan")
        self.assertEqual(rows["SM01-GONE"]["status"], "flagged")                 # flagged > needs_scan
        self.assertIn("SM01-ZERO", run("pending", "--state", self.sp)[1])
        # the pending shots are NOT in `unresolved` (that is for judgment, this is for a scanner)
        self.assertNotIn("SM01-ZERO", run("unresolved", "--state", self.sp)[1])
        # summary counts them; chips print PENDING
        rc, out, _ = run("summary", "--state", self.sp)
        self.assertIn("needs_scan: 1", out)
        self.assertIn("PENDING", run("show", "SM01-ZERO", "--state", self.sp)[1])
        # fill one macro: pending list shrinks, state carries the judgment, objects rebuilt
        rc, out, err = run("fill", "--state", self.sp, "--shot", "SM01-ZERO", "--macro", "M1", "--value", "MCU", "--by", "claude")
        self.assertEqual(rc, 0, out + err)
        m1 = self.load()["scenes"][0]["shots"][1]["macros"]["M1"]
        self.assertEqual((m1["value"], m1["status"], m1["filled_by"]), ("MCU", "resolved", "claude"))
        self.assertTrue(m1["why"])
        rows = dict((r["id"], r) for r in json.loads(run("pending", "--state", self.sp, "--json")[1]))
        self.assertEqual(rows["SM01-ZERO"]["pending"], ["machine"] + MACRO_KEYS[1:])
        o = self.objs()
        self.assertEqual(o["shots"]["SM01-ZERO"]["macros"]["M1"]["filled_by"], "claude")
        self.assertEqual(o["state_mtime"], os.path.getmtime(self.sp))

    def test_fill_flips_status_to_verified(self):
        st = self.load()
        st["scenes"][0]["shots"][2]["macros"]["M3"] = dict(PENDING)
        self.write(st)
        self.assertEqual(self.objs()["shots"]["SM01-OK"]["status"], "needs_scan")
        rc, out, err = run("fill", "--state", self.sp, "--shot", "SM01-OK", "--macro", "M3", "--value", "speech")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(json.loads(out)["status"], "verified")
        self.assertEqual(self.objs()["shots"]["SM01-OK"]["status"], "verified")
        # fill is allowed on an already-resolved macro (a judgment overrides)
        self.assertEqual(run("fill", "--state", self.sp, "--shot", "SM01-OK", "--macro", "M3", "--value", "no_speech")[0], 0)
        self.assertEqual(self.load()["scenes"][0]["shots"][2]["macros"]["M3"]["value"], "no_speech")

    def test_fill_refuses_unknown_shot_and_macro_and_leaves_state_alone(self):
        before = open(self.sp, "rb").read()
        rc, out, _ = run("fill", "--state", self.sp, "--shot", "NOPE", "--macro", "M1", "--value", "CU")
        self.assertEqual(rc, 1)
        self.assertIn("no shot", out)
        rc, out, _ = run("fill", "--state", self.sp, "--shot", "SM01-OK", "--macro", "M9", "--value", "CU")
        self.assertEqual(rc, 1)
        self.assertIn("unknown macro", out)
        self.assertEqual(run("fill", "--state", self.sp, "--shot", "SM01-OK", "--macro", "M1")[0], 2)   # no --value
        self.assertEqual(open(self.sp, "rb").read(), before)
        self.assertFalse(os.path.exists(self.sp + ".tmp"))

    def test_fill_desc_clears_stale(self):
        st = self.load()
        sh = st["scenes"][0]["shots"][2]
        sh["desc"] = ""
        sh["desc_stale"] = "old words"
        self.write(st)
        o = self.objs()["shots"]["SM01-OK"]
        self.assertEqual((o["status"], o["needs_scan"], o["needs_scan_why"]["desc"]),
                         ("needs_scan", ["desc"], "frame changed since description"))
        rc, out, err = run("fill", "--state", self.sp, "--shot", "SM01-OK", "--desc", "a woman at a desk", "--by", "claude")
        self.assertEqual(rc, 0, out + err)
        sh = self.load()["scenes"][0]["shots"][2]
        self.assertEqual((sh["desc"], sh["desc_filled_by"]), ("a woman at a desk", "claude"))
        self.assertNotIn("desc_stale", sh)
        self.assertEqual(self.objs()["shots"]["SM01-OK"]["status"], "verified")

    def test_fill_completes_a_bare_record_with_placeholders(self):
        self.write({"job": "J", "scenes": [{"id": "S", "slug": "s", "shots": [
            {"id": "1", "tc_in": 0, "tc_out": 1, "frame": self.frame("b.jpg")}]}]})
        self.assertEqual(run("fill", "--state", self.sp, "--shot", "1", "--macro", "M5", "--value", "day")[0], 0)
        mac = json.load(open(self.sp))["scenes"][0]["shots"][0]["macros"]
        self.assertEqual(sorted(mac), MACRO_KEYS)
        self.assertEqual(mac["M1"]["status"], "needs_scan")
        self.assertEqual(mac["M5"]["status"], "resolved")


class ObjectsFileAndSummaryTests(Base):
    def one(self, name="WATCH_STATE.json", job="J"):
        sp = os.path.join(self.d, name)
        full = dict((k, M("x")) for k in MACRO_KEYS)
        with open(sp, "w") as f:
            json.dump({"job": job, "scenes": [{"id": "S", "slug": "s", "shots": [
                {"id": "1", "tc_in": 0, "tc_out": 1, "frame": self.frame("f.jpg"), "desc": "wide",
                 "machine": {"caption": "x"}, "macros": full}]}]}, f)
        return sp

    def test_objects_named_after_state_stem_and_stamped(self):
        a, b = self.one("A.json"), self.one("B.json")            # same job, one folder
        for sp in (a, b):
            self.assertEqual(run("build", "--state", sp)[0], 0)
        for stem, sp in (("A", a), ("B", b)):
            with open(os.path.join(self.d, stem + "_objects.json")) as f:
                o = json.load(f)
            self.assertEqual(o["state_path"], os.path.abspath(sp))
            self.assertEqual(o["state_mtime"], os.path.getmtime(sp))

    def test_foreign_or_stale_objects_file_is_rebuilt(self):
        sp = self.one("A.json")
        op = os.path.join(self.d, "A_objects.json")
        run("build", "--state", sp)
        with open(op) as f:
            o = json.load(f)
        o["state_path"] = "/elsewhere/A.json"
        o["video"]["status"] = "STALE"
        with open(op, "w") as f:
            json.dump(o, f)
        future = os.path.getmtime(sp) + 100                      # objects file is newer than the state; only the stamp differs
        os.utime(op, (future, future))
        run("summary", "--state", sp)
        with open(op) as f:
            self.assertEqual(json.load(f)["state_path"], os.path.abspath(sp))
        o["state_path"] = os.path.abspath(sp)
        o["state_mtime"] = 1.0                                   # right path, wrong state mtime
        with open(op, "w") as f:
            json.dump(o, f)
        os.utime(op, (future, future))
        run("summary", "--state", sp)
        with open(op) as f:
            self.assertEqual(json.load(f)["state_mtime"], os.path.getmtime(sp))

    def test_summary_of_empty_state_is_not_verified(self):
        for scenes in ([], [{"id": "S", "slug": "s", "shots": []}]):
            self.write({"job": "J", "scenes": scenes})
            rc, out, _ = run("summary", "--state", self.sp)
            head = out.splitlines()[0]
            self.assertNotIn("verified", head)
            self.assertIn("0 shots", head)
            j = json.loads(run("summary", "--state", self.sp, "--json")[1])
            self.assertNotEqual(j["status"], "verified")
            self.assertEqual(j["needs_scan"], 0)

    def test_w6_needs_all_eight_macros(self):
        self.write({"job": "J", "scenes": [{"id": "S", "slug": "s", "shots": [
            {"id": "1", "tc_in": 0, "tc_out": 1, "frame": self.frame("p.jpg"), "machine": {"caption": "x"}, "macros": {"M1": M("WS")}}]}]})
        # write() pads missing macro keys, so rewrite the macros raw to get a genuinely partial record
        st = json.load(open(self.sp))
        st["scenes"][0]["shots"][0]["macros"] = {"M1": M("WS")}
        json.dump(st, open(self.sp, "w"))
        o = self.objs()["shots"]["1"]
        self.assertEqual(o["status"], "unresolved")
        self.assertTrue(o["unresolved"][0].startswith("no macros (missing M2"))
        w6 = [c for c in self.objs()["checks"] if c["check"] == "W6"][0]
        self.assertEqual(w6["result"], "fail")

    def test_placeholders_pass_w6_but_are_needs_scan(self):
        self.write({"job": "J", "scenes": [{"id": "S", "slug": "s", "shots": [
            {"id": "1", "tc_in": 0, "tc_out": 1, "frame": self.frame("p.jpg"),
             "machine": dict(PENDING), "macros": dict((k, dict(PENDING)) for k in MACRO_KEYS)}]}]})
        o = self.objs()
        self.assertEqual([c["result"] for c in o["checks"] if c["check"] == "W6"], ["pass"])
        self.assertEqual(o["shots"]["1"]["status"], "needs_scan")
        self.assertEqual(o["video"]["status"], "needs_scan")
        self.assertEqual(json.loads(run("summary", "--state", self.sp, "--json")[1])["needs_scan"], 1)

    def test_precedence_flagged_over_needs_scan_over_unresolved(self):
        mk = lambda i, **kw: dict({"id": i, "tc_in": 0, "tc_out": 1, "frame": self.frame(i + ".jpg"),
                                   "machine": {"caption": "x"}}, **kw)
        base = dict((k, M("x")) for k in MACRO_KEYS)
        res = dict(base, M2=M(None, "unresolved")); pen = dict(base, M2=dict(PENDING)); both = dict(pen, M3=M(None, "unresolved"))
        self.write({"job": "J", "scenes": [{"id": "S", "slug": "s", "shots": [
            {"id": "u", "tc_in": 0, "tc_out": 1, "frame": self.frame("u.jpg"), "machine": {"caption": "x"}, "macros": res},
            {"id": "n", "tc_in": 1, "tc_out": 2, "frame": self.frame("n.jpg"), "machine": {"caption": "x"}, "macros": both},
            {"id": "f", "tc_in": 2, "tc_out": 3, "frame": self.frame("f.jpg"), "machine": {"caption": "x"}, "macros": pen,
             "witness_flag": True, "semantic_check": "adjudicated"}]}]})
        s = self.objs()["shots"]
        self.assertEqual((s["u"]["status"], s["n"]["status"], s["f"]["status"]), ("unresolved", "needs_scan", "flagged"))


@unittest.skipIf(jinja2 is None, "jinja2 not installed")
class TemplateTests(unittest.TestCase):
    def render(self, font_dir="/fonts", macros=None):
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(HERE, "..", "templates")), autoescape=True)
        shot = {"id": "SM01-HEAD", "b64": "", "macros": macros, "desc": "live words", "desc_stale": "stale words"}
        return env.get_template("sheet.html.j2").render(
            job="J", subline="s", macro=None, board_pages=[[shot]], closer=None, duration="",
            font_dir=font_dir, page_w=1000, page_h=1500, thumb=300, thumb_h=170)

    def test_font_dir_not_html_escaped(self):
        html = self.render("/Users/d/Tom & Jerry/fonts")
        self.assertIn("file:///Users/d/Tom & Jerry/fonts/OstrichSans-Medium.otf", html)
        self.assertNotIn("&amp;", html)

    def test_pending_chips_and_stale_desc_hidden(self):
        mac = {"M1": dict(PENDING), "M2": M("moving"), "M5": M(None, "unresolved"), "M8": dict(PENDING)}
        html = self.render(macros=mac)
        self.assertIn("PENDING \u00b7 moving \u00b7 ?", html)      # M1 pending, M2 value, M5 unresolved; M8 pending is silent
        self.assertIn("live words", html)
        self.assertNotIn("stale words", html)


if __name__ == "__main__":
    unittest.main()
