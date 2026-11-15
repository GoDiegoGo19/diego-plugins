"""prwrite.build: make and grow a .prproj document, keeping the plain model in step.

In:  names, paths, `probe.MediaInfo`, `model.Rate` / `model.Time`.
Out: a `Doc` (prwrite.document) holding Premiere's object graph, and the
     matching `model.Project`; `save_version` writes `<base>_vNNN.prproj`.

    project, doc = new_project("TD")
    b = add_bin(doc, project, project.root, "01_MEDIA")
    mc = import_media(doc, project, b, path, probe(path))
    seq = add_sequence(doc, project, project.root, "TD_SEQ", Rate(24000, 1001), (1920, 1080))
    place(doc, seq, 0, mc, at=Time(), in_=Time(), out=mc.duration)
    save_version(doc, "/out/TD")      # -> /out/TD_v001.prproj

Every object we create is registered in `doc.index` (new top-level objects
through `doc.attach`, edits to existing objects through `_touch`, which
re-indexes that one object). Times are ticks (model.TICKS_PER_SECOND).

Facts come from the vendored py-premiere (MIT, see ATTRIBUTION.md); each is
cited `file:line` (paths under vendor/py-premiere/py_premiere/). The code is
ours: one small object factory, role-keyed id allocation, our own layout pass
(serializer.layout) instead of hand-set tails.

What is NOT verified in Premiere yet (runs here, unopened in Premiere):
the whole output. Specific guesses are marked GUESS in comments.
"""

from __future__ import annotations

import base64
import copy
import os
import re
import uuid
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from . import serializer as S
from .document import Doc
from .framing import write_prproj
from .model import (TICKS_PER_SECOND, Bin, MasterClip, Media, Project, Rate, Sequence,
                    Time, Track, TrackItem)
from .probe import MediaInfo

__all__ = ["BuildError", "new_project", "add_bin", "import_media", "add_sequence", "place",
           "save_version", "next_version_path", "find_element"]


class BuildError(ValueError):
    """A request the builder cannot turn into a valid document."""


# ------------------------------------------------------------------ ClassIDs
# (tag) -> (ClassID, Version). Sources:
#   skeleton.py:26-33,150-206   the nine new-project objects
#   project_item.py:162          BinProjectItem
#   media_import.py:32-51,1202-1605  import objects and their versions
#   sequence_builder.py:33-64    sequence graph
#   track.py:68-73,269-385       placement objects; merged_builder.py:27,448 Link
_CLASS = {
    "Project": ("62ad66dd-0dcd-42da-a660-6d8fbde94876", "45"),
    "RootProjectItem": ("1c307a89-9318-47d7-a583-bf2553736543", "1"),
    "ProjectSettings": ("50c16708-a1a1-4d2f-98d5-4e283ae28353", "21"),
    "ScratchDiskSettings": ("4c6ed82b-a81c-4df1-8bd0-750504c4b560", "4"),
    "IngestSettings": ("2db8f76b-2c37-48ee-925d-9a4f7278152d", "2"),
    "WorkspaceSettings": ("c4372273-e1aa-4683-98aa-a2ceadf3066c", "1"),
    "DummyCaptureSettings": ("328c2aa2-47f9-4211-805b-b6a6dbd4ca29", "1"),
    "DefaultSequenceSettings": ("567bdf53-d6d9-4d61-b2f1-f4834bebea9b", "2"),
    "BinProjectItem": ("dbfd6653-24da-480e-a35e-ba45e9504e4b", "1"),
    "ClipProjectItem": ("cb4e0ed7-aca1-4171-8525-e3658dec06dd", "1"),
    "MasterClip": ("fb11c33a-b0a9-4465-aa94-b6d5db2628cf", "12"),
    "ClipLoggingInfo": ("77ab7fdd-dcdf-465d-9906-7a330ca1e738", "10"),
    "VideoClip": ("9308dbef-2440-4acb-9ab2-953b9a4e82ec", "11"),
    "AudioClip": ("b8830d03-de02-41ee-84ec-fe566dc70cd9", "8"),
    "ClipChannelGroupVectorSerializer": ("a3127a8c-95d4-456e-a7f5-171b3f922426", "1"),
    "ClipChannelVectorSerializer": ("333d203b-3a53-4195-8894-fc7523ff3dc7", "1"),
    "ClipChannelSerializer": ("5c89aa7a-89a6-4483-becd-f2b1def42316", "1"),
    "Markers": ("bee50706-b524-416c-9f03-b596ce5f6866", "4"),
    "VideoMediaSource": ("e64ddf74-8fac-4682-8aa8-0e0ca2248949", "2"),
    "AudioMediaSource": ("f588da05-fc2a-4fbc-9383-74d653b379e3", "2"),
    "Media": ("7a5c103e-f3ac-4391-b6b4-7cc3d2f9a7ff", "30"),
    "VideoStream": ("a36e4719-3ec6-4a0c-ab11-8b4aab377aa5", "22"),
    "AudioStream": ("0b5cf52f-2b85-4863-890b-8844b64ecfe9", "8"),
    "AudioComponentChain": ("3cb131d1-d3c0-47ae-a19a-bdf75ea11674", "4"),
    "SecondaryContent": ("f9d004b5-cb04-4e2f-af6f-64fadc2c4be9", "1"),
    "AudioSequenceSource": ("e8d4cc83-38cb-491f-9d94-e5f7e3b205ee", "7"),
    "VideoSequenceSource": ("4752dfa9-7a7e-4a3b-a25b-cafde1a8d036", "3"),
    "Sequence": ("6a15d903-8739-11d5-af2d-9b7855ad8974", "12"),
    "VideoTrackGroup": ("9e9abf7a-0918-49c2-91ae-991b5dde77bb", "13"),
    "AudioTrackGroup": ("9b9238b9-53a8-4cc3-b03f-b36246d052e6", "6"),
    "DataTrackGroup": ("b714b71d-6838-48dd-9b77-db19088ced7e", "1"),
    "VideoClipTrack": ("f68dcd81-8805-11d5-af2d-9bfa89d4ddd4", "1"),
    "AudioClipTrack": ("097f6203-99ae-11d5-84f2-8cf14bde7040", "7"),
    "VideoComponentChain": ("0970e08a-f58f-4108-b29a-1a717b8e12e2", "3"),
    "AudioMixTrack": ("4b1d8400-e89e-11d5-abc4-a1a13b1e80a0", "4"),
    "StereoToStereoPanProcessor": ("7bf86a01-efbe-11d5-abc4-c1ce2b1e9090", "1"),
    "DefaultPanProcessor": ("33a94282-ee2c-11d5-abc4-c1cd7f9e3c10", "2"),
    "AudioTrackInlet": ("be3af080-e8c6-11d5-abc4-a1c6d5dee670", "4"),
    "AudioFader": ("1a38c583-ed5c-11d5-abc4-c1cbf61ec590", "3"),
    "AudioMeter": ("72ea4700-f615-11d5-abc4-c186585e63e0", "2"),
    "ScalarParam": ("a714635e-a628-4b27-9d59-77eba47dbc1a", "10"),
    "BoolParam": ("32657501-3aa4-445f-a49b-d09ecb9fa1ae", "10"),
    "SubClip": ("e0c58dc9-dbdd-4166-aef7-5db7e3f22e84", "6"),
    "VideoClipTrackItem": ("368b0406-29e3-4923-9fcd-094fbf9a1089", "8"),
    "AudioClipTrackItem": ("064ec682-9ba6-11d5-af2d-9ca32c7d6164", "11"),
    "Link": ("149d4ea5-a7d4-4b34-9bb7-16d783904bf2", "1"),
}
# A component-chain VideoComponentChain on a placement is the same class (track.py:69,284).

