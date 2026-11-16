#!/usr/bin/env python3
"""watch_setup.py - what the SEE pass needs on this machine, and the guided install (watch-video plugin 1.1.0).

Mirrors the scan plugin's install (rules.json -> vision.packages; p3_see_models.py install): Python 3.12, onnxruntime
pinned per machine (Intel Mac 1.23.2, Apple Silicon 1.31.1, Linux 1.23.2), the `all` list, then the CLIP weights.
DINOv2-small, YOLOX-S, MiniLM and YuNet ship INSIDE this skill (models/), so the quick SEE works with no download.

    python3 watch_setup.py check            # read-only: python, onnxruntime, every weight's sha256 (bundled + the
                                            # downloaded reader), whether the reader's graphs actually open, ffprobe, cv2
    python3 watch_setup.py ensure           # fetch what is missing (free, pinned, sha256-checked) WITHOUT asking
    python3 watch_setup.py install [--yes]  # prints the plan, ASKS, then pip-installs the pins and CLIP (core group)
Exit 0 ok · 1 something missing (check) or an install step failed · 2 usage.
"""
import argparse
import hashlib
import importlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
PLUGIN = SKILL.parent.parent
BUNDLED = ("dino_small", "yolox_s", "minilm", "yunet")

# The reader (the vision lane of the read-shots skill). Not bundled: it is downloaded once, pinned by revision and
# verified here by sha256. Pinned 261114 on Diego's Intel iMac Pro, then confirmed byte-identical against a fresh
# download in a Linux container, so these hashes identify the files themselves, not one machine's copy.
# The int8 vision encoder is listed and NOT required: it is signed-int8 ConvInteger, which the onnxruntime CPU
# provider has no kernel for (261114 probe 1). The uint8 one is what loads.
READER_REV = "d039ab0d4b9311b617e8ffec1d06c8464364aeb3"
READER_REPO = "HuggingFaceTB/SmolVLM-256M-Instruct"
READER = (
    ("config.json", "b70fb4bfde88df9eeebc9d8ff523733b8bf70d6c9b06c610325960e06ae9db52", True),
    ("tokenizer.json", "5ece781dc8d2b2f3e2f289ca0ae50b17cfc27dd27bfe7971bb8241e0b964331a", True),
    ("onnx/vision_encoder_uint8.onnx", "f82adc84246fbfe8651038470166385452682cc889538f5106544e622e5f1595", True),
    ("onnx/embed_tokens_int8.onnx", "4b919b829fe2bf225e42431230c56891116f0149e5170e36c99bcc0cfae0d2ef", True),
    ("onnx/decoder_model_merged_int8.onnx", "306fad6cec3b5b7613600b74abf1e410fa50000fbc81292c0d083bd8d85b5fe2", True),
    ("onnx/vision_encoder_int8.onnx", "d534ab668d3563e6d1d809bbbf1ff18308ded48651178614dbcc4a1de13d16f7", False),
)


def reader_dir():
    """Where the reader lives, in the order read_lane.py looks for it."""
    import os
    for d in (PLUGIN / "models" / "smolvlm", Path.home() / ".scan-and-index" / "models" / "smolvlm",
              Path(os.environ.get("WATCH_MODELS_DIR", "/nonexistent")) / "smolvlm"):
        if (d / "config.json").exists():
            return d
    return None


def check_reader():
    """Every reader file by sha256, then whether onnxruntime can really open the three graphs. Returns pieces missing."""
    d = reader_dir()
    if d is None:
        print("reader      %-11s %-40s %s" % ("smolvlm", "(not installed)",
                                              "optional: read-shots runs without it, every read slot stays unresolved"))
        return 0
    print("reader      %-11s %-40s %s" % ("smolvlm", "dir", d))
    bad = 0
    for rel, want, required in READER:
        f = d / rel
        if not f.exists():
            print("reader      %-11s %-40s %s" % ("smolvlm", rel, "MISSING" if required else "not downloaded (not needed)"))
            bad += required
            continue
        got = sha256(f)
        ok = got == want
        print("reader      %-11s %-40s %s %s" % ("smolvlm", rel, got[:16],
                                                 "ok" if ok else "SHA256 MISMATCH (expected %s)" % want[:16]))
        bad += not ok and required
    try:  # the only test that counts: can this machine actually build the sessions?
        sys.path.insert(0, str(PLUGIN / "skills" / "261114-read-shots-like-a-filmmaker-v1" / "scripts"))
        import importlib.util
        sp = importlib.util.spec_from_file_location(
            "rl", PLUGIN / "skills" / "261114-read-shots-like-a-filmmaker-v1" / "scripts" / "read_lane.py")
        rl = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(rl)
        m, why = rl.load_model(str(d))
        if m is None:
            print("reader      %-11s %-40s %s" % ("smolvlm", "load", "FAILED: %s" % why[:90]))
            bad += 1
        else:
            print("reader      %-11s %-40s %s" % ("smolvlm", "load", "ok, loaded " + ", ".join(sorted(m.loaded.values()))))
    except Exception as e:  # noqa: BLE001  a check never raises
        print("reader      %-11s %-40s %s" % ("smolvlm", "load", "could not test: %s" % str(e)[:80]))
    return bad


