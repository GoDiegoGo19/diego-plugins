#!/usr/bin/env python3
"""p3b_driver_red.py · RED (.R3D) clips through RED's own free command-line decoder, REDline.

ffmpeg cannot decode REDCODE, so RED clips need RED's software. REDline comes with REDCINE-X PRO:
  Mac:   brew install --cask redcine-x-pro   (REDline at /Applications/REDCINE-X Professional/
         REDCINE-X PRO.app/Contents/MacOS/REDline)
  RED stills run on the Mac (findings F8). REDLINE=/path still points at any REDline binary you already have.
A contact sheet needs only stills, and REDCODE is intraframe (every frame stands alone), so one small
still per keyframe is cheap.

This script never guesses REDline's options. It reads REDline's own --help, checks that every flag in
the command templates (rules.json -> red) is listed there, and finds the JPEG (or TIFF) format number
from the help text. Change the templates in rules.json, never here.

  python3 p3b_driver_red.py selftest                       # where REDline is, its version, the options found
  python3 p3b_driver_red.py meta  CLIP_001.R3D             # frames, fps, duration, resolution, timecode
  python3 p3b_driver_red.py still CLIP_001.R3D --t 4.2 --out frame.jpg [--fps 23.976]

Every function returns (result, error); nothing raises on a missing or odd REDline. Python 3.9+.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAC_APP = "/Applications/REDCINE-X Professional/REDCINE-X PRO.app/Contents/MacOS/REDline"
OTHER_PLACES = ["/usr/local/bin/REDline", "/opt/REDline/REDline", "/opt/redline/REDline",
                os.path.expanduser("~/REDline/REDline"), os.path.expanduser("~/bin/REDline")]
DEFAULT_RED = {
    "_note": "REDline command templates. Every --flag must appear in `REDline --help`; selftest checks. "
             "{clip} {dir} {name} {format} {frame} {res} are filled in. res: 1 full, 2 half, 4 quarter, 8 eighth "
             "(check the help text on first run).",
    "meta_args": ["--i", "{clip}", "--printMeta", "1"],
    "still_args": ["--i", "{clip}", "--outDir", "{dir}", "--o", "{name}", "--format", "{format}",
                   "--start", "{frame}", "--end", "{frame}", "--res", "{res}"],
    "res": 4,
    "timeout_s": 300,
    "frame_width": 640,
}
_HELP = {}


def rules_red(rules=None):
    r = dict(DEFAULT_RED)
    if rules is None:
        try:
            rules = json.loads((HERE.parent / "rules.json").read_text())
        except (OSError, ValueError):
            rules = {}
    r.update((rules or {}).get("red") or {})
    return r


def _tmp_root():
    """A3: temp outside home, from the environment profile (p0_env.py)."""
    try:
        sys.path.insert(0, str(HERE))
        import p0_env
        return p0_env.tmp_root()
    except Exception:
        return None


def find_redline():
    """Path to REDline, or None. REDLINE env first, then PATH, then the Mac app, then common places."""
    for c in [os.environ.get("REDLINE"), shutil.which("REDline"), shutil.which("redline"), MAC_APP] + OTHER_PLACES:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def help_text(exe):
    """REDline's own option list (--help, else no arguments), cached per executable."""
    if exe in _HELP:
        return _HELP[exe]
    text = ""
    for args in (["--help"], []):
        try:
            r = subprocess.run([exe] + args, capture_output=True, text=True, timeout=60)
            text = (r.stdout or "") + "\n" + (r.stderr or "")
        except (OSError, subprocess.TimeoutExpired) as e:
            text = "ERROR: %s" % e
        if "--" in text:
            break
    _HELP[exe] = text
    return text


def format_code(help_txt, want=("JPEG", "JPG")):
    """The number REDline's help gives a format, e.g. '3 = JPEG' or 'JPEG = 3' or '3: JPEG'."""
    for w in want:
        for pat in (r"(\d+)\s*[=:\-]\s*%s\b" % w, r"\b%s\s*[=:\-]\s*(\d+)" % w, r"(\d+)\s*\)\s*%s\b" % w):
            m = re.search(pat, help_txt, re.I)
            if m:
                return int(m.group(1))
    return None


def flags_in(args):
    return [a for a in args if a.startswith("--")]


def missing_flags(help_txt, args):
    return [f for f in flags_in(args) if not re.search(r"(^|\s)%s\b" % re.escape(f), help_txt)]


def version_of(help_txt):
    m = re.search(r"(?:REDline|Build|Version)[^\n\d]*(\d+(?:\.\d+)+)", help_txt, re.I)
    return m.group(1) if m else None


