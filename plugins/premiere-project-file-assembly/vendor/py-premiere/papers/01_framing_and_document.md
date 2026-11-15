# 01 Framing and document (`py_premiere/xml/`)

## 0 Executive summary
- What: turns a `.prproj` file (gzip around XML) into an editable XML tree and back, byte for byte.
- In: a file path or bytes. Out: `PremiereDocument` holding the tree; bytes on save.
- Called by: parsers, models, `__init__.parse/new`, `cli/compare`, `cli/inspect`.
- Imports nothing from the other sections.
- Watch: gzip inflate has no size cap; `save` writes where told.

## 1 Files
| file | lines | purpose |
|---|---|---|
| `__init__.py` | 11 | exports `PremiereDocument`, `parse_prproj` |
| `gzip_io.py` | 98 | read gzip header, inflate/deflate |
| `document.py` | 320 | the document: tree, object ids, references, payloads |
| `mutations.py` | 203 | helpers that add/remove/insert tree nodes |
| `serializer.py` | 98 | write the tree to XML text exactly as Premiere does |

## 2 Public names
- `gzip_io.py`: `GzipFraming` :27 (header bytes kept so output matches input; `.default()` :34), `decompress_prproj` :68, `compress_prproj` :91, constants `GZIP_MAGIC` :17, `DEFAULT_OS_BYTE` :19.
- `document.py`: `ReferenceIndex` :22 (who points at an object; `referrers_outside` :53), `PremiereDocument` :77 with `from_bytes` :128, `resolve` :149, `payload` :170, `payload_stored` :192, `next_object_id` :213, `add_object` :221, `attach_object` :238, `remove_object` :252, `owned_objects` :264, `to_xml_bytes` :301, `to_bytes` :305, `save` :309; `parse_prproj` :318.
- `mutations.py`: `indent_tree` :13, `build_leaf` :43, `append_uniform_child` :55, `append_pair` :73, `append_child` :97, `append_leaf` :122, `set_elided_flag` :129, `remove_child` :150, `insert_before` :171, `insert_leaf_before` :186.
- `serializer.py`: `serialize_document` :87, `serialize_element` :94, `PROLOG`, `EPILOGUE`.
- Private: `_header_length`, `_divergence_context`, `_is_reference`, `_write_element`, `_write_leaf_text`.

## 3 Seams
Imports from other sections: none. Internal: `document.py` has `from .gzip_io import compress_prproj, decompress_prproj`, `from .mutations import append_uniform_child, remove_child`, `from .serializer import serialize_document`.
Imported by others (exact lines):
- `from ..xml import parse_prproj` (cli/compare, cli/inspect); `from ..xml.serializer import serialize_element` (cli/inspect)
- `from ..xml.mutations import ...` (models: descriptors, track_item, caption_builder, track, project, sequence_builder, component, mask_builder, sequence, marker, multicam_builder, project_item, skeleton)
- `from ..xml.document import ReferenceIndex` (models/track, models/project_item)
- `from ..xml import PremiereDocument` (parsers, models; type hints only)
- `from .xml import PremiereDocument, parse_prproj` and `from .xml.gzip_io import GzipFraming` (`__init__.py`)

## 4 Setup and minimal call
Not run here.
```python
import sys
sys.path.insert(0, "<plugin>/vendor/py-premiere")
from py_premiere.xml import parse_prproj
doc = parse_prproj("/path/in.prproj")      # reads the file
data = doc.to_bytes()                      # same bytes back, in memory
```

## 5 Safety notes
- F4 `document.py:131`: `ET.fromstring`, default expat; no external entities fetched. Safe.
- F5 `gzip_io.py:73-86`: no size cap on inflate; a hostile `.prproj` could use much memory. CRC and length are checked.
- F6 `document.py:190,210`: base64/zlib only on payload blobs; kept as bytes, never run.
- `document.py:309-320`: `save` and `parse_prproj` read/write the path given.

## 6 Known limits
- `gzip_io.py:68-90`: a file without the gzip magic is read as plain XML (framing None); multi-member gzip is refused with ValueError (:84); bad CRC or length raises ValueError.
- `serializer.py` hard-codes the XML prolog (:24) and the trailing newlines (:25) to match Premiere.
- No TODO or assert statements in this section.