# Media-type GUIDs (sequence_builder.py:28-30).
_VIDEO_MEDIA = "228cda18-3625-4d2d-951e-348879e4ed93"
_AUDIO_MEDIA = "80b8e3d5-6dca-4195-aefb-cb5f407ab009"
_DATA_MEDIA = "d8143ffe-eec4-4d2a-a909-d5f7bf094dc5"
_ZERO_GUID = "00000000-0000-0000-0000-000000000000"  # skeleton.py:50
_IMPLEMENTATION_ID = "1fa18bfa-255c-44b1-ad73-56bcd99fceaf"  # media_import.py:43, used for movies :1296
_AUDIO_TPF_48K = 5292000  # ticks per 48 kHz sample (sequence_builder.py:73)
_STEREO = '[{"channellabel":100},{"channellabel":101}]'  # sequence_builder.py:75
_CHANNEL_TYPES = {1: 0, 2: 1, 6: 2}  # media_import.py:93
_AAC_SAMPLE_TYPE = "7"  # media_import.py:80 (movie audio)
# Label colours: AV / Video (media_import.py:58-63); sequence label (sequence_builder.py:98-99).
_LABEL_AV, _LABEL_VIDEO, _LABEL_AUDIO = (1, 6769408), (0, 11405886), (2, 480554)
_SEQ_LABEL, _SEQ_LABEL_COLOR = "BE.Prefs.LabelColors.5", "19005"
_PANNER_COMPONENT_ID, _NEXT_PANNER_ID = "4294967280", "4294967279"  # sequence_builder.py:100-101

# VideoStream profile per codec family (media_import.py:171-241): color space and optional flags.
_MOVIE_COLOR = ('{"baseColorProfile":{"colorProfileData":"AQAAAP////8=",'
                '"colorProfileName":"BT.709,32f,Display-Referred"},"baseProfileType":1}')
_RGB_FULL = '{"baseColorProfile":{"colorProfileName":"BT.709 RGB Full"},"baseProfileType":1}'
_H26X = dict(color=_MOVIE_COLOR, ignore_alpha=True, alpha_type="3", field_uncertain=True,
             orientation=True)
_PRORES = dict(color=_RGB_FULL, orientation=True)
_PROFILES = {"avc1": dict(_H26X), "hvc1": dict(_H26X, codec_type="HEVC"),
             "apch": _PRORES, "apcn": _PRORES, "apcs": _PRORES, "apco": _PRORES, "ap4h": _PRORES,
             "AVdh": _PRORES}
_CODEC_ALIASES = {"h264": "avc1", "avc": "avc1", "hevc": "hvc1", "h265": "hvc1", "hev1": "hvc1",
                  "prores": "apch"}

# Timecode display formats by ticks-per-frame. Logging/time display: media_import.py:70-76
# (25 -> 101, 29.97 -> 103 NDF / 102 DF, 30 DF -> 104) and the sequence presets
# sequence_builder.py:212-229 (23.976 -> 110, 50 -> 105, 59.94 -> 106, 60 -> 108).
# 24 -> 100 is the plugin's own addition (ppf_write_prproj.py), GUESS until Premiere shows it.
_TC_FORMAT = {10594584000: 110, 10584000000: 100, 10160640000: 101, 8475667200: 103,
              8467200000: 104, 5080320000: 105, 4237833600: 106, 4233600000: 108}
_SEQ_DISPLAY = {**_TC_FORMAT, 8475667200: 102}  # the 29.97 preset displays drop-frame

_WORK_AREA = 60 * TICKS_PER_SECOND  # sequence_builder.py:96, floored to whole frames (:821-824)
_PREVIEW_PRESET = ("EncoderPresets\\SequencePreview\\9678af98-a7b7-4bdb-b477-7ac9c8df4a4e"
                   "\\QuickTime.epr")  # sequence_builder.py:67-70
_EDITING_MODE = "9678af98-a7b7-4bdb-b477-7ac9c8df4a4e"  # sequence_builder.py:66
_PREVIEW_1080 = "fc3cd4d9-d839-8259-9276-05c5000000ea"  # sequence_builder.py:167
_PREVIEW_UHD = "41384a52-7e4a-3c48-e0ad-4939000000ea"  # sequence_builder.py:188


# ------------------------------------------------------------------ helpers

def _el(parent, tag, text=None, **attrs):
    e = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        e.text = str(text)
    return e


def _ref(parent, tag, target, index=None):
    """An ObjectRef (integer target) or ObjectURef (GUID target) element."""
    attrs = {} if index is None else {"Index": str(index)}
    attrs["ObjectURef" if "-" in str(target) else "ObjectRef"] = str(target)
    return ET.SubElement(parent, tag, attrs)


def _bag(parent, version="1"):
    """Node/Properties, the property bag (skeleton.py:151-152)."""
    node = _el(parent, "Node", Version="1")
    return node, _el(node, "Properties", Version=version)


class _Obj:
    """Makes top-level objects: identifier first, then ClassID, Version (skeleton.py:118-125)."""

    def __init__(self, doc: Doc):
        self.doc = doc
        self.made: list[ET.Element] = []

    def __call__(self, tag, *, uid=None, oid=None, element_tag=None):
        class_id, version = _CLASS[tag]
        attrs = {"ObjectUID": uid} if uid else {"ObjectID": oid or self.doc.new_id()}
        attrs.update(ClassID=class_id, Version=version)
        el = ET.Element(element_tag or tag, attrs)
        self.made.append(el)
        return el

    def attach_all(self):
        for el in self.made:
            S.layout(el, 1)
            self.doc.attach(el)
        self.made.clear()


def _touch(doc: Doc, top: ET.Element) -> None:
    """Re-lay out and re-index one top-level object we edited in place."""
    S.layout(top, 1)
    doc.index.remove(top)
    doc.index.add(top)


def find_element(doc: Doc, key: str) -> ET.Element:
    el = doc.find(key)
    if el is None:
        raise BuildError(f"no object {key!r} in the document")
    return el


def _add_item(doc: Doc, bin_el: ET.Element, uid: str) -> None:
    """File a panel item under a bin: ProjectItemContainer/Items/Item (project_item.py:193-211)."""
    container = bin_el.find("ProjectItemContainer")
    if container is None:
        raise BuildError(f"<{bin_el.tag}> has no ProjectItemContainer")
    items = container.find("Items")
    if items is None:
        items = _el(container, "Items", Version="1")
        container.text = None
    _ref(items, "Item", uid, index=len(items))
    _touch(doc, bin_el)


def _guid() -> str:
    return str(uuid.uuid4())


# ------------------------------------------------------------------ project

TEMPLATE = Path(__file__).resolve().parent / "data" / "blank_project.prproj"


def new_project(name: str = "Untitled", template: "str | os.PathLike | None" = TEMPLATE) -> tuple[Project, Doc]:
    """A fresh project. Preferred route (261113): start from a project Premiere itself saved.

    `data/blank_project.prproj` is Diego's blank project from Premiere 26.0.1. It holds the 40
    top-level objects a real project has: the `<Project ObjectRef="1"/>` pointer, Project 1,
    RootProjectItem and 37 settings objects at fixed ObjectIDs 3..39 (reports/261112-2340 handoff,
    file facts; fixtures/prproj/BLANK_PROJECT.prproj). The hand-built nine-object skeleton below
    is kept only as a fallback when the template is missing; a from-scratch file with 9 objects
    leaves dangling refs to 12..15 and is likely to be reported DAMAGED (problem X018).
    """
    if template and Path(template).exists():
        from . import framing as _framing, parse as _parse
        doc = Doc.parse(_framing.read_prproj(template))
        project = _parse.project_from_doc(doc)
        project.name = name
        project.extras.setdefault("root_tag", "PremiereData")
        project.extras.setdefault("root_version", "3")
        project.extras["template"] = str(template)
        doc.index.high = max(getattr(doc.index, "high", 0), 39)
        return project, doc
    return _new_project_skeleton(name)


