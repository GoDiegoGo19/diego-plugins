#!/usr/bin/env python3
"""ppf_ingest.py: scan P6 clips CSV, or a folder of media, -> objects.json.

  ppf_ingest.py SCAN_CLIPS.csv --media-root /path/to/DRIVE --out objects.json
  ppf_ingest.py /path/to/DRIVE --out objects.json            (ffprobe each file)

IDs are the scan's: clip_id = "<drive>/<card column>/<file_name>", the same three
keys the scan's P6 CSVs carry (drive, card, file). Metadata only: no audio or
picture content is decoded. Stdlib only; ffprobe is used only if present.
"""
import argparse, csv, datetime, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ppf_common import (VIDEO_EXT, AUDIO_EXT, STILL_EXT, GRAPHIC_EXT, NO_PROBE_EXT, SIDECAR_EXT,
                        ffprobe, have, save_json, short_id, clip_rate)

DAY_RE = re.compile(r"^(\d{6})(?:_|$)")
SKIP_DIRS = {".Spotlight-V100", ".Trashes", ".fseventsd", "NOTES", "_sources"}
PHONE_RE = re.compile(r"^(IMG|IMG_E|VID|PXL|MVIMG)_\d{3,}", re.I)   # iPhone IMG_1234.MOV, Android VID_/PXL_
LIVE_MAX_S = 3.5                                                  # a Live Photo's .MOV is ~3 s
HDR_TRC = {"arib-std-b67": "HLG", "smpte2084": "PQ"}


def shot_date(creation_time=None, creationdate=None):
    """(datetime with tz, local YYMMDD) from QuickTime tags (G3). com.apple.quicktime.creationdate carries the
    local offset (2026-11-10T11:30:00-0700) and wins; creation_time is UTC and is converted to the machine's zone."""
    for t, utc in ((creationdate, False), (creation_time, True)):
        if not t:
            continue
        x = t.strip().replace("Z", "+00:00")
        x = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", x)
        try:
            dt = datetime.datetime.fromisoformat(x)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        loc = dt.astimezone() if utc else dt
        return loc, loc.strftime("%y%m%d")
    return None, None


def role_of(rel):
    """master | proxy | sidecar. Proxies and P2 per-channel audio stay in bins, out of sequences."""
    parts = rel.replace("\\", "/").split("/")
    up = [p.upper() for p in parts]
    if "PROXY" in up or "SUB" in up or re.search(r"S\d\d\.MP4$", parts[-1].upper()):
        return "proxy"
    if "AUDIO" in up and "CONTENTS" in up:          # P2 card: audio lives in per-channel MXFs
        return "sidecar"
    return "master"


def day_and_card(rel, path, pf=None):
    """(day, card, day_from, folder_day). Shoot day: QuickTime creation date (G3) > YYMMDD_ folder > mtime.
    day_from is creation_time | folder | mtime; the card always comes from the folders."""
    parts = rel.replace("\\", "/").split("/")
    meta = shot_date((pf or {}).get("creation_time"), (pf or {}).get("creationdate"))[1]
    for i, p in enumerate(parts[:-1]):
        m = DAY_RE.match(p)
        if m:
            card = parts[i + 1] if i + 1 < len(parts) - 1 else "LOOSE"
            return (meta or m.group(1)), card, ("creation_time" if meta else "folder"), True
    card = parts[0] if len(parts) > 1 else "LOOSE"
    if meta:
        return meta, card, "creation_time", False
    try:
        d = datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%y%m%d")
    except OSError:
        d = "000000"
    return d, card, "mtime", False


def by_folder(clips, root_name="FOLDER"):
    """Re-label every clip so the bins MIRROR THE SOURCE TREE instead of the shoot day.

    261115, a stock pull: auto-organize read each file's date, which for downloads is the day it was
    downloaded, and built bins 261113 / 261114 that mixed DAY1 with DAY3. Dates are the right answer for
    camera cards, where the folder is a card and the date is the shoot. They are the wrong answer for stock,
    or for anything a person has already sorted into folders, where the FOLDER is the intent.

    day  = the first folder under the root (DAY1), card = the next one (ACT1), both from rel_path.
    Everything downstream (bins, one stringout per card per day, topics) then follows the tree unchanged.
    """
    for c in clips:
        parts = str(c.get("rel_path", "")).replace("\\", "/").split("/")
        folders = parts[:-1]
        c["day"] = folders[0] if folders else root_name
        c["card"] = folders[1] if len(folders) > 1 else "LOOSE"
        c["day_from"] = "folder (--by-folder)"
        c["date_source"] = c["day_from"]
    for i, c in enumerate(sorted(clips, key=lambda x: (x["day"], x["card"]))):
        pass
    seen = {}
    for c in sorted(clips, key=lambda x: (x["day"], x["card"], x.get("rel_path", ""))):
        k = (c["day"], c["card"])
        if k not in seen:
            seen[k] = "%02d" % (sum(1 for d, _ in seen if d == c["day"]) + 1)
        c["card_no"] = seen[k]
    return clips