def selftest(rules=None):
    """Everything a person needs to know before RED stills are trusted."""
    rr = rules_red(rules)
    exe = find_redline()
    if not exe:
        return {"ok": False, "redline": None,
                "why": "REDline not found: on the Mac run brew install --cask redcine-x-pro (RED stills run on the Mac)"}, "REDline not found"
    h = help_text(exe)
    jpeg, tiff = format_code(h), format_code(h, ("TIFF", "TIF"))
    miss = sorted(set(missing_flags(h, rr["meta_args"]) + missing_flags(h, rr["still_args"])))
    ok = not miss and (jpeg is not None or tiff is not None)
    why = "" if ok else ("flags not in REDline's help: %s" % ", ".join(miss) if miss else "no JPEG or TIFF format number in REDline's help")
    return {"ok": ok, "redline": exe, "version": version_of(h), "jpeg_format": jpeg, "tiff_format": tiff,
            "missing_flags": miss, "why": why, "help_chars": len(h)}, (None if ok else why)


def _num(pattern, text, cast=float):
    m = re.search(pattern, text, re.I | re.M)
    if not m:
        return None
    try:
        return cast(m.group(1))
    except ValueError:
        return None


def parse_meta(text):
    """Pull the facts a ledger needs out of REDline's metadata printout (labels vary by version)."""
    frames = _num(r"^\s*(?:Total\s*Frames|Frame\s*Count|Number\s*of\s*Frames|Frames)\s*[:=]\s*(\d+)", text, int)
    fps = _num(r"^\s*(?:Record\s*FPS|Frame\s*Rate|Video\s*FPS|FPS|Timebase)\s*[:=]\s*([\d.]+)", text)
    w = _num(r"^\s*(?:Frame\s*Width|Image\s*Width|Width)\s*[:=]\s*(\d+)", text, int)
    h = _num(r"^\s*(?:Frame\s*Height|Image\s*Height|Height)\s*[:=]\s*(\d+)", text, int)
    if not (w and h):
        m = re.search(r"(\d{3,5})\s*x\s*(\d{3,5})", text)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
    tc = re.search(r"^\s*(?:Abs(?:olute)?\s*(?:Start\s*)?TC|Start\s*(?:Abs\s*)?Timecode|Timecode(?:\s*In)?|Abs\s*Timecode)"
                   r"\s*[:=]\s*(\d\d:\d\d:\d\d[:;.]\d\d)", text, re.I | re.M)
    out = {"frames": frames, "fps": fps, "width": w, "height": h,
           "start_timecode": tc.group(1) if tc else None, "video_codec": "REDCODE"}
    out["duration"] = round(frames / fps, 3) if frames and fps else None
    return out


def meta(clip, rules=None):
    rr = rules_red(rules)
    exe = find_redline()
    if not exe:
        return None, "needs RED decoder (REDline not installed; run p0_setup_check.py)"
    h = help_text(exe)
    miss = missing_flags(h, rr["meta_args"])
    if miss:
        return None, "REDline has no %s (check rules.json -> red.meta_args against `REDline --help`)" % ", ".join(miss)
    args = [a.replace("{clip}", str(clip)) for a in rr["meta_args"]]
    try:
        r = subprocess.run([exe] + args, capture_output=True, text=True, timeout=int(rr["timeout_s"]))
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, "REDline metadata failed: %s" % e
    m = parse_meta((r.stdout or "") + "\n" + (r.stderr or ""))
    if not m["duration"]:
        return None, "REDline printed no frame count / fps (exit %d)" % r.returncode
    return m, None


COLOR_USED = {}   # clip -> "IPP2 LUT" / "as shot" / "as shot (LUT missing)", read by callers after still()


def color_recipe(rules=None):
    """The red color block as a short stable string (frame names hash it: a rule change re-pulls RED frames)."""
    return json.dumps(rules_red(rules).get("color") or {}, sort_keys=True)


def meta_text(clip, exe, rr):
    try:
        args = [a.replace("{clip}", str(clip)) for a in rr["meta_args"]]
        r = subprocess.run([exe] + args, capture_output=True, text=True, timeout=int(rr["timeout_s"]))
        return (r.stdout or "") + "\n" + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        return ""