def _new_project_skeleton(name: str = "Untitled") -> tuple[Project, Doc]:
    """FALLBACK ONLY: the nine-object skeleton (skeleton.py:3-6,147-206).

    Objects and fixed ObjectIDs (skeleton.py:38-44): `<Project ObjectRef="1"/>` pointer,
    Project 1, RootProjectItem (ObjectUID = document id), ProjectSettings 3,
    ScratchDiskSettings 9, IngestSettings 10, WorkspaceSettings 11,
    DummyCaptureSettings 16, DefaultSequenceSettings 17. ProjectSettings also
    points at 12-15 (video/audio/compile settings) which are deliberately NOT
    emitted: Premiere rebuilds them on first save (skeleton.py:97-107).
    `name` is the file's base name; Premiere stores no project name inside.
    """
    root = ET.Element("PremiereData", {"Version": "3"})  # skeleton.py:147
    doc = Doc.new(root)
    make = _Obj(doc)
    document_id, project_guid, view_state = _guid(), _guid(), _guid()
    build = "26.3.0x93 - 20/07/2026 10:07:43"  # skeleton.py:134 (the build stamp Premiere checks)

    stub = ET.Element("Project", {"ObjectRef": "1"})  # skeleton.py:148
    make.made.append(stub)

    prj = make("Project", oid="1")  # skeleton.py:150-164
    _, props = _bag(prj)
    for k, v in (("MZ.Project.WorkspaceName", "Learning"), ("MZ.BuildVersion.Created", build),
                 ("MZ.BuildVersion.Modified", build), ("MZ.Project.ApplicationID", "Pro"),
                 ("MZ.Project.GUID", project_guid), ("TL.PJSnappingState", "1")):
        _el(props, k, v)
    _ref(prj, "RootProjectItem", document_id)
    for tag, target in (("ProjectSettings", 3), ("ScratchDiskSettings", 9),
                        ("IngestSettings", 10), ("ProjectWorkspace", 11)):
        _el(prj, tag, ObjectRef=target)
    _el(prj, "NextID", "1000001")  # skeleton.py:47-48

    root_item = make("RootProjectItem", uid=document_id)  # skeleton.py:166-175
    pi = _el(root_item, "ProjectItem", Version="1")
    node, props = _bag(pi)
    _el(props, "ProjectViewState.ID", view_state)
    _el(node, "ID", "1000000")
    _el(pi, "Name", "Root Bin")
    _el(root_item, "ProjectItemContainer", Version="1")

    settings = make("ProjectSettings", oid="3")  # skeleton.py:100-107,177-183
    for tag, target in (("VideoSettings", 12), ("AudioSettings", 13), ("VideoCompileSettings", 14),
                        ("AudioCompileSettings", 15), ("CaptureSettings", 16),
                        ("DefaultSequenceSettings", 17)):
        _el(settings, tag, ObjectRef=target)
    for tag, value in (  # skeleton.py:65-83
            ("ColorManagementSettings", '{"enableLogColorManagement":2,"graphicsWhiteLuminance":203,'
                                        '"lutInterpolationMethod":1}'),
            ("VideoTimeDisplay", "102"), ("AudioTimeDisplay", "200"),
            ("ColorAwareEffectsEnabled", "0"), ("VideoTimeDisplayInitial", "102"),
            ("ActionSafeWidth", "10"), ("ActionSafeHeight", "10"), ("TitleSafeWidth", "20"),
            ("TitleSafeHeight", "20"), ("ShouldScaleMedia", "false"), ("EditingModeID", _ZERO_GUID),
            ("PreviewFileFormatID", _ZERO_GUID), ("UsePreviewCache", "false")):
        _el(settings, tag, value)

    scratch = make("ScratchDiskSettings", oid="9")  # skeleton.py:53-62,185-189
    for key in ("AudioPreviewLocation0", "VideoPreviewLocation0", "AutoSaveLocation0",
                "DVDEncodingLocation0", "TransferMediaLocation0", "CCLibrariesLocation0",
                "CapturedVideoLocation0", "CapsuleMediaLocation0"):
        _el(scratch, key, "SameAsProject")
    ingest = make("IngestSettings", oid="10")  # skeleton.py:191-193
    _el(ingest, "Action", "copy")
    _el(ingest, "Enabled", "false")
    make("WorkspaceSettings", oid="11")  # skeleton.py:195
    make("DummyCaptureSettings", oid="16")  # skeleton.py:196
    dss = make("DefaultSequenceSettings", oid="17")  # skeleton.py:87-95,198-206
    for tag, value in (("TotalVideoTracks", "1"), ("DefaultAudioStandardMonoTracks", "0"),
                       ("DefaultAudioStandardStereoTracks", "1"), ("DefaultAudioStandard51Tracks", "0"),
                       ("DefaultAudioSubmixMonoTracks", "0"), ("DefaultAudioSubmixStereoTracks", "0"),
                       ("DefaultAudioSubmix51Tracks", "0")):
        _el(dss, tag, value)
    make.attach_all()
    # Fixed ids 1..17 are taken; ObjectIDs we hand out later start above them.
    doc.index.high = max(doc.index.high, 17)

    project = Project(name=name, oid="1",
                      root=Bin(name="Root Bin", uid=document_id, kind="RootProjectItem"),
                      extras={"root_tag": "PremiereData", "root_version": "3"})
    return project, doc


def add_bin(doc: Doc, project: Project, parent_bin: Bin, name: str) -> Bin:
    """A new empty bin under `parent_bin` (project_item.py:165-190: empty property bag,
    Name, empty ProjectItemContainer; filed by an Items/Item ObjectURef)."""
    parent_el = find_element(doc, parent_bin.uid)
    make = _Obj(doc)
    uid = _guid()
    el = make("BinProjectItem", uid=uid)
    pi = _el(el, "ProjectItem", Version="1")
    _bag(pi)
    _el(pi, "Name", name)
    _el(el, "ProjectItemContainer", Version="1")
    make.attach_all()
    _add_item(doc, parent_el, uid)
    b = Bin(name=name, uid=uid, kind="BinProjectItem")
    parent_bin.bins.append(b)
    return b


# ------------------------------------------------------------------ media

def _profile(codec: str) -> tuple[str, dict]:
    four = _CODEC_ALIASES.get(codec.lower(), codec) if codec else "avc1"
    if four in _PROFILES:
        return four, _PROFILES[four]
    # GUESS: an unverified codec gets the H.264 profile; flagged on the model.
    return four, _PROFILES["avc1"]


def _channel_layout(n: int) -> str:
    """media_import.py:1048-1057: mono is channel 0; others label from 100 up."""
    if n == 1:
        return '[{"channellabel":0}]'
    return "[" + ",".join('{"channellabel":%d}' % (100 + i) for i in range(n)) + "]"


