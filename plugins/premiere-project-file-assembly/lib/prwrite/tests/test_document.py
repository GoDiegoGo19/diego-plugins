"""Tests for prwrite.document and prwrite.serializer (owner A).

There is no real .prproj in the repo. The fixtures are a hand-written minimal
document in Premiere's style (declaration with a space before `?>`, TAB
indent, ObjectID/ObjectUID objects, ObjectRef/ObjectURef references, a nested
private-scope view state, a base64 block leaf, `&#10;` data newlines, LF LF
after the root) and a synthetic 5,000-object document for speed.
"""

import sys
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # .../lib

import prwrite  # noqa: E402
from prwrite import Doc  # noqa: E402
from prwrite.document import DocumentError  # noqa: E402
from prwrite.serializer import layout  # noqa: E402

UID = "8f1c2a3b-0000-4000-8000-000000000001"

MINIMAL = (
    '<?xml version="1.0" encoding="UTF-8" ?>\n'
    '<PremiereData Version="3">\n'
    '\t<Project ObjectRef="1"/>\n'
    '\t<Project ObjectID="1" ClassID="62ad66dd-0dcd-42da-a660-6d8fbde94876" Version="45">\n'
    '\t\t<Node Version="1">\n'
    '\t\t\t<Properties Version="1">\n'
    '\t\t\t\t<MZ.Project.Note>line one&#10;line two &amp; &lt;three&gt;</MZ.Project.Note>\n'
    '\t\t\t\t<MZ.Quote Title="say &quot;hi&quot;&#9;tab"/>\n'
    '\t\t\t</Properties>\n'
    '\t\t</Node>\n'
    f'\t\t<RootProjectItem ObjectURef="{UID}"/>\n'
    '\t\t<ProjectSettings ObjectRef="3"/>\n'
    '\t\t<NextID>1000001</NextID>\n'
    '\t</Project>\n'
    f'\t<RootProjectItem ObjectUID="{UID}" ClassID="1c307a89-9318-47d7-a583-bf2553736543" Version="1">\n'
    '\t\t<ProjectItem Version="1">\n'
    '\t\t\t<Name>Root Bin</Name>\n'
    '\t\t</ProjectItem>\n'
    '\t\t<ProjectItemContainer Version="1">\n'
    '\t\t</ProjectItemContainer>\n'
    '\t\t<ViewState ObjectID="1" ClassID="aaaa" Version="1">\n'
    '\t\t\t<Column ObjectRef="3"/>\n'
    '\t\t</ViewState>\n'
    '\t</RootProjectItem>\n'
    '\t<ProjectSettings ObjectID="3" ClassID="50c16708-a1a1-4d2f-98d5-4e283ae28353" Version="21">\n'
    '\t\t<Blob BinaryHash="ab12">QUJDREVGR0g=\n\t\t</Blob>\n'
    '\t\t<Empty></Empty>\n'
    '\t</ProjectSettings>\n'
    '</PremiereData>\n\n'
)


def synthetic(n: int) -> str:
    parts = ['<?xml version="1.0" encoding="UTF-8" ?>\n<PremiereData Version="3">\n']
    for i in range(1, n + 1):
        parts.append(
            f'\t<ClipProjectItem ObjectID="{i}" ClassID="cb4e0ed7-4f36-4d4b-b3d2-4d8e6f2b3a{i % 100:02d}" Version="1">\n'
            f'\t\t<ProjectItem Version="1">\n\t\t\t<Name>clip_{i:05d} &amp; co</Name>\n\t\t</ProjectItem>\n'
            f'\t\t<MasterClip ObjectRef="{(i % n) + 1}"/>\n'
            f'\t\t<Note>a&#10;b</Note>\n'
            f'\t</ClipProjectItem>\n'
        )
    parts.append("</PremiereData>\n\n")
    return "".join(parts)


