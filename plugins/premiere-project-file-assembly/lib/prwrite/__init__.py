"""prwrite: our own Premiere .prproj library (read, edit, write).

In:  a .prproj path (gzip or plain XML).
Out: `Doc` (ElementTree + object index) and back to a .prproj.

    from prwrite import read_prproj, write_prproj, Doc
    doc = Doc.parse(read_prproj("in.prproj"))
    write_prproj("out.prproj", doc.serialize())

Schema facts are credited to py-premiere (MIT); see ATTRIBUTION.md.
"""

from .document import Doc, DocumentError
from .framing import FramingError, read_prproj, write_prproj

__version__ = "0.1.0"
__all__ = ["Doc", "DocumentError", "FramingError", "read_prproj", "write_prproj", "__version__"]