def import_media(doc: Doc, project: Project, bin: Bin, path: str, info: MediaInfo,
                 *, project_dir: str | None = None) -> MasterClip:
    """Media + MasterClip + ClipProjectItem (and the objects between them) for a movie.

    Object graph for movie media, from project.py:1215-1349 and media_import.py:1084-1605:
      VideoStream; AudioStream (if audio, given the MEDIA duration, project.py:1250-1260);
      Media (one object, AudioStream ref first, ConformedAudioRate, AlternateStart pair
      when the file has a start timecode, media_import.py:1262-1307);
      VideoMediaSource (OriginalDuration); Markers; ClipLoggingInfo (no CaptureMode when
      the media has both streams, project.py:1285-1290); VideoClip template;
      with audio: AudioMediaSource, SecondaryContent per channel, AudioClip,
      ClipChannelSerializer per channel, ClipChannelVectorSerializer,
      ClipChannelGroupVectorSerializer, master AudioComponentChain; without audio an
      empty ClipChannelGroupVectorSerializer; then MasterClip and ClipProjectItem.
    Audio-only files (wav) use the same graph minus the video half (GUESS: the
    reference's wav path, media_import.py:1353-1397, adds FileKey sharing we skip).
    Stills are not supported yet (their placement default is a Premiere preference,
    track.py:1191-1195).
    """
    if info.kind == "still":
        raise NotImplementedError("stills are not supported by prwrite.build yet")
    path = os.path.abspath(path)
    bin_el = find_element(doc, bin.uid)
    has_video = info.kind == "video"
    channels = info.audio_channels or 0
    if channels and channels not in _CHANNEL_TYPES:
        raise NotImplementedError(f"{channels}-channel audio is not supported (media_import.py:93)")
    if has_video and not info.fps:
        raise BuildError(f"no frame rate for {path}")
    rate = Rate.from_fps(info.fps) if has_video else None
    tpf = rate.ticks_per_frame if rate else _AUDIO_TPF_48K  # raises for a non-whole rate
    duration = info.duration_ticks
    if not duration:
        raise BuildError(f"no duration for {path}")
    start = info.start.ticks
    tc_format = _TC_FORMAT.get(tpf, 101) if has_video else 200
    name = os.path.basename(path)
    content_state = _guid()
    label = _LABEL_AV if (has_video and channels) else (_LABEL_VIDEO if has_video else _LABEL_AUDIO)
    make = _Obj(doc)
    media_uid, master_uid, item_uid = _guid(), _guid(), _guid()
    codec, prof = _profile(info.codec) if has_video else ("", {})

    vstream = None
    if has_video:  # media_import.py:1181-1228
        vstream = make("VideoStream")
        _el(vstream, "FrameRate", tpf)
        _el(vstream, "Duration", duration)
        if prof.get("ignore_alpha"):
            _el(vstream, "IgnoreAlpha", "true")
        _el(vstream, "FrameRect", f"0,0,{info.width},{info.height}")
        stamp = prof.get("codec_type") or codec
        _el(vstream, "CodecType", int.from_bytes(stamp.encode("latin-1")[:4].ljust(4), "big"))
        _el(vstream, "OriginalColorSpace", prof["color"])
        if prof.get("alpha_type"):
            _el(vstream, "AlphaType", prof["alpha_type"])
        if prof.get("field_uncertain"):
            _el(vstream, "FieldTypeIsUncertain", "true")
        if prof.get("orientation"):
            _el(vstream, "OriginalImageOrientationType", "1")
    astream = None
    audio_rate = None
    if channels:  # media_import.py:1323-1337
        audio_rate = TICKS_PER_SECOND // (info.sample_rate or 48000)
        astream = make("AudioStream")
        _el(astream, "FrameRate", audio_rate)
        _el(astream, "AudioChannelLayout", _channel_layout(channels))
        _el(astream, "Duration", duration)
        _el(astream, "SampleType", _AAC_SAMPLE_TYPE)  # GUESS for PCM-in-MOV (reference: AAC)

    media = make("Media", uid=media_uid)  # media_import.py:1262-1307
    if astream is not None:
        _ref(media, "AudioStream", astream.get("ObjectID"))
    if vstream is not None:
        _ref(media, "VideoStream", vstream.get("ObjectID"))
    blob = base64.b64encode(content_state.encode("utf-16-le")).decode("ascii")
    _el(media, "ModificationState", blob + "\n\t\t", Encoding="base64", BinaryHash=_guid())
    rel = os.path.relpath(path, project_dir) if project_dir else name
    _el(media, "RelativePath", rel)
    _el(media, "FilePath", path)
    _el(media, "ImplementationID", _IMPLEMENTATION_ID)
    _el(media, "Title", name)
    _el(media, "FileKey", _guid())
    if audio_rate is not None:
        _el(media, "ConformedAudioRate", audio_rate)
    if start:
        _el(media, "AlternateStart", start)
        _el(media, "UseAlternateStart", "true")
    _el(media, "ContentAndMetadataState", content_state)
    _el(media, "ActualMediaFilePath", path)

    def media_source(tag):  # media_import.py:1310-1320,1340-1350
        src = make(tag)
        ms = _el(src, "MediaSource", Version="4")
        _el(ms, "Content", Version="10")
        _ref(ms, "Media", media_uid)
        _el(src, "OriginalDuration", duration)
        return src

    def template_clip(tag, source):  # media_import.py:1231-1259,1399-1436
        clip = make(tag)
        core = _el(clip, "Clip", Version="18")
        _, props = _bag(core)
        _el(props, "asl.clip.label.color", label[1])
        _el(props, "asl.clip.label.name", f"BE.Prefs.LabelColors.{label[0]}")
        owner = _el(core, "MarkerOwner", Version="1")
        _ref(owner, "Markers", markers.get("ObjectID"))
        _ref(core, "Source", source.get("ObjectID"))
        _el(core, "ClipID", _guid())
        _el(core, "InUse", "false")
        return clip

    vsource = media_source("VideoMediaSource") if has_video else None
    markers = make("Markers")  # media_import.py:1121-1126
    _el(markers, "ByGUID", "byGUID")
    _el(markers, "LastMetadataState", _ZERO_GUID)
    _el(markers, "LastContentState", content_state)
    logging = make("ClipLoggingInfo")  # media_import.py:1093-1118
    if not (has_video and channels):
        _el(logging, "CaptureMode", "2" if has_video else "1")
    _el(logging, "ClipName", name)
    _el(logging, "TimecodeFormat", tc_format)
    _el(logging, "MediaInPoint", start)
    _el(logging, "MediaOutPoint", start + duration)
    _el(logging, "MediaFrameRate", tpf)
    clip_ids = []
    if has_video:
        clip_ids.append(template_clip("VideoClip", vsource).get("ObjectID"))
    chain = None
    if channels:  # project.py:1307-1343
        asource = media_source("AudioMediaSource")
        secondaries = []
        for ch in range(channels):  # media_import.py:1439-1443
            sc = make("SecondaryContent")
            _ref(sc, "Content", asource.get("ObjectID"))
            _el(sc, "ChannelIndex", ch)
            secondaries.append(sc.get("ObjectID"))
        aclip = template_clip("AudioClip", asource)
        sec = _el(aclip, "SecondaryContents", Version="1")
        for i, sid in enumerate(secondaries):
            _ref(sec, "SecondaryContentItem", sid, index=i)
        _el(aclip, "AudioChannelLayout", _channel_layout(channels))
        clip_ids.append(aclip.get("ObjectID"))
        serializers = []
        for ch in range(channels):  # media_import.py:1154-1158
            ser = make("ClipChannelSerializer")
            _el(ser, "SourceClipIndex", 0)
            _el(ser, "mSourceChannelIndex", ch)
            serializers.append(ser.get("ObjectID"))
        vector = make("ClipChannelVectorSerializer")  # media_import.py:1144-1151
        cl = _el(vector, "ClipChannels", Version="1")
        for i, sid in enumerate(serializers):
            _ref(cl, "ClipChannelItem", sid, index=i)
        _el(vector, "ChannelType", _CHANNEL_TYPES[channels])
        groups = make("ClipChannelGroupVectorSerializer")  # media_import.py:1135-1141
        cv = _el(groups, "ClipChannelVectors", Version="1")
        _ref(cv, "ClipChannelVectorItem", vector.get("ObjectID"), index=0)
        chain = make("AudioComponentChain")  # media_import.py:1161-1178
        _el(chain, "DefaultVol", "true")
        _el(chain, "DefaultVolumeComponentID", "1")
        if channels > 1:
            _el(chain, "DefaultChannelVolumeComponentID", "2")
        _el(chain, "ComponentChain", Version="3")
        if channels > 1:
            _el(chain, "AudioChannelLayout", _channel_layout(channels))
            _el(chain, "ChannelType", _CHANNEL_TYPES[channels])
    else:
        groups = make("ClipChannelGroupVectorSerializer")  # media_import.py:1129-1132

    master = make("MasterClip", uid=master_uid)  # media_import.py:1446-1473 / 1585-1599
    _ref(master, "LoggingInfo", logging.get("ObjectID"))
    if chain is not None:
        acc = _el(master, "AudioComponentChains", Version="1")
        _ref(acc, "AudioComponentChain", chain.get("ObjectID"), index=0)
    clips = _el(master, "Clips", Version="1")
    for i, cid in enumerate(clip_ids):
        _ref(clips, "Clip", cid, index=i)
    _ref(master, "AudioClipChannelGroups", groups.get("ObjectID"))
    _el(master, "Name", name)
    if chain is not None:
        _el(master, "DefMappingID", _guid())
    _el(master, "MasterClipChangeVersion", "1" if chain is not None else "0")

    item = make("ClipProjectItem", uid=item_uid)  # media_import.py:1602-1623
    pi = _el(item, "ProjectItem", Version="1")
    _, props = _bag(pi)
    _el(props, "Column.PropertyText.Label", f"BE.Prefs.LabelColors.{label[0]}")
    _el(pi, "Name", name)
    _ref(item, "MasterClip", master_uid)
    make.attach_all()
    _add_item(doc, bin_el, item_uid)

    m = Media(path=path, uid=media_uid, rate=rate, duration=Time(duration), width=info.width,
              height=info.height, audio_channels=channels, sample_rate=info.sample_rate,
              has_video=has_video, timecode_start=info.timecode)
    if has_video and codec not in _PROFILES:
        m.extras["unverified_codec"] = codec
    project.media[media_uid] = m
    mc = MasterClip(name=name, uid=master_uid, item_uid=item_uid, media_uid=media_uid,
                    duration=Time(duration))
    project.clips[master_uid] = mc
    bin.clips.append(master_uid)
    return mc


