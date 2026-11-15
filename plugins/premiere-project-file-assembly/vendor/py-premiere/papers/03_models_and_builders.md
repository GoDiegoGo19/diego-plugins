# 03 Models and builders (`py_premiere/models/`)

## 0 Executive summary
- What: the typed project objects (Project, Sequence, Track, TrackItem, ...) and the builders that write new objects into the XML tree.
- In: parsed objects, plus caller paths for media, SRT, .mogrt. Out: edited tree; file on `save`.
- Called by: parsers, `__init__`, export, cli.
- Imports xml, enums, data, and parsers (lazily).
- Watch: `project.py` reads media and prefs; `.mogrt` import can write outside the project folder (F2).

## 1 Files (26)
| file | lines | purpose |
|---|---|---|
| project.py | 1698 | Project; save; import_files; add_sequence; merged/multicam |
| media_import.py | 1623 | read image/audio/video headers by hand |
| project_item.py | 1467 | bins and clips, footage settings |
| caption.py | 1268 | captions, text/style payload bytes |
| track.py | 1282 | tracks; add/move/remove clip, transition, graphic, mgt |
| sequence.py | 823 | Sequence, SequenceSettings |
| sequence_builder.py | 860 | new-sequence XML, format presets |
| component.py | 946 | effect components, keyframes |
| track_item.py | 694 | timeline clip |
| merged_builder.py | 574 | merged-clip XML |
| caption_builder.py | 511 | caption XML, SRT cues |
| mgt_builder.py | 488 | .mogrt reader, template XML |
| multicam_builder.py | 426 | multicam XML |
| graphic_builder.py | 363 | text graphic XML |
| marker.py | 282 | markers |
| validators.py | 277 | argument checks |
| time.py | 235 | Time in ticks |
| skeleton.py | 209 | empty project XML |
| mask_builder.py | 150 | mask XML |
| transition.py | 145 | transitions |
| preferences.py | 138 | Premiere prefs reader |
| descriptors.py | 120 | XmlField property helper |
| named_list.py | 62 | lookup list |
| __init__.py | 39 | exports |
| application.py | 23 | Application |
| color.py | 14 | Color |

## 2 Public names
- Core objects: `Application` application.py:11; `Project` project.py:209 (`import_files` :306, `add_sequence` :602, `create_merged_clip` :669, `create_multicam_clip` :1024, `save` :1642, `save_in_place` :1657); `ProjectItem` project_item.py:535 (`add_bin` :603, `move_to` :687, `create_sub_clip` :1217, `attach_proxy` :1071, `add_marker` :1416); `FootageInterpretation` :266; `Sequence` sequence.py:227 (`add_linked_clip` :568, `import_mgt` :639, `create_caption_track` :657); `SequenceSettings` :96; `Track` track.py:394 (`add_clip` :842, `insert_clip` :1142, `move_clip` :782, `remove_clip` :749, `add_transition` :573, `add_graphic` :939, `add_mgt` :1039); `TrackItem` track_item.py:145 (`add_mask` :239, `set_time_remap` :458); `Time` time.py:49; `Component` component.py:801; `ComponentParam` :317; `Marker` marker.py:76; `Color` color.py:8; `Caption` caption.py:875; `CaptionTrack` :1216; `Transition` transition.py:16; `Preferences` preferences.py:51; `NamedList` named_list.py:13; `XmlField` descriptors.py:22.
- Builders: `build_empty_project` skeleton.py:129; `build_sequence` sequence_builder.py:449; `build_multicam` multicam_builder.py:95; `build_merged_sequence` merged_builder.py:98; `new_caption_track` caption_builder.py:400, `parse_srt` :77; `build_graphic_clip` graphic_builder.py:308; `build_mask_component` mask_builder.py:60; `build_template_item` mgt_builder.py:346, `Template` :138; media readers `read_png_size`... `read_mp4_info` :557, `read_wav_info` :723 (media_import.py).
- Validators: `validate_enum` :77, `validate_string` :135, `validate_path` :179, `validate_color` :235 (validators.py).

## 3 Seams
Imports from other sections:
- `from ..xml.mutations import ...` and `from ..xml.document import ReferenceIndex`; `from ..xml import PremiereDocument` (hints)
- `from ..enums import ProjectItemType` (project, project_item), `CaptionFormat` (caption, sequence), `BlendMode, TimeInterpolationType` (track_item), `KeyframeInterpolation` (component)
- `from ..data.text_template import SOURCE_TEXT_PAYLOAD` (graphic_builder); `from ..data.caption_template import (` (caption_builder); `from ..data.mask_template import (` (mask_builder); `from ..data.marker_colors import (` (marker)
- `from ..parsers.sequence import parse_sequence` (project.py, three call-time imports); `from ..parsers.caption import parse_caption_tracks` (sequence.py)
Imported by: parsers (`from ..models import ...`), `__init__.py` (`from .models import TICKS_PER_SECOND, Time`; `from .models.skeleton import build_empty_project`), `export/fcpxml.py` (`from ..models.time import TICKS_PER_SECOND`), cli/validate and cli/visualize.

## 4 Setup and minimal call
Not run here.
```python
import sys
sys.path.insert(0, "<plugin>/vendor/py-premiere")
import py_premiere
app = py_premiere.new()
seq = app.project.add_sequence("Rough", preset="1080p23976")
app.project.save("/path/out.prproj")   # refuses to overwrite
```

## 5 Safety notes
- F1 preferences.py:33-48, project.py:338-341: with no `preferences_path`, reads newest `~/Documents/Adobe/Premiere Pro/*/Profile-*/Adobe Premiere Pro Prefs`. Read only.
- F2 mgt_builder.py:193-200, track.py:1063-1075: .mogrt capsuleID/name are used in an output path with no `../` check; add only trusted .mogrt files.
- F3 project.py:1655-1680: `save` will not overwrite; `save_in_place` does; both write `<name>.tmp` then replace.
- Reads caller paths: project.py:160, 1454, 1581, SRT :367.

## 6 Known limits
- About 25 `NotImplementedError` raises mark unsupported cases: project.py:174, 189, 1309, 1363, 1405, 1463, 1577; track.py:1162 (insert splits a clip), 1192 (unmarked still); media_import.py:361 (non-integer AVI rates), 1044, 1200.
- Hard-coded: label colours media_import.py:54-67, 166-167; still frame rate :163; `DEFAULT_PRESET = "1080p23976"` sequence_builder.py:268; `TICKS_PER_SECOND` time.py:10.
