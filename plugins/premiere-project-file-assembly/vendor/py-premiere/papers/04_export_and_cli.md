# 04 Export and CLI (`py_premiere/export/`, `py_premiere/cli/`)

## 0 Executive summary
- What: a one-way FCPXML writer and four command-line tools.
- In: a parsed Project (export); `.prproj` paths (cli). Out: an `.fcpxml` file; printed text.
- Called by: your code (`export_fcpxml`) or the shell (`pr-*` scripts, only if installed; vendored copy has no scripts, use `python -m`).
- Imports models, xml, data, and `py_premiere`.
- Watch: two commands can write and overwrite a file you name.

## 1 Files
| file | lines | purpose |
|---|---|---|
| export/__init__.py | 11 | exports `export_fcpxml` |
| export/fcpxml.py | 333 | write FCPXML 1.9 |
| cli/__init__.py | 1 | empty |
| cli/inspect.py | 184 | `pr-inspect`: summary, list, tree, dump, xml |
| cli/compare.py | 411 | `pr-compare`: structural diff of two files |
| cli/validate.py | 823 | `pr-validate`: check against ground-truth JSON |
| cli/visualize.py | 98 | `pr-visualize`: print item and sequence trees |

## 2 Public names
- `export_fcpxml(project, path, sequence=None)` fcpxml.py:287 returns the path written; `FCPXML_VERSION` :44.
- `inspect.main` :106; `compare.main` :308, `VOLATILE_ATTRS` :42; `validate.main` :727, `validate_file` :667, `Problems` :42, `KNOWN_DIVERGENCES` :71; `visualize.main` :65.
- Options: inspect `--list --tree --dump --xml --out`; compare two files; validate `--json --report-json --coverage`; visualize `--items --sequences`.
- Private: fcpxml `_Asset`, `_Resources`, `_build_spine`, `_connect`, `_clip_element`; compare `_match_objects`, `_align_children`, `_diff_pair`; validate `_validate_*` (about 12).

## 3 Seams
Imports from other sections:
- `from ..models.time import TICKS_PER_SECOND` (fcpxml); `from ..models import Project, Sequence, TrackItem` (hints)
- `from ..xml import parse_prproj` (compare, inspect); `from ..xml.serializer import serialize_element` (inspect)
- `from ..data.param_classes import VERIFIED_VALUE_CLASS_IDS`; `from ..models.time import UNSET_TICKS`; `import py_premiere` (validate)
- `import py_premiere`; `from ..models import TICKS_PER_SECOND` (visualize)
Imported by: nothing in the library. `pyproject.toml` maps the four commands to `py_premiere.cli.<name>:main`.

## 4 Setup and minimal call
Not run here.
```python
import sys
sys.path.insert(0, "<plugin>/vendor/py-premiere")
import py_premiere
from py_premiere.export import export_fcpxml
app = py_premiere.parse("/path/in.prproj")
export_fcpxml(app.project, "/path/out.fcpxml")
# shell, equivalent of pr-visualize:
#   cd <plugin>/vendor/py-premiere && python -m py_premiere.cli.visualize /path/in.prproj
```

## 5 Safety notes
- fcpxml.py:329 writes the caller's path (and a `<!DOCTYPE fcpxml>` line, output only; F8).
- F7: `pr-inspect --xml --out FILE` (inspect.py:168) and `pr-validate --report-json FILE` (validate.py:817) write to the path given and overwrite it. Others read only.
- validate reads a sibling `.json` by default.

## 6 Known limits
- fcpxml.py:279 raises `NotImplementedError` when a connected clip has no spine clip under it (it would be lost). Drop-frame test uses a fixed set (:50).
- `FCPXML_VERSION` is fixed at 1.9.
- validate.py `KNOWN_DIVERGENCES` is a hand-kept table.
- The `if __name__ == "__main__"` form exists (compare.py:411); I did not confirm `python -m` works for the other three.
