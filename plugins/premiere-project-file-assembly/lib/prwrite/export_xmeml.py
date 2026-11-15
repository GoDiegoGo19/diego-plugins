"""prwrite.export_xmeml: Project -> FCP7 xmeml (version 5) text for Premiere's XML import.

In:  a `prwrite.model.Project` (root bin tree, master clips and media by uid,
     sequences with video/audio tracks of TrackItems, all times in ticks).
Out: `project_to_xmeml(project) -> str`, a complete document starting with the
     XML declaration and `<!DOCTYPE xmeml>`. Same Project in -> same bytes out
     (ids are hashes of model keys, never counters over a dict or a clock).

Tier 3 fallback (Premiere's XML import makes duplicate media; the .prproj
writer is tier 1). Shapes follow the plugin's working writer
(skills/261112-premiere-project-file-assembly-v2/references/xmeml-shape.md);
every shape a Rosetta v7 rule decides is cited by id:

  PC-1.4  print-contract.md §1.4  declared version fixes the legal element set: version="5",
          and no vendor extensions (pproTicksIn/Out etc. are in no DTD, §4 named causes).
  PC-4R   print-contract.md §4 "reference table": an id registers ONCE; later uses are refs.
  FL-9    failure-library.md #9: duplicate full <file id> declarations = silent import death.
          Full <file> at first occurrence in document order, bare <file id="x"/> after.
  FL-7    failure-library.md #7: audio clipitems carry <sourcetrack>, not video anatomy.
  FL-3 / RD-K2  failure-library.md #3, reader-doctrine.md "three coordinate killers" #2:
          true fps, never nominal. NTSC rates carry the 1001 factor; frames come from ticks
          through model.Rate.ticks_per_frame (TPS*1001/(tb*1000) for NTSC).
  RD-K3   reader-doctrine.md killer #3: start/end are timeline frames, in/out source frames.
  OR-182  or-rulings.md line 182: file needs name or pathurl (one_or_both); we write both.
  OR-868  or-rulings.md line 868: timecode = string or (frame and displayformat); we write all.
  OR-106  or-rulings.md line 106: link = linkclipref or (mediatype, trackindex, clipindex);
          we write linkclipref plus the triple, as the working writer does.

Media facts come from the `Media` fields (rate, duration, width, height, audio_channels,
sample_rate, has_video, timecode_start) first, then from `Media.extras` /
`MasterClip.extras` (keys listed in `_MEDIA_KEYS`); what is missing falls back to
the stated defaults below, never to a value from another clip.
"""

from __future__ import annotations

import hashlib
import os
import uuid
import xml.etree.ElementTree as ET
from fractions import Fraction
from urllib.parse import quote

from .model import Project, Rate, Sequence, Time, TrackItem  # noqa: F401  (Project: type of the input)

# Fixed namespace: the same Project gives the same uuids on every run.
_NS = uuid.UUID("3b0e7c52-5d1a-4f0e-8a51-261112d0e0d5")

# extras keys we read (first match wins). Rates may be Rate, [num, den], or an fps number/string.
_MEDIA_KEYS = {
    "rate": ("rate", "fps", "frame_rate"),
    "duration": ("duration", "duration_ticks"),
    "width": ("width",),
    "height": ("height",),
    "channels": ("audio_channels", "channels", "channelcount"),
    "has_video": ("has_video",),
    "tc": ("timecode_start", "tc_start", "timecode"),
    "samplerate": ("samplerate", "sample_rate"),
}
DEFAULT_RATE = Rate(25)          # only when neither media, clip, nor any sequence names a rate
DEFAULT_SIZE = (1920, 1080)
AUDIO_EXT = {".wav", ".aif", ".aiff", ".mp3", ".m4a", ".aac", ".flac", ".bwf"}
STILL_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".psd", ".bmp", ".gif"}
MAX_MASTER_AUDIO = 2             # working writer's choice on the test drive (xmeml-shape.md)


def _key(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:10]


def _uuid(kind: str, key: str, uid: str = "") -> str:
    """Premiere's own uid when it is a UUID, else a stable uuid5."""
    try:
        return str(uuid.UUID(uid))
    except (ValueError, AttributeError, TypeError):
        return str(uuid.uuid5(_NS, kind + "|" + key))


# model.Media fields that carry the same facts (preferred over extras; owner C, 261112).
_FIELDS = {"rate": "rate", "duration": "duration", "width": "width", "height": "height",
           "channels": "audio_channels", "has_video": "has_video", "tc": "timecode_start",
           "samplerate": "sample_rate"}