def vision():
    return json.load(open(SKILL / "rules.json"))["vision"]


def machine():
    if sys.platform == "darwin":
        return "mac_silicon" if platform.machine() == "arm64" else "mac_intel"
    return "linux_box"


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def model_path(key, f):
    """The installed copy (scan's models dir, ~/.cache/scan-and-index-models) first, then this skill's models/."""
    sys.path.insert(0, str(HERE))
    try:
        import p0_env
        return p0_env.model_file(key, f["file"], f["bytes"])
    except Exception:
        p = SKILL / "models" / key / f["file"]
        return p if p.is_file() else None


def mod(name):
    try:
        m = importlib.import_module(name)
        return getattr(m, "__version__", "yes")
    except Exception:
        return None


def check():
    v, bad = vision(), 0
    want_py = v["packages"]["python"]
    py = "%d.%d" % sys.version_info[:2]
    print("python      %s%s" % (py, "" if py == want_py else "   (the pinned environment is %s; others usually work)" % want_py))
    ort = mod("onnxruntime")
    pin = (v["packages"].get(machine()) or ["?"])[0]
    print("onnxruntime %s   (pin for %s: %s)" % (ort or "MISSING", machine(), pin))
    bad += ort is None
    for k in BUNDLED + ("clip",):
        for f in v["models"][k]["files"]:
            p = model_path(k, f)
            if p is None:
                print("model       %-11s %-40s %s" % (k, f["file"], "not installed (CLIP: python3 scripts/p3_see_models.py install core)"
                                                       if k == "clip" else "MISSING"))
                bad += k != "clip"
                continue
            got = sha256(p)
            ok = got == f["sha256"]
            print("model       %-11s %-40s %s %s" % (k, f["file"], got[:16], "ok" if ok else "SHA256 MISMATCH"))
            bad += not ok
    bad += check_reader()
    for tool in ("ffprobe", "ffmpeg", "tesseract"):
        print("%-11s %s" % (tool, shutil.which(tool) or "MISSING"))
        bad += tool != "tesseract" and not shutil.which(tool)
    for m in ("cv2", "av", "scenedetect", "ruptures", "tokenizers", "numpy", "weasyprint"):
        got = mod(m)
        print("%-11s %s" % (m, got or "MISSING"))
        bad += got is None and m != "weasyprint"
    print("result      %s" % ("ready for the SEE pass" if not bad else "%d piece(s) missing: run watch_setup.py install" % bad))
    return 0 if not bad else 1


# ---------------------------------------------------------------- provisioning that does not ask
# Diego's rule, 261116: "things have to just work no matter what the instance." A fresh install has the
# bundled weights (they ride in the package) but not CLIP or the reader, and the old behaviour was to print
# a command for a human to go and run. On a new machine, or a cloud session, or a friend's laptop, that is a
# broken skill. So the skill fetches them itself, with three limits that stay:
#   - FREE and PINNED only: an exact revision, a sha256 checked after the download, deleted on mismatch.
#   - into the PLUGIN'S OWN folder. Nothing system-wide, no sudo, no apt, no brew, no pip.
#   - never fatal. No network, or a refused host, means it says what is reduced and carries on.
# Anything paid, or anything that touches the machine outside this folder, still asks. That line does not move.
READER_FILES = [rel for rel, _sha, required in READER if required]


def _get(url, dest, want_sha, label):
    """One pinned file, verified. Returns True when dest ends up correct."""
    import urllib.request
    if dest.exists() and sha256(dest) == want_sha:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        print("   fetching %s" % label, flush=True)
        with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
    except Exception as e:  # noqa: BLE001  no network is a reduced run, never a crash
        tmp.unlink(missing_ok=True)
        print("   could not fetch %s: %s" % (label, str(e)[:90]))
        return False
    got = sha256(tmp)
    if got != want_sha:
        tmp.unlink(missing_ok=True)
        print("   %s FAILED sha256 (got %s, want %s): deleted, not used" % (label, got[:16], want_sha[:16]))
        return False
    tmp.replace(dest)
    return True


