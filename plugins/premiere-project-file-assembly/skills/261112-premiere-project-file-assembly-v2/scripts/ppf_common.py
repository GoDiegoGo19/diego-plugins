#!/usr/bin/env python3
"""ppf_common.py: shared helpers (rates, timecode, ids, versions). Stdlib only."""
import hashlib, json, os, re, shutil, subprocess

VIDEO_EXT = {".mov", ".mp4", ".mxf", ".r3d", ".braw", ".avi", ".mts", ".m2ts", ".mkv"}
AUDIO_EXT = {".wav", ".aif", ".aiff", ".mp3", ".m4a", ".bwf"}
STILL_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".heif", ".dng", ".cr2", ".cr3", ".nef", ".arw"}
GRAPHIC_EXT = {".aep", ".psd", ".ai", ".svg"}
SIDECAR_EXT = {".aae", ".thm", ".xmp"}   # Live Photo edits, thumbnails, metadata: ignored, listed in the report
NO_PROBE_EXT = {".r3d", ".braw"}   # ffprobe cannot read these; duration comes from the scan or stays null


STD_FPS = (23.976, 24.0, 25.0, 29.97, 30.0, 50.0, 59.94, 60.0)
VFR_TOL = 0.005        # measured more than 0.5 % off its conformed rate -> flagged VFR (phone footage)
DOMINANT_TOL = 0.15    # within 15 % of the shoot's dominant standard rate -> conform to it (white paper 261113 Q3)
HFR_FPS = (100.0, 119.88, 120.0, 239.76, 240.0)   # slo-mo: legal CLIP rates, never sequence rates (G8)
HFR_MIN = 90.0


def clip_rate(fps):
    """Slo-mo clip rate (G8): measured >= 90 fps -> nearest of HFR_FPS; the clip keeps it as its file rate."""
    f = float(fps or 0)
    return min(HFR_FPS, key=lambda r: abs(r - f)) if f >= HFR_MIN else None


def seq_fps_of(c, dominant=None):
    """The rate a clip votes for when a SEQUENCE format is picked: conformed standard rate; a slo-mo
    clip votes for the day's dominant rate (29.97 when the day has none), never 60 or 120 (G8)."""
    f = c.get("fps_measured") or c["fps"]
    if c.get("slomo") or clip_rate(f):
        return dominant if dominant in STD_FPS else 29.97
    return conform_fps(f, dominant)[0]


def conform_fps(fps, dominant=None):
    """Measured fps (iPhone VFR: 28.945, 26.48, 29.976 ...) -> (fps_std, vfr, fps_measured).

    fps_std is always one of STD_FPS, so nothing raw reaches prwrite's Rate (whole ticks per frame)
    or the xmeml timebase. With `dominant` (the shoot day's most common standard rate), a measured
    rate within 15 % of it conforms to it; otherwise to the nearest standard rate. vfr is True when
    the measured rate is more than 0.5 % off fps_std. The clip is flagged, never transcoded."""
    f = float(fps or 0)
    if f <= 0:
        return 23.976, False, f
    d = float(dominant) if dominant else None
    if d in STD_FPS and abs(f - d) / d <= DOMINANT_TOL:
        std = d
    else:
        std = min(STD_FPS, key=lambda r: abs(r - f))
    return std, abs(f - std) / std > VFR_TOL, f


def day_dominant_fps(clips):
    """{day: most common standard rate} over clips with an fps, each conformed without a dominant.
    Ties go to the first standard rate in STD_FPS order."""
    from collections import Counter
    by_day = {}
    for c in clips:
        if c.get("fps") and not c.get("slomo"):
            by_day.setdefault(c.get("day"), Counter())[conform_fps(c.get("fps_measured") or c["fps"])[0]] += 1
    return {d: sorted(n.items(), key=lambda kv: (-kv[1], STD_FPS.index(kv[0])))[0][0] for d, n in by_day.items()}


