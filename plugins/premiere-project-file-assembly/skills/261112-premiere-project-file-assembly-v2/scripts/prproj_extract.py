#!/usr/bin/env python3
"""prproj_extract.py: copy ONE sequence (and what it needs) out of a .prproj into a new, slim .prproj.

  prproj_extract.py extract SRC.prproj "SEQ NAME" OUT_DIR
      -> OUT_DIR/<src stem>__<seq slug>_<YYMMDD-HHMM>.prproj   (refuses to overwrite; SRC is only read)
  prproj_extract.py check FILE.prproj
      -> the six checks, then a last line `PASS ...` or `FAIL <n> ...` with counts
         (objects, sequences, master clips, bins, media). Exit 0 on PASS, 1 on FAIL.

Spec: reports/261112-2340_mac_handoff_extract-one-sequence-prproj.md (method steps 1-8, rules 1-3,
checks 1-6). Stdlib only. Internal switch for the P02 split test / tests:
PRPROJ_EXTRACT_SKIP_RESTORE=1 (or --skip-restore) skips step 7 and so writes a file check must FAIL.

How the text is read (no ElementTree, no DOM):
* Every object is a direct child of <PremiereData>, i.e. a line that starts with exactly one TAB and '<'.
  It ends on its own line if that line self-closes, else at the next line starting "\\t</".
* Inside an object the depth of an element is its count of leading TABs (Premiere writes one TAB per
  level). Only the start tag that BEGINS a line is read, so ObjectRef="..." text inside escaped
  export-preset XML is never a ref (rule 1).
* A ref is LOCAL (not top-level) only when some enclosing nested element (depth >= 2, the ref's own
  element included) has, in its subtree, an element carrying that ObjectID/ObjectUID (rule 2).
  Project's direct children (<ProjectSettings ObjectRef="3"/>) are therefore top-level.

Memory shape: the inflated XML is held ONCE as one bytes buffer (342 MB for the PROJECT_A case);
objects are (start, end) offsets into it plus their tag, top-level refs and BinaryHash list
(Python objects: measured ~0.5-0.8 KB per object on a 300k-object fake, 37 MB XML -> 285 MB peak
RSS incl. the generator; estimate ~1 GB peak for PROJECT_A's 719k objects / 342 MB). Only rewritten
objects (bins, Project, payload targets) get new byte strings. The output is deflated in a stream;
the XML well-formedness check feeds expat in 1 MB slices. Expect peak RSS around 2x the XML size.
"""
import os, re, struct, sys, time, zlib
import xml.parsers.expat
from collections import defaultdict

PREMIERE_HEADERS = (bytes.fromhex("1f8b080000000000000a"), bytes.fromhex("1f8b0800000000000013"))
PREMIERE_HEADER = PREMIERE_HEADERS[0]
TAIL = b"</PremiereData>\n\n"
SETTINGS = range(3, 40)                     # the 37 settings objects every project has
BIN_TAGS = (b"BinProjectItem", b"RootProjectItem")
CLIP_TAGS = (b"VideoClip", b"AudioClip")

START_RE = re.compile(rb'^(\t+)<([A-Za-z_][\w.:-]*)((?:[ \t]+[\w.:-]+="[^"]*")*)[ \t]*(/?)>', re.M)
ATTR_RE = re.compile(rb'([\w.:-]+)="([^"]*)"')
NAME_RE = re.compile(rb'^\t\t<Name>([^<\n]*)</Name>', re.M)
ID_RE = re.compile(rb'^\t\t<ID>([^<\n]*)</ID>', re.M)
OPENSEQ_RE = re.compile(rb'^(\t+)<MZ\.PrefixKey\.OpenSequenceGuidList\.(\d+)>([^<\n]*)'
                        rb'</MZ\.PrefixKey\.OpenSequenceGuidList\.\d+>$', re.M)


class PrprojError(Exception):
    pass


