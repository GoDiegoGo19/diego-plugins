"""prwrite.parse: a parsed document -> the plain `Project` model.

In:  a `Doc` (prwrite.document) or anything with the same small surface:
     `.root` (the PremiereData element; `.tree.getroot()` also accepted)
     and `.find(id)` returning the top-level element whose ObjectID or
     ObjectUID is `id` (None if absent).
Out: `project_from_doc(doc) -> Project` with the bins tree, master clips,
     media paths, sequences (rate, frame size, starting timecode) with
     video/audio tracks and their clip items. Read only; the Doc is not
     changed. Anything read but not modeled lands in `extras`. Missing
     links are skipped, never raised, so a partial file still gives a model.

Element paths are facts from the vendored reference (see SPEC.md, Shared
facts); the walk itself is ours: one pass per object kind over the Doc's
id index instead of re-walking the tree.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from .model import Bin, MasterClip, Media, Project, Rate, Sequence, Time, Track, TrackItem

_BIN_TAGS = ("RootProjectItem", "BinProjectItem", "SmartBinProjectItem")


def _root_of(doc: Any) -> ET.Element:
    root = getattr(doc, "root", None)
    if root is None:
        root = doc.tree.getroot()
    return root


def _follow(doc: Any, ref: ET.Element | None) -> ET.Element | None:
    """Resolve an element carrying ObjectRef (-> ObjectID) or ObjectURef
    (-> ObjectUID)."""
    if ref is None:
        return None
    key = ref.get("ObjectRef") or ref.get("ObjectURef")
    return None if key is None else doc.find(key)


def _ids(el: ET.Element) -> dict:
    return {"oid": el.get("ObjectID", ""), "uid": el.get("ObjectUID", "")}


def _meta(el: ET.Element) -> dict:
    out = {"tag": el.tag}
    for k in ("ClassID", "Version"):
        if el.get(k) is not None:
            out[k] = el.get(k)
    return out


def _int(text: str | None) -> int | None:
    try:
        return int(text) if text not in (None, "") else None
    except ValueError:
        return None


def _properties(el: ET.Element, path: str = "Node/Properties") -> dict:
    bag = el.find(path)
    if bag is None:
        return {}
    return {child.tag: child.text or "" for child in bag if len(child) == 0}


def _clip_core(el: ET.Element) -> ET.Element | None:
    core = el.find("Clip")
    return core if core is not None else el.find("*/Clip")


# ------------------------------------------------------------- panel items

def _parse_master(doc: Any, item: ET.Element, project: Project) -> str | None:
    master = _follow(doc, item.find("MasterClip"))
    if master is None:
        return None
    mc = MasterClip(name=master.findtext("Name") or item.findtext("ProjectItem/Name") or "",
                    item_uid=item.get("ObjectUID", ""), extras=_meta(master), **_ids(master))
    mc.extras["item"] = _meta(item)
    for clip_ref in master.findall("Clips/Clip"):
        clip = _follow(doc, clip_ref)
        core = None if clip is None else _clip_core(clip)
        source = None if core is None else _follow(doc, core.find("Source"))
        if source is None:
            continue
        if mc.duration is None and _int(source.findtext("OriginalDuration")) is not None:
            mc.duration = Time(int(source.findtext("OriginalDuration")))
        seq_ref = source.find("SequenceSource/Sequence")
        if seq_ref is not None and not mc.sequence_uid:
            mc.sequence_uid = seq_ref.get("ObjectURef", "")
        media = _follow(doc, source.find("MediaSource/Media"))
        if media is None or mc.media_uid:
            continue
        path = media.findtext("FilePath") or ""
        # Generators store a numeric token in FilePath, not a path.
        if path and not path.isdigit():
            m = Media(path=path, extras=_meta(media), **_ids(media))
            key = m.uid or m.oid
            project.media.setdefault(key, m)
            mc.media_uid = key
    key = mc.uid or mc.oid
    project.clips[key] = mc
    return key


def _parse_bin(doc: Any, el: ET.Element, project: Project, seen: set) -> Bin:
    b = Bin(name=el.findtext("ProjectItem/Name") or el.findtext("*/ProjectItem/Name") or "",
            kind=el.tag, extras=_meta(el), **_ids(el))
    props = _properties(el, "ProjectItem/Node/Properties")
    if props:
        b.extras["properties"] = props
    container = el.find("ProjectItemContainer")
    if container is None:
        container = el.find("*/ProjectItemContainer")
    for ref in [] if container is None else container.findall("Items/Item"):
        child = _follow(doc, ref)
        if child is None or id(child) in seen:
            continue
        seen.add(id(child))
        if child.tag in _BIN_TAGS:
            b.bins.append(_parse_bin(doc, child, project, seen))
        else:
            key = _parse_master(doc, child, project)
            if key is not None:
                b.clips.append(key)
            else:
                b.extras.setdefault("unmodeled_items", []).append(_meta(child) | _ids(child))
    return b


# --------------------------------------------------------------- sequences

def _parse_item(doc: Any, el: ET.Element) -> TrackItem | None:
    cti = el.find("ClipTrackItem")
    if cti is None:
        return None
    ti = TrackItem(extras=_meta(el), **_ids(el))
    ti.start = Time(_int(cti.findtext("TrackItem/Start")) or 0)  # Premiere elides a zero start
    ti.end = Time(_int(cti.findtext("TrackItem/End")) or 0)
    subclip_ref = cti.find("SubClip")
    subclip = _follow(doc, subclip_ref)
    if subclip is not None:
        ti.subclip_oid = subclip.get("ObjectID", "")
        ti.name = subclip.findtext("Name") or ""
        mref = subclip.find("MasterClip")
        ti.master_uid = "" if mref is None else (mref.get("ObjectURef") or mref.get("ObjectRef") or "")
        clip = _follow(doc, subclip.find("Clip"))
        if clip is not None:
            ti.clip_oid = clip.get("ObjectID", "")
            core = _clip_core(clip)
            if core is not None:
                ti.in_ = Time(_int(core.findtext("InPoint")) or 0)
                ti.out = Time(_int(core.findtext("OutPoint")) or 0)
                speed = core.findtext("PlaybackSpeed")
                if speed:
                    ti.extras["playback_speed"] = speed
    return ti


def _parse_track(doc: Any, el: ET.Element, kind: str) -> Track:
    t = Track(kind=kind, extras=_meta(el), **_ids(el))
    t.track_id = _int(el.findtext("ClipTrack/Track/ID")) or 0
    t.index = _int(el.findtext("ClipTrack/Track/Index")) or 0
    for ref in el.findall("ClipTrack/ClipItems/TrackItems/TrackItem"):
        item_el = _follow(doc, ref)
        item = None if item_el is None else _parse_item(doc, item_el)
        if item is not None:
            t.items.append(item)
    transitions = [r.get("ObjectRef", "") for r in
                   el.findall("ClipTrack/TransitionItems/TrackItems/TrackItem")]
    if transitions:
        t.extras["transition_oids"] = transitions
    return t


def _frame_size(rect: str | None) -> tuple[int, int] | None:
    parts = (rect or "").split(",")
    if len(parts) != 4:
        return None
    try:
        return int(parts[2]), int(parts[3])
    except ValueError:
        return None


def _parse_sequence(doc: Any, el: ET.Element) -> Sequence:
    s = Sequence(name=el.findtext("Name") or "", extras=_meta(el), **_ids(el))
    props = _properties(el)
    if props:
        s.extras["properties"] = props
    s.zero_point = Time(_int(props.get("MZ.ZeroPoint")) or 0)
    for pair in el.findall("TrackGroups/TrackGroup"):
        group = _follow(doc, pair.find("Second"))
        if group is None:
            continue
        if group.tag == "VideoTrackGroup":
            kind, target = "Video", s.video_tracks
            tpf = _int(group.findtext("TrackGroup/FrameRate"))
            if tpf:
                s.rate = Rate.from_ticks_per_frame(tpf)
            s.frame_size = _frame_size(group.findtext("FrameRect"))
        elif group.tag == "AudioTrackGroup":
            kind, target = "Audio", s.audio_tracks
            s.extras["audio_ticks_per_frame"] = _int(group.findtext("TrackGroup/FrameRate"))
            s.extras["audio_channels"] = _int(group.findtext("NumAdaptiveChannels"))
        else:
            s.extras.setdefault("other_groups", []).append(_meta(group) | _ids(group))
            continue
        for ref in group.findall("TrackGroup/Tracks/Track"):
            track_el = _follow(doc, ref)
            if track_el is not None:
                target.append(_parse_track(doc, track_el, kind))
    return s


# -------------------------------------------------------------------- entry

def project_from_doc(doc: Any) -> Project:
    """Build the plain model from a parsed document. See module docstring."""
    root = _root_of(doc)
    project = Project(extras={"root_tag": root.tag, "root_version": root.get("Version", "")})
    project_el = _follow(doc, root.find("Project"))
    if project_el is not None:
        project.oid, project.uid = project_el.get("ObjectID", ""), project_el.get("ObjectUID", "")
        root_item = _follow(doc, project_el.find("RootProjectItem"))
        if root_item is not None:
            project.root = _parse_bin(doc, root_item, project, {id(root_item)})
            project.name = project.root.name
    for el in root:
        if el.tag == "Sequence":
            project.sequences.append(_parse_sequence(doc, el))
    return project
