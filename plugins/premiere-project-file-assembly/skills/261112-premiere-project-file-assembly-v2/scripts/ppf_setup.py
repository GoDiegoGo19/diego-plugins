#!/usr/bin/env python3
"""ppf_setup.py: one-time setup for the small model inside this plugin (MiniLM, 23 MB, bundled).

  ppf_setup.py [install] [--yes]   make ~/.ppf/venv with this python3 and install ONLY
                                   onnxruntime + numpy (pins below); asks y/n first unless --yes
  ppf_setup.py check               python version, onnxruntime import, model files + sha256,
                                   ffprobe; read-only

Pins come from the scan plugin's lock (scan-and-index-hard-drives rules.json vision.packages):
onnxruntime==1.23.2 (Intel Mac, Linux; 1.31.1 on Apple Silicon, as the scan lock says) and
numpy==2.5.4. pip runs with --only-binary=:all:. Nothing else is installed. Without the venv
everything still runs: ppf.py uses the current python and falls back to TF-IDF if
onnxruntime/numpy are missing.
"""
import argparse, hashlib, os, platform, shutil, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ppf_common import PPF_VENV, venv_python
import ppf_match

ORT = "onnxruntime==1.31.1" if (sys.platform == "darwin" and platform.machine() == "arm64") else "onnxruntime==1.23.2"
PINS = [ORT, "numpy==2.5.4"]
MODEL_SHA = {"onnx/model_quantized.onnx": "afdb6f1a0e45b715d0bb9b11772f032c399babd23bfc31fed1c170afc848bdb1",
             "tokenizer.json": "da0e79933b9ed51798a3ae27893d3c5fa4a201126cef75586296df9b4d2c62a0"}


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


MIN_PY = (3, 9)


def python_line(v=None):
    """(ok, one line saying what to do about this interpreter). The version is not optional knowledge:
    261115, a friend's run hit macOS's default python3 3.7, pip found no onnxruntime wheel for it, and the
    failure surfaced far from its cause."""
    v = v or sys.version_info[:3]
    if tuple(v[:2]) >= MIN_PY:
        return True, ""
    return False, ("python %d.%d is too old for this plugin (needs %d.%d+; macOS ships 3.7 by default).\n"
                   "    brew install python@3.12   then rerun with python3.12, or:\n"
                   "    /opt/homebrew/bin/python3.12 -m venv ~/.ppf/venv  (Intel: /usr/local/bin/python3.12)"
                   % (v[0], v[1], MIN_PY[0], MIN_PY[1]))


def install(yes=False):
    py = shutil.which("python3") or sys.executable
    pv = subprocess.run([py, "-c", "import sys;print('%d %d %d' % sys.version_info[:3])"],
                        capture_output=True, text=True).stdout.split()
    ok, why = python_line([int(x) for x in pv] if len(pv) == 3 else None)
    if not ok:
        print("ppf setup: " + why, file=sys.stderr)
        return 2
    print("ppf setup will:")
    print("  1. create %s with %s (%s)" % (PPF_VENV, py, platform.python_version()))
    print("  2. pip install --only-binary=:all: %s" % " ".join(PINS))
    print("  nothing else; the model itself is already inside the plugin (models/minilm)")
    if not yes:
        # 261115: run from a script with nobody at the keyboard, this blocked on input() forever. A prompt
        # with no one to answer it is not a prompt; say what to pass and stop.
        if not sys.stdin.isatty():
            print("setup: nothing done. Nobody is at the keyboard (stdin is not a terminal), so there is no\n"
                  "       one to answer the question. Rerun as:  ppf_setup.py install --yes", file=sys.stderr)
            return 2
        if input("go ahead? [y/N] ").strip().lower() not in ("y", "yes"):
            print("setup: nothing done"); return 1
    if not venv_python():
        subprocess.run([py, "-m", "venv", PPF_VENV], check=True)
    vp = venv_python()
    subprocess.run([vp, "-m", "pip", "install", "--only-binary=:all:"] + PINS, check=True)
    print("setup: done; run `ppf_setup.py check`")
    return 0


def check():
    ok = True
    vp = venv_python()
    py = vp or sys.executable
    v = subprocess.run([py, "-c", "import sys;print(sys.version.split()[0])"], capture_output=True, text=True).stdout.strip()
    pok, pwhy = python_line([int(x) for x in v.split(".")] if v.count(".") == 2 else None)
    print("python      %s  %s%s" % (v, py, "" if vp else "  (no ~/.ppf/venv: current python; run ppf_setup.py to make one)"))
    if not pok:
        ok = False
        print("            TOO OLD. " + pwhy.replace("\n", "\n            "))
    r = subprocess.run([py, "-c", "import onnxruntime, numpy;print(onnxruntime.__version__, numpy.__version__)"], capture_output=True, text=True)
    if r.returncode == 0:
        o, n = r.stdout.split()
        print("onnxruntime %s  numpy %s  OK%s" % (o, n, "" if o in ORT else "  (pin is %s)" % ORT))
    else:
        ok = False; print("onnxruntime NOT importable -> organize/match fall back to TF-IDF")
    md = ppf_match.find_model()
    if not md:
        ok = False; print("model       MISSING (looked in %s)" % ", ".join(ppf_match.MODEL_DIRS))
    else:
        print("model       %s" % md)
        for f, want in MODEL_SHA.items():
            p = os.path.join(md, f)
            got = sha256(p) if os.path.isfile(p) else None
            good = got == want; ok &= good
            print("  %-26s %s" % (f, "sha256 OK" if good else "sha256 MISMATCH (%s)" % got))
    ff = shutil.which("ffprobe")
    print("ffprobe     %s" % (ff or "not found (optional; folder ingest needs it for durations)"))
    print("check: %s" % ("OK, organize engine = minilm" if ok else "NOT READY (see lines above)"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="install", choices=["install", "check"])
    ap.add_argument("--yes", action="store_true", help="do not ask y/n")
    a = ap.parse_args(argv)
    return install(a.yes) if a.cmd == "install" else check()


if __name__ == "__main__":
    sys.exit(main())
