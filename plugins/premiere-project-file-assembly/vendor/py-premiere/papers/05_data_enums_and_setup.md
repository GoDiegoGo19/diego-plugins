# 05 Data, enums and setup (`data/`, `enums/`, `__init__.py`)

## 0 Executive summary
- What: constant tables, Premiere enums, and the two entry points `parse()` and `new()`.
- In: a path or nothing. Out: an `Application`.
- Called by: models (data, enums), parsers (enums), cli (data).
- Imports nothing from the library except `__init__.py`, which pulls in models, parsers, xml.
- Watch: `parse()` may read Premiere's prefs unless you pass `preferences_path`.

## 1 Files
| file | lines | purpose |
|---|---|---|
| `__init__.py` | 70 | `parse`, `new`, `__version__` |
| enums/`__init__.py` | 25 | exports |
| enums/general.py | 151 | eight IntEnums |
| data/`__init__.py` | 1 | empty |
| data/mask_template.py | 377 | mask parameter template |
| data/caption_template.py | 45 | caption payload pieces |
| data/marker_colors.py | 33 | marker colour map |
| data/param_classes.py | 21 | verified param class ids |
| data/text_template.py | 17 | text payload (base64 literal) |

## 2 Public names
- `__init__.py`: `parse` :29, `new` :49, `__version__`, re-exports `Time`, `TICKS_PER_SECOND`.
- enums/general.py: `ProjectItemType` :8, `KeyframeInterpolation` :21, `CaptionFormat` :40, `BlendMode` :59, `AlphaUsage` :100, `VideoFieldType` :112, `GeneratorType` :124, `TimeInterpolationType` :140.
- data: `SOURCE_TEXT_PAYLOAD` (text_template :9), `PAYLOAD_HEAD/TAIL`, `COLLECTION_METADATA` (caption_template :12-30), `MARKER_COLOR_PACKED` :14, `PACKED_TO_INDEX` :25, `MASK_CLASS_ID` :10, `MASK_PARAMS` :22, `VERIFIED_VALUE_CLASS_IDS` :14.

## 3 Seams
- data/enums import nothing from the library.
- `__init__.py` imports: `from .models import TICKS_PER_SECOND, Time`; `from .models.skeleton import build_empty_project`; `from .parsers import parse_application`; `from .xml import PremiereDocument, parse_prproj`; `from .xml.gzip_io import GzipFraming`.
- Imported by: models (`..enums`, `..data.*`), parsers (`..enums`), cli/validate (`..data.param_classes`).

## 4 Setup and minimal call
Not run here. No install step: copy-in use.
```python
import sys
sys.path.insert(0, "<plugin>/vendor/py-premiere")   # folder that holds py_premiere/
import py_premiere
app = py_premiere.parse("/path/in.prproj", preferences_path="/dev/null")
print(py_premiere.__version__)    # "0.0.0" from a source tree
```
Python: `pyproject.toml` says `>=3.7`, classifiers 3.7 to 3.14. Dependencies: none (only `importlib_metadata` on 3.7, guarded at `__init__.py:9-11`). Licence MIT. `pyproject` lists four console scripts and a hatch build with `packages = ["src/py_premiere"]`, which does not match this folder layout; the vendored copy is not pip-installed.

## 5 Safety notes
- F1 `__init__.py:35-40`: no `preferences_path` means a read-only scan of the user's Premiere prefs folder; absent on a cloud box, so defaults are used. Pass a path to pin it.
- F6 data/*.py: base64 decode of literal constants (caption_template :12-15, text_template :9) only; kept as bytes.
- Nothing in these files writes to disk.

## 6 Known limits
- `__version__` is "0.0.0" without an installed package (`__init__.py:26`).
- `new()` builds 9 objects only; Premiere restores the rest on first save (docstring :49).
- `data/caption_template.py` and `mask_template.py` are fixed captures; values like `TEMPLATE_PADDED = 24` (:27), `MASK_VERSION = "9"` are hard-coded.
- The 3.7 claim was not tested; I did not check every file for newer syntax.