# ---------------------------------------------------------------- gzip framing (self-contained)
def read_framed(path):
    """-> (gzip header bytes or None for plain XML, inflated XML bytes)."""
    with open(path, "rb") as f:
        raw = f.read()
    if not raw.startswith(b"\x1f\x8b"):
        return None, raw
    if len(raw) < 18 or raw[2] != 8:
        raise PrprojError("not a deflate gzip stream")
    flg, at = raw[3], 10
    if flg & 4:
        at += 2 + struct.unpack_from("<H", raw, at)[0]
    for bit in (8, 16):
        if flg & bit:
            at = raw.index(b"\x00", at) + 1
    if flg & 2:
        at += 2
    d = zlib.decompressobj(-zlib.MAX_WBITS)
    xml = d.decompress(raw[at:])
    if not d.eof:
        raise PrprojError("truncated deflate stream")
    trailer = d.unused_data
    if len(trailer) != 8:
        raise PrprojError("gzip trailer missing or more than one member")
    crc, isize = struct.unpack("<II", trailer)
    if crc != zlib.crc32(xml) or isize != len(xml) & 0xFFFFFFFF:
        raise PrprojError("gzip CRC/length mismatch")
    header = raw[:at]
    del raw
    return header, xml


def write_premiere_gzip(path, pieces):
    """Premiere's 10-byte header, raw deflate level 6, CRC32 + ISIZE. Exclusive create: never overwrites."""
    comp = zlib.compressobj(6, zlib.DEFLATED, -zlib.MAX_WBITS)
    crc, size = 0, 0
    try:
        with open(path, "xb") as f:
            f.write(PREMIERE_HEADER)
            for p in pieces:
                crc = zlib.crc32(p, crc)
                size += len(p)
                f.write(comp.compress(p))
            f.write(comp.flush())
            f.write(struct.pack("<II", crc, size & 0xFFFFFFFF))
    except FileExistsError:
        raise
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- step 1: parse
class Obj:
    __slots__ = ("start", "end", "tag", "key", "refs", "hashes", "name", "ids")

    def __init__(self, start, end, tag, key):
        self.start, self.end, self.tag, self.key = start, end, tag, key
        self.refs = []        # [(target key, is_bin_item)] top-level refs only, in line order
        self.hashes = None    # [(hash, has_data)] in line order
        self.name = None      # Sequence <Name>
        self.ids = ()         # Sequence: ObjectUID + <ID> texts (for OpenSequenceGuidList)


def _key(attrs, id_attr, uid_attr):
    if id_attr in attrs:
        return b"#" + attrs[id_attr]
    if uid_attr in attrs:
        return b"@" + attrs[uid_attr]
    return None


