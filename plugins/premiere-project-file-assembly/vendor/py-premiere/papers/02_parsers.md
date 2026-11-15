# 02 Parsers (`py_premiere/parsers/`)

## 0 Executive summary
- What: reads the XML tree and builds model objects (Application, Project, Sequence, tracks, clips, components, markers, captions).
- In: a `PremiereDocument` and the file path. Out: an `Application` object.
- Called by: `py_premiere.parse()` and `new()`, and (lazily) by models/project and models/sequence.
- Imports models, enums; xml only for type hints.
- Watch: models and parsers import each other (one at load time, one at call time).

## 1 Files
| file | lines | purpose |
|---|---|---|
| `__init__.py` | 7 | exports `parse_application` |
| `application.py` | 52 | top entry; finds the active sequence |
| `project.py` | 114 | bins, clips, media paths |
| `sequence.py` | 102 | sequences, tracks, track items, transitions |
| `component.py` | 82 | effect components and parameters |
| `marker.py` | 26 | markers |
| `caption.py` | 86 | caption tracks and text |

## 2 Public names
- `application.py`: `parse_application` :41.
- `project.py`: `parse_project` :103.
- `sequence.py`: `parse_sequence` :68.
- `component.py`: `parse_components` :56, `parse_selection_components` :71.
- `marker.py`: `parse_markers` :15.
- `caption.py`: `parse_caption_tracks` :59.
- Private helpers: `_active_sequence_id`, `_media_path`, `_parse_item`, `_assign_node_ids`, `_parse_track_item`, `_parse_track`, `_parse_frame_rect`, `_inner_component`, `_build_component`, `_parse_chain`, `_text_data`, `_hash`, `_caption_text`, `_source_times`.

## 3 Seams
Imports from other sections:
- `from ..models.caption import (` ... ; `from ..models.time import Time` (caption.py)
- `from ..enums import ProjectItemType`; `from ..models import Project, ProjectItem`; `from ..models.project_item import clip_core, item_container` (project.py)
- `from ..models import Component, ComponentParam` (component.py)
- `from ..models import Sequence, Track, TrackItem, Transition` (sequence.py)
- `from ..models import Marker` (marker.py); `from ..models import Application` (application.py)
- Type-hint only: `from ..xml import PremiereDocument` in every file; `from ..models.sequence import Sequence`, `from ..models import TrackItem`, `from ..models import Project`.
Imported by:
- `from .parsers import parse_application` (`__init__.py`)
- `from ..parsers.sequence import parse_sequence` (inside functions, models/project.py x3)
- `from ..parsers.caption import parse_caption_tracks` (inside a function, models/sequence.py)

## 4 Setup and minimal call
Not run here.
```python
import sys
sys.path.insert(0, "<plugin>/vendor/py-premiere")
from pathlib import Path
from py_premiere.xml import parse_prproj
from py_premiere.parsers import parse_application
p = Path("/path/in.prproj")
app = parse_application(parse_prproj(p), p)
print(app.project.name)
```

## 5 Safety notes
- No file access in this section (review table: "parsers/*.py (7) none").
- No eval/exec; media paths are read from the XML as strings only.

## 6 Known limits
- `project.py:18` `_ITEM_TYPES` is a fixed table; other item kinds are not mapped.
- No TODO, FIXME or assert in this section. Parsing is lenient: a missing object yields None or empty lists rather than an error (see `_active_sequence_id` and `_media_path`, which return `None`).
- Parsers build objects that point into the live tree; edits go back to that same tree, not a copy.
