---
name: "260929-run-premiere-project-engine-v2"
description: "Front door to Diego's Premiere Project Engine (the BODY), v2: writing a .prproj with py-premiere is PROVEN (a client project opened in Premiere; PROJECT_A built with no drives mounted). Use whenever about to touch a .prproj, build a project from CSVs, brief an engine worker, or pick a library. Not for reading or writing xmeml."
---

# Run the Premiere Project Engine (v2)

**Kind: Procedure.** Run it before engine work; don't just cite it. Tags: [W] witnessed (ran, output seen) · [D] designed (read, never run) · [X] ruled out. A [D] presented as [W] is the failure this file exists to refuse. **The opposite failure is now on record too (261026): Claude treated a [W] as [D], overrode Diego's statement that a client project opened fine, and spent hours gating a proven path. Diego's word about his own tools is [W].**

## What changed in v2 (261026, from the PROJECT_A build)

| Claim | Tag | Evidence |
|---|---|---|
| py-premiere writes a .prproj that opens and works in Premiere | **[W]** | a client project breakdown project (`CLIENT_A_BREAKDOWN_-_v05_261013_0327.prproj`) was built by `sel_build.py` / `asm_build.py` of `261021-interview-to-assembly-premiere-v1` and opened by Diego. Diego's ruling 261026 06:52: "it was open and it worked fine." |
| `add_sequence(name, preset='1080p23976')`, `add_bin`, `move_to`, `Track.add_clip(item, start=Time)`, `in_point`/`out_point` setters, `save(new path)` | [W] | PROJECT_A pilot 261026 09:32: 5 sequences, 630/630 clips, reparsed from the saved file |
| `import_files()` of NEW media into a project | [W] | PROJECT_A: 18,574 files imported into bins without reading them (see no-drive build) |
| Build a whole project from CSVs with NO drives mounted | [W] | `scripts/build_project_a.py` + `scripts/nodrive_shim.py`: media facts (fps, duration, w×h, channels) fed from the MEDIA_MASTER spreadsheet; every `ActualMediaFilePath` lands as `/Volumes/...`; Premiere relinks on open |
| Director opens the no-drive PROJECT_A OUT file in Premiere | [D] | not yet done at packaging (the a client project proof covers the library; this covers the shim) |
| Writing a transcript INTO the .prproj | [D] | only decoding is proven; use translate-v8 step 3 (Import Static Transcript) |
| Multicam from timecode on real media (G1 audio angles) | [D] | unchanged from v1 |

## The build recipe (the proven path, in order)

1. **Base:** a project Premiere wrote (an empty one Diego saves, or a copy of an existing one). Never the original: `OUT_<job>_<stamp>.prproj`; `save()` refuses overwrite.
2. **Inputs are CSVs**, written before any build: `01_BINS` (bin_id, bin_path, parent), `02_MASTERCLIPS` (media_id → bin_path, source_path), `03_SEQUENCES` (seq → bin_path, name, fps), `04_CLIPS` (seq, track V1..Vn/A1..An, timeline in/out s, source in/out s, media_id, link_group). PROJECT_A v14 is the reference set.
3. **Run** `python3 scripts/build_project_a.py BASE.prproj GUIDE_DIR OUT.prproj MEDIA_MASTER.csv [--only CODE]`. It creates bins on demand (parent first), imports media, adds sequences, places clips, saves, reparses and prints expected-vs-found counts.
4. **No drives?** `nodrive_shim.py` is imported by the builder: it replaces the library's file probes with spreadsheet facts and routes every video/audio extension (.mts, .mxf, .aif, …) to the right importer. Premiere refreshes codec/geometry from the real file on open.
5. **Verify from the saved file, never from the script's report:** reparse, count clips per sequence, grep `ActualMediaFilePath` for `/Volumes`.
6. **Director opens it.** PASS/FAIL recorded here.

## Gotchas (lived, 261026)

- `if not item:` on a py-premiere ProjectItem is False for a bin-less item (it has no children) → every clip was skipped as "media not imported". Use `is None`. Cost: 2 hours and three free-model fix loops that never found it.
- `--only` filters must keep every ancestor bin and every media row the kept clips need, not just bins whose path matches.
- Unsupported still suffixes (.cr2 .nef .heic .dng .psd variants, .jpeg/.tiff spelled long) raise NotImplementedError; map .jpeg→.jpg, .tiff→.tif, skip RAW.
- `('mov','MPEG')` has no VideoStream profile: map mpeg2video-in-MOV to `avc1` or import it later.
- TIMECODE_FORMATS in the library only knows 25/29.97/30; add (10594584000,False):110 and (10584000000,False):100 for 23.976/24 or `_probe_movie` raises.
- The library reads each media file when importing. Where drives are not mounted, feed facts (the shim), never rewrite paths after the fact (X4 still holds).
- Free LLMs (NVIDIA lightning, Groq gpt-oss, Gemini) could not write this builder from the API signatures alone; 3 rounds × 8 fixes repeated the same import error. They can write CSV content and tags; the glue code against an unfamiliar library is Claude's or a human's.

## Everything else

The three organs, the MAP router, sync math, track logic, call-up cards, queue, doctrine and ground rules are unchanged from v1 (`260929-run-premiere-project-engine-v1`): read that file's sections for them. Two of its open items are now closed: **O2** (nothing the engine wrote has been opened) is closed by a client project; **unit 8** of the queue (director opens OUT copy) is done for a client project, pending for the no-drive PROJECT_A file.

## Scripts

- `scripts/build_project_a.py` [W]: the CSV → .prproj builder (Groq-drafted from `sel_build.py`, Claude-fixed). Run it.
- `scripts/nodrive_shim.py` [W]: spreadsheet-fed media probes; import it before building without drives.
- `scripts/sync_math.py`, `kit/*`: as v1.

## Self-check

- [ ] Did Diego say a path works? Then it is [W]; do not re-gate it.
- [ ] CSVs written and counted before the build; verification counted from the saved file.
- [ ] Output is a new file; base untouched; paths are `/Volumes/...`.
- [ ] Any [D] above that this run proved: moved to [W] with the evidence line.