def ensure_reader(download=True):
    """(ok, note) for the vision reader. Downloads the pinned SmolVLM ONNX parts into the plugin if missing."""
    d = reader_dir() or (PLUGIN / "models" / "smolvlm")
    missing = [rel for rel in READER_FILES if not (d / rel).exists()]
    if not missing:
        return True, "reader present"
    if not download:
        return False, "reader missing (%d file(s)); read slots stay unresolved" % len(missing)
    print("reader: %d of %d pinned file(s) missing, fetching (~260 MB, revision %s)"
          % (len(missing), len(READER_FILES), READER_REV[:12]))
    base = "https://huggingface.co/%s/resolve/%s/" % (READER_REPO, READER_REV)
    shas = {rel: sha for rel, sha, _r in READER}
    got = all(_get(base + rel, d / rel, shas[rel], rel) for rel in missing)
    return (got, "reader installed at %s" % d) if got else (False, "reader incomplete; read slots stay unresolved")


def ensure_clip(download=True):
    """(ok, note) for CLIP. Delegates to the engine's own pinned installer, which both skills share."""
    v = vision()
    if all(model_path("clip", f) is not None for f in v["models"]["clip"]["files"]):
        return True, "CLIP present"
    if not download:
        return False, "CLIP missing; tags are skipped"
    print("CLIP: fetching the pinned weights (~156 MB)")
    r = subprocess.run([sys.executable, str(HERE / "p3_see_models.py"), "install", "core"])
    ok = r.returncode == 0 and all(model_path("clip", f) is not None for f in v["models"]["clip"]["files"])
    return (ok, "CLIP installed") if ok else (False, "CLIP not installed; tags are skipped")


def ensure(download=True, quiet=False):
    """Make this machine ready without asking. Returns a list of notes about anything still reduced."""
    notes = []
    for fn in (ensure_clip, ensure_reader):
        ok, note = fn(download)
        if not quiet:
            print("%s %s" % ("ok  " if ok else "note", note))
        if not ok:
            notes.append(note)
    for tool, what in (("tesseract", "on-screen text is skipped"), ("weasyprint", "the PDF sheet is skipped")):
        if not (shutil.which(tool) or mod(tool)):
            # A system package needs the machine's package manager, so this one still only reports.
            notes.append("%s not installed, so %s (Mac: brew install %s)"
                         % (tool, what, "tesseract" if tool == "tesseract" else "weasyprint"))
    return notes


def install(yes):
    v = vision()
    pins = list(v["packages"].get(machine()) or []) + list(v["packages"]["all"])
    py = v["packages"]["python"]
    print("Plan (%s):" % machine())
    if "%d.%d" % sys.version_info[:2] != py:
        print("  - Python %s is the pinned environment (this is %d.%d). On a Mac: brew install python@%s, then rerun with it."
              % (py, sys.version_info[0], sys.version_info[1], py))
    print("  - pip install %s" % " ".join(pins))
    print("  - CLIP weights (~156 MB, pinned revision, sha256-checked): p3_see_models.py install core")
    if not yes:
        try:
            if input("Install these now? [y/N] ").strip().lower() not in ("y", "yes"):
                print("Nothing installed.")
                return 0
        except EOFError:
            print("No answer; nothing installed (use --yes to skip the question).")
            return 0
    r = subprocess.run([sys.executable, "-m", "pip", "install"] + pins)
    if r.returncode:
        print("pip failed (%d)" % r.returncode)
        return 1
    r = subprocess.run([sys.executable, str(HERE / "p3_see_models.py"), "install", "core"])
    return 1 if r.returncode else check()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "install", "ensure"])
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--no-download", action="store_true", help="ensure: report what is missing, fetch nothing")
    a = ap.parse_args()
    if a.cmd == "check":
        return check()
    if a.cmd == "ensure":
        notes = ensure(download=not a.no_download)
        print("result      %s" % ("ready, nothing reduced" if not notes else "running reduced: " + "; ".join(notes)))
        return 0  # ensure NEVER fails a run: a reduced machine still works, it just does less
    return install(a.yes)


if __name__ == "__main__":
    sys.exit(main())