class RoundTripTests(unittest.TestCase):
    def test_package_exports(self):
        self.assertEqual(prwrite.__version__, "0.1.0")
        for name in ("read_prproj", "write_prproj", "Doc"):
            self.assertTrue(hasattr(prwrite, name), name)

    def test_minimal_untouched_is_byte_identical(self):
        doc = Doc.parse(MINIMAL)
        self.assertEqual(doc.serialize(), MINIMAL)
        self.assertTrue(doc.untouched(doc.root))
        self.assertEqual(Doc.parse(doc.serialize()).serialize(), MINIMAL)

    def test_quirks_kept_when_untouched(self):
        odd = ("<?xml version='1.0'?>\n<!-- c -->\n<PremiereData  Version='3' >\n"
               "  <A b = \"1\"   c='2' />\n  <!-- inner -->\n  <B>x&#x41;y</B>\n</PremiereData>")
        self.assertEqual(Doc.parse(odd).serialize(), odd)

    def test_synthetic_5000_objects_fast_and_identical(self):
        text = synthetic(5000)
        t0 = time.perf_counter()
        doc = Doc.parse(text)
        t1 = time.perf_counter()
        out = doc.serialize()
        t2 = time.perf_counter()
        self.assertEqual(out, text)
        self.assertEqual(len(doc.index.objects), 5000)
        self.assertEqual(len(doc.refs_to("1")), 1)
        doc.find("2500").find("ProjectItem/Name").text = "renamed"
        t3 = time.perf_counter()
        edited = doc.serialize()
        t4 = time.perf_counter()
        self.assertEqual(edited, text.replace("clip_02500 &amp; co", "renamed"))
        print(f"\n  5000 objects, {len(text):,} chars: parse {t1 - t0:.3f}s, "
              f"serialize {t2 - t1:.3f}s, serialize after one edit {t4 - t3:.3f}s", file=sys.stderr)
        self.assertLess(t1 - t0, 5.0)
        self.assertLess(t4 - t3, 5.0)


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.doc = Doc.parse(MINIMAL)

    def test_find_and_refs(self):
        doc = self.doc
        self.assertEqual(doc.find("1").get("Version"), "45")
        self.assertEqual(doc.find(UID).tag, "RootProjectItem")
        self.assertIsNone(doc.find("999"))
        sites = doc.refs_to("1")
        self.assertEqual([(s.element.tag, s.attribute, s.owner.tag) for s in sites],
                         [("Project", "ObjectRef", "Project")])
        self.assertEqual([s.attribute for s in doc.refs_to(UID)], ["ObjectURef"])
        # ProjectSettings is referenced from Project; the <Column ObjectRef="3"> inside
        # the nested ViewState scope does NOT count.
        self.assertEqual([s.owner.get("ObjectID") for s in doc.refs_to("3")], ["1"])

    def test_find_empty_element_by_object_id(self):
        # An Element with no children is falsy; find must still return it.
        doc = Doc.parse('<PremiereData Version="3">\n\t<Media ObjectID="7" ClassID="m" Version="1"/>\n'
                        '\t<Other ObjectUID="u-1" ClassID="o" Version="1"/>\n</PremiereData>\n\n')
        media = doc.find("7")
        self.assertIsNotNone(media)
        self.assertEqual(len(media), 0)
        self.assertEqual(media.tag, "Media")
        self.assertEqual(doc.find("u-1").tag, "Other")
        self.assertIs(doc.root[0], media)

    def test_nested_scope_not_indexed(self):
        self.assertEqual(self.doc.find("1").tag, "Project")  # not the nested ViewState ObjectID="1"

    def test_new_id(self):
        self.assertEqual([self.doc.new_id() for _ in range(3)], ["4", "5", "6"])

    def test_duplicate_object_id_rejected(self):
        bad = MINIMAL.replace('ObjectID="3"', 'ObjectID="1"')
        with self.assertRaises(DocumentError):
            Doc.parse(bad)

    def test_bad_input(self):
        for text in ("<PremiereData>", "<Other/>", '<!DOCTYPE x [<!ENTITY a "b">]><PremiereData/>', ""):
            with self.subTest(text), self.assertRaises(DocumentError):
                Doc.parse(text)


