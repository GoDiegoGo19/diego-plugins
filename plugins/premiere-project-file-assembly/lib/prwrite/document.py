"""document: XML text <-> `Doc` (an ElementTree plus one object index).

In:  the XML text of a .prproj (what `framing.read_prproj` returns), or a
     freshly built `PremiereData` root element (`Doc.new`).
Out: `Doc` with
     * `root`   - the `PremiereData` element (plain `xml.etree` elements);
     * `index`  - built once: ObjectID -> element, ObjectUID -> element, and
                  id/uid -> every element that points at it (`RefSite`);
     * `serialize()` -> text. Elements that were not touched since parse are
                  copied byte for byte from the input (declaration, indent,
                  attribute order and quoting, entity spelling, empty-element
                  style, even comments). Only touched or new elements go
                  through `serializer`.

How "untouched" is decided: while parsing we record, per element, its byte
span in the input and a snapshot of what it held (tag, attributes in order,
text, the exact child objects and their tails). At serialize time an element
whose snapshot still matches and whose children are all untouched is one
slice of the input. A changed element re-uses every unchanged piece (its
start tag, its text run, each child's tail run, its end tag) and spells only
the changed piece in Premiere's style.

Identifier rules (facts from the vendored py-premiere, see SPEC.md):
* objects live directly under `PremiereData` and carry `ObjectID` (integer
  strings) or `ObjectUID` (GUIDs); a duplicate is an error;
* references are `ObjectRef="<ObjectID>"` and `ObjectURef="<ObjectUID>"`;
* an element BELOW the top level that carries its own ObjectID/ObjectUID
  (project view state, column classes) opens a private id scope: nothing in
  that subtree is indexed as a definition or a reference.

The index follows `attach`/`detach`. If you edit references or ids with raw
ElementTree calls, call `reindex()` (one pass) before asking it questions.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import xml.parsers.expat as expat
from typing import NamedTuple

from . import serializer as S

__all__ = ["Doc", "DocumentError", "Index", "RefSite"]


class DocumentError(ValueError):
    """The text is not a document we can index or write back."""


class RefSite(NamedTuple):
    element: ET.Element  # the element carrying the reference attribute
    attribute: str  # "ObjectRef" or "ObjectURef"
    owner: ET.Element  # the top-level object it sits in


class Index:
    """ObjectID/ObjectUID definitions and reference sites, from one tree pass."""

    def __init__(self) -> None:
        self.objects: dict[str, ET.Element] = {}  # ObjectID  -> top-level element
        self.uids: dict[str, ET.Element] = {}  # ObjectUID -> top-level element
        self.sites: dict[str, list[RefSite]] = {}  # ref value -> sites pointing at it
        self.high = 0  # highest integer ObjectID seen or handed out

    @classmethod
    def build(cls, root: ET.Element) -> "Index":
        index = cls()
        for top in root:
            index.add(top)
        return index

    def add(self, top: ET.Element) -> None:
        """Register a top-level element's definition and all its ref sites."""
        oid = top.get("ObjectID")
        if oid is not None:
            if oid in self.objects:
                raise DocumentError(f"duplicate ObjectID {oid!r}")
            self.objects[oid] = top
            if oid.isdigit():
                self.high = max(self.high, int(oid))
        uid = top.get("ObjectUID")
        if uid is not None:
            if uid in self.uids:
                raise DocumentError(f"duplicate ObjectUID {uid!r}")
            self.uids[uid] = top
        for site in _ref_sites(top):
            self.sites.setdefault(site.element.get(site.attribute), []).append(site)

    def remove(self, top: ET.Element) -> None:
        oid, uid = top.get("ObjectID"), top.get("ObjectUID")
        if oid is not None and self.objects.get(oid) is top:
            del self.objects[oid]
        if uid is not None and self.uids.get(uid) is top:
            del self.uids[uid]
        for key in [k for k, v in self.sites.items() if any(s.owner is top for s in v)]:
            kept = [s for s in self.sites[key] if s.owner is not top]
            if kept:
                self.sites[key] = kept
            else:
                del self.sites[key]


def _ref_sites(top: ET.Element):
    """Reference-carrying elements in `top`, skipping private-scope subtrees."""
    stack = [top]
    while stack:
        node = stack.pop()
        if node is not top and ("ObjectID" in node.attrib or "ObjectUID" in node.attrib):
            continue  # nested definition: its own id scope
        for attribute in ("ObjectRef", "ObjectURef"):
            if attribute in node.attrib:
                yield RefSite(node, attribute, top)
        stack.extend(reversed(node))


# --------------------------------------------------------------------- parse

