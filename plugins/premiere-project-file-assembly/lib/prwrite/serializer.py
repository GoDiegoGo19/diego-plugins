"""serializer: how Premiere spells XML, for elements we create or change.

In:  an `xml.etree.ElementTree.Element` (tag, ordered attrib, text, tail).
Out: `str` pieces in Premiere's style.

`Doc.serialize` copies untouched elements straight from the original bytes;
it only calls into this module for the parts that are new or edited. So the
rules here decide how OUR output looks, never how Premiere's output is
re-spelled.

Premiere's style (facts from the vendored py-premiere, see SPEC.md):
* declaration `<?xml version="1.0" encoding="UTF-8" ?>` + LF (note the space
  before `?>`), then the `PremiereData Version="3"` root, then LF LF after it;
* LF line ends, one TAB per nesting level, no spaces;
* attributes in insertion order; ObjectID / ObjectUID first, then ClassID,
  then Version on object elements;
* an empty element is self-closing (`<X ObjectRef="5"/>`) only when it is a
  pure reference; an empty content element is spelled out with its closing
  indent (`<X>` LF tabs `</X>`);
* leaf text is data: `& < >` escaped, CR and LF as `&#13;` `&#10;`; a leaf
  holding a base64 block ends with a raw LF + tabs before its close tag;
* attribute values escape `& < > "` plus CR, LF, TAB as `&#13; &#10; &#9;`.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

__all__ = [
    "DECLARATION",
    "TRAILER",
    "INDENT",
    "text_escape",
    "attr_escape",
    "start_tag",
    "end_tag",
    "leaf_text",
    "layout",
    "is_reference",
]

DECLARATION = '<?xml version="1.0" encoding="UTF-8" ?>\n'
TRAILER = "\n\n"
INDENT = "\t"

_TEXT_MAP = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;", "\r": "&#13;", "\n": "&#10;"})
_ATTR_MAP = str.maketrans(
    {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "\r": "&#13;", "\n": "&#10;", "\t": "&#9;"}
)
# LF followed by one or more tabs at the very end: closing indent, not data.
_CLOSING_RUN = re.compile(r"\n\t+\Z")


def text_escape(value: str) -> str:
    return value.translate(_TEXT_MAP)


def attr_escape(value: str) -> str:
    return value.translate(_ATTR_MAP)


def start_tag(tag: str, attrib: dict[str, str], *, self_closing: bool = False) -> str:
    attrs = "".join(f' {name}="{attr_escape(value)}"' for name, value in attrib.items())
    return f"<{tag}{attrs}{'/>' if self_closing else '>'}"


def end_tag(tag: str) -> str:
    return f"</{tag}>"


def leaf_text(value: str) -> str:
    """Text of a childless element, spelled as Premiere spells it."""
    if "\n" in value and value.isspace():
        return value  # expanded empty element: the text is only its closing indent
    run = _CLOSING_RUN.search(value)
    if run:
        return text_escape(value[: run.start()]) + run.group()
    return text_escape(value)


def formatting_text(value: str) -> str:
    """Text between child elements: whitespace goes out raw, anything else escaped."""
    return value if value.isspace() else text_escape(value)


def is_reference(element: ET.Element) -> bool:
    return "ObjectRef" in element.attrib or "ObjectURef" in element.attrib


def layout(element: ET.Element, depth: int) -> None:
    """Give a freshly built subtree Premiere's tab layout, in place.

    `depth` is the nesting level `element` will sit at (root = 0). Sets the
    text/tail whitespace of `element`'s descendants and the closing indent of
    empty content elements; it does not touch `element.tail` (the caller
    placing it decides that). Elements that already carry data text keep it.
    """
    stack = [(element, depth)]
    while stack:
        node, level = stack.pop()
        kids = list(node)
        closing = "\n" + INDENT * level
        if not kids:
            if node.text is None and not is_reference(node):
                node.text = closing
            continue
        inner = closing + INDENT
        node.text = inner
        for kid in kids:
            kid.tail = inner
            stack.append((kid, level + 1))
        kids[-1].tail = closing