class Project:
    def __init__(self, path):
        self.path = path
        self.header, self.buf = read_framed(path)
        self.objs, self.by_key = [], {}
        self.dup_keys = []
        self._split()

    def _split(self):
        buf = self.buf
        root = buf.find(b"<PremiereData")
        if root < 0:
            raise PrprojError("no <PremiereData> root")
        pos = buf.find(b"\n", root)
        self.head_end = None
        while True:
            i = buf.find(b"\n\t<", pos)
            if i < 0 or buf[i + 3:i + 4] == b"/":
                break
            s = i + 1
            le = buf.find(b"\n", s)
            line = buf[s:le].rstrip()
            m = START_RE.match(line)
            if not m:
                raise PrprojError("unreadable object start at byte %d" % s)
            tag = m.group(2)
            if m.group(4) or line.endswith(b"</" + tag + b">"):
                e = le
            else:
                c = buf.find(b"\n\t</", le - 1)
                if c < 0:
                    raise PrprojError("object at byte %d never closes" % s)
                e = buf.find(b"\n", c + 1)
            if self.head_end is None:
                self.head_end = s
            attrs = dict(ATTR_RE.findall(m.group(3)))
            o = Obj(s, e, tag, _key(attrs, b"ObjectID", b"ObjectUID"))
            self._analyse(o)
            if o.key is not None:
                if o.key in self.by_key:
                    self.dup_keys.append(o.key)
                else:
                    self.by_key[o.key] = len(self.objs)
            self.objs.append(o)
            pos = e
        if self.head_end is None:
            raise PrprojError("no objects")
        self.tail_start = self.objs[-1].end + 1

    def _analyse(self, o):
        buf = self.buf
        first_nl = buf.find(b"\n", o.start, o.end)
        nested_defs = first_nl >= 0 and (buf.find(b' ObjectID="', first_nl, o.end) >= 0
                                         or buf.find(b' ObjectUID="', first_nl, o.end) >= 0)
        is_bin = o.tag in BIN_TAGS
        elems = []       # (depth, parent index) when nested_defs
        stack = {}
        raw_refs = []    # (elem index, key, is_item)
        defs = []        # (elem index, key)
        hashes = []
        for m in START_RE.finditer(buf, o.start, o.end):
            depth = len(m.group(1))
            a = m.group(3)
            if nested_defs:
                idx = len(elems)
                elems.append((depth, stack.get(depth - 1, -1)))
                stack[depth] = idx
            else:
                idx = -1
            if b"Object" not in a and b"BinaryHash" not in a:
                continue
            attrs = dict(ATTR_RE.findall(a))
            if depth >= 2:
                k = _key(attrs, b"ObjectID", b"ObjectUID")
                if k is not None:
                    defs.append((idx, k))
            k = _key(attrs, b"ObjectRef", b"ObjectURef")
            if k is not None:
                raw_refs.append((idx, k, is_bin and m.group(2) == b"Item"))
            h = attrs.get(b"BinaryHash")
            if h is not None:
                hashes.append((h, not m.group(4)))
        if defs:
            scope = defaultdict(set)
            for idx, k in defs:
                while idx >= 0 and elems[idx][0] >= 2:
                    scope[idx].add(k)
                    idx = elems[idx][1]
            for idx, k, item in raw_refs:
                j, local = idx, False
                while j >= 0 and elems[j][0] >= 2:
                    if k in scope.get(j, ()):
                        local = True
                        break
                    j = elems[j][1]
                if not local:
                    o.refs.append((k, item))
        else:
            o.refs = [(k, item) for _, k, item in raw_refs]
        o.hashes = hashes or None
        if o.tag == b"Sequence":
            m = NAME_RE.search(buf, o.start, o.end)
            o.name = _unescape(m.group(1)) if m else None
            ids = [i for i in ID_RE.findall(buf, o.start, o.end)]
            if o.key and o.key[:1] == b"@":
                ids.append(o.key[1:])
            o.ids = tuple(ids)

    def blob(self, i):
        o = self.objs[i]
        return self.buf[o.start:o.end]

    def idx(self, key):
        return self.by_key.get(key)


def _unescape(b):
    s = b.decode("utf-8", "replace")
    for a, c in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&")):
        s = s.replace(a, c)
    return s


# ---------------------------------------------------------------- graph helpers
def _referrers(p, wanted):
    """target index -> [referrer index] for referrers whose tag satisfies wanted(tag)."""
    rev = defaultdict(list)
    for i, o in enumerate(p.objs):
        if wanted(o.tag):
            for k, item in o.refs:
                t = p.by_key.get(k)
                if t is not None and not item:
                    rev[t].append(i)
    return rev


def _bin_parents(p):
    par = {}
    for i, o in enumerate(p.objs):
        if o.tag in BIN_TAGS:
            for k, item in o.refs:
                t = p.by_key.get(k)
                if item and t is not None:
                    par.setdefault(t, i)
    return par


def _owner_chain(p, rev, seq_i):
    """Sequence <- *SequenceSource <- Video/AudioClip <- MasterClip <- ClipProjectItem (rule 3)."""
    out = {"src": [], "clip": [], "master": [], "cpi": []}
    srcs = [r for r in rev[seq_i] if p.objs[r].tag.endswith(b"SequenceSource")]
    clips = [r for s in srcs for r in rev[s] if p.objs[r].tag in CLIP_TAGS]
    masters = sorted({r for c in clips for r in rev[c] if p.objs[r].tag == b"MasterClip"})
    cpis = sorted({r for m in masters for r in rev[m] if p.objs[r].tag == b"ClipProjectItem"})
    out.update(src=srcs, clip=clips, master=masters, cpi=cpis)
    return out


