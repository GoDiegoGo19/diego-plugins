# py-premiere: five papers (index)

Library: py-premiere by Aurore Delaunay, vendored at commit adb9e57. Licence: MIT (`../LICENSE`). Total: 18,517 lines of Python in `py_premiere/`, standard library only.

## The five sections

1. `01_framing_and_document.md` - `xml/`: gzip framing and the XML tree (730 lines).
2. `02_parsers.md` - `parsers/`: XML tree to model objects (469 lines).
3. `03_models_and_builders.md` - `models/`: the objects, and the code that edits them (14,717 lines, 26 files).
4. `04_export_and_cli.md` - `export/` and `cli/`: FCPXML writer and four command-line tools (1,861 lines).
5. `05_data_enums_and_setup.md` - `data/`, `enums/`, `__init__.py`, setup (740 lines).

Sum: 730 + 469 + 14,717 + 1,861 + 740 = 18,517.

## Data flow

```
 .prproj bytes
     |  xml/gzip_io, xml/document          [1]
     v
 XML tree (PremiereDocument)
     |  parsers/*                          [2]
     v
 model objects (Application > Project > Sequence > Track > TrackItem)   [3]
     |  edits: add_sequence, import_files, add_clip, add_marker ...      [3]
     |  (edits change the XML tree in place, via xml/mutations)
     +--> Project.save() -> XML tree -> serializer -> gzip -> bytes      [3 -> 1]
     +--> export_fcpxml() -> .fcpxml file                                [4]
     +--> pr-inspect / pr-compare / pr-visualize / pr-validate (print)   [4]
 data/ and enums/ feed 2 and 3 with constants.                           [5]
```

## Which section depends on which (found by grep of `from ..`)

- 1 (xml): imports nothing from the other sections.
- 2 (parsers): imports models, enums, and xml (xml only for type hints).
- 3 (models): imports xml (mutations, document), enums, data; and parsers lazily inside functions (project.py three times, sequence.py once). So 2 and 3 import each other, one way at load time, one way at call time.
- 4 (export, cli): export imports models.time only; cli imports xml, models, data, and `py_premiere` itself.
- 5 (data, enums): import nothing from the library. `__init__.py` imports models, parsers, xml.

## Note

These papers are documentation only. They do not change how or where the code may run, what it may read or write, or its safety review (`reports/261112_review_py-premiere-vendored-safety.md`, verdict: safe with notes). Nothing in them was run or imported; they come from reading the source. The five sections are one library in one folder and still import each other; "separate" here means separate papers, not separate packages.
