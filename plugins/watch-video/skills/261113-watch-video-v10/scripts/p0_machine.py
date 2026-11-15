#!/usr/bin/env python3
"""p0_machine.py · Step 0 for every skill that shares this engine: where am I, is this machine ready, set it up.

Diego's rule (261107): these skills are small programs that should run on HIS machines, not on Claude's
tokens. Local Mac first, a Linux box second (e.g. Cowork), a cloud session last and only by choice.
A new or dedicated machine is set up by walking through the pieces once, or by running one setup file.

    python3 scripts/p0_machine.py where     # where am I + the verdict (Mac best · Linux OK · cloud = token warning)
    python3 scripts/p0_machine.py check     # each piece this skill needs: OK / MISSING, what it is, why, what a "no" costs
    python3 scripts/p0_machine.py walk      # the NEXT install step only: explain it, the exact line, how to verify it
    python3 scripts/p0_machine.py script    # write one setup file for this machine (~/.skills-machine/setup_<platform>.sh)
    python3 scripts/p0_machine.py card      # save what this machine is ready for (~/.skills-machine/machine.json)
    add --json to where / check / walk for machine-readable output

It installs nothing by itself. Claude reads `walk` to the person, asks [Install now · Not now], runs the
approved line (Homebrew's line the person pastes into Terminal themselves: it asks for the Mac password,
which Claude never types or sees), then runs `walk` again. "Not now" is fine: say what it costs, mark that
feature PENDING, and carry on. The pieces a skill needs are listed in its rules.json -> "machine".

Shared engine file (identical in the scan and watch skills; see engine_sync.py).
"""
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
IS_MAC = sys.platform == "darwin"
PIP = "python3 -m pip install --user" + ("" if IS_MAC else " --break-system-packages")
MACHINE_DIR = Path(os.environ.get("SKILLS_MACHINE_DIR") or (Path.home() / ".skills-machine"))

CLOUD_WARNING = ("This is a CLOUD session. The skill can run here, but every step runs on Claude and uses your "
                 "tokens, and it cannot see the drives or files on your own machines. Recommended: run it on your "
                 "Mac (or a Linux box such as Cowork), where it is local and free. Continue here only if you choose to.")


# --------------------------------------------------------------------- where am I

def _ccr_dir():
    """True when /root/.ccr is a readable dir; a missing or unreadable path is skipped quietly (R12)."""
    try:
        return Path("/root/.ccr").is_dir()
    except OSError:
        return False


def is_cloud():
    return bool(os.environ.get("CLAUDE_CODE_REMOTE") or _ccr_dir())


def detect():
    forced = os.environ.get("SKILLS_PROFILE") or os.environ.get("SCAN_PROFILE")
    if forced in ("mac", "linux_box", "cloud_dev"):
        return forced, "forced by environment variable"
    if IS_MAC:
        return "mac", "macOS"
    if is_cloud():
        return "cloud_dev", "Linux cloud session"
    if (Path.home() / "mnt").is_dir():
        return "linux_box", "Linux with ~/mnt (e.g. Cowork)"
    return "linux_box", "Linux"


VERDICT = {
    "mac": ("BEST", "Local Mac: free, private, your drives and files are right here. Run it here."),
    "linux_box": ("OK", "Linux box (backup): local and free. No Homebrew (apt + pip instead); drives usually under ~/mnt."),
    "cloud_dev": ("WARNING", CLOUD_WARNING),
}


def where():
    prof, how = detect()
    level, text = VERDICT[prof]
    return {"profile": prof, "detected_by": how, "verdict": level, "say": text,
            "os": "%s %s" % (platform.system(), platform.release()), "python": platform.python_version(),
            "homebrew": shutil.which("brew"), "root": hasattr(os, "geteuid") and os.geteuid() == 0,
            "user": os.environ.get("USER") or os.environ.get("USERNAME") or "?"}


# --------------------------------------------------------------------- the pieces

def _have_mod(name):
    return importlib.util.find_spec(name) is not None


def _which(*names):
    paths = [shutil.which(n) for n in names]
    return (all(paths), paths[0] or "not installed")