# ---------------------------------------------------------------- extract (steps 2-8)
def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "sequence"


def plan_keep(p, seq_name):
    seqs = [i for i, o in enumerate(p.objs) if o.tag == b"Sequence" and o.name == seq_name]
    if not seqs:
        raise PrprojError("no sequence named %r" % seq_name)
    if len(seqs) > 1:
        raise PrprojError("%d sequences are named %r; refusing to guess" % (len(seqs), seq_name))
    keep = set()

    def close(todo):  # forward closure; bins' <Item> lines are never followed (step 5 prunes them)
        todo = [i for i in todo if i not in keep]
        keep.update(todo)
        while todo:
            o = p.objs[todo.pop()]
            for k, item in o.refs:
                if item:
                    continue
                t = p.by_key.get(k)
                if t is not None and t not in keep:
                    keep.add(t)
                    todo.append(t)

    close(seqs)                                                       # step 2
    base = [i for i, o in enumerate(p.objs) if o.key is None]        # header refs like <Project ObjectRef="1"/>
    base += [i for i, o in enumerate(p.objs) if o.tag in (b"Project", b"RootProjectItem")]
    base += [p.by_key[b"#%d" % n] for n in SETTINGS if b"#%d" % n in p.by_key]
    close(base)                                                       # step 4
    rev = _referrers(p, lambda t: t.endswith(b"SequenceSource") or t in CLIP_TAGS
                     or t in (b"MasterClip", b"ClipProjectItem"))
    par = _bin_parents(p)
    while True:                                                       # step 3, to a fixpoint
        add = []
        for i in list(keep):
            tag = p.objs[i].tag
            if tag == b"Sequence":
                ch = _owner_chain(p, rev, i)
                add += ch["src"] + ch["clip"] + ch["master"] + ch["cpi"]
            elif tag == b"MasterClip":
                add += [r for r in rev[i] if p.objs[r].tag == b"ClipProjectItem"]
        for i in list(add) + [i for i in keep if p.objs[i].tag == b"ClipProjectItem"]:
            j = par.get(i)
            while j is not None:
                add.append(j)
                j = par.get(j)
        add = [i for i in set(add) if i not in keep]
        if not add:
            break
        close(add)
    return seqs[0], keep


def _items_rewrite(blob, alive):
    """Drop <Item ... ObjectURef/ObjectRef=dead/> lines, renumber Index from 0 per Items container."""
    out, n = [], 0
    for line in blob.split(b"\n"):
        m = START_RE.match(line)
        if m and m.group(2) == b"Items":
            n = 0
        elif m and m.group(2) == b"Item" and len(m.group(1)) >= 2:
            attrs = dict(ATTR_RE.findall(m.group(3)))
            k = _key(attrs, b"ObjectRef", b"ObjectURef")
            if k is not None:
                if k not in alive:
                    continue
                if b"Index" in attrs:
                    line = re.sub(rb'Index="\d+"', b'Index="%d"' % n, line, count=1)
                n += 1
        out.append(line)
    return b"\n".join(out)


