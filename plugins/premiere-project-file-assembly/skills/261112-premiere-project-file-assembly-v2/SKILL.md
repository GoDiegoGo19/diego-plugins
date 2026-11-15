---
name: 261112-premiere-project-file-assembly-v2
description: "A plugin that helps a language model WRITE Premiere project files from a conversation, a new version every time. Takes the scan's data (or a media folder), makes every clip an object with the scan's ids (drive/card/clip), pre-sorts it into Diego's default project (00_CUTS, 01_SEQUENCES, 02_FOOTAGE by shoot day, 03_AUDIO, 04_GRAPHICS, 05_STILLS), builds one YYMMDD_NN_CARD_stringout per card per day, YYMMDD_INTERVIEWS and _BROLL, and _SYNC_n sequences aligned by timecode when timecode exists. The user talks; the LLM writes plain ops; deterministic code writes PROJECT_vNNN. Tier 1 writes .prproj with the vendored py-premiere; tier 3 writes FCP7 xmeml (import duplicates media). Use when Diego asks to build, organize, string out, sync or revise a Premiere project. Not for: driving a running Premiere (MCP, tier 2); scanning drives (the scan plugin); reading XML (Rosetta)."
---

# Premiere project file assembly v2

**Kind: Procedure.** Tags: [W] witnessed here, [D] designed (written, not run here).

## Objective

Write the Premiere project file from the conversation, a new version each time. Diego's words: "write directly from a conversation to a project file in Premiere"; "a model that would take the information and pre-sort it, organize it, even pre-cut it, so it's easy to modify as objects"; "a client project is literally that process, but it was bumbling". The press is deterministic code; the LLM only writes ops.

## The shape

```
 scan P6 CSV / media folder           user conversation
          |                                  |
   ppf_ingest.py -> objects.json      LLM writes ops.json  (references/ops.md)
          |                                  |   (ppf_match.py ranks clips for a note)
   ppf_plan.py  -> plan.json  <---- ppf.py revise applies ops
          |   rule-based bins, stringouts, INTERVIEWS/BROLL, SYNC
   ppf_organize.py (MiniLM bundled in ../../models/minilm; TF-IDF fallback; engine line printed)
          |   + <day>_BROLL_<topic>, <day>_TAKES_<line>, <date>_SELECTS_<note> as plain ops
          |
   WRITER ADAPTERS
    tier 1  ppf_write_prproj.py  -> NAME_vNNN.prproj   our prwrite library (lib/prwrite), vendored py-premiere fallback; user's .prproj as base  [W here, unopened in Premiere]
    tier 2  live MCP / bridge driving Premiere          OUT OF SCOPE
    tier 3  ppf_write_xmeml.py   -> NAME_vNNN.xml      FCP7 xmeml v5 (import duplicates media)  [W here]
          |
   verify: tier 1 reparse with py-premiere (+ ppf_prproj_peek.py, stdlib)
           tier 3 ppf_verify.py (xml.etree gate + Rosetta v7 report)
          |
   version: NAME_vNNN + NAME_vNNN.plan.json; next write is vNNN+1; never overwrite
```

## The method

1. **Front door.** `python3 scripts/ppf.py build <media_folder | P6_clips.csv> --project NAME --out DIR --base COPY_OF_USER.prproj` (tier 1, default `--to prproj`; no `--base` = a fresh project). For a P6 CSV add `--media-root <drive root>`. Tier 3: `--to xmeml`.
2. **Ingest** (`ppf_ingest.py`): one object per media file. `clip_id = <drive>/<card column>/<file>` (the scan's keys). Shoot day from the QuickTime creation date (`creationdate` offset, else `creation_time` in this machine's zone), else a `YYMMDD_` folder, else mtime; `date_source` says which. Timecode from the scan or ffprobe's `timecode` tag. Kind: interview when the scan says transcribe=likely, still/graphic/audio by extension or GRAPHICS folder or alpha pixel format, else broll. Role: proxy (`PROXY/`, `Sub/`, `S03.MP4`) and sidecar (P2 `CONTENTS/AUDIO`) go to bins, never to sequences.
   Phone pack: card `PHONE` per day for phone-named/tagged or card-less video (`device` = make + model); `orientation` from the display size after the rotation tag/display matrix; `hdr` HLG|PQ + `color_space`; `slomo` for >= 90 fps; `.AAE/.THM/.XMP` skipped into `sidecars`; `.HEIC/.HEIF` are stills; a `.MOV` < 3.5 s beside a same-stem `.HEIC` is kind `live`.