def _cv2():
    if not _have_mod("cv2"):
        return False, "not installed"
    try:
        import cv2
    except Exception as e:
        return False, "import fails: %s" % str(e)[:80]
    if not (hasattr(cv2, "dnn") and hasattr(cv2.dnn, "readNetFromCaffe")):
        return False, "OpenCV %s is too new (5.x dropped the model loader)" % cv2.__version__
    return True, "OpenCV %s" % cv2.__version__


def _weasy():
    if not (_have_mod("weasyprint") and _have_mod("jinja2")):
        return False, "not installed"
    try:
        import weasyprint
        return True, "WeasyPrint %s" % weasyprint.__version__
    except Exception as e:
        return False, "installed but cannot start (usually pango missing): %s" % str(e)[:80]


def _pango():
    import ctypes.util
    lib = ctypes.util.find_library("pango-1.0") or ctypes.util.find_library("pangoft2-1.0")
    if not lib and IS_MAC:
        for d in ("/opt/homebrew/lib", "/usr/local/lib"):
            if any(Path(d).glob("libpango-1.0*.dylib")):
                lib = d
    return bool(lib), lib or "not found"


def _mod(name, label=None):
    return lambda: (_have_mod(name), (label or name) + (" installed" if _have_mod(name) else " not installed"))


# id: (order, what, why, size, mac line, linux line, if no, check)
CATALOG = {
    "homebrew": (0, "Homebrew, the Mac's free package installer.",
                 "It installs every command-line tool below with one short line each, and keeps them updatable.",
                 "about 500 MB with Apple's command line tools",
                 '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
                 None,
                 "Each Mac tool would need its own separate installer. Alternative: a Linux box, where apt does the installs.",
                 lambda: (bool(shutil.which("brew")), shutil.which("brew") or "not installed")),
    "python": (1, "Python 3.9 or newer, which runs the skill's scripts.", "Every step is a Python script.",
               "built into macOS", "xcode-select --install", "sudo apt-get install -y python3 python3-pip",
               "Nothing runs.", lambda: (sys.version_info >= (3, 9), platform.python_version())),
    "ffmpeg": (2, "ffmpeg and ffprobe, the standard free video tools.",
               "They read each clip's length, codec and timecode, and pull the frames for the contact sheets.",
               "about 80 MB", "brew install ffmpeg", "sudo apt-get install -y ffmpeg",
               "No durations or timecode and no frames: no contact sheets.", lambda: _which("ffmpeg", "ffprobe")),
    "tesseract": (3, "Tesseract, the free text reader (OCR).",
                  "Reads slates, lower thirds and burned-in timecode on frames.", "about 30 MB",
                  "brew install tesseract", "sudo apt-get install -y tesseract-ocr",
                  "No on-screen text notes; everything else works.", lambda: _which("tesseract")),
    "pango": (4, "Pango, the text layout library the page renderer draws with.",
              "Without it the renderer installs but cannot start.", "about 40 MB",
              "brew install pango", "sudo apt-get install -y libpango-1.0-0 libpangoft2-1.0-0",
              "No sheet pages or PDF; the data and notes still work.", _pango),
    "poppler": (5, "Poppler, free PDF tools.", "Turns sheets into page images and joins them into one PDF.",
                "about 30 MB", "brew install poppler", "sudo apt-get install -y poppler-utils",
                "No sheet pages or PDF; the data and notes still work.",
                lambda: _which("pdftoppm", "pdfunite", "pdfinfo")),
    "weasyprint": (6, "WeasyPrint and Jinja2 (Python): lay out the contact-sheet pages.",
                   "They turn each sheet into a paged PDF in the house style.", "about 20 MB",
                   PIP + " weasyprint jinja2", PIP + " weasyprint jinja2",
                   "No sheet pages or PDF; the data and notes still work.", _weasy),
    "opencv": (7, "OpenCV and NumPy (Python), pinned below version 5.",
               "They run the small local detector (people, faces, objects) and the light, sharpness and duplicate checks.",
               "about 60 MB", PIP + ' "opencv-python-headless<5" numpy', PIP + ' "opencv-python-headless<5" numpy',
               "No automatic notes (shot size, faces, light, duplicates): they stay PENDING.", _cv2),
    "webrtcvad": (8, "webrtcvad (Python), a tiny speech detector (~100 KB).",
                  "Answers 'is someone talking?' for the SOT / b-roll note. Audio is only checked when asked.",
                  "under 1 MB", PIP + " webrtcvad-wheels", PIP + " webrtcvad-wheels",
                  "No speech note; the role note stays '?'.", _mod("webrtcvad")),
    "scenedetect": (9, "PySceneDetect (Python), cut detection for single videos.",
                    "Finds the cuts in a spot or reel more reliably on handheld footage.", "about 5 MB",
                    PIP + " scenedetect", PIP + " scenedetect",
                    "Cuts are found with plain ffmpeg instead (works, a little less reliable on handheld).",
                    _mod("scenedetect")),
}


