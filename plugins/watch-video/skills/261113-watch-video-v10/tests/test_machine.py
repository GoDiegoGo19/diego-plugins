"""p0_machine.py: where am I, the ready check, the one-step-at-a-time walk, the setup file, the machine card."""
import json, os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
M = os.path.join(ROOT, "scripts", "p0_machine.py")


def run(*a, **env):
    e = dict(os.environ, SKILLS_MACHINE_DIR=env.pop("mdir", tempfile.mkdtemp()))
    e.update(env)
    return subprocess.run([sys.executable, M] + list(a), capture_output=True, text=True, env=e)


class Where(unittest.TestCase):
    def test_cloud_warns_about_tokens(self):
        r = run("where", SKILLS_PROFILE="cloud_dev")
        self.assertEqual(r.returncode, 3)
        self.assertIn("uses your tokens", r.stdout)
        self.assertIn("Switch to my Mac", r.stdout)

    def test_mac_is_best_linux_ok(self):
        self.assertIn("BEST", run("where", SKILLS_PROFILE="mac").stdout)
        self.assertIn("OK", run("where", SKILLS_PROFILE="linux_box").stdout)
        self.assertEqual(json.loads(run("where", "--json", SKILLS_PROFILE="linux_box").stdout)["verdict"], "OK")


class ReadyAndWalk(unittest.TestCase):
    def nopath(self):
        d = tempfile.mkdtemp()
        return d  # an empty PATH: no ffmpeg, tesseract, brew

    def test_check_lists_this_skills_needs(self):
        out = json.loads(run("check", "--json").stdout)
        ids = [i["id"] for i in out["items"]]
        for need in ("python", "ffmpeg", "opencv"):
            self.assertIn(need, ids)
        for i in out["items"]:
            for k in ("what", "why", "size", "if_no"):
                self.assertTrue(i[k], (i["id"], k))

    def test_walk_on_a_bare_mac_starts_with_homebrew(self):
        r = run("walk", "--json", SKILLS_PROFILE="mac", PATH=self.nopath())
        nxt = json.loads(r.stdout)["next"]
        self.assertEqual(nxt["id"], "homebrew")
        self.assertTrue(nxt["you_paste_it"])

    def test_walk_on_bare_linux_gives_one_apt_step(self):
        nxt = json.loads(run("walk", "--json", SKILLS_PROFILE="linux_box", PATH=self.nopath()).stdout)["next"]
        self.assertEqual(nxt["id"], "ffmpeg")
        self.assertIn("apt-get", nxt["install"])

    def test_not_now_moves_past_a_step(self):
        nxt = json.loads(run("walk", "--json", SKILLS_PROFILE="linux_box", PATH=self.nopath(),
                             SKILLS_SKIP="ffmpeg").stdout)["next"]
        self.assertNotEqual(nxt["id"], "ffmpeg")


class Files(unittest.TestCase):
    def test_setup_file_and_card(self):
        d = tempfile.mkdtemp()
        r = run("script", mdir=d, SKILLS_PROFILE="linux_box")
        self.assertEqual(r.returncode, 0, r.stdout)
        sh = open(os.path.join(d, "setup_linux.sh")).read()
        self.assertIn("ffmpeg", sh)
        run("card", mdir=d)
        card = json.load(open(os.path.join(d, "machine.json")))
        self.assertIn("skills", card)
        self.assertEqual(len(card["skills"]), 1)


class CcrUnreadable(unittest.TestCase):
    """R12: /root/.ccr that raises PermissionError is skipped quietly, in both p0 files."""
    def test_permission_error_is_not_cloud(self):
        import importlib.util
        from pathlib import Path
        from unittest import mock
        mods = []
        for name, fn in (("p0_env", "_cloud"), ("p0_machine", "is_cloud")):
            spec = importlib.util.spec_from_file_location("r12_" + name, os.path.join(ROOT, "scripts", name + ".py"))
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            mods.append(getattr(m, fn))
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_REMOTE": ""}), \
             mock.patch.object(Path, "is_dir", side_effect=PermissionError(13, "Permission denied", "/root/.ccr")):
            for f in mods:
                self.assertFalse(f())


if __name__ == "__main__":
    unittest.main()