def iphone_fields(c, pf, fn):
    """Annotate one clip in place with the iPhone pack: date_source/shot_at, device, PHONE card (by day),
    orientation (display size after rotation), hdr/color_space."""
    dt = shot_date(pf.get("creation_time"), pf.get("creationdate"))[0]
    c["shot_at"] = dt.isoformat() if dt else None
    c["date_source"] = c["day_from"]
    c["device"] = " ".join(x for x in (pf.get("make"), pf.get("model")) if x) or None
    ext = os.path.splitext(fn)[1].lower()
    if ext in VIDEO_EXT and (c["device"] or PHONE_RE.match(fn) or c["card"] == "LOOSE"):
        c["card_folder"], c["card"] = c["card"], "PHONE"          # Diego: "I would just probably go by day."
    w, h = c.get("width"), c.get("height")
    c["rotation"] = pf.get("rotation") or 0
    c["orientation"] = ("vertical" if h > w else "horizontal") if w and h else None
    c["hdr"], c["color_space"] = pf.get("hdr"), pf.get("color_space")
    c["slomo"] = bool(c.get("fps") and clip_rate(c["fps"]))       # >= 90 fps keeps its native clip rate (G8)


def kind_of(ext, rel, transcribe, pix_fmt, codec):
    up = rel.upper()
    if ext in STILL_EXT and "GRAPHIC" not in up:
        return "still"
    if ext in GRAPHIC_EXT or "GRAPHIC" in up or (pix_fmt or "").startswith(("yuva", "rgba", "argb", "bgra")) \
            or (codec or "") in ("qtrle",):
        return "graphic"
    if ext in AUDIO_EXT:
        return "audio"
    if (transcribe or "").lower() == "likely":
        return "interview"
    return "broll"


def probe_fields(path):
    return fields_from_probe(ffprobe(path) if os.path.splitext(path)[1].lower() not in NO_PROBE_EXT else {})


def fields_from_probe(j):
    f = {"duration_s": None, "fps": None, "tc_start": None, "audio_channels": 0, "width": None,
         "height": None, "pix_fmt": None, "codec": None, "creation_time": None, "creationdate": None,
         "make": None, "model": None, "rotation": 0, "hdr": None, "color_space": None}
    if not j:
        return f
    fmt = j.get("format", {})
    try:
        f["duration_s"] = round(float(fmt.get("duration")), 3)
    except (TypeError, ValueError):
        pass
    tags = fmt.get("tags", {}) or {}
    f["tc_start"] = tags.get("timecode")
    f["creation_time"] = tags.get("creation_time")
    f["creationdate"] = tags.get("com.apple.quicktime.creationdate")
    f["make"], f["model"] = tags.get("com.apple.quicktime.make"), tags.get("com.apple.quicktime.model")
    for s in j.get("streams", []):
        st = s.get("tags", {}) or {}
        if not f["tc_start"] and st.get("timecode"):
            f["tc_start"] = st["timecode"]
        if s.get("codec_type") == "video" and f["width"] is None:
            f["width"], f["height"] = s.get("width"), s.get("height")
            f["pix_fmt"], f["codec"] = s.get("pix_fmt"), s.get("codec_name")
            rot = st.get("rotate") or next((d.get("rotation") for d in s.get("side_data_list") or []
                                            if d.get("rotation") is not None), 0)
            try:
                f["rotation"] = int(float(rot)) % 360
            except (TypeError, ValueError):
                f["rotation"] = 0
            if f["rotation"] in (90, 270) and f["width"] and f["height"]:
                f["width"], f["height"] = f["height"], f["width"]      # display size (G4)
            f["hdr"] = HDR_TRC.get(s.get("color_transfer"))
            f["color_space"] = s.get("color_primaries") or s.get("color_space")
            fr = s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/1"
            try:
                a, b = fr.split("/"); f["fps"] = round(float(a) / float(b), 3) if float(b) else None
            except ValueError:
                pass
        if s.get("codec_type") == "audio":
            f["audio_channels"] += int(s.get("channels") or 0)
    return f


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def from_scan(csv_path, media_root, drive, sidecars=None):
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    clips = []
    for r in rows:
        rel = r["rel_path"]
        if os.path.splitext(rel)[1].lower() in SIDECAR_EXT:
            if sidecars is not None:
                sidecars.append(rel)
            continue
        path = os.path.join(media_root, rel) if media_root else rel
        pf = probe_fields(path) if os.path.exists(path) else {}
        day, card, day_src, _ = day_and_card(rel, path, pf)
        ext = os.path.splitext(rel)[1].lower()
        c = {
            "clip_id": "%s/%s/%s" % (drive, r["card"], r["file_name"]),
            "drive": drive, "card": card, "scan_card": r["card"], "clip": r["file_name"],
            "path": os.path.abspath(path), "rel_path": rel, "day": day, "day_from": day_src,
            "duration_s": num(r.get("duration_s")) or pf.get("duration_s"),
            "fps": num(r.get("fps")) or pf.get("fps"),
            "tc_start": (r.get("start_timecode") or "").strip() or pf.get("tc_start"),
            "width": pf.get("width") or int(num(r.get("width")) or 0) or None,      # ffprobe's is the DISPLAY size (rotation)
            "height": pf.get("height") or int(num(r.get("height")) or 0) or None,
            "audio_channels": pf.get("audio_channels", 0),
            "transcribe": r.get("transcribe"), "segments": r.get("segments"),
            "source": "scan P6 clips CSV" + (" + ffprobe" if pf else ""),
        }
        iphone_fields(c, pf, r["file_name"])
        c["role"] = role_of(rel)
        c["kind"] = kind_of(ext, rel, r.get("transcribe"), pf.get("pix_fmt"), pf.get("codec"))
        clips.append(c)
    mark_live(clips)
    return clips