def skill_needs():
    """The pieces THIS skill needs, from its rules.json -> machine (required / optional)."""
    try:
        with open(SKILL_ROOT / "rules.json") as f:
            m = json.load(f).get("machine") or {}
    except (OSError, ValueError):
        m = {}
    req = m.get("required") or ["python", "ffmpeg", "tesseract", "pango", "poppler", "weasyprint", "opencv"]
    opt = m.get("optional") or []
    return m.get("skill") or SKILL_ROOT.name, req, opt


def check():
    prof, _ = detect()
    skill, req, opt = skill_needs()
    ids = (["homebrew"] if prof == "mac" else []) + req + [o for o in opt if o not in req]
    rows = []
    for i in sorted(ids, key=lambda k: CATALOG[k][0] if k in CATALOG else 99):
        if i not in CATALOG:
            continue
        order, what, why, size, mac, lin, if_no, chk = CATALOG[i]
        try:
            ok, detail = chk()
        except Exception as e:
            ok, detail = False, "check failed: %s" % str(e)[:80]
        line = mac if prof == "mac" else lin
        rows.append({"id": i, "ok": bool(ok), "detail": detail, "required": i in req or i == "homebrew",
                     "what": what, "why": why, "size": size, "install": None if ok else line, "if_no": if_no,
                     "you_paste_it": i == "homebrew"})
    return skill, prof, rows


def next_step(rows, skipped=()):
    for r in rows:
        if not r["ok"] and r["id"] not in skipped:
            if r["install"] and r["install"].startswith("brew ") and not shutil.which("brew"):
                return next(x for x in rows if x["id"] == "homebrew")
            return r
    return None


# --------------------------------------------------------------------- outputs

def write_script(prof, rows):
    MACHINE_DIR.mkdir(parents=True, exist_ok=True)
    name = "setup_mac.sh" if prof == "mac" else "setup_linux.sh"
    p = MACHINE_DIR / name
    L = ["#!/bin/bash", "# One-file setup for this machine (written by p0_machine.py, %s)." % datetime.now().strftime("%y%m%d %H:%M"),
         "# Run it yourself in Terminal:  bash %s" % p, "# Safe to run again: each step is skipped when already installed.",
         "set -u", ""]
    for r in rows:
        order, what, why, size, mac, lin, if_no, chk = CATALOG[r["id"]]
        line = mac if prof == "mac" else lin
        if not line:
            continue
        L.append("echo; echo '== %s'" % what.replace("'", ""))
        if r["id"] == "homebrew":
            L.append("command -v brew >/dev/null || %s" % line)
            L.append('[ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"')
        elif line.startswith("brew "):
            pkg = line.split()[-1]
            L.append("brew list %s >/dev/null 2>&1 || %s" % (pkg, line))
        else:
            L.append(line + " || echo '   (failed: see the message above)'")
    L += ["", "echo; echo 'Done. Check with:  python3 scripts/p0_machine.py check'"]
    p.write_text("\n".join(L) + "\n")
    p.chmod(0o755)
    return p


