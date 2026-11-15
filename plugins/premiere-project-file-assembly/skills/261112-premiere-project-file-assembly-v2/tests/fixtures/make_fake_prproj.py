#!/usr/bin/env python3
"""make_fake_prproj.py: write a small, realistic FAKE .prproj for prproj_extract tests (stdlib only).

  make_fake_prproj.py OUT.prproj [--drop-settings 3-11]

Shape (follows the handoff's file facts; no real Premiere file was available):
* <Project ObjectRef="1"/> header line, then Project (ObjectID 1) whose nested <LocalObjects> holds
  LOCAL ObjectIDs 1..20 that refer to each other (they collide with top-level 1..20), plus direct-child
  refs to the 37 settings objects 3..39 and to RootProjectItem.
* Settings 3..39: 3..8 refer to their Video/Audio settings in 12..39; 12..39 refer to nothing.
  CompileSettings 4 carries an escaped export preset with a fake ObjectRef="999".
* Sequences: A (top-level, uses media 1, 2 and nested N1), B (top-level, uses media 1, 3, 4),
  N1 (used by A, nests N2), N2 (used by N1). Each has ClipProjectItem -> MasterClip -> Video/AudioClip ->
  Video/AudioSequenceSource. Track groups -> tracks -> track items -> subclips -> clips -> media sources -> Media.
* Bins: 01_SEQUENCES (A, B, N1, N2), 02_MEDIA (media 1, 2, 3 and the sub-bin), 02_MEDIA/B_ONLY (media 4).
* Store-once payloads H1 (StartKeyframeValue) and H2 (PremiereFilterPrivateData): their data copies live in
  filter components used ONLY by B (written first in file order); A's components carry the empty forms,
  H2 under a different tag name as well.
* OpenSequenceGuidList = [B, A].
"""
import struct, sys, uuid, zlib

HEADER = bytes.fromhex("1f8b080000000000000a")
H1, H2 = "4f1c0e5a-0000-4000-8000-0000000000h1", "4f1c0e5a-0000-4000-8000-0000000000h2"
DATA1 = "QUFBQUJCQkJDQ0NDRERERA==" * 3
DATA2 = "WldYWllZWVo/Ky8rUVFRUQ==" * 2


def U(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "fake-prproj/" + name))


def gz(text):
    raw = text.encode("utf-8")
    c = zlib.compressobj(6, zlib.DEFLATED, -zlib.MAX_WBITS)
    return HEADER + c.compress(raw) + c.flush() + struct.pack("<II", zlib.crc32(raw), len(raw))


class Gen:
    def __init__(self):
        self.next_id = 40
        self.objs = []          # (key, text)

    def nid(self):
        self.next_id += 1
        return self.next_id

    def add(self, text, key=None):
        self.objs.append((key, text))


def obj(tag, idattr, body, ver="1"):
    inner = "".join("\t\t" + l + "\n" for l in body)
    return "\t<%s %s ClassID=\"%s\" Version=\"%s\">\n%s\t</%s>" % (tag, idattr, U("class/" + tag), ver, inner, tag)