def _field(obj, what):
    """The real model field for `what`, or None when it holds no fact."""
    value = getattr(obj, _FIELDS[what], None) if obj is not None else None
    if value is None:
        return None
    if what == "has_video":
        return value  # False is a fact
    if what == "channels":
        return value  # 0 is a fact (no audio); None (unknown) returned above
    return None if value in (0, "") else value


def _extra(obj, what):
    value = _field(obj, what)
    if value is not None:
        return value
    ex = getattr(obj, "extras", None) or {}
    for k in _MEDIA_KEYS[what]:
        if k in ex and ex[k] not in (None, ""):
            return ex[k]
    return None


def _as_rate(v) -> Rate | None:
    if v is None:
        return None
    if isinstance(v, Rate):
        return v
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return Rate(int(v[0]), int(v[1]))
    return Rate.from_fps(v)


def _as_time(v) -> Time | None:
    if v is None:
        return None
    if isinstance(v, Time):
        return v
    return Time(int(v))


def frames(t: Time | None, rate: Rate) -> int:
    """Ticks -> whole frames at `rate`, via model.Rate (FL-3 / RD-K2: true fps, 1001 honored)."""
    if t is None:
        return 0
    try:
        return t.to_frames(rate)
    except ValueError:  # a rate with no whole tick count per frame: exact fraction, rounded
        return round(Fraction(t.ticks) * rate.fps / 254016000000)


def _sub(parent, tag, text=None, **attrs):
    e = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        e.text = str(text)
    return e


def _rate_el(parent, rate: Rate):
    r = _sub(parent, "rate")
    _sub(r, "timebase", rate.nominal)                       # nominal counting base
    _sub(r, "ntsc", "TRUE" if rate.den != 1 else "FALSE")   # FL-3: the 1001 flag carries true fps
    return r


def pathurl(path: str) -> str:
    """Absolute local path -> file://localhost/... percent-encoded (the working writer's form)."""
    p = path.replace("\\", "/")
    if len(p) > 1 and p[1] == ":":            # C:/x -> /C:/x
        p = "/" + p
    return "file://localhost" + quote(p, safe="/:")