def color_args(clip, exe, help_txt, rr):
    """(extra REDline args, label). IPP2/Log3G10 -> LUT args; Legacy/REDgamma/unknown -> as shot.
    Additive: skipped when the LUT file is missing or a color flag is not in REDline's help."""
    col = rr.get("color") or {}
    if not col.get("ipp2_log3g10"):
        return [], "as shot"
    lut = col.get("lut_path") or ""
    if not lut or not os.path.isfile(lut):
        return [], "as shot (LUT missing)"
    txt = meta_text(clip, exe, rr)
    if not txt.strip():
        return [], "as shot (meta unknown)"
    kv = {}                                           # "Key: value" lines, keys lowercased (review 261110, blocker 1)
    for line in txt.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            kv[k.strip().lower()] = v.strip()
    gamma = next((v for k, v in kv.items() if "gamma" in k), "")
    bypass = next((v for k, v in kv.items() if "bypass" in k and "ipp2" in k), "")
    if re.search(r"log3g10", gamma, re.I) or gamma.strip() == "34" or bypass.lower() in ("true", "1", "yes"):
        pass                                          # IPP2 / Log3G10: the LUT applies
    elif re.search(r"redgamma", gamma, re.I) or gamma.strip() == "30" or re.search(r"legacy", txt, re.I):
        return list(col.get("legacy") or []), "as shot (Legacy)"
    else:
        return [], "as shot (meta unknown)"
    args = [a.replace("{lut}", lut) for a in col["ipp2_log3g10"]]
    if missing_flags(help_txt, args):
        return [], "as shot (REDline lacks color flags)"
    return args, "IPP2 LUT"


def still(clip, t, out_jpg, fps, rules=None):
    """One small JPG at t seconds (frame = round(t * fps)). (True, None) or (False, why)."""
    rr = rules_red(rules)
    exe = find_redline()
    if not exe:
        return False, "needs RED decoder (REDline not installed; run p0_setup_check.py)"
    if not fps:
        return False, "no frame rate for this RED clip"
    h = help_text(exe)
    miss = missing_flags(h, rr["still_args"])
    if miss:
        return False, "REDline has no %s (check rules.json -> red.still_args against `REDline --help`)" % ", ".join(miss)
    code, ext_conv = format_code(h), False
    if code is None:
        code, ext_conv = format_code(h, ("TIFF", "TIF")), True
    if code is None:
        return False, "no JPEG or TIFF format number in REDline's help"
    frame = max(0, int(round(float(t) * float(fps))))
    tmp = tempfile.mkdtemp(prefix="redline_", dir=_tmp_root())
    try:
        fill = {"{clip}": str(clip), "{dir}": tmp, "{name}": "still", "{format}": str(code),
                "{frame}": str(frame), "{res}": str(rr["res"])}
        args = []
        for a in rr["still_args"]:
            for k, v in fill.items():
                a = a.replace(k, v)
            args.append(a)
        extra, COLOR_USED[str(clip)] = color_args(clip, exe, h, rr)
        args += extra
        try:
            r = subprocess.run([exe] + args, capture_output=True, text=True, timeout=int(rr["timeout_s"]))
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, "REDline still failed: %s" % e
        imgs = sorted(glob.glob(os.path.join(tmp, "**", "*.*"), recursive=True))
        imgs = [p for p in imgs if p.lower().endswith((".jpg", ".jpeg", ".tif", ".tiff"))]
        if not imgs:
            last = ((r.stderr or r.stdout or "").strip().splitlines() or ["exit %d" % r.returncode])[-1]
            return False, "REDline wrote no image: %s" % last[:160]
        out = Path(out_jpg)
        out.parent.mkdir(parents=True, exist_ok=True)
        c = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", imgs[0], "-vf",
                            "scale=%d:-2" % int(rr["frame_width"]), "-q:v", "3", "-y", str(out)],
                           capture_output=True, text=True, timeout=120)
        if c.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
            return False, "could not convert REDline's still: %s" % (c.stderr.strip()[-160:] or "ffmpeg failed")
        return True, None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("selftest", "meta", "still"):
        print(__doc__)
        return 2
    if argv[0] == "selftest":
        rep, err = selftest()
        print(json.dumps(rep, indent=1))
        return 0 if not err else 1
    if len(argv) < 2:
        print(__doc__)
        return 2
    if argv[0] == "meta":
        m, err = meta(argv[1])
        print(json.dumps(m if m else {"error": err}, indent=1))
        return 0 if m else 1
    opt = lambda k, d=None: argv[argv.index(k) + 1] if k in argv and argv.index(k) + 1 < len(argv) else d
    fps = opt("--fps")
    if fps is None:
        m, err = meta(argv[1])
        if not m:
            print(json.dumps({"error": err}))
            return 1
        fps = m["fps"]
    ok, err = still(argv[1], float(opt("--t", "0")), opt("--out", "still.jpg"), float(fps))
    print(json.dumps({"ok": ok, "out": opt("--out", "still.jpg")} if ok else {"error": err}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