# A start tag from `<` to its closing `>` or `/>`, quote-aware.
_START_TAG = re.compile(rb"""<[^\s/>]+(?:\s+[^\s=/>]+\s*=\s*(?:"[^"]*"|'[^']*'))*\s*/?>""")


class _Span:
    """Where an element came from in the input, and what it held then."""

    __slots__ = ("el", "start", "head_end", "tail_start", "end", "empty",
                 "tag", "attrs", "text", "kids", "tails", "text_raw", "tail", "tail_raw")

    def __init__(self, el: ET.Element, start: int, head_end: int) -> None:
        self.el = el  # keeps the element alive so id(el) is never reused
        self.start = start  # offset of `<`
        self.head_end = head_end  # offset just past the start tag
        self.tail_start = head_end  # offset of `</` (== end for `<x/>`)
        self.end = head_end  # offset just past the element
        self.empty = False  # written `<x/>`
        self.tag = el.tag
        self.attrs = list(el.attrib.items())
        self.text: str | None = None
        self.kids: tuple = ()
        self.tails: tuple = ()
        self.text_raw = b""
        self.tail: str | None = None
        self.tail_raw = b""


class _Builder:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.spans: dict[int, _Span] = {}
        self.stack: list[tuple[ET.Element, _Span]] = []
        self.root: ET.Element | None = None
        self.buf: list[str] = []
        self.parser = p = expat.ParserCreate("UTF-8")
        p.ordered_attributes = True
        p.buffer_text = True
        p.StartElementHandler = self.start
        p.EndElementHandler = self.finish
        p.CharacterDataHandler = self.buf.append
        p.StartDoctypeDeclHandler = self.refuse_doctype

    def refuse_doctype(self, *_args) -> None:
        raise DocumentError("DOCTYPE declarations are not allowed in a .prproj")

    def flush(self) -> None:
        if not self.buf or not self.stack:
            self.buf.clear()
            return
        chunk = "".join(self.buf)
        self.buf.clear()
        parent = self.stack[-1][0]
        if len(parent):
            last = parent[-1]
            last.tail = (last.tail or "") + chunk
        else:
            parent.text = (parent.text or "") + chunk

    def start(self, tag: str, flat_attrs: list[str]) -> None:
        self.flush()
        at = self.parser.CurrentByteIndex
        head = _START_TAG.match(self.data, at)
        if head is None:
            raise DocumentError(f"cannot measure start tag <{tag}> at byte {at}")
        el = ET.Element(tag, dict(zip(flat_attrs[::2], flat_attrs[1::2])))
        span = _Span(el, at, head.end())
        span.empty = self.data[head.end() - 2 : head.end()] == b"/>"
        self.spans[id(el)] = span
        if self.stack:
            self.stack[-1][0].append(el)
        else:
            self.root = el
        self.stack.append((el, span))

    def finish(self, _tag: str) -> None:
        self.flush()
        el, span = self.stack.pop()
        at = self.parser.CurrentByteIndex
        if span.empty:
            span.tail_start = span.end = span.head_end
        else:
            span.tail_start = at
            span.end = self.data.index(b">", at) + 1
        span.text = el.text
        kids = tuple(el)
        span.kids = kids
        data = self.data
        stop = self.spans[id(kids[0])].start if kids else span.tail_start
        span.text_raw = data[span.head_end : stop]
        for i, kid in enumerate(kids):
            ks = self.spans[id(kid)]
            nxt = self.spans[id(kids[i + 1])].start if i + 1 < len(kids) else span.tail_start
            ks.tail = kid.tail
            ks.tail_raw = data[ks.end : nxt]
        span.tails = tuple(k.tail for k in kids)


# ----------------------------------------------------------------------- Doc


