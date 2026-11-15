#!/usr/bin/env python3
"""run_tests.py: end to end on the fixed test case TEST_DRIVE_v1. Prints PASS/FAIL/SKIP per check.

  python3 tests/run_tests.py [--keep DIR]

Writes only into a temp folder (or --keep DIR). Needs ffprobe for folder ingest; uses
ffmpeg (if present) to make one overlapping-timecode pair; otherwise synthesizes the pair
in objects.json. The prproj adapter test is SKIPPED when py-premiere is not installed.
"""
import argparse, contextlib, gzip, hashlib, io, json, os, shutil, subprocess, sys, tempfile
import xml.etree.ElementTree as ET
sys.dont_write_bytecode = True        # no __pycache__ inside the plugin (size gate)
HERE = os.path.dirname(os.path.abspath(__file__))
SK = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SK, "scripts"))
import ppf, ppf_plan, ppf_write_xmeml, ppf_verify, ppf_prproj_peek, ppf_match, ppf_organize, ppf_setup
from ppf_common import load_json, save_json, have

REPO = os.path.normpath(os.path.join(SK, "../../../.."))
DRIVE = os.path.join(REPO, "skills/261105-scan-and-index-hard-drives/test_drive/TEST_DRIVE_v1")
P6 = os.path.join(REPO, "reports/261108-2200_scan-v16_review/P6_DATASETS")
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append("PASS" if cond else "FAIL")
    print("%s  %s%s" % ("PASS" if cond else "FAIL", name, ("  [%s]" % detail) if detail else ""))


def skip(name, why):
    RESULTS.append("SKIP"); print("SKIP  %s  [%s]" % (name, why))


def quiet(fn, *a):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            r = fn(*a)
        except SystemExit as e:
            r = ("exit", e.code)
    return r, buf.getvalue()


def seqs_of(xml):
    return [s.findtext("name") for s in ET.parse(xml).getroot().iter("sequence")]


def make_tc_fixture(root, with_tc=True):
    """Two cameras, same day, overlapping timecode: CAM_A 10:00:00:00 4 s, CAM_B 10:00:02:00 4 s."""
    for cam, tc in (("CAM_A", "10:00:00:00"), ("CAM_B", "10:00:02:00")):
        d = os.path.join(root, "261001_SHOOT_DAY_1", cam); os.makedirs(d, exist_ok=True)
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=160x90:r=25:d=4",
               "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "4", "-c:v", "mpeg4", "-c:a", "aac"]
        if with_tc:
            cmd += ["-timecode", tc]
        subprocess.run(cmd + [os.path.join(d, cam + "_C001.mov")], check=True)


def make_iphone_fixture(root):
    """Two phones, one day (creation_time 2026-11-10, file mtime today): IMG_0001 29.97 on phone A, IMG_0002 120 fps
    on phone B starting 1 s later (3 s wall-clock overlap), IMG_0003 a 1.5 s Live Photo .MOV + .HEIC + .AAE sidecar."""
    for sub_, n, rate, d, ct, model in (("PHONE_A", 1, "30000/1001", 4, "2026-11-10T18:30:00Z", "iPhone 15 Pro"),
                                        ("PHONE_B", 2, "120", 4, "2026-11-10T18:30:01Z", "iPhone 13"),
                                        ("PHONE_A", 3, "30000/1001", 1.5, "2026-11-10T19:00:00Z", "iPhone 15 Pro")):
        os.makedirs(os.path.join(root, sub_), exist_ok=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=64x36:r=%s:d=%s" % (rate, d),
                        "-c:v", "mpeg4", "-movflags", "use_metadata_tags", "-metadata", "creation_time=" + ct,
                        "-metadata", "com.apple.quicktime.make=Apple", "-metadata", "com.apple.quicktime.model=" + model,
                        os.path.join(root, sub_, "IMG_%04d.MOV" % n)], check=True)
    for fn_ in ("IMG_0003.HEIC", "IMG_0003.AAE", "IMG_0001.THM", "IMG_0002.XMP"):
        open(os.path.join(root, "PHONE_A" if fn_ != "IMG_0002.XMP" else "PHONE_B", fn_), "wb").write(b"synthetic")