def write_card(prof, rows, skill):
    MACHINE_DIR.mkdir(parents=True, exist_ok=True)
    p = MACHINE_DIR / "machine.json"
    try:
        card = json.loads(p.read_text())
    except (OSError, ValueError):
        card = {}
    w = where()
    card.update({"machine": platform.node(), "profile": prof, "os": w["os"], "python": w["python"],
                 "homebrew": bool(w["homebrew"]), "updated": datetime.now().strftime("%y%m%d %H:%M")})
    tools = card.setdefault("tools", {})
    for r in rows:
        tools[r["id"]] = {"ok": r["ok"], "detail": r["detail"]}
    missing_req = [r["id"] for r in rows if r["required"] and not r["ok"]]
    card.setdefault("skills", {})[skill] = {"ready": not missing_req, "missing": missing_req,
                                            "checked": card["updated"]}
    p.write_text(json.dumps(card, indent=1) + "\n")
    return p, card


# --------------------------------------------------------------------- main

def main(argv):
    cmd = argv[1] if len(argv) > 1 else "where"
    as_json = "--json" in argv
    if cmd == "where":
        w = where()
        if as_json:
            print(json.dumps(w, indent=1)); return 0
        mark = {"BEST": "OK  ", "OK": "OK  ", "WARNING": "WARN"}[w["verdict"]]
        print("WHERE AM I · %s (%s)" % (w["profile"], w["detected_by"]))
        print("  %s %s" % (w["os"], "· root" if w["root"] else ""))
        print("  python %s · homebrew %s" % (w["python"], "yes" if w["homebrew"] else "no"))
        print("  %s %s: %s" % (mark, w["verdict"], w["say"]))
        if w["verdict"] == "WARNING":
            print("  ASK: [ Switch to my Mac (recommended) · Continue in the cloud ]")
        return 0 if w["verdict"] != "WARNING" else 3
    skill, prof, rows = check()
    if cmd == "check":
        missing = [r for r in rows if not r["ok"]]
        if as_json:
            print(json.dumps({"skill": skill, "profile": prof, "items": rows, "missing": len(missing)}, indent=1))
            return 0 if not any(r["required"] for r in missing) else 1
        print("READY CHECK · %s · %s" % (skill, prof))
        for r in rows:
            print("  %s %-12s %s%s" % ("OK  " if r["ok"] else "MISS", r["id"], r["detail"], "" if r["required"] else "  (optional)"))
        req_missing = [r for r in missing if r["required"]]
        print("\n%s" % ("Ready: everything required is installed." if not req_missing else
                        "%d required piece(s) missing. Next: python3 scripts/p0_machine.py walk" % len(req_missing)))
        return 0 if not req_missing else 1
    if cmd == "walk":
        skipped = [s for s in (os.environ.get("SKILLS_SKIP") or "").split(",") if s]
        r = next_step(rows, skipped)
        if as_json:
            print(json.dumps({"skill": skill, "profile": prof, "next": r}, indent=1)); return 0 if r is None else 1
        if r is None:
            print("Nothing left to install for %s. Next: python3 scripts/p0_machine.py card" % skill); return 0
        done = sum(1 for x in rows if x["ok"])
        print("STEP %d of %d · %s" % (done + 1, len(rows), r["id"]))
        print("  what:     %s" % r["what"])
        print("  why:      %s" % r["why"])
        print("  size:     %s%s" % (r["size"], "" if r["required"] else " · optional"))
        print("  install:  %s" % r["install"])
        if r["you_paste_it"]:
            print("  who:      YOU paste this line into Terminal (it asks for your Mac password; Claude never sees it).")
        print("  if no:    %s" % r["if_no"])
        print("  ASK: [ Install now · Not now ]  then run: python3 scripts/p0_machine.py walk")
        print("  (Not now: run walk with SKILLS_SKIP=%s to move past it; the feature stays PENDING.)" % r["id"])
        return 1
    if cmd == "script":
        p = write_script(prof, rows)
        print("setup file: %s\nRun it yourself in Terminal:  bash %s" % (p, p)); return 0
    if cmd == "card":
        p, card = write_card(prof, rows, skill)
        s = card["skills"][skill]
        print("machine card: %s · %s %s" % (p, skill, "READY" if s["ready"] else "NOT READY (missing: %s)" % ", ".join(s["missing"])))
        for k, v in sorted(card["skills"].items()):
            print("  %-46s %s" % (k, "ready" if v["ready"] else "missing " + ", ".join(v["missing"])))
        return 0 if s["ready"] else 1
    print(__doc__); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