def from_folder(root, drive, sidecars=None):
    clips = []
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if not d.startswith(".") and d not in SKIP_DIRS)
        for fn in sorted(fns):
            ext = os.path.splitext(fn)[1].lower()
            if ext in SIDECAR_EXT and sidecars is not None and not fn.startswith("."):
                sidecars.append(os.path.relpath(os.path.join(dp, fn), root)); continue
            if fn.startswith(".") or ext not in VIDEO_EXT | AUDIO_EXT | STILL_EXT | GRAPHIC_EXT:
                continue
            path = os.path.join(dp, fn)
            rel = os.path.relpath(path, root)
            pf = probe_fields(path)
            day, card, day_src, in_day = day_and_card(rel, path, pf)
            card_col = "/".join(rel.split(os.sep)[:2]) if in_day else card
            c = {"clip_id": "%s/%s/%s" % (drive, card_col, fn), "drive": drive, "card": card,
                 "scan_card": card_col, "clip": fn, "path": os.path.abspath(path), "rel_path": rel,
                 "day": day, "day_from": day_src, "transcribe": None, "segments": None,
                 "source": "ffprobe" if pf.get("duration_s") is not None else "file only (no probe)"}
            for k in ("duration_s", "fps", "tc_start", "width", "height", "audio_channels"):
                c[k] = pf.get(k)
            iphone_fields(c, pf, fn)
            c["role"] = role_of(rel)
            c["kind"] = kind_of(ext, rel, None, pf.get("pix_fmt"), pf.get("codec"))
            clips.append(c)
    mark_live(clips)
    return clips


def mark_live(clips):
    """A .MOV under 3.5 s with a same-stem .HEIC/.HEIF beside it is a Live Photo clip: kind 'live',
    kept in 05_STILLS/<day>/_live, never in a sequence."""
    stills = {os.path.splitext(c["path"])[0].lower() for c in clips
              if os.path.splitext(c["path"])[1].lower() in (".heic", ".heif")}
    for c in clips:
        if c["kind"] in ("broll", "interview") and os.path.splitext(c["path"])[0].lower() in stills \
                and c.get("duration_s") is not None and c["duration_s"] < LIVE_MAX_S:
            c["kind"] = "live"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="scan P6 clips CSV, or a media folder (drive root)")
    ap.add_argument("--media-root", help="drive root the scan's rel_path is relative to")
    ap.add_argument("--drive", help="drive name (default: folder name of the media root)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.source.lower().endswith(".csv"):
        drive = a.drive or os.path.basename(os.path.normpath(a.media_root or "DRIVE"))
        sidecars = []
        clips = from_scan(a.source, a.media_root, drive, sidecars); mode = "scan"
    elif os.path.isdir(a.source):
        drive = a.drive or os.path.basename(os.path.normpath(a.source))
        sidecars = []
        clips = from_folder(a.source, drive, sidecars); mode = "folder"
    else:
        sys.exit("ppf_ingest: give a P6 clips .csv or a media folder (handoff JSON not read yet)")
    for c in clips:
        c["xml_key"] = short_id(c["clip_id"])
    cards = {}
    for c in sorted(clips, key=lambda c: (c["day"], c["card"])):
        cards.setdefault(c["day"], [])
        if c["card"] not in cards[c["day"]]:
            cards[c["day"]].append(c["card"])
    for c in clips:
        c["card_no"] = "%02d" % (cards[c["day"]].index(c["card"]) + 1)
    obj = {"made_by": "ppf_ingest.py", "mode": mode, "source": os.path.abspath(a.source),
           "drive": drive, "ffprobe": have("ffprobe"), "clips": clips, "sidecars": sidecars}
    save_json(a.out, obj)
    print("ingest: %d objects (%s) -> %s" % (len(clips), mode, a.out))
    return obj


if __name__ == "__main__":
    main()
