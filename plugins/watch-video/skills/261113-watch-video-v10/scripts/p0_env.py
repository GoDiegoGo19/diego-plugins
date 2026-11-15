#!/usr/bin/env python3
"""p0_env.py · P0 · the environment, defined once: where we are, what we have, and how things are named.

Every phase script and test imports this instead of guessing paths, temp folders, tools or names.
Each value is resolved ONCE in a fixed order: command-line flag -> environment variable -> profile default.

    python3 p0_env.py            # the "where am I" card
    python3 p0_env.py --json     # the same, as JSON

PROFILES
  mac        macOS (preferred): drives in /Volumes, Homebrew, catalog ~/Downloads/MEDIA_CATALOG
  linux_box  a Linux VM next to the Mac, e.g. Cowork: drives in ~/mnt, the Mac's Downloads at
             ~/mnt/Downloads (a mounted folder), catalog ~/mnt/Downloads/MEDIA_CATALOG
  cloud_dev  a cloud dev session (like Claude Code on the web): no real drives, test media only

NAMING (Diego, 261107): date · drive · task · output · phase-seq · time
  <YYMMDD>_<drive>_<task>_<output>_P<phase>-<seq>_<HHMM>.<ext>
  e.g. 261107_IC_23_ledger_media_P2-01_1434.csv, filed under <catalog>/<drive>/P2_LEDGER/
"""
import datetime as dt
import glob
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS.parent

# ---- phases: one code each, used in script names, folders, file names and LEDGER.csv ----------
PHASES = {
    "P0": ("SETUP", "the environment and the guided installs"),
    "P1": ("SCAN", "snapshot of the drive: folders, labels, files"),
    "P2": ("LEDGER", "facts per file + lane + transcribe? tag"),
    "P3": ("PROCESS", "FFmpeg main line: keyframes, look, analyse, tag"),
    "P3B": ("DRIVER", "side row: RED / BRAW through a maker's driver, then merge"),
    "P4": ("STAGING", "objects: Drive, ShootDay, Card, Clip, Frame; checks; calls"),
    "P5": ("PDF", "the shipment: summary + contact sheets"),
    "P6": ("DATASETS", "the datasets, printed: shots, segments, families (CSV + one readable page)"),
}
PHASE_DIRS = {"P1": "P1_SCAN", "P2": "P2_LEDGER", "P3": "P3_PROCESS", "P3B": "P3_PROCESS", "P4": "P4_STAGING", "P5": "P5_PDF", "P6": "P6_DATASETS"}
RUNS_DIR = "_RUNS"


def _ccr_dir():
    """True when /root/.ccr is a readable dir; a missing or unreadable path is skipped quietly (R12)."""
    try:
        return Path("/root/.ccr").is_dir()
    except OSError:
        return False


def _cloud():
    return bool(os.environ.get("CLAUDE_CODE_REMOTE") or _ccr_dir())


def _mounted(path):
    """True when `path` (or its nearest existing parent) is on a different filesystem than home."""
    p = Path(path).expanduser()
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return os.stat(p).st_dev != os.stat(Path.home()).st_dev
    except OSError:
        return False


def _user():
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER") or ""


def detect_profile():
    if os.environ.get("SCAN_PROFILE") in ("mac", "linux_box", "cloud_dev"):
        return os.environ["SCAN_PROFILE"], "SCAN_PROFILE"
    if sys.platform == "darwin":
        return "mac", "macOS"
    if (Path.home() / "mnt").is_dir():
        return "linux_box", "Linux with drives mounted under ~/mnt"
    if _cloud():
        return "cloud_dev", "Linux cloud session"
    return "linux_box", "Linux"


def default_catalog(profile):
    if profile == "linux_box" and (Path.home() / "mnt" / "Downloads").is_dir():
        return Path.home() / "mnt" / "Downloads" / "MEDIA_CATALOG"
    if profile == "mac":
        return Path.home() / "Documents" / "MEDIA_CATALOG"    # v17: a Mac's catalog lives in Documents, never Downloads
    return Path.home() / "Downloads" / "MEDIA_CATALOG"


CONFIG = Path.home() / ".scan-and-index" / "config.json"


def saved_catalog():
    """The ledger location the user chose at first run (v16 scan menu), or None."""
    try:
        return json.load(open(CONFIG)).get("ledger_home") or None
    except (OSError, ValueError):
        return None


def save_catalog(path):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    try:
        d = json.load(open(CONFIG))
    except (OSError, ValueError):
        d = {}
    d["ledger_home"] = str(path)
    with open(CONFIG, "w") as f:
        json.dump(d, f, indent=1)