class _Writer:
    def __init__(self, project: Project):
        self.p = project
        self.declared_files: set[str] = set()       # FL-9 / PC-4R
        self.clip_ids: dict[str, str] = {}
        seq_rates = [s.rate for s in project.sequences if s.rate]
        self.fallback_rate = seq_rates[0] if seq_rates else DEFAULT_RATE
        self.seq_by_uid = {s.uid: s for s in project.sequences if s.uid}
        self.observed_audio = self._observe_audio()

    # ---------- facts
    def _observe_audio(self) -> dict[str, int]:
        """Most linked audio items seen per master clip on any timeline."""
        out: dict[str, int] = {}
        for s in self.p.sequences:
            for grp in self._groups(s).values():
                n = sum(1 for kind, _, _ in grp if kind == "audio")
                for _, _, it in grp:
                    out[it.master_uid] = max(out.get(it.master_uid, 0), n)
        return out

    def media_of(self, mc):
        return self.p.media.get(getattr(mc, "media_uid", "") or "")

    def rate_of(self, mc, media) -> Rate:
        return (_as_rate(_extra(media, "rate")) if media else None) or _as_rate(_extra(mc, "rate")) \
            or self.fallback_rate

    def kinds_of(self, mc, media) -> tuple[bool, int]:
        """(has video, audio channel count) for a master clip."""
        ext = os.path.splitext(media.path)[1].lower() if media else ""
        hv = _extra(media, "has_video") if media else None
        has_video = bool(hv) if hv is not None else ext not in AUDIO_EXT
        ch = _extra(media, "channels") if media else None
        if ch is None:
            ch = self.observed_audio.get(mc.uid)
        if ch is None:
            ch = 0 if ext in STILL_EXT else 2
        return has_video, int(ch)

    def duration_of(self, mc, media) -> Time | None:
        return mc.duration or (_as_time(_extra(media, "duration")) if media else None)

    def mc_id(self, mc) -> str:
        if mc.uid not in self.clip_ids:
            self.clip_ids[mc.uid] = "masterclip-" + _key("clip", mc.uid or mc.oid or mc.name)
        return self.clip_ids[mc.uid]

    @staticmethod
    def file_id(media) -> str:
        return "file-" + _key("file", media.uid or media.path)

    # ---------- file: declare once (FL-9, PC-4R)
    def file_el(self, parent, mc, media):
        fid = self.file_id(media)
        f = _sub(parent, "file", id=fid)
        if fid in self.declared_files:
            return f                                    # bare reference after the first
        self.declared_files.add(fid)
        rate = self.rate_of(mc, media)
        _sub(f, "name", os.path.basename(media.path) or mc.name)       # OR-182 branch 1
        _sub(f, "pathurl", pathurl(media.path))                          # OR-182 branch 2
        _rate_el(f, rate)
        _sub(f, "duration", frames(self.duration_of(mc, media), rate))
        tc = _sub(f, "timecode")                                         # OR-868: string + frame + format
        _rate_el(tc, rate)
        start = _extra(media, "tc")
        drop = False
        if isinstance(start, str):
            drop = ";" in start
            t = Time.from_timecode(start, rate)
        else:
            t = _as_time(start) or Time()
        _sub(tc, "string", t.to_timecode(rate, drop=drop))
        _sub(tc, "frame", frames(t, rate))
        _sub(tc, "displayformat", "DF" if drop else "NDF")
        m = _sub(f, "media")
        has_video, ch = self.kinds_of(mc, media)
        if has_video:
            sc = _sub(_sub(m, "video"), "samplecharacteristics")
            _rate_el(sc, rate)
            _sub(sc, "width", _extra(media, "width") or DEFAULT_SIZE[0])
            _sub(sc, "height", _extra(media, "height") or DEFAULT_SIZE[1])
            _sub(sc, "pixelaspectratio", "square")
        if ch:
            a = _sub(m, "audio")
            sc = _sub(a, "samplecharacteristics")
            _sub(sc, "depth", 16)
            _sub(sc, "samplerate", _extra(media, "samplerate") or 48000)
            _sub(a, "channelcount", ch)
        return f

    # ---------- bins and master clips
    def bin_el(self, parent, b):
        el = _sub(parent, "bin")
        _sub(el, "uuid", _uuid("bin", b.uid or b.oid or b.name, b.uid))
        _sub(el, "name", b.name)
        self.fill(_sub(el, "children"), b)

    def fill(self, children, b):
        for child in b.bins:
            self.bin_el(children, child)
        for cuid in b.clips:
            self.item_el(children, cuid)

    def item_el(self, children, cuid):
        mc = self.p.clips.get(cuid)
        if mc is None:
            return
        if mc.sequence_uid:
            s = self.seq_by_uid.get(mc.sequence_uid)
            if s is not None and id(s) not in self.placed_seqs:
                self.placed_seqs.add(id(s))
                self.sequence_el(children, s)
            return
        if id(mc) in self.placed_clips or self.media_of(mc) is None:
            return                                   # generators: no file to point at
        self.placed_clips.add(id(mc))
        self.masterclip_el(children, mc)

    def masterclip_el(self, parent, mc):
        media = self.media_of(mc)
        rate = self.rate_of(mc, media)
        mid = self.mc_id(mc)
        dur = frames(self.duration_of(mc, media), rate)
        el = _sub(parent, "clip", id=mid)                 # PC-4R: registers once
        _sub(el, "uuid", _uuid("clip", mc.uid or mid, mc.uid))
        _sub(el, "masterclipid", mid)
        _sub(el, "ismasterclip", "TRUE")
        _sub(el, "duration", dur)
        _rate_el(el, rate)
        _sub(el, "name", mc.name)
        mel = _sub(el, "media")
        has_video, ch = self.kinds_of(mc, media)
        items = (["video"] if has_video else []) + ["audio"] * min(ch, MAX_MASTER_AUDIO)
        a_n = 0
        for i, kind in enumerate(items):
            group = mel.find(kind) if mel.find(kind) is not None else _sub(mel, kind)
            ci = _sub(_sub(group, "track"), "clipitem", id="%s-%s%d" % (mid, kind[0], i))
            _sub(ci, "masterclipid", mid)
            _sub(ci, "name", mc.name)
            _rate_el(ci, rate)
            for tag, v in (("start", 0), ("end", dur), ("in", 0), ("out", dur)):
                _sub(ci, tag, v)
            self.file_el(ci, mc, media)
            if kind == "audio":                                      # FL-7
                a_n += 1
                st = _sub(ci, "sourcetrack")
                _sub(st, "mediatype", "audio")
                _sub(st, "trackindex", a_n)

    # ---------- sequences
    @staticmethod
    def _groups(s: Sequence):
        """Link groups: items of one master clip at the same start/end across tracks."""
        groups: dict[tuple, list] = {}
        for kind, tracks in (("video", s.video_tracks), ("audio", s.audio_tracks)):
            for ti, t in enumerate(tracks, 1):
                for it in t.items:
                    key = (it.master_uid, it.start.ticks, it.end.ticks) if it.master_uid else ("solo", id(it))
                    groups.setdefault(key, []).append((kind, ti, it))
        return groups

    def sequence_el(self, parent, s: Sequence):
        rate = s.rate or self.fallback_rate
        skey = _key("seq", s.uid or s.oid or s.name)
        el = _sub(parent, "sequence", id="sequence-" + skey)
        _sub(el, "uuid", _uuid("seq", s.uid or skey, s.uid))
        _sub(el, "name", s.name)
        _sub(el, "duration", frames(s.end, rate))
        _rate_el(el, rate)
        tc = _sub(el, "timecode")                                       # OR-868
        _rate_el(tc, rate)
        _sub(tc, "string", s.zero_point.to_timecode(rate))
        _sub(tc, "frame", frames(s.zero_point, rate))
        _sub(tc, "displayformat", "NDF")
        media = _sub(el, "media")
        video = _sub(media, "video")
        sc = _sub(_sub(video, "format"), "samplecharacteristics")
        _rate_el(sc, rate)
        w, h = s.frame_size or DEFAULT_SIZE
        _sub(sc, "width", w)
        _sub(sc, "height", h)
        _sub(sc, "pixelaspectratio", "square")
        audio = _sub(media, "audio")

        # pass 1: ids and positions, so links can name clipitems not yet written
        placed = []              # (kind, track_no, clip_index, item, id)
        n = 0
        for kind, tracks in (("video", s.video_tracks), ("audio", s.audio_tracks)):
            for ti, t in enumerate(tracks, 1):
                ci_idx = 0
                for it in t.items:
                    mc = self.p.clips.get(it.master_uid)
                    if mc is None or self.media_of(mc) is None:
                        continue
                    n += 1
                    ci_idx += 1
                    placed.append((kind, ti, ci_idx, it, "clipitem-%s-%d" % (skey, n)))
        by_item = {id(p[3]): p for p in placed}
        links = {}
        for grp in self._groups(s).values():
            members = [by_item[id(it)] for _, _, it in grp if id(it) in by_item]
            if len(members) > 1:
                for m in members:
                    links[id(m[3])] = members

        # pass 2: write
        tr_el = {}
        for kind, tracks in (("video", s.video_tracks), ("audio", s.audio_tracks)):
            for ti, _ in enumerate(tracks, 1):
                tr_el[(kind, ti)] = _sub(video if kind == "video" else audio, "track")
        for kind, ti, _, it, cid in placed:
            mc = self.p.clips[it.master_uid]
            md = self.media_of(mc)
            ci = _sub(tr_el[(kind, ti)], "clipitem", id=cid)
            _sub(ci, "masterclipid", self.mc_id(mc))
            _sub(ci, "name", it.name or mc.name)
            _sub(ci, "enabled", "TRUE")
            _sub(ci, "duration", frames(self.duration_of(mc, md), rate))
            _rate_el(ci, rate)
            # RD-K3: start/end timeline frames, in/out source frames, never mixed
            _sub(ci, "start", frames(it.start, rate))
            _sub(ci, "end", frames(it.end, rate))
            _sub(ci, "in", frames(it.in_, rate))
            _sub(ci, "out", frames(it.out, rate))
            self.file_el(ci, mc, md)
            group = links.get(id(it))
            if kind == "audio":                                          # FL-7
                st = _sub(ci, "sourcetrack")
                _sub(st, "mediatype", "audio")
                k = [m for m in group if m[0] == "audio"].index(by_item[id(it)]) + 1 if group else 1
                _sub(st, "trackindex", k)
            for lk, lti, lidx, _, lid in group or ():                    # OR-106
                ln = _sub(ci, "link")
                _sub(ln, "linkclipref", lid)
                _sub(ln, "mediatype", lk)
                _sub(ln, "trackindex", lti)
                _sub(ln, "clipindex", lidx)

    # ---------- document
    def run(self) -> ET.Element:
        self.placed_seqs: set[int] = set()
        self.placed_clips: set[int] = set()
        root = ET.Element("xmeml", version="5")                          # PC-1.4
        proj = _sub(root, "project")
        _sub(proj, "name", self.p.name or "Untitled")
        children = _sub(proj, "children")
        self.fill(children, self.p.root)
        for cuid in self.p.clips:                     # clips no bin lists: project root
            self.item_el(children, cuid)
        for s in self.p.sequences:                    # sequences with no master clip: project root
            if id(s) not in self.placed_seqs:
                self.placed_seqs.add(id(s))
                self.sequence_el(children, s)
        return root


def project_to_xmeml(project: Project) -> str:
    """Project -> xmeml v5 text (declaration, DOCTYPE, two-space indent, trailing newline)."""
    root = _Writer(project).run()
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + body + "\n"


def write_xmeml(project: Project, path: str) -> str:
    """Write next to nothing existing: mode "x" refuses to overwrite (SPEC improvement 3)."""
    with open(path, "x", encoding="utf-8") as fh:
        fh.write(project_to_xmeml(project))
    return path