class EditTests(unittest.TestCase):
    def test_attribute_edit_changes_only_that_line(self):
        doc = Doc.parse(MINIMAL)
        doc.find("3").set("Version", "22")
        out = doc.serialize().splitlines()
        orig = MINIMAL.splitlines()
        diff = [(a, b) for a, b in zip(orig, out) if a != b]
        self.assertEqual(len(out), len(orig))
        line = next(i for i, l in enumerate(orig) if 'ObjectID="3"' in l)
        self.assertEqual(diff, [(orig[line], orig[line].replace('Version="21"', 'Version="22"'))])

    def test_text_edit_escapes_like_premiere(self):
        doc = Doc.parse(MINIMAL)
        name = doc.find(UID).find("ProjectItem/Name")
        name.text = 'A & B <c>\r\n"q"'
        self.assertIn('<Name>A &amp; B &lt;c&gt;&#13;&#10;"q"</Name>', doc.serialize())
        self.assertFalse(doc.untouched(doc.root))
        self.assertTrue(doc.untouched(doc.find("3")))

    def test_self_closing_gains_content(self):
        doc = Doc.parse(MINIMAL)
        quote = doc.find("1").find("Node/Properties/MZ.Quote")
        quote.text = "x"
        self.assertIn('<MZ.Quote Title="say &quot;hi&quot;&#9;tab">x</MZ.Quote>', doc.serialize())

    def test_attach_new_object_in_premiere_layout(self):
        doc = Doc.parse(MINIMAL)
        oid = doc.new_id()
        el = ET.Element("BinProjectItem", {"ObjectID": oid, "ClassID": "bbbb", "Version": "3"})
        item = ET.SubElement(el, "ProjectItem", {"Version": "1"})
        ET.SubElement(item, "Name").text = "Selects\nnight"
        ET.SubElement(el, "ProjectItemContainer", {"Version": "1"})
        ET.SubElement(el, "Parent", {"ObjectURef": UID})
        layout(el, 1)
        doc.attach(el)
        out = doc.serialize()
        self.assertTrue(out.endswith(
            '\t<BinProjectItem ObjectID="4" ClassID="bbbb" Version="3">\n'
            '\t\t<ProjectItem Version="1">\n'
            '\t\t\t<Name>Selects&#10;night</Name>\n'
            '\t\t</ProjectItem>\n'
            '\t\t<ProjectItemContainer Version="1">\n'
            '\t\t</ProjectItemContainer>\n'
            f'\t\t<Parent ObjectURef="{UID}"/>\n'
            '\t</BinProjectItem>\n'
            '</PremiereData>\n\n'))
        self.assertTrue(out.startswith(MINIMAL[: MINIMAL.index("</PremiereData>")]))
        self.assertEqual(len(doc.refs_to(UID)), 2)
        again = Doc.parse(out)
        self.assertEqual(again.serialize(), out)
        doc.detach(el)
        self.assertEqual(doc.serialize(), MINIMAL)
        self.assertIsNone(doc.find("4"))
        self.assertEqual(len(doc.refs_to(UID)), 1)

    def test_new_doc_from_built_tree(self):
        root = ET.Element("PremiereData", {"Version": "3"})
        ET.SubElement(root, "Project", {"ObjectRef": "1"})
        ET.SubElement(root, "Project", {"ObjectID": "1", "ClassID": "c", "Version": "45"})
        layout(root, 0)
        doc = Doc.new(root)
        out = doc.serialize()
        self.assertEqual(out, '<?xml version="1.0" encoding="UTF-8" ?>\n<PremiereData Version="3">\n'
                              '\t<Project ObjectRef="1"/>\n\t<Project ObjectID="1" ClassID="c" Version="45">\n'
                              '\t</Project>\n</PremiereData>\n\n')
        self.assertEqual(Doc.parse(out).serialize(), out)
        self.assertEqual(doc.new_id(), "2")


if __name__ == "__main__":
    unittest.main()