def catalog(flag=None):
    """Flag -> CATALOG -> the ledger home chosen at first run -> profile default. The one rule for where outputs go.
    The ledger lives on the WORK drive (SSD, Documents, scratch disk), never on a camera drive."""
    return Path(os.path.expanduser(flag or os.environ.get("CATALOG") or saved_catalog()
                                   or str(default_catalog(detect_profile()[0])))).resolve()


def drive_roots(profile):
    return {"mac": ["/Volumes"], "linux_box": [str(Path.home() / "mnt"), "/mnt", "/media"],
            "cloud_dev": []}.get(profile, [])


def tmp_root():
    """A temp folder OUTSIDE home (the scan guard refuses plain folders under home): TMPDIR if it is
    outside home, else /tmp, else the system default."""
    home = str(Path.home().resolve())
    for cand in (os.environ.get("TMPDIR"), "/tmp", None):
        d = cand or __import__("tempfile").gettempdir()
        try:
            r = str(Path(d).resolve())
        except OSError:
            continue
        if os.path.isdir(r) and os.access(r, os.W_OK) and not (r == home or r.startswith(home + "/")):
            return r
    return __import__("tempfile").gettempdir()


def models_dir(flag=None):
    """Where the vision models live (v16): flag -> SCAN_MODELS -> ~/.cache/scan-and-index-models.
    Outside the skill folder (an installed skill may be read-only) and outside the catalog."""
    d = flag or os.environ.get("SCAN_MODELS") or str(Path.home() / ".cache" / "scan-and-index-models")
    return Path(os.path.expanduser(d))


BUNDLED = SKILL_DIR / "models"     # v16: small engines shipped INSIDE the skill (work offline from the first click)


def model_file(key, rel, size=None, flag=None):
    """The installed copy (models_dir) if it is complete, else the copy bundled inside the skill, else None."""
    for base in (models_dir(flag), BUNDLED):
        p = base / key / rel
        if p.is_file() and (size is None or p.stat().st_size == size):
            return p
    return None


def model_groups(flag=None):
    """{group: (installed, missing files)} from rules.json -> vision.models. Each model lives in models_dir()/<key>/
    exactly as huggingface_hub writes it (local_dir: one copy, no second cache)."""
    try:
        models = json.load(open(SKILL_DIR / "rules.json"))["vision"]["models"]
    except (OSError, ValueError, KeyError):
        return {}
    d, out = models_dir(flag), {}
    for key, m in models.items():
        miss = [f["file"] for f in m["files"] if not model_file(key, f["file"], f["bytes"], flag)]
        g = out.setdefault(m["group"], [True, []])
        if miss:
            g[0] = False
            g[1] += ["%s/%s" % (key, x) for x in miss]
    return {k: (v[0], v[1]) for k, v in out.items()}


def models_ok(flag=None):
    """(ok, detail): the CORE models are installed (the quick pass can run). Other groups are listed, not required."""
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False, "onnxruntime not installed"
    g = model_groups(flag)
    if not g:
        return False, "rules.json has no vision.models"
    core_ok, core_miss = g.get("core", (False, ["core"]))
    extra = ", ".join("%s %s" % (k, "yes" if v[0] else "no") for k, v in sorted(g.items()) if k != "core")
    return core_ok, ("core ready · %s" % extra) if core_ok else "core missing: %s" % ", ".join(core_miss[:3])


def mem_gb():
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
    except (ValueError, OSError, AttributeError):
        try:
            return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) / 1e9
        except Exception:
            return 8.0


