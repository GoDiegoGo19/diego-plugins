"""The watch engine is the same in the scan skill and the watch skill (Diego, 261107).

Standalone: this skill's engine files match its ENGINE.json fingerprints.
In the repo (both skills present, or SIBLING_SKILL set): the two copies are identical.
"""
import glob, os, subprocess, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYNC = os.path.join(ROOT, "scripts", "engine_sync.py")


def sibling():
    if os.environ.get("SIBLING_SKILL"):
        return os.environ["SIBLING_SKILL"]
    me = os.path.basename(ROOT)
    other = "watch-video-shot-contact-sheet" if "scan-and-index" in me else "scan-and-index-hard-drives"
    hits = sorted(glob.glob(os.path.join(ROOT, "..", "..", "..", "*", "skill", "*" + other + "-v*")))
    hits = [h for h in hits if os.path.isdir(h)]
    if not hits:
        return None
    return max(hits, key=lambda h: int(h.rsplit("-v", 1)[1]) if h.rsplit("-v", 1)[1].isdigit() else -1)


class Engine(unittest.TestCase):
    def test_engine_matches_manifest(self):
        r = subprocess.run([sys.executable, SYNC, "check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_same_engine_as_the_other_skill(self):
        other = sibling()
        if not other:
            self.skipTest("the other skill is not beside this one (standalone install)")
        r = subprocess.run([sys.executable, SYNC, "compare", other], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main()