def conform_clips(clips):
    """Annotate clip objects in place: fps -> conformed standard rate, fps_measured, vfr.
    Idempotent (re-reads fps_measured). Returns ({day: dominant}, [vfr clip ids])."""
    for c in clips:                       # slo-mo keeps its measured rate as the clip rate (G8)
        if c.get("fps") and clip_rate(c.get("fps_measured") or c["fps"]):
            meas = float(c.get("fps_measured") or c["fps"])
            c["fps"], c["vfr"], c["fps_measured"], c["slomo"] = clip_rate(meas), False, meas, True
    dom = day_dominant_fps(clips)
    vfr = []
    for c in clips:
        if not c.get("fps") or c.get("slomo"):
            continue
        std, v, meas = conform_fps(c.get("fps_measured") or c["fps"], dom.get(c.get("day")))
        c["fps"], c["vfr"], c["fps_measured"] = std, v, meas
        if v:
            vfr.append(c["clip_id"])
    return dom, vfr


def rate_of(fps, seq=False):
    """fps float -> (timebase int, ntsc bool) the way FCP7 xmeml writes it. Conformed first (G2):
    28.945 -> 29.97 -> (30, True), never timebase 29. A clip rate >= 90 (slo-mo) stays native
    (120 -> (120, False), 119.88 -> (120, True)); seq=True always conforms (G8)."""
    f = (not seq and clip_rate(fps)) or conform_fps(fps or 25)[0]
    for nom in (24, 30, 60, 48, 120, 240):
        if abs(f - nom * 1000 / 1001) < 0.01:
            return nom, True
    return int(round(f)), False


def real_fps(timebase, ntsc):
    return timebase * 1000 / 1001 if ntsc else float(timebase)


def tc_to_frames(tc, timebase):
    """'HH:MM:SS:FF' or 'HH:MM:SS;FF' -> frame count at the nominal timebase (non-drop count)."""
    m = re.match(r"^(\d+):(\d+):(\d+)[:;.](\d+)$", (tc or "").strip())
    if not m:
        return None
    h, mi, s, f = (int(x) for x in m.groups())
    return ((h * 60 + mi) * 60 + s) * timebase + f


def frames_to_tc(frames, timebase):
    f = frames % timebase; s = frames // timebase
    return "%02d:%02d:%02d:%02d" % (s // 3600, (s // 60) % 60, s % 60, f)


def short_id(text, n=10):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]


def have(tool):
    return shutil.which(tool) is not None


def ffprobe(path):
    """Metadata only (never decodes audio or picture). Returns {} when ffprobe is missing or fails."""
    if not have("ffprobe"):
        return {}
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                              "format=duration:format_tags:"
                              "stream=codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,channels,pix_fmt,"
                              "color_transfer,color_primaries,color_space:stream_tags:stream_side_data_list",
                              "-of", "json", path], capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout or "{}")
    except Exception:
        return {}


def load_json(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def save_json(p, obj):
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, p)


def next_version_path(out_dir, project, ext):
    """<project>_v<NNN><ext>, the first number not taken by ANY ext of this project. Never overwrites."""
    os.makedirs(out_dir, exist_ok=True)
    pat = re.compile(r"^%s_v(\d{3})\." % re.escape(project))
    taken = [int(m.group(1)) for n in os.listdir(out_dir) for m in [pat.match(n)] if m]
    n = max(taken) + 1 if taken else 1
    return os.path.join(out_dir, "%s_v%03d%s" % (project, n, ext)), n


PPF_HOME = os.path.expanduser("~/.ppf")
PPF_VENV = os.path.join(PPF_HOME, "venv")


def venv_python():
    p = os.path.join(PPF_VENV, "bin", "python")
    return p if os.path.exists(p) else None


def maybe_reexec():
    """Run under ~/.ppf/venv/bin/python when it exists (ppf_setup.py makes it); else stay put.
    PPF_NO_REEXEC=1 disables. Only called from __main__ blocks, never on import."""
    import sys
    vp = venv_python()
    if not vp or os.environ.get("PPF_NO_REEXEC") == "1":
        return
    if os.path.realpath(sys.prefix) == os.path.realpath(PPF_VENV):
        return
    os.environ["PPF_NO_REEXEC"] = "1"             # one hop only
    os.execv(vp, [vp] + sys.argv)