def build(drop_settings=()):
    g = Gen()
    seqs = {}
    # ---- media 1..4: Media, VideoMediaSource, MasterClip(+VideoClip), ClipProjectItem
    media = {}
    for m in range(1, 5):
        mu, vms, vclip, mc, cpi, aclip, ams = (U("media%d" % m), g.nid(), g.nid(), U("mc%d" % m),
                                               U("cpi%d" % m), g.nid(), g.nid())
        media[m] = dict(media=mu, vms=vms, vclip=vclip, mc=mc, cpi=cpi)
        body = ['<FilePath>/Volumes/DRIVE/clip_%d.mov</FilePath>' % m, '<Title>clip_%d.mov</Title>' % m]
        if m == 1:
            body.append('<ModificationState Encoding="base64" BinaryHash="%s">AAEC%d\n\t\t</ModificationState>' % (U("ms1"), m))
        g.add(obj("Media", 'ObjectUID="%s"' % mu, body), "@" + mu)
        g.add(obj("VideoMediaSource", 'ObjectID="%d"' % vms, ['<MediaSource Version="2">',
              '\t<Media ObjectURef="%s"/>' % mu, '</MediaSource>']), "#%d" % vms)
        g.add(obj("AudioMediaSource", 'ObjectID="%d"' % ams, ['<MediaSource Version="2">',
              '\t<Media ObjectURef="%s"/>' % mu, '</MediaSource>']), "#%d" % ams)
        g.add(obj("VideoClip", 'ObjectID="%d"' % vclip, ['<Clip Version="18">', '\t<Source ObjectRef="%d"/>' % vms,
              '\t<ClipID>%s</ClipID>' % U("clipid%d" % m), '</Clip>']), "#%d" % vclip)
        g.add(obj("AudioClip", 'ObjectID="%d"' % aclip, ['<Clip Version="18">', '\t<Source ObjectRef="%d"/>' % ams,
              '</Clip>']), "#%d" % aclip)
        g.add(obj("MasterClip", 'ObjectUID="%s"' % mc, ['<Clips Version="1">',
              '\t<Clip Index="0" ObjectRef="%d"/>' % aclip, '\t<Clip Index="1" ObjectRef="%d"/>' % vclip,
              '</Clips>', '<Name>clip_%d.mov</Name>' % m]), "@" + mc)
        g.add(obj("ClipProjectItem", 'ObjectUID="%s"' % cpi, ['<ProjectItem Version="1">',
              '\t<Name>clip_%d.mov</Name>' % m, '</ProjectItem>', '<MasterClip ObjectURef="%s"/>' % mc]), "@" + cpi)

    def filt(payloads):
        """A VideoFilterComponent object holding store-once elements: [(tag, hash, data or None)]."""
        i = g.nid()
        body = ['<Component Version="7">', '\t<Params Version="1">']
        for tag, h, data in payloads:
            if data is None:
                body.append('\t\t<%s Encoding="base64" BinaryHash="%s"/>' % (tag, h))
            else:
                body.append('\t\t<%s Encoding="base64" BinaryHash="%s">%s\n\t\t\t\t</%s>' % (tag, h, data, tag))
        body += ['\t</Params>', '</Component>']
        g.add(obj("VideoFilterComponent", 'ObjectID="%d"' % i, body), "#%d" % i)
        return i

    def item(clip_ref, master_uref, comp=None):
        sub, ti = g.nid(), g.nid()
        g.add(obj("SubClip", 'ObjectID="%d"' % sub, ['<Clip ObjectRef="%d"/>' % clip_ref,
              '<MasterClip ObjectURef="%s"/>' % master_uref]), "#%d" % sub)
        body = ['<ClipTrackItem Version="8">', '\t<TrackItem Version="4">', '\t\t<Start>0</Start>',
                '\t</TrackItem>', '\t<SubClip ObjectRef="%d"/>' % sub, '</ClipTrackItem>']
        if comp:
            body.append('<Components ObjectRef="%d"/>' % comp)
        g.add(obj("VideoClipTrackItem", 'ObjectID="%d"' % ti, body), "#%d" % ti)
        return ti

    def media_item(m, comp=None):
        c = g.nid()   # each use gets its own VideoClip, as Premiere does
        g.add(obj("VideoClip", 'ObjectID="%d"' % c, ['<Clip Version="18">',
              '\t<Source ObjectRef="%d"/>' % media[m]["vms"], '</Clip>']), "#%d" % c)
        return item(c, media[m]["mc"], comp)

    def sequence(name, items, extra=()):
        su, vg, track, mc, cpi = U("seq/" + name), g.nid(), U("track/" + name), U("seqmc/" + name), U("seqcpi/" + name)
        vss, ass, vc, ac = g.nid(), g.nid(), g.nid(), g.nid()
        g.add(obj("VideoClipTrack", 'ObjectUID="%s"' % track, ['<ClipTrack Version="1">', '\t<ClipItems Version="1">',
              '\t\t<TrackItems Version="1">'] + ['\t\t\t<TrackItem Index="%d" ObjectRef="%d"/>' % (k, t)
              for k, t in enumerate(items)] + ['\t\t</TrackItems>', '\t</ClipItems>', '</ClipTrack>']), "@" + track)
        g.add(obj("VideoTrackGroup", 'ObjectID="%d"' % vg, ['<TrackGroup Version="1">', '\t<Tracks Version="1">',
              '\t\t<Track Index="0" ObjectURef="%s"/>' % track, '\t</Tracks>', '</TrackGroup>']), "#%d" % vg)
        g.add(obj("Sequence", 'ObjectUID="%s"' % su, ['<Node Version="1">', '\t<Properties Version="1">',
              '\t\t<MZ.EditLine>0</MZ.EditLine>', '\t</Properties>', '</Node>', '<TrackGroups Version="1">',
              '\t<TrackGroup Version="1" Index="0">', '\t\t<First>%s</First>' % U("videomedia"),
              '\t\t<Second ObjectRef="%d"/>' % vg, '\t</TrackGroup>', '</TrackGroups>',
              '<Name>%s</Name>' % name] + list(extra)), "@" + su)
        for tag, i in (("VideoSequenceSource", vss), ("AudioSequenceSource", ass)):
            g.add(obj(tag, 'ObjectID="%d"' % i, ['<SequenceSource Version="4">', '\t<Sequence ObjectURef="%s"/>' % su,
                  '</SequenceSource>', '<OriginalDuration>0</OriginalDuration>']), "#%d" % i)
        for tag, i, s in (("VideoClip", vc, vss), ("AudioClip", ac, ass)):
            g.add(obj(tag, 'ObjectID="%d"' % i, ['<Clip Version="18">', '\t<Source ObjectRef="%d"/>' % s, '</Clip>']), "#%d" % i)
        g.add(obj("MasterClip", 'ObjectUID="%s"' % mc, ['<Clips Version="1">', '\t<Clip Index="0" ObjectRef="%d"/>' % ac,
              '\t<Clip Index="1" ObjectRef="%d"/>' % vc, '</Clips>', '<Name>%s</Name>' % name]), "@" + mc)
        g.add(obj("ClipProjectItem", 'ObjectUID="%s"' % cpi, ['<ProjectItem Version="1">', '\t<Name>%s</Name>' % name,
              '</ProjectItem>', '<MasterClip ObjectURef="%s"/>' % mc]), "@" + cpi)
        seqs[name] = dict(uid=su, mc=mc, cpi=cpi, vc=vc)

    # B first: it owns the only data copies of H1 and H2
    fb = filt([("StartKeyframeValue", H1, DATA1), ("PremiereFilterPrivateData", H2, DATA2)])
    sequence("SEQ B", [media_item(1, fb), media_item(3), media_item(4, filt([("StartKeyframeValue", H1, None)]))])
    sequence("NEST 2", [media_item(2)])
    sequence("NEST 1", [item(seqs["NEST 2"]["vc"], seqs["NEST 2"]["mc"]), media_item(2)])
    fa1 = filt([("StartKeyframeValue", H1, None), ("SecondaryFilterData", H2, None)])
    fa2 = filt([("PremiereFilterPrivateData", H2, None), ("StartKeyframeValue", H1, None)])
    preset = ('<ExporterPreset>&lt;ExportPreset&gt;&lt;Track ObjectRef="999"/&gt;&lt;Clip ObjectURef="%s"/&gt;\n'
              'ObjectRef="998" &lt;/ExportPreset&gt;</ExporterPreset>' % U("ghost"))
    sequence("SEQ A", [media_item(1, fa1), media_item(2, fa2), item(seqs["NEST 1"]["vc"], seqs["NEST 1"]["mc"])],
             extra=[preset])

    # bins
    root, b1, b2, b3 = U("root"), U("bin1"), U("bin2"), U("bin3")

    def bin_obj(tag, uid, name, kids):
        body = ['<ProjectItem Version="1">', '\t<Name>%s</Name>' % name, '</ProjectItem>',
                '<ProjectItemContainer Version="1">', '\t<Items Version="1">']
        body += ['\t\t<Item Index="%d" ObjectURef="%s"/>' % (k, u) for k, u in enumerate(kids)]
        body += ['\t</Items>', '</ProjectItemContainer>']
        return obj(tag, 'ObjectUID="%s"' % uid, body)

    bins = [bin_obj("BinProjectItem", b1, "01_SEQUENCES", [seqs[n]["cpi"] for n in ("SEQ B", "SEQ A", "NEST 1", "NEST 2")]),
            bin_obj("BinProjectItem", b2, "02_MEDIA", [media[1]["cpi"], media[2]["cpi"], media[3]["cpi"], b3]),
            bin_obj("BinProjectItem", b3, "B_ONLY", [media[4]["cpi"]])]

    # Project with nested LOCAL ObjectIDs 1..20
    stags = (["ProjectSettings"] + ["CompileSettings"] * 5 + ["ScratchDiskSettings", "IngestSettings",
             "WorkspaceSettings"] + ["VideoSettings", "AudioSettings"] * 14)
    body = ['<Node Version="1">', '\t<Properties Version="1">',
            '\t\t<MZ.PrefixKey.OpenSequenceGuidList.1>%s</MZ.PrefixKey.OpenSequenceGuidList.1>' % seqs["SEQ B"]["uid"],
            '\t\t<MZ.PrefixKey.OpenSequenceGuidList.2>%s</MZ.PrefixKey.OpenSequenceGuidList.2>' % seqs["SEQ A"]["uid"],
            '\t</Properties>', '</Node>', '<LocalObjects Version="1">']
    for k in range(1, 21):
        body += ['\t<LocalThing ObjectID="%d" Version="1">' % k]
        if k < 20:
            body += ['\t\t<Next ObjectRef="%d"/>' % (k + 1)]
        body += ['\t</LocalThing>']
    body += ['</LocalObjects>', '<RootProjectItem ObjectURef="%s"/>' % root]
    body += ['<%s ObjectRef="%d"/>' % (t, n) for n, t in zip(range(3, 40), stags)]
    head = [("#1", obj("Project", 'ObjectID="1"', body, ver="40")),
            ("@" + root, bin_obj("RootProjectItem", root, "root", [b1, b2]))]
    for n, t in zip(range(3, 40), stags):
        sb = ['<Name>%s %d</Name>' % (t, n)]
        if 3 <= n <= 8:
            sb += ['<VideoSettings ObjectRef="%d"/>' % (12 + 2 * (n - 3)), '<AudioSettings ObjectRef="%d"/>' % (13 + 2 * (n - 3))]
        if n == 4:
            sb += ['<ExporterPreset>&lt;Preset&gt;&lt;Setting ObjectRef="999"/&gt;&lt;/Preset&gt;</ExporterPreset>']
        if n not in drop_settings:
            head.append(("#%d" % n, obj(t, 'ObjectID="%d"' % n, sb)))
    texts = ['<?xml version="1.0" encoding="UTF-8" ?>', '<PremiereData Version="3">', '\t<Project ObjectRef="1"/>']
    texts += [t for _, t in head] + bins + [t for _, t in g.objs]
    texts.append('</PremiereData>\n')
    return "\n".join(texts) + "\n", dict(seqs=seqs, media=media, H1=H1, H2=H2, DATA1=DATA1, DATA2=DATA2,
                                          bins=dict(root=root, seq=b1, media=b2, b_only=b3))


def write(path, drop_settings=()):
    text, info = build(drop_settings)
    with open(path, "wb") as f:
        f.write(gz(text))
    return info


if __name__ == "__main__":
    drop = ()
    if "--drop-settings" in sys.argv:
        a, b = sys.argv[sys.argv.index("--drop-settings") + 1].split("-")
        drop = range(int(a), int(b) + 1)
    write(sys.argv[1], drop)
    print("wrote", sys.argv[1])