def workers(heavy=False):
    """Clockwork: bounded workers for one gear, sized from this machine (rules.json -> vision.clockwork)."""
    try:
        c = json.load(open(SKILL_DIR / "rules.json"))["vision"]["clockwork"]
    except (OSError, ValueError, KeyError):
        c = {"max_workers": 8, "heavy_workers": 1, "mem_gb_per_worker": 3}
    if heavy:
        return max(1, int(c.get("heavy_workers", 1)))
    cores = os.cpu_count() or 2
    return max(1, min(int(c.get("max_workers", 8)), cores // 2 or 1, int(mem_gb() // float(c.get("mem_gb_per_worker", 3))) or 1))


# ---- shared facts: one place each (findings paper F2, F4, F11, 261108) ---------------------------
def norm(text):
    """ONE name normaliser for every phase: case, spaces, dashes, underscores collapse.
    'FX3 - 01' and 'NENN_02_SOURCE_260901_-_FX3_-_01' then match."""
    import re
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())


def matches(only, name):
    """The single --only matcher, used by P3 and P5 alike: every word of `only`, in order, inside the name
    (case, spaces, dashes and underscores ignored). 'FX3 - 01' matches 'NENN_02_SOURCE_260901_-_FX3_-_01';
    'Day 1 FX3 A' matches '260901 NENN DAY 1/VIDEO/FX3 A'."""
    import re
    if not only:
        return True
    hay, pos = norm(name), 0
    for w in re.findall(r"[a-z0-9]+", str(only).lower()):
        i = hay.find(w, pos)
        if i < 0:
            return False
        pos = i + len(w)
    return True


def catalog_db(cat=None, flag=None):
    """Flag -> CATALOG_DB -> <catalog>/catalog_objects.sqlite. P4 builds it in temp and copies it here once."""
    return Path(os.path.expanduser(flag or os.environ.get("CATALOG_DB") or str(Path(cat or catalog()) / "catalog_objects.sqlite")))


def db_ok(path):
    """(True, '') when the catalog database exists, is not empty and has its tables; else (False, why)."""
    import sqlite3
    p = Path(path)
    if not p.is_file():
        return False, "no object database at %s (run P4: pipeline.py run <drive> --from 4)" % p
    if p.stat().st_size == 0:
        return False, "the object database is empty (0 bytes): rerun P4"
    try:
        db = sqlite3.connect("file:%s?mode=ro&immutable=1" % p.as_posix(), uri=True)
        n = db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('drives','clips','cards')").fetchone()[0]
    except sqlite3.Error as e:
        return False, "the object database cannot be read (%s): rerun P4" % e
    return (n == 3, "" if n == 3 else "the object database has no tables: rerun P4")


def cards():
    """The card library (cards.json at the skill root): every card type, declared once."""
    try:
        return json.loads((SKILL_DIR / "cards.json").read_text(encoding="utf-8"))["cards"]
    except (OSError, ValueError, KeyError):
        return []


def rel_to_drive(text, root):
    """Replace a drive's absolute root inside free text (e.g. an ffprobe error) with '<drive>/'. Datasets
    store drive-relative paths only (F1)."""
    if not text or not root:
        return text
    root = str(root).rstrip("/")
    return str(text).replace(root + "/", "<drive>/").replace(root, "<drive>")


# ---- naming -------------------------------------------------------------------------------------
def stamp(when=None):
    w = when or dt.datetime.now()
    return w.strftime("%y%m%d"), w.strftime("%H%M")


def out_name(drive, phase, task, output, seq, ext, when=None):
    """<YYMMDD>_<drive>_<task>_<output>_P<phase>-<seq>_<HHMM>.<ext>  (ext '' for a folder)."""
    d, t = stamp(when) if not isinstance(when, tuple) else when
    name = "%s_%s_%s_%s_%s-%02d_%s" % (d, drive, task, output, phase, int(seq), t)
    return name + ("." + ext if ext else "")


def phase_dir(cat, drive, phase):
    p = Path(cat) / drive / PHASE_DIRS[phase]
    p.mkdir(parents=True, exist_ok=True)
    return p


def out_path(cat, drive, phase, task, output, seq, ext, when=None):
    return phase_dir(cat, drive, phase) / out_name(drive, phase, task, output, seq, ext, when)


def newest(cat, drive, phase, task, output, ext):
    """The newest output of one kind (any seq, any phase code that files into the same folder)."""
    d = Path(cat) / drive / PHASE_DIRS[phase]
    # the 6-digit date pins the start, so drive "IC_23" never matches "A_IC_23" files (review 261107)
    pat = "[0-9][0-9][0-9][0-9][0-9][0-9]_%s_%s_%s_P*-*_*" % (glob.escape(drive), task, output) + ("." + ext if ext else "")
    hits = [Path(p) for p in glob.glob(str(d / pat))]
    hits.sort(key=lambda p: (p.name.split("_", 1)[0], p.name.rsplit("_", 1)[-1], p.stat().st_mtime))
    return hits[-1] if hits else None


def parse_name(name):
    """'261107_IC_23_ledger_media_P2-01_1434.csv' -> dict(date, drive, task, output, phase, seq, time, ext), or None."""
    stem, _, ext = name.partition(".") if not name.startswith(".") else (name, "", "")
    parts = stem.split("_")
    try:
        ps = next(i for i in range(len(parts) - 1, 0, -1) if parts[i].startswith("P") and "-" in parts[i])
        phase, seq = parts[ps].split("-")
        return {"date": parts[0], "drive": "_".join(parts[1:ps - 2]), "task": parts[ps - 2], "output": parts[ps - 1],
                "phase": phase, "seq": int(seq), "time": parts[ps + 1], "ext": ext}
    except (StopIteration, ValueError, IndexError):
        return None


# ---- tools and lanes ----------------------------------------------------------------------------
def _ver(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return ((r.stdout or r.stderr).strip().splitlines() or [""])[0][:60]
    except (OSError, subprocess.TimeoutExpired):
        return ""


def tools():
    out = {}
    out["python"] = {"ok": sys.version_info >= (3, 9), "version": platform.python_version(), "path": sys.executable}
    out["homebrew"] = {"ok": bool(shutil.which("brew")), "version": _ver(["brew", "--version"]) if shutil.which("brew") else ""}
    out["ffmpeg"] = {"ok": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
                     "version": (_ver(["ffprobe", "-version"]).replace("ffprobe version ", "").split(" ") or [""])[0]}
    out["tesseract"] = {"ok": bool(shutil.which("tesseract")), "version": _ver(["tesseract", "--version"])}
    out["poppler"] = {"ok": bool(shutil.which("pdftoppm") and shutil.which("pdfunite")), "version": ""}
    try:
        import cv2
        ok = hasattr(cv2, "dnn") and hasattr(cv2.dnn, "readNetFromCaffe")
        out["opencv"] = {"ok": ok, "version": cv2.__version__ + ("" if ok else " (too new: needs <5)")}
    except Exception:
        out["opencv"] = {"ok": False, "version": "not installed"}
    try:
        import weasyprint
        out["weasyprint"] = {"ok": True, "version": weasyprint.__version__}
    except Exception as e:
        out["weasyprint"] = {"ok": False, "version": "not installed" if isinstance(e, ImportError) else "cannot start"}
    sys.path.insert(0, str(SCRIPTS))
    try:
        import p3b_driver_red as red
        exe = red.find_redline()
        if exe:
            stand_in = "fake_REDline" in exe or "test stand-in" in red.help_text(exe)
            out["redline"] = {"ok": True, "version": red.version_of(red.help_text(exe)) or "?", "path": exe,
                              "kind": "STAND-IN (tests only)" if stand_in else "real"}
        else:
            out["redline"] = {"ok": False, "version": "not installed"}
    except Exception as e:
        out["redline"] = {"ok": False, "version": "check failed: %s" % str(e)[:60]}
    return out


def keys_file():
    return os.environ.get("WORKFORCE_KEYS") or ""


def profile(flag_catalog=None):
    prof, why = detect_profile()
    cat = catalog(flag_catalog)
    return {
        "profile": prof, "detected_from": why, "os": "%s %s" % (platform.system(), platform.release()),
        "user": _user(), "is_root": hasattr(os, "geteuid") and os.geteuid() == 0,
        "home": str(Path.home()), "skill_dir": str(SKILL_DIR),
        "catalog": str(cat), "catalog_source": "flag" if flag_catalog else ("CATALOG" if os.environ.get("CATALOG") else "profile default"),
        "catalog_fs": "mounted" if _mounted(cat) else "local",
        "drive_roots": [d for d in drive_roots(prof) if os.path.isdir(d)],
        "tmp": tmp_root(), "keys_file": keys_file() or "none",
        "cloud_session": _cloud(), "tools": tools(), "vision": models_ok(),
    }


def card(p=None):
    p = p or profile()
    t = p["tools"]
    tl = lambda k: ("%s %s" % ("yes" if t[k]["ok"] else "NO", t[k].get("version") or "")).strip()
    red = t["redline"]
    lines = [
        "WHERE AM I · %s (%s)" % (p["profile"], p["detected_from"]),
        "  machine   %s · user %s%s%s" % (p["os"], p["user"] or "?", " · ROOT" if p["is_root"] else "",
                                         " · cloud session" if p["cloud_session"] else ""),
        "  python    %s" % tl("python"),
        "  homebrew  %s" % tl("homebrew"),
        "  catalog   %s  [%s, from %s]" % (p["catalog"], p["catalog_fs"], p["catalog_source"]),
        "  drives    %s" % (", ".join(p["drive_roots"]) or "none (no drive folders here)"),
        "  temp      %s" % p["tmp"],
        "  tools     ffmpeg %s · tesseract %s · opencv %s · weasyprint %s · poppler %s" % (
            tl("ffmpeg"), "yes" if t["tesseract"]["ok"] else "NO", tl("opencv"), tl("weasyprint"),
            "yes" if t["poppler"]["ok"] else "NO"),
        "  vision    %s" % (("yes · " + p["vision"][1]) if p["vision"][0] else "NO · " + p["vision"][1]),
        "  REDline   %s" % (("%s %s · %s" % ("yes", red.get("version", ""), red.get("kind", ""))) if red["ok"] else red["version"]),
        "  keys      %s" % p["keys_file"],
        "  skill     %s" % p["skill_dir"],
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    if "--json" in sys.argv:
        print(json.dumps(profile(), indent=1))
    else:
        print(card())
