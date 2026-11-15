# shared_ids.py · source: plugins/shared/shared_ids.py · copies must stay byte-identical (plugins/shared/check_copies.py)
"""The shared IDs between scan, watch, print and read-shots, in code. The schema in words: SHARED_IDS.md (repo root).

    drive    TEST_DRIVE_v1                                       one name part
    card     260901_SHOOT_DAY_1/FX6_A001  |  GH_CARD  |  LOOSE   the card column: the path down to the folder under
                                                                 the first day folder, else the first folder, else LOOSE
    clip     <drive>/<card>/<file name>                          TEST_DRIVE_v1/260901_SHOOT_DAY_1/FX6_A001/A001C001_260901AB.MXF
    segment  <clip_id>#S<nn>                                     ...A001C001_260901AB.MXF#S02   (nn from 01, 2+ digits)
    time     seconds from the clip's first frame, a number >= 0, 3 decimals

Standard library only. Describes and checks; it mints nothing on its own that a plugin does not already mint.
"""
import re

KINDS = ("drive", "card", "clip", "segment", "time", "timecode")
DAY_RE = re.compile(r"^(\d{6})(?:_|$)")                   # a shoot-day folder: YYMMDD or YYMMDD_<words>
SEG_RE = re.compile(r"^S(\d{2,})$")
TC_RE = re.compile(r"^\d{2}:\d{2}:\d{2}[:;]\d{2}$")
_BAD = re.compile(r"[/\x00-\x1f\x7f]")


def _part_ok(s):
    """One name part: not empty, no slash, no control characters, no edge spaces, not . or .."""
    return isinstance(s, str) and s != "" and s == s.strip() and s not in (".", "..") and not _BAD.search(s)


def card_of(rel):
    """Drive-relative path -> the card column the clip_id carries (the rule ppf_ingest and watch_see both state)."""
    parts = rel.replace("\\", "/").split("/")
    for i, p in enumerate(parts[:-1]):
        if DAY_RE.match(p):
            return "/".join(parts[:i + 2]) if i + 1 < len(parts) - 1 else "LOOSE"
    return parts[0] if len(parts) > 1 else "LOOSE"


def make_clip_id(drive, card, file):
    cid = "%s/%s/%s" % (drive, card, file)
    if not validate(cid, "clip"):
        raise ValueError("not a clip_id: %r" % cid)
    return cid


def parse_clip_id(cid):
    """'<drive>/<card>/<file>' -> {'drive', 'card', 'file'}. The card is everything between the first and last slash."""
    if not isinstance(cid, str) or cid.count("/") < 2:
        raise ValueError("not a clip_id: %r" % (cid,))
    drive, rest = cid.split("/", 1)
    card, file = rest.rsplit("/", 1)
    if not (_part_ok(drive) and validate(card, "card") and _part_ok(file)):
        raise ValueError("not a clip_id: %r" % (cid,))
    return {"drive": drive, "card": card, "file": file}


def make_segment_id(clip_id, n):
    if not (isinstance(n, int) and n >= 1):
        raise ValueError("segment number must be an int >= 1: %r" % (n,))
    parse_clip_id(clip_id)
    return "%s#S%02d" % (clip_id, n)


def parse_segment_id(sid):
    """'<clip_id>#S<nn>' -> {'clip_id', 'n', + the clip_id's parts}. Splits at the last '#'."""
    if not isinstance(sid, str) or "#" not in sid:
        raise ValueError("not a segment_id: %r" % (sid,))
    cid, tail = sid.rsplit("#", 1)
    m = SEG_RE.match(tail)
    if not m or int(m.group(1)) < 1:
        raise ValueError("not a segment_id: %r" % (sid,))
    return dict(parse_clip_id(cid), clip_id=cid, n=int(m.group(1)))


def validate(value, kind):
    """True when value is a well-formed ID of this kind; False otherwise. Never raises on bad input."""
    if kind not in KINDS:
        raise ValueError("kind must be one of %s" % ", ".join(KINDS))
    try:
        if kind == "drive":
            return _part_ok(value)
        if kind == "card":
            if not isinstance(value, str):
                return False
            ps = value.split("/")                  # one folder, or ...<day folder>/<card folder> (day folder second to last)
            return all(map(_part_ok, ps)) and (len(ps) == 1 or bool(DAY_RE.match(ps[-2])))
        if kind == "clip":
            parse_clip_id(value); return True
        if kind == "segment":
            parse_segment_id(value); return True
        if kind == "time":
            v = float(value)
            return v >= 0 and v == v and v != float("inf") and round(v, 3) == v
        if kind == "timecode":
            return isinstance(value, str) and bool(TC_RE.match(value))
    except (ValueError, TypeError):
        return False
    return False


def time_to_frames(seconds, fps):
    """Seconds from the clip's first frame -> frame index at fps (nearest frame). Print turns times into frames."""
    return int(round(float(seconds) * float(fps)))