def _restore_payload(p, blob, h):
    """Put the source's data for BinaryHash h into the first empty element carrying h in `blob`."""
    src = None
    for o in p.objs:
        if o.hashes and (h, True) in o.hashes:
            src = o
            break
    if src is None:
        return blob, False
    hq = re.escape(b'BinaryHash="' + h + b'"')
    sm = re.compile(rb'^(\t+)<([\w.:-]+)[^\n]*?' + hq + rb'[^\n>]*(?<!/)>', re.M).search(p.buf, src.start, src.end)
    if not sm:
        return blob, False
    close = p.buf.find(b"</" + sm.group(2) + b">", sm.end(), src.end + 1)
    if close < 0:
        return blob, False
    data = p.buf[sm.end():close].rstrip(b"\t\n\r ")
    tm = re.compile(rb'^(\t+)<([\w.:-]+)([^\n]*?' + hq + rb'[^\n]*?)[ \t]*/>$', re.M).search(blob)
    if not tm:
        return blob, False
    ind, tag = tm.group(1), tm.group(2)
    new = ind + b"<" + tag + tm.group(3) + b">" + data + b"\n" + ind + b"</" + tag + b">"
    return blob[:tm.start()] + new + blob[tm.end():], True


def extract(src, seq_name, out_dir, *, stamp=None, skip_restore=None, log=print):
    if skip_restore is None:
        skip_restore = os.environ.get("PRPROJ_EXTRACT_SKIP_RESTORE") == "1"
    stem = os.path.splitext(os.path.basename(src))[0]
    stamp = stamp or time.strftime("%y%m%d-%H%M")
    out = os.path.join(out_dir, "%s__%s_%s.prproj" % (stem, slug(seq_name), stamp))
    if os.path.exists(out):
        raise FileExistsError("refusing to overwrite %s" % out)
    if os.path.abspath(out) == os.path.abspath(src):
        raise FileExistsError("output would be the source")
    p = Project(src)
    seq_i, keep = plan_keep(p, seq_name)
    # step 5: bins. Drop empty bins to a fixpoint, then rewrite Item lists.
    while True:
        alive = {p.objs[i].key for i in keep if p.objs[i].key is not None}
        empty = [i for i in keep if p.objs[i].tag == b"BinProjectItem"
                 and not any(item and k in alive for k, item in p.objs[i].refs)]
        if not empty:
            break
        keep.difference_update(empty)
    alive = {p.objs[i].key for i in keep if p.objs[i].key is not None}
    blobs = {}
    for i in keep:
        if p.objs[i].tag in BIN_TAGS:
            blobs[i] = _items_rewrite(p.blob(i), alive)
    # step 6: OpenSequenceGuidList in Project
    kept_seq_ids = {x for i in keep if p.objs[i].tag == b"Sequence" for x in p.objs[i].ids}
    for i in keep:
        if p.objs[i].tag == b"Project":
            n = [0]

            def fix(m):
                if m.group(3) not in kept_seq_ids:
                    return b"\x00DROP"
                n[0] += 1
                return m.group(1) + b"<MZ.PrefixKey.OpenSequenceGuidList.%d>" % n[0] + m.group(3) + \
                    b"</MZ.PrefixKey.OpenSequenceGuidList.%d>" % n[0]
            b = OPENSEQ_RE.sub(fix, blobs.get(i, p.blob(i)))
            blobs[i] = b"\n".join(l for l in b.split(b"\n") if l != b"\x00DROP")
    # step 7: orphaned store-once payloads
    order = sorted(keep)
    with_data, first_holder = set(), {}
    for i in order:
        for h, d in p.objs[i].hashes or ():
            if d:
                with_data.add(h)
            first_holder.setdefault(h, i)
    orphans = sorted(h for h in first_holder if h not in with_data)
    restored = []
    if not skip_restore:
        for h in orphans:
            i = first_holder[h]
            b, ok = _restore_payload(p, blobs.get(i, p.blob(i)), h)
            if ok:
                blobs[i] = b
                restored.append(h)
    # step 8: write in original order
    pieces = [p.buf[:p.head_end]]
    for i in order:
        pieces.append(blobs.get(i, p.blob(i)))
        pieces.append(b"\n")
    pieces.append(p.buf[p.tail_start:])
    os.makedirs(out_dir, exist_ok=True)
    write_premiere_gzip(out, pieces)
    tags = [p.objs[i].tag for i in order]
    log("wrote %s" % out)
    log("kept %d of %d objects; sequences %d; master clips %d; bins %d; media %d"
        % (len(order), len(p.objs), tags.count(b"Sequence"), tags.count(b"MasterClip"),
           tags.count(b"BinProjectItem"), tags.count(b"Media")))
    log("payloads: %d orphaned, %d restored%s" % (len(orphans), len(restored),
                                                   " (STEP 7 SKIPPED)" if skip_restore else ""))
    log("status: written, checked by nobody yet, unopened (run `check`, then open in Premiere)")
    return out