def iphone_tests(work, out, prwrite_ok, vclip):
    import ppf_ingest
    from ppf_common import rate_of
    # 1. shoot date from metadata (G3)
    loc, day = ppf_ingest.shot_date("2026-11-10T18:30:00Z")
    _, day2 = ppf_ingest.shot_date("2026-11-10T23:30:00Z", "2026-11-10T16:30:00-0700")
    check("shot_date: creation_time 18:30Z -> 261110 (local zone); quicktime creationdate offset wins (-0700 -> 261110)",
          day == "261110" and day2 == "261110", "%s %s" % (day, day2))
    if not (have("ffmpeg") and have("ffprobe")):
        skip("iPhone fixture tests", "ffmpeg/ffprobe not found")
    else:
        ip = os.path.join(work, "IPHONE"); make_iphone_fixture(ip)
        (path, good), log = quiet(ppf.main, ["build", ip, "--project", "IP", "--out", out, "--to", "xmeml", "--date", "261113"])
        o = {c["clip"]: c for c in load_json(os.path.join(out, "IP_objects.json"))["clips"]}
        c1, c2 = o.get("IMG_0001.MOV", {}), o.get("IMG_0002.MOV", {})
        check("G3: ffmpeg creation_time=2026-11-10T18:30:00Z, mtime today -> day 261110, date_source creation_time",
              c1.get("day") == "261110" and c1.get("date_source") == "creation_time", "%s %s" % (c1.get("day"), c1.get("date_source")))
        check("verify PASS on the iPhone fixture build", good, "" if good else log[-400:])
        names = seqs_of(path)
        so = "261110_01_PHONE_stringout"
        root = ET.parse(path).getroot()
        seq = next((s for s in root.iter("sequence") if s.findtext("name") == so), None)
        placed = sorted({ci.findtext("name") for ci in seq.iter("clipitem")}) if seq is not None else []
        check("PHONE by day: two phones' IMG files in one %s, device stored" % so,
              placed == ["IMG_0001.MOV", "IMG_0002.MOV"] and c1.get("card") == c2.get("card") == "PHONE"
              and c1.get("device") == "Apple iPhone 15 Pro" and c2.get("device") == "Apple iPhone 13", "%s %s" % (placed, names))
        f120 = next((f for f in root.iter("file") if f.findtext("name") == "IMG_0002.MOV" and f.find("rate") is not None), None)
        check("slo-mo: xmeml <file><rate> of the 120 fps clip is 120, its sequence is 29.97 (30 ntsc)",
              f120 is not None and f120.findtext("rate/timebase") == "120" and f120.findtext("rate/ntsc") == "FALSE"
              and seq is not None and seq.findtext("rate/timebase") == "30" and seq.findtext("rate/ntsc") == "TRUE"
              and c2.get("fps") == 120.0 and c2.get("slomo"), "file %s seq %s" % (f120 is not None and f120.findtext("rate/timebase"),
                                                                             seq is not None and seq.findtext("rate/timebase")))
        n2 = [ci for ci in seq.iter("clipitem") if ci.findtext("name") == "IMG_0002.MOV"] if seq is not None else []
        check("slo-mo placed by real duration: 4 s at 29.97 = 120 frames", bool(n2) and int(n2[0].findtext("end")) - int(n2[0].findtext("start")) == 120,
              n2 and "%s-%s" % (n2[0].findtext("start"), n2[0].findtext("end")))
        live = o.get("IMG_0003.MOV", {})
        bins = [b.findtext("name") for b in root.iter("bin")]
        check("Live Photo: 1.5 s .MOV with same-stem .HEIC -> kind live, 05_STILLS/<day>/_live, in no sequence; .HEIC -> still",
              live.get("kind") == "live" and "_live" in bins and o.get("IMG_0003.HEIC", {}).get("kind") == "still"
              and not any(ci.findtext("name") == "IMG_0003.MOV" for s_ in root.iter("sequence") for ci in s_.iter("clipitem")),
              live.get("kind"))
        ob = load_json(os.path.join(out, "IP_objects.json"))
        rp = [f for f in os.listdir(out) if f.startswith("IP_v001") and f.endswith("_report.md")]
        rtxt = open(os.path.join(out, rp[0])).read() if rp else ""
        check("sidecars .AAE/.THM/.XMP ignored and listed (objects + report)",
              sorted(os.path.basename(x) for x in ob.get("sidecars", [])) == ["IMG_0001.THM", "IMG_0002.XMP", "IMG_0003.AAE"]
              and "Sidecars" in rtxt and not [c for c in o if c.upper().endswith((".AAE", ".THM", ".XMP"))], str(ob.get("sidecars")))
        check("approx sync on real files: 261110_SYNC_approx_1 (phones overlap 3 s), in the report",
              "261110_SYNC_approx_1" in names and "wall-clock sync, check against audio" in rtxt, ", ".join(names))
        if prwrite_ok:
            (pp, _), plog = quiet(ppf.main, ["build", ip, "--project", "IPp", "--out", out, "--to", "prproj", "--date", "261113"])
            line = [l for l in plog.splitlines() if l.startswith("prproj[prwrite]")]
            check("prproj build with a 120 fps clip in a 29.97 sequence does not raise",
                  str(pp).endswith("IPp_v001.prproj") and bool(line) and "FAIL" not in plog, (line or [plog.strip()[-300:]])[0])
        else:
            skip("prproj slo-mo build", "prwrite not importable")
    # 3. vertical (G4): rotate tag / display matrix, own bin + stringout
    side = ppf_ingest.fields_from_probe({"format": {"duration": "3"}, "streams": [{"codec_type": "video", "width": 1920, "height": 1080,
              "avg_frame_rate": "30000/1001", "side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}]}]})
    tag = ppf_ingest.fields_from_probe({"format": {"duration": "3"}, "streams": [{"codec_type": "video", "width": 1920, "height": 1080,
              "avg_frame_rate": "30000/1001", "tags": {"rotate": "90"}}]})
    check("rotation: 1920x1080 with display matrix -90 / rotate=90 -> display 1080x1920",
          (side["width"], side["height"], tag["width"], tag["height"]) == (1080, 1920, 1080, 1920), str((side["rotation"], tag["rotation"])))
    vc = [vclip(1, 29.97, "PHONE_01"), vclip(2, 29.97, "PHONE_01"), vclip(3, 29.97, "PHONE_01")]
    vc[1].update(width=1080, height=1920, orientation="vertical")
    rc = {"day_from": "mtime", "card": "PHONE_01", "width": tag["width"], "height": tag["height"]}
    ppf_ingest.iphone_fields(rc, tag, "IMG_0009.MOV")
    vc[2].update(width=1080, height=1920, orientation=rc["orientation"], rotation=90)
    for c in vc:
        c.setdefault("orientation", "horizontal")
    vp, _ = quiet(ppf_plan.build_plan, {"clips": vc}, "VERT", "261110", None, False, 3, "tfidf")
    vs = [x for x in vp["ops"] if x["op"] == "add_sequence" and x["name"] == "261110_VERTICAL_stringout"]
    inv = [x["clip"] for x in vp["ops"] if x["op"] == "place_clip" and x["seq"] == "261110_VERTICAL_stringout"]
    inh = {x["clip"] for x in vp["ops"] if x["op"] == "place_clip" and x["seq"] != "261110_VERTICAL_stringout"}
    mb = {x["clip"]: x["bin"] for x in vp["ops"] if x["op"] == "add_masterclip"}
    check("vertical: 1080x1920 and rotate=90 objects -> _vertical bin, 261110_VERTICAL_stringout 1080x1920 at 29.97, not in horizontal sequences",
          rc["orientation"] == "vertical" and vs and (vs[0]["width"], vs[0]["height"], vs[0]["fps"]) == (1080, 1920, 29.97)
          and inv == ["IMG_0002", "IMG_0003"] and inh == {"IMG_0001"} and mb["IMG_0002"].endswith("/_vertical"), "%s %s" % (inv, inh))
    # 4. HDR flag
    h = ppf_ingest.fields_from_probe({"format": {"duration": "3"}, "streams": [{"codec_type": "video", "width": 1920, "height": 1080,
              "avg_frame_rate": "30/1", "color_transfer": "arib-std-b67", "color_primaries": "bt2020"}]})
    pq = ppf_ingest.fields_from_probe({"format": {}, "streams": [{"codec_type": "video", "width": 1920, "height": 1080,
              "avg_frame_rate": "30/1", "color_transfer": "smpte2084", "color_primaries": "bt2020"}]})
    hc = [vclip(5, 29.97, "PHONE_01")]; hc[0].update(hdr=h["hdr"], color_space=h["color_space"])
    hp, _ = quiet(ppf_plan.build_plan, {"clips": hc}, "HDR", "261110", None, False, 3, "tfidf")
    check("HDR: arib-std-b67 -> HLG, smpte2084 -> PQ, color_space bt2020, listed in the report",
          (h["hdr"], pq["hdr"], h["color_space"]) == ("HLG", "PQ", "bt2020") and hp["hdr_clips"] == ["IMG_0005"]
          and "HDR clips (set sequence color to Rec.709 or work in HLG)" in (hp.get("selects_report") or ""), str((h["hdr"], pq["hdr"])))
    # 6. approximate sync, synthetic; a TC clip is never mixed in
    ac = [vclip(10, 29.97, "PHONE_01"), vclip(11, 29.97, "PHONE_01"), vclip(12, 29.97, "PHONE_01")]
    ac[0].update(shot_at="2026-11-10T18:30:00+00:00", date_source="creation_time", device="Apple iPhone 15 Pro", duration_s=10.0)
    ac[1].update(shot_at="2026-11-10T18:30:05+00:00", date_source="creation_time", device="Apple iPhone 13", duration_s=10.0)
    ac[2].update(shot_at="2026-11-10T18:30:02+00:00", date_source="creation_time", device="Apple iPhone 12", tc_start="18:30:02:00")
    ap_, _ = quiet(ppf_plan.build_plan, {"clips": ac}, "SYN", "261110", None, False, 3, "tfidf")
    sq = [x for x in ap_["ops"] if x["op"] == "add_sequence" and "_SYNC_approx_" in x["name"]]
    pl = sorted((x["clip"], x["track"], x["at_s"], x.get("approx")) for x in ap_["ops"] if x["op"] == "place_clip" and sq and x["seq"] == sq[0]["name"])
    check("approx sync: two clips overlapping 5 s -> 261110_SYNC_approx_1, V1/V2 offset 5 s, approx true, TC clip left out, in report",
          [x["name"] for x in sq] == ["261110_SYNC_approx_1"] and sq[0].get("approx") is True
          and pl == [("IMG_0010", "V1", 0.0, True), ("IMG_0011", "V2", 5.0, True)]
          and "wall-clock sync, check against audio" in (ap_.get("selects_report") or ""), str(pl))
    # 7. sidecars and stills, synthetic names
    sc = [vclip(20, 29.97, "PHONE_01"), vclip(21, 29.97, "PHONE_01")]
    sc[0].update(path="/x/IMG_0020.MOV", duration_s=2.8); sc[1].update(path="/x/IMG_0021.MOV", duration_s=2.8)
    sc.append(dict(vclip(20, None, "PHONE_01"), clip_id="IMG_0020H", path="/x/IMG_0020.HEIC", kind="still", duration_s=None))
    ppf_ingest.mark_live(sc)
    check("Live Photo by name: IMG_0020.MOV (2.8 s) + IMG_0020.HEIC -> live; IMG_0021.MOV without a HEIC stays broll; .heif/.aae/.thm/.xmp sets",
          [c["kind"] for c in sc] == ["live", "broll", "still"] and ".heif" in ppf_ingest.STILL_EXT
          and ppf_ingest.SIDECAR_EXT == {".aae", ".thm", ".xmp"}, str([c["kind"] for c in sc]))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--keep"); a = ap.parse_args()
    work = a.keep or tempfile.mkdtemp(prefix="ppf_tests_")
    out = os.path.join(work, "out")
    print("work: %s  ffprobe=%s ffmpeg=%s" % (work, have("ffprobe"), have("ffmpeg")))

    # 1. build (tier 3 adapter, runs here) from the media folder
    (path, good), log = quiet(ppf.main, ["build", DRIVE, "--project", "TD", "--out", out, "--to", "xmeml", "--date", "261112"])
    check("build TEST_DRIVE_v1 -> TD_v001.xml", os.path.basename(path) == "TD_v001.xml", path)
    check("verify PASS on v001", good, "" if good else log[-400:])
    root = ET.parse(path).getroot()
    top = [b.findtext("name") for b in root.find("project/children").findall("bin")]
    check("6 top bins 00-05", top == ppf_plan.TOP_BINS, ", ".join(top))
    names = seqs_of(path)
    want = ["260901_01_AERIAL_DJI_stringout", "260901_02_FX6_A001_stringout", "260901_03_GH5S_A_stringout",
            "260901_04_P2_A001_stringout", "260902_01_EX1_A_stringout"]
    check("one stringout per card per day, right names", sorted(n for n in names if n.endswith("_stringout")) == want,
          ", ".join(n for n in names if n.endswith("_stringout")))
    check("INTERVIEWS and BROLL per day", all(("%s_%s" % (d, k)) in names for d in ("260901", "260902") for k in ("INTERVIEWS", "BROLL")))
    check("no SYNC sequence on TEST_DRIVE_v1 (no cross-card TC overlap)", not [n for n in names if "_SYNC_" in n])
    cuts = [b.findtext("name") for b in root.find("project/children").findall("bin")[0].find("children").findall("bin")]
    check("00_CUTS/<date>_cut01", cuts == ["261112_cut01"], ", ".join(cuts))
    days = [b.findtext("name") for b in root.find("project/children").findall("bin")[2].find("children").findall("bin")]
    check("02_FOOTAGE by shoot day", days == ["260901", "260902"], ", ".join(days))

    # 2. versions: second build is v002, v001 untouched
    h1 = hashlib.sha256(open(path, "rb").read()).hexdigest()
    (path2, good2), _ = quiet(ppf.main, ["build", DRIVE, "--project", "TD", "--out", out, "--to", "xmeml", "--date", "261112"])
    check("second build writes _v002, not overwrite", os.path.basename(path2) == "TD_v002.xml" and hashlib.sha256(open(path, "rb").read()).hexdigest() == h1)
    check("same input -> same XML (stable ids)", open(path).read() == open(path2).read())

    # 3. revise: an add_sequence + append_clips op from a conversation
    objs = load_json(os.path.join(out, "TD_objects.json"))
    aerial = [c["clip_id"] for c in objs["clips"] if c["card"] == "AERIAL_DJI"]
    ops = os.path.join(work, "ops.json")
    save_json(ops, [{"op": "add_sequence", "name": "261112_cut01_aerials", "bin": "00_CUTS/261112_cut01", "fps": 29.97, "width": 1920, "height": 1080},
                    {"op": "append_clips", "seq": "261112_cut01_aerials", "clips": aerial}])
    (path3, good3), log3 = quiet(ppf.main, ["revise", os.path.join(out, "TD_v002.plan.json"), "--ops", ops, "--to", "xmeml"])
    check("revise applies add_sequence -> _v003", os.path.basename(path3) == "TD_v003.xml" and "261112_cut01_aerials" in seqs_of(path3))
    check("verify PASS on v003", good3, "" if good3 else log3[-400:])
    save_json(ops + ".bad", [{"op": "place_clip", "seq": "NOPE", "clip": aerial[0]}])
    r, _ = quiet(ppf.main, ["revise", os.path.join(out, "TD_v002.plan.json"), "--ops", ops + ".bad", "--to", "xmeml"])
    check("revise refuses an op on a missing sequence", isinstance(r, tuple) and r[0] == "exit")

    # 4. scan P6 CSV input
    csvs = [f for f in os.listdir(P6) if "clips_P6-04" in f] if os.path.isdir(P6) else []
    if csvs:
        (p4, g4), log4 = quiet(ppf.main, ["build", os.path.join(P6, csvs[0]), "--media-root", DRIVE, "--project", "TDscan",
                                          "--out", out, "--to", "xmeml", "--date", "261112"])
        check("build from scan P6 clips CSV, verify PASS", g4, "" if g4 else log4[-400:])
    else:
        skip("build from scan P6 clips CSV", "no P6 CSV in repo")

    # 5. TC sync fixture
    fx = os.path.join(work, "FIX_TC"); fx0 = os.path.join(work, "FIX_NOTC")
    if have("ffmpeg"):
        make_tc_fixture(fx, True); make_tc_fixture(fx0, False)
        (p5, g5), _ = quiet(ppf.main, ["build", fx, "--project", "FX", "--out", out, "--to", "xmeml", "--date", "261112"])
        (p6, g6), _ = quiet(ppf.main, ["build", fx0, "--project", "FX0", "--out", out, "--to", "xmeml", "--date", "261112"])
        s5 = [s for s in ET.parse(p5).getroot().iter("sequence") if s.findtext("name") == "261001_SYNC_01"]
        starts = sorted(int(c.findtext("start")) for c in s5[0].findall("media/video/track/clipitem")) if s5 else []
        check("TC overlap -> 261001_SYNC_01, 2 video tracks, offset 2 s (50 fr)", bool(s5) and len(s5[0].findall("media/video/track")) == 2 and starts == [0, 50], str(starts))
        check("no TC -> no SYNC sequence", not [n for n in seqs_of(p6) if "_SYNC_" in n])
        check("verify PASS on both fixtures", g5 and g6)
    else:
        o = load_json(os.path.join(out, "TD_objects.json"))
        a_, b_ = [c for c in o["clips"] if c["card"] in ("GH5S_A", "AERIAL_DJI")][:2]
        a_.update(tc_start="10:00:00:00", fps=25.0); b_.update(tc_start="10:00:02:00", fps=25.0)
        g, _ = ppf_plan.sync_groups([a_, b_])
        check("synthesized TC overlap -> one sync group", len(g) == 1)

    # 6. tier 1 adapter (.prproj via the vendored py-premiere)
    vendor = os.path.normpath(os.path.join(SK, "../../vendor/py-premiere"))
    base = os.path.join(vendor, "samples", "49_seq_settings.prproj")
    base_real = os.path.exists(base) and not open(base, "rb").read(40).startswith(b"version https://git-lfs")
    if not base_real:
        RESULTS.append("TAG"); print("TAG   vendored base sample is a Git LFS pointer stub, not a .prproj [%s]; prproj test falls back to py_premiere.new()" % base)
    import ppf_write_prproj
    prwrite_ok = ppf_write_prproj.load_prwrite() is not None   # our lib/prwrite: runs here, no SKIP
    if prwrite_ok:
        RESULTS.append("TAG"); print("TAG   tier 1 uses prwrite (plugin lib/); output runs here, unopened in Premiere")
    if prwrite_ok or os.environ.get("PPF_RUN_PRPROJ") == "1":
        (p7, _), log7 = quiet(ppf.main, ["build", DRIVE, "--project", "TDp", "--out", out, "--to", "prproj", "--date", "261112"]
                              + (["--base", base] if base_real else []))
        check("prproj build writes TDp_v001.prproj", str(p7).endswith("TDp_v001.prproj"), log7.strip().splitlines()[-1] if log7.strip() else "")
        if str(p7).endswith(".prproj"):
            cb = quiet(ppf_prproj_peek.peek, base)[0] if base_real else {}
            cn, _ = quiet(ppf_prproj_peek.peek, p7)
            check("new .prproj has more bins and sequences than the base (stdlib peek)",
                  cn.get("BinProjectItem", 0) > cb.get("BinProjectItem", 0) and cn.get("Sequence", 0) > cb.get("Sequence", 0),
                  "bins %d->%d, sequences %d->%d" % (cb.get("BinProjectItem", 0), cn.get("BinProjectItem", 0), cb.get("Sequence", 0), cn.get("Sequence", 0)))
            (p8, _), _ = quiet(ppf.main, ["build", DRIVE, "--project", "TDp", "--out", out, "--to", "prproj", "--date", "261112"]
                               + (["--base", base] if base_real else []))
            check("second prproj build writes _v002", str(p8).endswith("TDp_v002.prproj"))
    else:
        skip("prproj build (tier 1)", "py_premiere execution not allowed in this sandbox; run PPF_RUN_PRPROJ=1 python3 tests/run_tests.py on the Mac")
    check("vendored py-premiere present", os.path.exists(os.path.join(vendor, "py_premiere", "__init__.py")))

    # 7. stdlib peek on a gzipped XML stand-in
    gz = os.path.join(work, "standin.prproj")
    open(gz, "wb").write(gzip.compress(b'<PremiereData Version="3"><Project ObjectID="1"/><Sequence ObjectID="2"/><Sequence ObjectID="3"/></PremiereData>'))
    c, _ = quiet(ppf_prproj_peek.peek, gz)
    check("prproj peek gunzips and counts (stand-in, not a Premiere file)", c.get("Sequence") == 2)

    # 8. match probe
    (eng, ranked), _ = quiet(ppf_match.rank, "aerial drone shot", objs)
    check("match ranks an AERIAL clip first (engine=%s)" % eng, "AERIAL_DJI" in ranked[0][1], ranked[0][1])

    # 9. organize by content (ppf_organize.py)
    td = load_json(os.path.join(out, "TD_objects.json"))
    if csvs:
        so = os.path.join(out, "TDscan_objects.json")
        (ops9, i9), log9 = quiet(ppf_organize.organize, load_json(so), [], "261112")
        topics = [o["name"] for o in ops9 if o["op"] == "add_sequence" and "_BROLL_" in o["name"]]
        check("organize with scan P6 CSV -> BROLL_<topic> sequences (%s)" % i9.get("mode"), bool(topics), ", ".join(topics))
        print("      " + log9.splitlines()[0])
    else:
        skip("organize with scan data", "no P6 CSV in repo")
    for eng in ("auto", "tfidf"):
        (ops10, i10), log10 = quiet(ppf_organize.organize, td, [], "261112", "drone over the house", 5, eng)
        line = log10.splitlines()[0]
        print("      " + line)
        if eng == "auto":
            # The bundled MiniLM needs numpy and onnxruntime in THIS interpreter. When they are absent the
            # organize step correctly falls back to tfidf and says so, which is the designed behaviour, not a
            # defect: 261114, this read as a hard FAIL on a Python 3.12 without numpy and sent us looking for a
            # bug in the plugin. A missing optional dependency skips, and names what to install.
            try:
                import numpy  # noqa: F401
                import onnxruntime  # noqa: F401
                have_minilm = True
            except Exception:  # noqa: BLE001
                have_minilm = False
            if have_minilm:
                check("engine line says minilm (bundled model, onnxruntime here)", "engine: minilm" in line, line)
            else:
                skip("engine line says minilm", "this interpreter has no numpy / onnxruntime, so organize fell back "
                                                "to tfidf as designed: %s" % line)
        aer = sorted(c["clip_id"] for c in td["clips"] if c["card"] == "AERIAL_DJI")
        grouped = any(sorted(t["clips"]) == aer for t in i10["topics"])
        check("[%s] no scan data: the two DJI aerials grouped in one BROLL topic" % i10["engine"], grouped,
              "; ".join("%s=%d" % (t["seq"], len(t["clips"])) for t in i10["topics"]))
        sel = [o for o in ops10 if o["op"] == "place_clip" and "_SELECTS_" in o["seq"]]
        first = sel[0]["clip"] if sel else ""
        sc = i10["selects"][0]["picks"][0][0] if i10["selects"] else None
        check("[%s] notes 'drone over the house' -> SELECTS first clip is a DJI aerial (score %s)" % (i10["engine"], sc),
              "AERIAL_DJI" in first, first + ("  NOTE: score 0, the DJI won on file order, not meaning" if i10["engine"] == "tfidf" and not sc else ""))
    # takes: synthesized segments with a repeated line
    segd = os.path.join(work, "SEGS"); os.makedirs(segd, exist_ok=True)
    so2 = dict(load_json(os.path.join(out, "TDscan_objects.json")) if csvs else td)
    if csvs:
        import csv as _csv
        rows = list(_csv.DictReader(open(os.path.join(P6, [f for f in os.listdir(P6) if "segments" in f][0]), encoding="utf-8")))
        for k, r in enumerate(rows[:3]):
            r["text"] = "we built this house in nineteen seventy" if k != 1 else "We built this house in nineteen-seventy."
        cp = os.path.join(segd, "X_clips_P6-04.csv"); shutil.copy(so2["source"], cp)
        with open(os.path.join(segd, "X_segments_P6-02.csv"), "w", newline="", encoding="utf-8") as fh:
            w = _csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
        so2["source"] = cp
        (ops11, i11), _ = quiet(ppf_organize.organize, so2, [], "261112")
        tk = [o for o in ops11 if o["op"] == "add_sequence" and "_TAKES_" in o["name"]]
        n_tk = sum(1 for o in ops11 if o["op"] == "place_clip" and tk and o["seq"] == tk[0]["name"])
        check("segments with a repeated line -> one TAKES sequence with every take (3)", len(tk) == 1 and n_tk == 3,
              tk[0]["name"] if tk else "none")
    # build end to end with --notes: plan carries the ops, report is written, revise still works
    nf = os.path.join(work, "notes.txt"); open(nf, "w").write("drone over the house\n")
    (p12, g12), log12 = quiet(ppf.main, ["build", DRIVE, "--project", "TDorg", "--out", out, "--to", "xmeml", "--date", "261112", "--notes", nf])
    rep = os.path.join(out, "TDorg_v001_selects_report.md")
    check("build --notes: SELECTS + topics in the written XML, verify PASS, report written",
          g12 and any("_SELECTS_" in n for n in seqs_of(p12)) and any("_BROLL_" in n for n in seqs_of(p12)) and os.path.exists(rep),
          "" if g12 else log12[-300:])
    save_json(ops + ".org", [{"op": "rename_sequence", "name": "261112_SELECTS_drone_over_the_house", "to": "261112_SELECTS_drone"}])
    (p13, g13), _ = quiet(ppf.main, ["revise", os.path.join(out, "TDorg_v001.plan.json"), "--ops", ops + ".org", "--to", "xmeml"])
    check("revise renames an organize sequence -> v002, verify PASS", str(p13).endswith("TDorg_v002.xml") and g13 and "261112_SELECTS_drone" in seqs_of(p13))
    (rc, log14) = quiet(ppf_setup.check)
    print("      setup check: " + log14.strip().splitlines()[-1])
    check("ppf_setup.py check: model sha256 OK", log14.count("sha256 OK") == 2, log14.strip().replace("\n", " | ")[:300])
    # 14. iPhone VFR (white paper 261113): conform to a standard rate, flag, never a raw rate in Rate or xmeml
    from ppf_common import conform_fps, STD_FPS
    cases = [((28.945,), 29.97, True), ((26.48, 29.97), 29.97, True), ((26.48,), 25.0, True), ((29.976,), 29.97, False),
             ((23.982,), 23.976, False), ((30.011,), 30.0, False), ((59.94,), 59.94, False)]
    got = [(a, conform_fps(*a)) for a, _, _ in cases]
    check("conform_fps: 28.945->29.97, 26.48->29.97 (dominant 29.97) / 25 (none), 29.976->29.97, 23.982->23.976, 30.011->30, 59.94",
          all(g[0] == std and g[1] == v and g[2] == a[0] for (a, g), (_, std, v) in zip(got, cases)),
          "; ".join("%s->%s" % (a, g[:2]) for a, g in got))
    tb = ET.Element("x"); ppf_write_xmeml.rate_el(tb, 28.945)
    check("xmeml rate for 28.945 is timebase 30 ntsc TRUE (not 29)", tb.findtext("rate/timebase") == "30" and tb.findtext("rate/ntsc") == "TRUE",
          "%s %s" % (tb.findtext("rate/timebase"), tb.findtext("rate/ntsc")))
    # the measured rates of the Fox Field scan (paper section 3), spread over small cards so small groups pick odd rates
    rates = ([29.975] * 21 + [29.976] * 30 + [29.977] * 18 + [30.0] * 16 + [23.981] + [23.982] * 2 + [23.983] * 2
             + [23.984] * 4 + [23.985] + [23.988] + [30.006, 30.011])
    vclips = []
    def vclip(n, fps, card):
        return {"clip_id": "IMG_%04d" % n, "clip": "IMG_%04d.MOV" % n, "xml_key": "k%04d" % n, "day": "261110",
                "card": card, "card_no": card[-2:], "rel_path": "%s/IMG_%04d.MOV" % (card, n), "kind": "broll", "role": "master",
                "duration_s": 4.0, "fps": fps, "width": 1920, "height": 1080, "path": os.path.join(work, "no_media", "IMG_%04d.MOV" % n)}
    for n, f in enumerate(rates):
        vclips.append(vclip(n, f, "PHONE_%02d" % (n // 4)))
    vclips += [vclip(900, 28.945, "PHONE_90"), vclip(901, 26.48, "PHONE_91"),
               vclip(902, 23.982, "PHONE_92"), vclip(903, 23.984, "PHONE_92")]
    vobj = {"clips": vclips, "mode": "folder", "source": None}
    (vplan, vlog) = quiet(ppf_plan.build_plan, vobj, "VFR", "261110", "iphone footage\n", True, 3, "tfidf")
    seq_fps = [o["fps"] for o in vplan["ops"] if o["op"] == "add_sequence"]
    pl = [o for o in vplan["ops"] if o["op"] == "place_clip"]
    check("plan from %d mixed iPhone rates (%d distinct): every add_sequence fps is standard (%d sequences)"
          % (len(vclips), len({c["fps_measured"] for c in vclips}), len(seq_fps)),
          seq_fps and all(f in STD_FPS for f in seq_fps), ", ".join(sorted({str(f) for f in seq_fps})))
    vv = {v["clip"]: v for v in vplan.get("vfr_clips", [])}
    check("VFR flagged, not transcoded: plan['vfr_clips'] has 28.945 and 26.48 (both -> 29.97), clips keep fps_measured, place_clip ops carry it",
          set(vv) == {"IMG_0900", "IMG_0901"} and vv["IMG_0901"]["fps"] == 29.97 and vv["IMG_0900"]["fps_measured"] == 28.945
          and all("fps_measured" in o and "vfr" in o for o in pl) and any(o["vfr"] for o in pl)
          and "VFR clips" in (vplan.get("selects_report") or ""), "vfr_clips=%s" % sorted(vv))
    if prwrite_ok:
        import ppf_write_prproj
        vout = os.path.join(work, "vfr_out")
        vres, vwlog = quiet(ppf_write_prproj.write_prwrite, vplan, vobj, None, vout)
        line = [l for l in vwlog.splitlines() if l.startswith("prproj[prwrite]")]
        check("prwrite writes the mixed-rate plan to a .prproj, every sequence on reparse (no BuildError)",
              str(vres).endswith("VFR_v001.prproj") and line and "sequences %d/%d" % (len(seq_fps), len(seq_fps)) in line[0],
              (line or [vwlog.strip()[-300:]])[0])
    else:
        skip("prwrite mixed-rate build", "prwrite not importable")

    # iPhone pack (2.2.0): shoot date, slo-mo, vertical, HDR, PHONE by day, approx sync, sidecars/stills
    iphone_tests(work, out, prwrite_ok, vclip)

    # size gate
    plug = os.path.normpath(os.path.join(SK, "../.."))
    kb = int(subprocess.run(["du", "-sk", plug], capture_output=True, text=True).stdout.split()[0])
    check("size gate: plugin folder <= 25 MB (du -sk %d KB)" % kb, kb <= 25 * 1024)

    print("\n%d PASS, %d FAIL, %d SKIP, %d TAG" % (RESULTS.count("PASS"), RESULTS.count("FAIL"), RESULTS.count("SKIP"), RESULTS.count("TAG")))
    if not a.keep:
        shutil.rmtree(work, ignore_errors=True)
    sys.exit(1 if "FAIL" in RESULTS else 0)


if __name__ == "__main__":
    main()