class Doc:
    """A .prproj document: `root`, `index`, and a byte-faithful `serialize`."""

    def __init__(self, root: ET.Element, *, source: bytes | None = None,
                 spans: dict[int, _Span] | None = None) -> None:
        if root.tag != "PremiereData":
            raise DocumentError(f"root element is <{root.tag}>, expected <PremiereData>")
        self.root = root
        self._source = source
        self._spans = spans or {}
        self.index = Index.build(root)

    # -- construction
    @classmethod
    def parse(cls, text: str | bytes) -> "Doc":
        """XML text (or UTF-8 bytes) -> Doc. Raises DocumentError on bad input."""
        data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
        builder = _Builder(data)
        try:
            builder.parser.Parse(data, True)
        except expat.ExpatError as exc:
            raise DocumentError(f"not well-formed XML: {exc}") from None
        if builder.root is None:
            raise DocumentError("no root element")
        return cls(builder.root, source=data, spans=builder.spans)

    @classmethod
    def new(cls, root: ET.Element) -> "Doc":
        """Wrap a built `PremiereData` tree (no source text; all of it is new)."""
        return cls(root)

    # -- queries
    def find(self, object_id: str) -> ET.Element | None:
        """The top-level object with this ObjectID (or ObjectUID), or None."""
        # Explicit None checks: an Element with no children is falsy.
        found = self.index.objects.get(object_id)
        if found is None:
            found = self.index.uids.get(object_id)
        return found

    def refs_to(self, object_id: str) -> list[RefSite]:
        """Every ObjectRef/ObjectURef site pointing at this id or uid."""
        return list(self.index.sites.get(object_id, ()))

    def new_id(self) -> str:
        """A fresh integer ObjectID; each call returns a different one."""
        self.index.high += 1
        return str(self.index.high)

    def untouched(self, element: ET.Element) -> bool:
        """True when `element` would be written as the exact input bytes."""
        return self._clean(element, {})

    # -- edits that keep the index current
    def attach(self, element: ET.Element) -> ET.Element:
        """Append a top-level object, copying the root's child spacing, and index it."""
        kids = list(self.root)
        if kids:
            element.tail = kids[-1].tail
            kids[-1].tail = kids[-2].tail if len(kids) > 1 else self.root.text
        else:
            self.root.text, element.tail = "\n\t", "\n"
        self.root.append(element)
        self.index.add(element)
        return element

    def detach(self, element: ET.Element) -> None:
        """Remove a top-level object and drop it from the index."""
        kids = list(self.root)
        at = kids.index(element)
        if at == len(kids) - 1 and at > 0:
            kids[at - 1].tail = element.tail
        self.root.remove(element)
        self.index.remove(element)

    def reindex(self) -> None:
        """Rebuild the index after raw ElementTree edits (one pass)."""
        high = self.index.high
        self.index = Index.build(self.root)
        self.index.high = max(self.index.high, high)

    # -- output
    def serialize(self) -> str:
        """Doc -> XML text. Untouched elements come out exactly as read."""
        clean: dict[int, bool] = {}
        out: list[bytes] = []
        root_span = self._spans.get(id(self.root))
        if self._source is not None and root_span is not None:
            out.append(self._source[: root_span.start])
            self._emit(self.root, out, clean)
            out.append(self._source[root_span.end :])
        else:
            out.append(S.DECLARATION.encode())
            self._emit(self.root, out, clean)
            out.append(S.TRAILER.encode())
        return b"".join(out).decode("utf-8")

    def _clean(self, el: ET.Element, memo: dict[int, bool]) -> bool:
        key = id(el)
        hit = memo.get(key)
        if hit is not None:
            return hit
        s = self._spans.get(key)
        ok = (
            s is not None
            and el.tag == s.tag
            and el.text == s.text
            and len(el) == len(s.kids)
            and list(el.attrib.items()) == s.attrs
            and all(a is b and a.tail == t for a, b, t in zip(el, s.kids, s.tails))
        )
        if ok:
            ok = all([self._clean(kid, memo) for kid in el])
        memo[key] = ok
        return ok

    def _emit(self, el: ET.Element, out: list[bytes], memo: dict[int, bool]) -> None:
        src = self._source
        s = self._spans.get(id(el))
        if s is not None and self._clean(el, memo):
            out.append(src[s.start : s.end])
            return
        has_content = len(el) > 0 or bool(el.text)
        same_head = s is not None and el.tag == s.tag and list(el.attrib.items()) == s.attrs
        # start tag
        if same_head:
            head = src[s.start : s.head_end]
            if s.empty and not has_content:
                out.append(head)
                return
            out.append(head[:-2].rstrip() + b">" if s.empty else head)
        else:
            self_close = not has_content and (s.empty if s is not None else el.text is None)
            out.append(S.start_tag(el.tag, el.attrib, self_closing=self_close).encode())
            if self_close:
                return
        # text
        if el.text:
            if s is not None and el.text == s.text:
                out.append(s.text_raw)
            else:
                out.append((S.formatting_text(el.text) if len(el) else S.leaf_text(el.text)).encode())
        # children and the runs between them
        for kid in el:
            self._emit(kid, out, memo)
            if kid.tail:
                ks = self._spans.get(id(kid))
                if ks is not None and kid.tail == ks.tail:
                    out.append(ks.tail_raw)
                else:
                    out.append(S.formatting_text(kid.tail).encode())
        # end tag
        if same_head and not s.empty:
            out.append(src[s.tail_start : s.end])
        else:
            out.append(S.end_tag(el.tag).encode())