3. **Plan** (`ppf_plan.py`): bins `00_CUTS/<date>_cut01`, `01_SEQUENCES/<day>`, `02_FOOTAGE/<day>/<NN>_<CARD>[/_proxy|_sidecar]`, `03_AUDIO/<day>`, `04_GRAPHICS/<day>`, `05_STILLS/<day>`; sequences per day: stringouts (card's clips back to back), INTERVIEWS, BROLL, and SYNC groups (clips from different cards with overlapping TC ranges; one video track per card; offset = TC start minus group start). No TC, no SYNC; instead clips with creation_time whose wall-clock ranges overlap >= 2 s get `<day>_SYNC_approx_<n>` (`approx: true`, never mixed with TC groups). Vertical clips: `_vertical` bin + `<day>_VERTICAL_stringout` 1080x1920, never in horizontal sequences. Live Photos: `05_STILLS/<day>/_live`. Rules: references/bins.md.
4. **Organize by content** (`ppf_organize.py`, run by plan; `--no-organize` skips). With scan data (P6 clips CSV + families/segments CSVs beside it): `<YYMMDD>_BROLL_<family label>` per family with >= 2 clips (segment in/out from the scan) and `<YYMMDD>_TAKES_<line>` for lines said more than once. Without: MiniLM embeds folders, file name, sidecar .txt/.srt/.json, kind; average-linkage cosine grouping -> topics named by the most central clip. `--notes notes.txt` (one per line) -> `<date>_SELECTS_<note>`, top `--top 5`, plus `NAME_vNNN_selects_report.md` with scores. Setup once: `scripts/ppf_setup.py` (venv `~/.ppf/venv`, onnxruntime + numpy only); `ppf_setup.py check`.
5. **Conversation.** User says what he wants; the LLM writes ops (references/ops.md) using clip_ids from objects.json (use `ppf_match.py --text "..."` to find them); `ppf.py revise NAME_vNNN.plan.json --ops ops.json --base ...` writes vNNN+1.
6. **Write.** Tier 1: references/prproj-recipe.md (exact py-premiere calls, gzip, ids). Tier 3: references/xmeml-shape.md.
7. **Verify from the saved file**, never from the script's report. Tier 3 prints PASS/FAIL per check; tier 1 reparses and counts sequences; on the Mac `ppf_prproj_peek.py FILE --names` lists what is inside without py-premiere.
8. **Status.** `ppf.py status --out DIR` lists versions, sequence and clip counts, revisions.

## Ground rules

- Never overwrite: versions are picked by number, files opened with mode "x"; py-premiere `save()` refuses overwrite. The user's base project is read, never written.
- Metadata only: ffprobe reads headers; no audio or picture is decoded. ffprobe/ffmpeg are used only when `shutil.which` finds them.
- No network at run time. The one install is `ppf_setup.py` (asks y/n): onnxruntime + numpy into `~/.ppf/venv`; ppf.py re-execs under it when present. py-premiere is vendored in `../../vendor/py-premiere` (MIT, adb9e57, unpatched; VENDORED.md). If it cannot import, tier 1 exits 2 with one line; say so, offer tier 3 with its cost (duplicate media).
- MiniLM ships inside (`models/minilm`, 23 MB, Apache-2.0, sha256 in models/MODELS.md); lookup: plugin models/ first, scan plugin's copy second, TF-IDF last. Plugin folder stays <= 25 MB (test gate).
- The LLM writes ops, the code writes the file. Do not hand-edit XML or .prproj.
- Diego's word about his own tools is [W] (engine v2 rule): py-premiere writing a .prproj that opens is proven on a client project.

## Self-check

- [ ] Output is a new `_vNNN` file; earlier versions unchanged.
- [ ] Every object in objects.json is in a bin; every stringout name is `<YYMMDD>_<NN>_<CARD>_stringout`.
- [ ] SYNC sequences exist only where two cards' timecode overlaps.
- [ ] Verify ran on the saved file and every line is PASS (tier 3) / reparse counts match (tier 1).
- [ ] What was not run here is tagged [D] in the report, with the Mac step that closes it.
- [ ] `python3 tests/run_tests.py` ends with 0 FAIL.

## Gotchas

- **From the first file Premiere opened (261112):** a reader passing is not proof; start from a Premiere-saved `--base`, not from scratch (37 settings objects at IDs 3..39 that `new_project()` does not yet fully create); store-once `BinaryHash` payloads must keep their first data copy; write Premiere's 10-byte gzip header; nested objects carry local ObjectIDs. Six pre-flight checks: README "Gotchas learned on a real project".

- Tier 3 is a fallback: Diego, 261112: "XML workflow ingesting is problematic because it creates duplicate media". Use it to preview a plan or when py-premiere is absent.
- Tier 1 [D]: written against the vendored source (signatures with file:line in references/prproj-recipe.md) but not executed in the sandbox. Audio halves are placed on the matching audio track without a link object. The vendored sample .prproj files are Git LFS stubs, so the Mac test uses `py_premiere.new()`.
- py-premiere TIMECODE_FORMATS knows only 25/29.97/30; the adapter adds 23.976/24 in memory (engine v2 gotcha). Use `is None`, never `not item`, on ProjectItems. No 24/30/60 presets at adb9e57: 24 maps to 1080p23976, 30 to 1080p2997.
- RED .R3D: ffprobe cannot read them and the scan marks them PENDING, so they get bins but no sequences until a duration exists.
- Timecode math counts frames at the nominal rate; drop-frame (`;`) drift over long takes is ignored.
- Two clips on one card with the same start TC (FX6, P2 in TEST_DRIVE_v1) are not synced: sync is across cards only.
- Slo-mo (G8): clip/file rate stays native (120, 119.88, 240); only sequence rates are conformed (`rate_of(fps, seq=True)`). The py-premiere fallback has no vertical preset: VERTICAL sequences are 1080x1920 only through prwrite.
- `_SYNC_approx_` trusts phone clocks to the second; phones drift and creation_time may mark the start or the end of a take. Check against audio.
- Mixed frame rates in INTERVIEWS/BROLL: the sequence takes the day's most common rate; clips are conformed by Premiere.
- Rosetta v7 reads one top sequence; on a whole project it exits 2 with many REFUSED lines. It is reported, not the gate.
- Organize without scan data groups by words, not pictures: on a drive named by camera it mostly regroups each card. TF-IDF fallback cannot link "drone" to "aerial" (score 0; ties fall to file order). Grouping is O(n^3) per day: fine for hundreds of clips a day, slow for thousands.
- Paths: tier 3 writes the absolute paths found at ingest; on the Mac pass `--path-map /old/root=/Volumes/DRIVE` to ppf_write_xmeml.py.