# ---------------------------------------------------------------- check
def check(path, log=print):
    fails = []

    def res(n, ok, msg):
        log("%d %s  %s" % (n, "PASS" if ok else "FAIL", msg))
        if not ok:
            fails.append(n)

    p = Project(path)
    # 1 store-once payloads
    seen_data, orphan, late, empties = set(), defaultdict(int), set(), 0
    data_anywhere = {h for o in p.objs for h, d in (o.hashes or ()) if d}
    for o in p.objs:
        for h, d in o.hashes or ():
            if d:
                seen_data.add(h)
            else:
                empties += 1
                if h not in data_anywhere:
                    orphan[h] += 1
                elif h not in seen_data:
                    late.add(h)
    ok = not orphan and not late
    msg = "store-once payloads: %d hashes with data, %d empty elements" % (len(data_anywhere), empties)
    if orphan:
        msg += "; ORPHANED payloads (no data anywhere): %d hashes on %d empty elements: %s" % (
            len(orphan), sum(orphan.values()), ", ".join(h.decode() for h in sorted(orphan)[:10]))
    if late:
        msg += "; data after first empty use: %s" % ", ".join(h.decode() for h in sorted(late)[:10])
    res(1, ok, msg)
    # 2 dangling top-level refs (scope-aware)
    dangling = defaultdict(int)
    for o in p.objs:
        for k, _ in o.refs:
            if k not in p.by_key:
                dangling[k] += 1
    n = sum(dangling.values())
    res(2, n == 0 and not p.dup_keys, "dangling top-level refs: %d (targets: %s)%s" % (
        n, ", ".join(k.decode() for k in sorted(dangling)[:12]) or "none",
        "; DUPLICATE object keys: %d" % len(p.dup_keys) if p.dup_keys else ""))
    # 3 base objects
    tags = [o.tag for o in p.objs]
    miss = [n for n in SETTINGS if b"#%d" % n not in p.by_key]
    has_proj, has_root = b"Project" in [o.tag for o in p.objs if o.key], b"RootProjectItem" in tags
    res(3, has_proj and has_root and not miss, "Project %s, RootProjectItem %s, settings 3..39 missing: %s" % (
        "ok" if has_proj else "MISSING", "ok" if has_root else "MISSING",
        ",".join(map(str, miss)) or "none"))
    # 4 sequences listed once, bins
    rev = _referrers(p, lambda t: t.endswith(b"SequenceSource") or t in CLIP_TAGS
                     or t in (b"MasterClip", b"ClipProjectItem"))
    listed = defaultdict(int)
    problems = []
    warnings = []
    for i, o in enumerate(p.objs):
        if o.tag in BIN_TAGS:
            idxs, nitems = [], 0
            for line in p.blob(i).split(b"\n"):
                m = START_RE.match(line)
                if m and m.group(2) == b"Items":
                    if idxs != list(range(len(idxs))):
                        problems.append("Index not contiguous in %s" % (o.key or b"?").decode())
                    idxs = []
                elif m and m.group(2) == b"Item":
                    attrs = dict(ATTR_RE.findall(m.group(3)))
                    k = _key(attrs, b"ObjectRef", b"ObjectURef")
                    if k is not None:
                        nitems += 1
                        t = p.by_key.get(k)
                        if t is not None:
                            listed[t] += 1
                        if b"Index" in attrs:
                            idxs.append(int(attrs[b"Index"]))
            if idxs != list(range(len(idxs))):
                problems.append("Index not contiguous in %s" % (o.key or b"?").decode())
            if o.tag == b"BinProjectItem" and nitems == 0:
                # WARN, not FAIL: an EXTRACT must leave no empty bins (handoff step 5), but a freshly
                # BUILT project keeps its default bins (03_AUDIO, 05_STILLS...) empty until media
                # arrives, and Premiere opens projects with empty bins. The extract path still drops them.
                warnings.append("empty bin %s" % (o.key or b"?").decode())
    for i, o in enumerate(p.objs):
        if o.tag == b"Sequence":
            ch = _owner_chain(p, rev, i)
            nm = o.name or "?"
            if not ch["master"]:
                problems.append("sequence %r has no MasterClip" % nm)
            if not ch["cpi"]:
                problems.append("sequence %r has no ClipProjectItem" % nm)
            for c in ch["cpi"]:
                if listed[c] != 1:
                    problems.append("sequence %r ClipProjectItem listed in %d bins" % (nm, listed[c]))
    open_ids = {x for o in p.objs if o.tag == b"Sequence" for x in o.ids}
    for o in p.objs:
        if o.tag == b"Project" and o.key:
            got = OPENSEQ_RE.findall(p.buf, o.start, o.end)
            # WARN only: Premiere's own blank project (fixtures/prproj/BLANK_PROJECT.prproj, 261113)
            # carries 46 OpenSequenceGuidList entries naming sequences that do not exist, so a
            # stale or oddly numbered list is tolerated by Premiere and must not fail check 4.
            stale = [g for g in got if g[2] not in open_ids]
            if [int(g[1]) for g in got] != list(range(1, len(got) + 1)) or stale:
                warnings.append("OpenSequenceGuidList: %d stale entr%s, numbering %s (WARN, Premiere tolerates this)"
                                % (len(stale), "y" if len(stale) == 1 else "ies",
                                   "from 1" if [int(g[1]) for g in got] == list(range(1, len(got) + 1)) else "irregular"))
    res(4, not problems, "sequences have MasterClip + ClipProjectItem in exactly one bin; bins non-empty; "
        "Index contiguous%s%s" % ("; " + "; ".join(problems[:10]) if problems else "",
                                  "; " + "; ".join(warnings) if warnings else ""))
    # 5 framing
    fp = []
    if p.header not in PREMIERE_HEADERS:
        fp.append("gzip header %s is not Premiere's 10-byte header" % (p.header or b"(plain XML)").hex())
    if b"\r" in p.buf:
        fp.append("CR line ends present")
    if not p.buf.endswith(TAIL):
        fp.append("tail is not </PremiereData>\\n\\n")
    try:
        px = xml.parsers.expat.ParserCreate()
        mv = memoryview(p.buf)
        for a in range(0, len(mv), 1 << 20):
            px.Parse(bytes(mv[a:a + (1 << 20)]), False)
        px.Parse(b"", True)
    except xml.parsers.expat.ExpatError as e:
        fp.append("XML does not parse: %s" % e)
    res(5, not fp, "framing: header %s, LF, tail, XML parses%s" % (
        (p.header or b"plain").hex(), ("; " + "; ".join(fp)) if fp else ""))
    log("6 TODO  a person opens it in Premiere; until then this file is: written, checked, unopened")
    counts = "objects=%d sequences=%d master_clips=%d bins=%d media=%d" % (
        len(p.objs), tags.count(b"Sequence"), tags.count(b"MasterClip"),
        tags.count(b"BinProjectItem"), tags.count(b"Media"))
    log(("PASS " if not fails else "FAIL %d " % len(fails)) + counts)
    return not fails


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    skip = "--skip-restore" in a
    a = [x for x in a if x != "--skip-restore"]
    try:
        if len(a) == 4 and a[0] == "extract":
            extract(a[1], a[2], a[3], skip_restore=True if skip else None)
            return 0
        if len(a) == 2 and a[0] == "check":
            return 0 if check(a[1]) else 1
    except (PrprojError, FileExistsError) as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 2
    print(__doc__.split("\n\n")[0], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