# ------------------------------------------------------------------ sequence

def _check_rate(rate: Rate) -> int:
    """Presets plus any rate whose ticks-per-frame is whole (SPEC answer to B)."""
    try:
        return rate.ticks_per_frame
    except ValueError:
        raise BuildError(f"rate {rate} has no whole tick count per frame") from None


def add_sequence(doc: Doc, project: Project, bin: Bin, name: str, rate: Rate,
                 frame_size: tuple[int, int] = (1920, 1080), zero_point: Time = Time(),
                 *, video_tracks: int = 3, audio_tracks: int = 3) -> Sequence:
    """A new sequence with its full panel trio (ClipProjectItem + MasterClip +
    Video/AudioSequenceSource) and empty V1..Vn / A1..An stereo tracks.

    Graph and order: sequence_builder.py:449-741 (stereo layout: two secondaries
    and two channel serializers, :469-472; one mixer strip per audio track:
    chain, StereoToStereo panner, fader, meter, balance/volume/mute params; master
    strip; AudioMixTrack; AudioTrackInlet). Video tracks ID 1..n, audio tracks
    ID 2.. (:583-608). The reference default is 4 audio tracks; a sequence Premiere
    derives from clips has 3 (project.py:620-622), which is our default.
    `zero_point` is written as `MZ.ZeroPoint` (sequence.py:478-486) when nonzero
    (GUESS on its position in the bag: last).
    """
    tpf = _check_rate(rate)
    w, h = frame_size
    bin_el = find_element(doc, bin.uid)
    make = _Obj(doc)
    seq_uid, master_uid, item_uid = _guid(), _guid(), _guid()
    v_uids = [_guid() for _ in range(video_tracks)]
    a_uids = [_guid() for _ in range(audio_tracks)]
    ids: dict[str, str] = {}

    def oid(role):  # role-keyed ids so forward refs resolve before the object exists
        if role not in ids:
            ids[role] = doc.new_id()
        return ids[role]

    # panel item + master clip (:487-509)
    item = make("ClipProjectItem", uid=item_uid)
    pi = _el(item, "ProjectItem", Version="1")
    _, props = _bag(pi)
    _el(props, "project.icon.view.grid.order", "0")
    _el(props, "Column.PropertyText.Label", _SEQ_LABEL)
    _el(pi, "Name", name)
    _ref(item, "MasterClip", master_uid)
    master = make("MasterClip", uid=master_uid)
    _ref(master, "LoggingInfo", oid("logging"))
    acc = _el(master, "AudioComponentChains", Version="1")
    _ref(acc, "AudioComponentChain", oid("master_chain"), index=0)
    clips = _el(master, "Clips", Version="1")
    _ref(clips, "Clip", oid("audio_clip"), index=0)
    _ref(clips, "Clip", oid("video_clip"), index=1)
    _ref(master, "AudioClipChannelGroups", oid("channel_groups"))
    _el(master, "Name", name)
    _el(master, "MasterClipChangeVersion", "3")
    make("ClipLoggingInfo", oid=oid("logging"))  # empty (:511)
    chain = make("AudioComponentChain", oid=oid("master_chain"))  # (:513-519)
    _el(chain, "DefaultVol", "true")
    _el(chain, "DefaultVolumeComponentID", "1")
    _el(chain, "DefaultChannelVolumeComponentID", "2")
    _el(chain, "ComponentChain", Version="3")
    _el(chain, "AudioChannelLayout", _STEREO)
    _el(chain, "ChannelType", "1")

    def seq_clip(tag, role, source_role):  # (:351-359)
        clip = make(tag, oid=oid(role))
        core = _el(clip, "Clip", Version="18")
        _, props = _bag(core)
        _el(props, "asl.clip.label.color", _SEQ_LABEL_COLOR)
        _el(props, "asl.clip.label.name", _SEQ_LABEL)
        _ref(core, "Source", oid(source_role))
        _el(core, "ClipID", _guid())
        _el(core, "InUse", "false")
        return clip

    aclip = seq_clip("AudioClip", "audio_clip", "audio_source")  # (:521-529)
    sc = _el(aclip, "SecondaryContents", Version="1")
    for ch in range(2):
        _ref(sc, "SecondaryContentItem", oid(f"secondary_{ch}"), index=ch)
    _el(aclip, "AudioChannelLayout", _STEREO)
    seq_clip("VideoClip", "video_clip", "video_source")
    groups = make("ClipChannelGroupVectorSerializer", oid=oid("channel_groups"))  # (:534-536)
    _ref(_el(groups, "ClipChannelVectors", Version="1"), "ClipChannelVectorItem",
         oid("channel_vector"), index=0)

    def seq_source(tag, role):  # (:384-388)
        src = make(tag, oid=oid(role))
        ss = _el(src, "SequenceSource", Version="4")
        _el(ss, "Content", Version="10")
        _ref(ss, "Sequence", seq_uid)
        _el(src, "OriginalDuration", "0")

    seq_source("AudioSequenceSource", "audio_source")
    for ch in range(2):
        sec = make("SecondaryContent", oid=oid(f"secondary_{ch}"))
        _ref(sec, "Content", oid("audio_source"))
        _el(sec, "ChannelIndex", ch)
    seq_source("VideoSequenceSource", "video_source")
    vector = make("ClipChannelVectorSerializer", oid=oid("channel_vector"))
    cl = _el(vector, "ClipChannels", Version="1")
    for ch in range(2):
        _ref(cl, "ClipChannelItem", oid(f"serializer_{ch}"), index=ch)
    _el(vector, "ChannelType", "1")

    # the Sequence object (:806-860)
    seq = make("Sequence", uid=seq_uid)
    _, props = _bag(seq)
    for k, v in (("AMM.CurrentSolo", "[]"), ("TL.SQTimePerPixel", "0.90634441087613293"),
                 ("Monitor.ProgramZoomIn", "0"), ("Monitor.ProgramZoomOut", "0"),
                 ("TL.SQHeaderWidth", "180"), ("TL.SQVisibleBaseTime", "0"),
                 ("TL.SQVideoVisibleBase", "0"), ("TL.SQAudioVisibleBase", "0"),
                 ("TL.SQDataVisibleBase", "0"), ("TL.SQHideShyTracks", "0"),
                 ("TL.SQAVDividerPosition", "0.5"), ("MZ.WorkInPoint", "0"),
                 ("MZ.WorkOutPoint", _WORK_AREA // tpf * tpf), ("MZ.EditLine", "0"),
                 ("MZ.Sequence.VideoTimeDisplayFormat", _SEQ_DISPLAY.get(tpf, 101)),
                 ("MZ.Sequence.AudioTimeDisplayFormat", "200"),
                 ("MZ.Sequence.EditingModeGUID", _EDITING_MODE),
                 ("MZ.Sequence.PreviewUseMaxBitDepth", "false"),
                 ("MZ.Sequence.PreviewUseMaxRenderQuality", "false"),
                 ("MZ.Sequence.PreviewRenderingPresetPath", _PREVIEW_PRESET),
                 ("MZ.Sequence.PreviewRenderingPresetCodec", int.from_bytes(b"apcs", "big")),
                 ("MZ.Sequence.PreviewRenderingClassID", "1061109567"),
                 ("MZ.Sequence.PreviewFrameSizeWidth", w), ("MZ.Sequence.PreviewFrameSizeHeight", h)):
        _el(props, k, v)
    if zero_point.ticks:
        _el(props, "MZ.ZeroPoint", zero_point.ticks)
    _el(_el(seq, "PersistentGroupContainer", Version="1"), "LinkContainer", Version="1")
    tg = _el(seq, "TrackGroups", Version="1")
    for i, (media, role) in enumerate(((_VIDEO_MEDIA, "video_group"), (_AUDIO_MEDIA, "audio_group"),
                                       (_DATA_MEDIA, "data_group"))):
        pair = _el(tg, "TrackGroup", Version="1", Index=i)
        _el(pair, "First", media)
        _ref(pair, "Second", oid(role))
    _el(seq, "Name", name)
    uhd = w >= 3840
    _el(seq, "PreviewFormatIdentifier", _PREVIEW_UHD if uhd else _PREVIEW_1080)  # GUESS off-preset sizes

    for ch in range(2):  # (:560-563)
        ser = make("ClipChannelSerializer", oid=oid(f"serializer_{ch}"))
        _el(ser, "SourceClipIndex", 0)
        _el(ser, "mSourceChannelIndex", ch)

    vg = make("VideoTrackGroup", oid=oid("video_group"))  # (:743-785)
    inner = _el(vg, "TrackGroup", Version="1")
    tr = _el(inner, "Tracks", Version="1")
    for i, u in enumerate(v_uids):
        _ref(tr, "Track", u, index=i)
    _el(inner, "FrameRate", tpf)
    _el(inner, "NextTrackID", video_tracks + 1)
    _el(vg, "ColorManagementSettings",
        '{"autoToneMapEnabled":true,"enableLogColorManagement":2,"lutInterpolationMethod":1}')
    if uhd:
        _el(vg, "AllowLinearCompositing", "false")
    _el(vg, "ImmersiveVideoVRConfiguration",
        '{"ambisonicsHRIR":"","ambisonicsMonitoringType":0,"capturedHorizontalView":360,'
        '"capturedVerticalView":180,"fieldOfHorizontalView":108,"fieldOfVerticalView":108,'
        '"projectionType":0,"stereoscopicEye":0,"stereoscopicType":0,"version":3}')
    _el(vg, "OutputColorSpace", _RGB_FULL)
    _el(vg, "AutoInputGamutCompressionEnabled", "true")
    _el(vg, "IsGraphicsWhiteSameAsProject", "false")
    _el(vg, "IsColorAwareEffectsEnabledSameAsProject", "false")
    _el(vg, "FrameRect", f"0,0,{w},{h}")
    _ref(_el(vg, "ComponentOwner", Version="1"), "Components", oid("video_chain"))

    ag = make("AudioTrackGroup", oid=oid("audio_group"))  # (:788-803)
    inner = _el(ag, "TrackGroup", Version="1")
    tr = _el(inner, "Tracks", Version="1")
    for i, u in enumerate(a_uids):
        _ref(tr, "Track", u, index=i)
    _el(inner, "FrameRate", _AUDIO_TPF_48K)
    _el(inner, "NextTrackID", audio_tracks + 2)
    _ref(ag, "MasterTrack", oid("mix_track"))
    _el(ag, "ID", _guid())
    _el(ag, "AutomationSafeFlags", "0")
    _el(ag, "NumAdaptiveChannels", "2")
    dg = make("DataTrackGroup", oid=oid("data_group"))  # (:576-579)
    inner = _el(dg, "TrackGroup", Version="1")
    _el(inner, "FrameRate", tpf)
    _el(inner, "NextTrackID", "1")

    def clip_track(tag, media, uid, track_id, index, targeted):  # (:407-446)
        t = make(tag, uid=uid)
        ct = _el(t, "ClipTrack", Version="2")
        inner = _el(ct, "Track", Version="4")
        _, props = _bag(inner)
        _el(props, "TL.SQTrackExpanded", "0")
        _el(props, "TL.SQTrackExpandedHeight", "41")
        _el(props, "MZ.SourceTrackState", "0")
        _el(props, "MZ.SourceTrackNumber", index)
        _el(props, "MZ.TrackTargeted", "1" if targeted else "0")
        if index < 2:
            _el(props, "TL.SQTrackShy", "0")
        if tag == "AudioClipTrack":
            if index < 2:
                _el(props, "TL.SQTrackAudioKeyframeStyle", "0")
            _el(props, "CM.KeyframeMode", "true")
        _el(inner, "ID", track_id)
        _el(inner, "MediaType", media)
        _el(inner, "Index", index)
        for holder in ("ClipItems", "TransitionItems"):
            hold = _el(ct, holder, Version="3")
            _el(hold, "MediaType", media)
            _el(hold, "Index", index)
        return t

    for i, u in enumerate(v_uids):
        clip_track("VideoClipTrack", _VIDEO_MEDIA, u, i + 1, i, i == 0)
    _el(make("VideoComponentChain", oid=oid("video_chain")), "ComponentChain", Version="3")
    for i, u in enumerate(a_uids):  # (:597-614)
        t = clip_track("AudioClipTrack", _AUDIO_MEDIA, u, i + 2, i, True)
        at = _el(t, "AudioTrack", Version="12")
        _ref(_el(at, "ComponentOwner", Version="1"), "Components", oid(f"strip_chain_{i}"))
        _ref(at, "Panner", oid(f"panner_{i}"))
        _el(at, "ID", _guid())
        _el(at, "NextPannerID", _NEXT_PANNER_ID)
    mix = make("AudioMixTrack", oid=oid("mix_track"))  # (:620-638)
    at = _el(mix, "AudioTrack", Version="12")
    _ref(_el(at, "ComponentOwner", Version="1"), "Components", oid("master_strip_chain"))
    _ref(at, "Panner", oid("default_panner"))
    _el(at, "ID", _guid())
    _el(at, "SubType", "3")
    _el(at, "Assign", "0")
    _el(at, "NextPannerID", _NEXT_PANNER_ID)
    inner = _el(mix, "Track", Version="4")
    _, props = _bag(inner)
    _el(props, "TL.SQTrackExpanded", "0")
    _el(props, "TL.SQTrackExpandedHeight", "41")
    _el(inner, "ID", "1")
    _el(inner, "MediaType", _AUDIO_MEDIA)
    _el(inner, "Index", "0")
    _ref(mix, "Inlet", oid("inlet"))

    def audio_component(parent, component_id, ctype, params):  # (:362-381)
        ac = _el(parent, "AudioComponent", Version="3")
        comp = _el(ac, "Component", Version="7")
        if params:
            hold = _el(comp, "Params", Version="1")
            for i, p in enumerate(params):
                _ref(hold, "Param", p, index=i)
        _el(comp, "ID", component_id)
        _el(ac, "FrameRate", _AUDIO_TPF_48K)
        _el(ac, "AudioChannelLayout", _STEREO)
        _el(ac, "ChannelType", "1")
        _el(ac, "AudioComponentType", ctype)

    def strip_chain(role, strip):  # (:391-404)
        c = make("AudioComponentChain", oid=oid(role))
        comps = _el(_el(c, "ComponentChain", Version="3"), "Components", Version="1")
        _ref(comps, "Component", oid(f"fader_{strip}"), index=0)
        _ref(comps, "Component", oid(f"meter_{strip}"), index=1)
        _el(c, "AudioChannelLayout", _STEREO)
        _el(c, "ChannelType", "1")

    strips = [str(i) for i in range(audio_tracks)] + ["master"]
    for i in range(audio_tracks):  # (:641-677)
        strip_chain(f"strip_chain_{i}", str(i))
        pan = make("StereoToStereoPanProcessor", oid=oid(f"panner_{i}"))
        audio_component(_el(pan, "PanProcessor", Version="3"), _PANNER_COMPONENT_ID, "0",
                        [oid(f"balance_{i}")])
    strip_chain("master_strip_chain", "master")  # (:679-685)
    dp = make("DefaultPanProcessor", oid=oid("default_panner"))
    audio_component(_el(dp, "PanProcessor", Version="3"), _PANNER_COMPONENT_ID, "0", [])
    _el(dp, "DefaultPannerInputChannelType", "1")
    _el(dp, "DefaultPannerOutputChannelType", "1")
    inlet = make("AudioTrackInlet", oid=oid("inlet"))  # (:691-696)
    srcs = _el(inlet, "Sources", Version="1")
    for i, u in enumerate(a_uids):
        _ref(srcs, "Source", u, index=i)
    _el(inlet, "AudioChannelLayout", _STEREO)
    for strip in strips:  # (:698-727)
        fader = make("AudioFader", oid=oid(f"fader_{strip}"))
        audio_component(fader, "1", "1", [oid(f"volume_{strip}"), oid(f"mute_{strip}")])
        meter = make("AudioMeter", oid=oid(f"meter_{strip}"))
        audio_component(meter, "2", "2", [])
        if strip == "master":
            continue
        bal = make("ScalarParam", oid=oid(f"balance_{strip}"), element_tag="AudioComponentParam")
        _el(bal, "StartKeyframe", "-91445760000000000,0.5,0,0,0,0,0,0")
        _el(bal, "CurrentValue", "0.5")
        _el(bal, "Name", "Balance")
        _el(bal, "IsInverted", "true")
    for strip in strips:  # (:729-740)
        vol = make("ScalarParam", oid=oid(f"volume_{strip}"), element_tag="AudioComponentParam")
        _el(vol, "Name", "Volume")
        _el(vol, "UpperBound", "5.6234130859375")
        _el(vol, "RangeLocked", "false")
        _el(vol, "UnitsString", "dB")
        mute = make("BoolParam", oid=oid(f"mute_{strip}"), element_tag="AudioComponentParam")
        _el(mute, "Name", "Mute")
        _el(mute, "RangeLocked", "false")
    make.attach_all()
    _add_item(doc, bin_el, item_uid)

    s = Sequence(name=name, uid=seq_uid, rate=rate, frame_size=(w, h), zero_point=zero_point,
                 video_tracks=[Track("Video", uid=u, index=i, track_id=i + 1) for i, u in enumerate(v_uids)],
                 audio_tracks=[Track("Audio", uid=u, index=i, track_id=i + 2) for i, u in enumerate(a_uids)],
                 extras={"audio_ticks_per_frame": _AUDIO_TPF_48K, "audio_channels": 2})
    project.sequences.append(s)
    project.clips[master_uid] = MasterClip(name=name, uid=master_uid, item_uid=item_uid,
                                           sequence_uid=seq_uid, duration=Time(0))
    bin.clips.append(master_uid)
    return s


# ------------------------------------------------------------------ place

def _source_raw(ticks: int, speed: Fraction) -> int:
    """Source-domain ticks -> the stored timeline-domain value that reads back exactly
    as `ticks` under int(raw / speed) (track_item.py:555-588)."""
    if speed == 1:
        return ticks
    raw = round(ticks * speed)
    while int(raw / speed) < ticks:
        raw += 1
    while raw > 0 and int((raw - 1) / speed) >= ticks:
        raw -= 1
    return raw


def place(doc: Doc, seq: Sequence, track_index: int, master_clip: MasterClip, at: Time,
          in_: Time, out: Time, *, speed: float | Fraction = 1) -> list[TrackItem]:
    """Place `master_clip` [in_, out) (SOURCE time) at timeline `at` on V<n> and A<n>,
    linked. Returns the placed TrackItems (video first when present).

    Per half (track.py:842-937): a deep copy of the master's template clip with a
    fresh ClipID, no InUse, InPoint/OutPoint before ClipID (:913-917, raw =
    source*speed, track_item.py:555-588), audio halves get their own
    SecondaryContent objects (:1221-1235); SubClip {Clip, MasterClip, Name,
    OrigChGrp} (:266-276); a fresh default component chain (:279-321); the
    Video/AudioClipTrackItem with ClipTrackItem/TrackItem Start (elided at 0) and
    End (:335-393, :1237-1256); a TrackItems/TrackItem ref on the track before
    MediaType (:1258-1275). The halves are joined by a Link object listed in the
    sequence's PersistentGroupContainer/LinkContainer/Links (sequence.py:604-626,
    merged_builder.py:441-460). The sequence's SequenceSource OriginalDuration
    grows to cover the content (sequence.py:356-385).
    """
    speed = Fraction(speed).limit_denominator(1000000)
    if speed <= 0:
        raise BuildError("speed must be positive")
    if out.ticks <= in_.ticks:
        raise BuildError("out must be after in")
    seq_el = find_element(doc, seq.uid)
    master_el = find_element(doc, master_clip.uid)
    if master_clip.sequence_uid == seq.uid:
        raise BuildError("cannot place a sequence on its own timeline (track.py:860-865)")
    templates = {}
    for ref in master_el.findall("Clips/Clip"):
        el = doc.find(ref.get("ObjectRef", ""))
        if el is not None:
            templates.setdefault(el.tag, el)
    raw_in, raw_out = _source_raw(in_.ticks, speed), _source_raw(out.ticks, speed)
    length = round(Fraction(out.ticks - in_.ticks) / speed)
    start, end = at.ticks, at.ticks + length
    make = _Obj(doc)
    placed: list[tuple[ET.Element, ET.Element, Track, TrackItem]] = []
    for kind, tag, tracks in (("Video", "VideoClip", seq.video_tracks),
                              ("Audio", "AudioClip", seq.audio_tracks)):
        template = templates.get(tag)
        if template is None:
            continue
        if track_index >= len(tracks):
            raise BuildError(f"sequence {seq.name!r} has no {kind} track {track_index + 1}")
        track = tracks[track_index]
        clip = copy.deepcopy(template)
        clip.set("ObjectID", doc.new_id())
        core = clip.find("Clip")
        for leaf in core.findall("InUse") + core.findall("InPoint") + core.findall("OutPoint"):
            core.remove(leaf)
        at_clipid = list(core).index(core.find("ClipID"))
        new = [("OutPoint", raw_out), ("InPoint", raw_in)]
        if speed != 1:
            new.append(("PlaybackSpeed", str(float(speed))))  # GUESS on position
        for offset, (t, v) in enumerate(new):
            leaf = ET.Element(t)
            leaf.text = str(v)
            core.insert(at_clipid + offset, leaf)
        core.find("ClipID").text = _guid()
        if tag == "AudioClip":
            for item_ref in clip.findall("SecondaryContents/SecondaryContentItem"):
                tmpl = find_element(doc, item_ref.get("ObjectRef"))
                sc = make("SecondaryContent")
                _ref(sc, "Content", tmpl.find("Content").get("ObjectRef"))
                _el(sc, "ChannelIndex", tmpl.findtext("ChannelIndex") or "0")
                item_ref.set("ObjectRef", sc.get("ObjectID"))
        make.made.append(clip)
        sub = make("SubClip")
        _ref(sub, "Clip", clip.get("ObjectID"))
        _ref(sub, "MasterClip", master_clip.uid)
        _el(sub, "Name", master_clip.name)
        _el(sub, "OrigChGrp", "0")
        if kind == "Video":
            chain = make("VideoComponentChain")
            for k, v in (("DefaultMotion", "true"), ("DefaultOpacity", "true"),
                         ("DefaultMotionComponentID", "1"), ("DefaultOpacityComponentID", "2")):
                _el(chain, k, v)
            _el(chain, "ComponentChain", Version="3")
        else:
            chain = make("AudioComponentChain")
            _el(chain, "DefaultVol", "true")
            _el(chain, "DefaultVolumeComponentID", "1")
            cc = _el(chain, "ComponentChain", Version="3")
            _, props = _bag(cc)
            _el(props, "MZ.ComponentChain.ActiveComponentID", "1")
            _el(props, "MZ.ComponentChain.ActiveComponentParamIndex", "4294967295")
        item = make(f"{kind}ClipTrackItem")
        cti = _el(item, "ClipTrackItem", Version="8")
        _ref(_el(cti, "ComponentOwner", Version="1"), "Components", chain.get("ObjectID"))
        ti = _el(cti, "TrackItem", Version="4")
        if start:
            _el(ti, "Start", start)
        _el(ti, "End", end)
        _ref(cti, "SubClip", sub.get("ObjectID"))
        if kind == "Video":
            stream = _video_stream(doc, core)
            _el(item, "PixelAspectRatio",
                (stream.findtext("PixelAspectRatio") if stream is not None else None) or "1,1")
            _el(item, "ToneMapSettings", '{"peak":-1,"version":3}')
            rect = stream.findtext("FrameRect") if stream is not None else None
            if rect:
                _el(item, "FrameRect", rect)
        else:
            _el(item, "ID", _guid())
            _el(item, "PreRenderComponentChainHashVersion", "1")
        model_item = TrackItem(name=master_clip.name, oid=item.get("ObjectID"), start=Time(start),
                               end=Time(end), in_=Time(raw_in), out=Time(raw_out),
                               master_uid=master_clip.uid, clip_oid=clip.get("ObjectID"),
                               subclip_oid=sub.get("ObjectID"))
        if speed != 1:
            model_item.extras["playback_speed"] = str(float(speed))
        placed.append((item, find_element(doc, track.uid), track, model_item))
    if not placed:
        raise BuildError(f"{master_clip.name!r} has no video or audio clip to place")
    if len(placed) > 1:
        link = make("Link")
        items = _el(_el(link, "TrackItemGroup", Version="1"), "TrackItems", Version="1")
        for i, (item, *_rest) in enumerate(placed):
            _ref(items, "TrackItem", item.get("ObjectID"), index=i)
    make.attach_all()
    for item, track_el, track, model_item in placed:
        holder = track_el.find("ClipTrack/ClipItems")
        lst = holder.find("TrackItems")
        if lst is None:
            lst = ET.Element("TrackItems", {"Version": "1"})
            holder.insert(0, lst)  # before MediaType (track.py:1264-1272)
        _ref(lst, "TrackItem", item.get("ObjectID"), index=len(lst))
        _touch(doc, track_el)
        track.items.append(model_item)
    if len(placed) > 1:
        lc = seq_el.find("PersistentGroupContainer/LinkContainer")
        links = lc.find("Links")
        if links is None:
            links = _el(lc, "Links", Version="1")
            lc.text = None
        _ref(links, "Link", link.get("ObjectID"), index=len(links))
        _touch(doc, seq_el)
    _grow_sources(doc, seq)
    return [p[3] for p in placed]


def _video_stream(doc: Doc, core: ET.Element) -> ET.Element | None:
    """Clip core -> Source -> MediaSource/Media -> VideoStream (track.py:1207-1219)."""
    src = doc.find((core.find("Source").get("ObjectRef") or "")) if core.find("Source") is not None else None
    mref = None if src is None else src.find("MediaSource/Media")
    media = None if mref is None else doc.find(mref.get("ObjectURef", ""))
    sref = None if media is None else media.find("VideoStream")
    return None if sref is None else doc.find(sref.get("ObjectRef", ""))


def _grow_sources(doc: Doc, seq: Sequence) -> None:
    content = seq.end.ticks
    for site in doc.refs_to(seq.uid):
        owner = site.owner
        if not owner.tag.endswith("SequenceSource"):
            continue
        dur = owner.find("OriginalDuration")
        if dur is not None and int(dur.text or 0) < content:
            dur.text = str(content)


# ------------------------------------------------------------------ save

_VERSIONED = re.compile(r"^(?P<base>.*)_v(?P<n>\d{3,})$")


def next_version_path(path_base: str | os.PathLike) -> Path:
    """`/dir/NAME` (or NAME.prproj, or NAME_v004.prproj) -> /dir/NAME_v<next>.prproj."""
    p = Path(path_base)
    stem = p.name[:-7] if p.name.endswith(".prproj") else p.name
    m = _VERSIONED.match(stem)
    if m:
        stem = m.group("base")
    folder = p.parent
    pat = re.compile(re.escape(stem) + r"_v(\d{3,})\.prproj$")
    taken = [int(mm.group(1)) for f in (os.listdir(folder) if folder.is_dir() else [])
             if (mm := pat.match(f))]
    return folder / f"{stem}_v{max(taken, default=0) + 1:03d}.prproj"


def save_version(doc: Doc, path_base: str | os.PathLike) -> Path:
    """Write the next `_vNNN.prproj` beside `path_base`; never overwrites.
    gzip framing and the .tmp-then-replace write are framing.write_prproj's."""
    target = next_version_path(path_base)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise BuildError(f"{target} exists; refusing to overwrite")
    return write_prproj(target, doc.serialize(), gzip=True)
