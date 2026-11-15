# 261113 · Premiere Project File Assembly · plugin v2.2.0 (skill 261112-premiere-project-file-assembly-v2)

Talk to the model, get a Premiere project file. A new numbered version every time, never an overwrite.

How this plugin fits with scan and watch (shared IDs, which to reach for): see `HOW_THE_THREE_FIT.md`.

## What it does

1. **Reads your footage** from a scan clips CSV (the scan-and-index-hard-drives plugin) or straight from a media folder.
2. **Pre-sorts it into Diego's default project:** bins `00_CUTS`, `01_SEQUENCES`, `02_FOOTAGE` by shoot day, `03_AUDIO`, `04_GRAPHICS`, `05_STILLS`.
3. **Builds the sequences you'd build by hand:** one string-out per card per day (`YYMMDD_NN_CARD_stringout`), `INTERVIEWS` and `BROLL` per day, and `SYNC` sequences aligned by timecode where timecode overlaps.
4. **Organizes by content with a small model inside (MiniLM, 23 MB):** B-roll by topic, every take of a line, selects from your notes (`--notes notes.txt`, one note per line; a report lists each pick and its score).
5. **Handles phone footage (2.2.0):** shoot day from the clip's own creation date, not the download date; slo-mo keeps its 120/240 fps clip rate inside a 29.97 sequence; vertical clips get their own `_vertical` bin and `<day>_VERTICAL_stringout` (1080x1920); HLG/PQ clips are flagged; iPhones with no card folder group as one `PHONE` card per day (the phone model is kept); overlapping phone clips without timecode get `<day>_SYNC_approx_<n>` (wall-clock, check against audio); `.AAE/.THM/.XMP` are skipped and listed; `.HEIC` and Live Photo clips go to `05_STILLS`.
6. **Writes `NAME_v001.prproj`.** Say what to change in plain words; the model writes ops; the plugin writes `v002`. Fallback: FCP7 XML for import (duplicates media on import, so it is the last resort).

## Install

```
/plugin marketplace add GoDiegoGo19/Diego-work
/plugin install premiere-project-file-assembly@diego-work
```

Needs Python 3.10 or newer (3.12 on Intel Macs). `ffprobe` is optional but recommended for media facts. Nothing is downloaded at run time; the project-file library (2 MB) and the MiniLM model (23 MB, `models/`, see MODELS.md) ship inside.

One-time setup for the model (asks before installing; `--yes` skips the question):

```
python3 skills/261112-premiere-project-file-assembly-v2/scripts/ppf_setup.py          # ~/.ppf/venv + onnxruntime==1.23.2, numpy==2.5.4
python3 skills/261112-premiere-project-file-assembly-v2/scripts/ppf_setup.py check    # python, onnxruntime, model sha256, ffprobe
```

Without it everything still runs; organize and match fall back to TF-IDF and say so on their first line.

## Commands

| Command | Does |
|---|---|
| `/ppf-build <media or CSV> --project NAME` | Build `NAME_v001.prproj` |
| `/ppf-revise NAME_v001.plan.json` | Turn your notes into ops, write `v002` |
| `/ppf-status` | List versions and verify state |

Always pass a **copy** of your project as `--base`. The plugin reads the base and never writes to it.

## Status (honest)

- **2.2.0 (261113): iPhone pack, tested on ffmpeg fixtures and synthetic objects, not on a real iPhone drive yet.** Shoot day from `creation_time` / `com.apple.quicktime.creationdate` (`date_source` on each clip), slo-mo native clip rate (G8 closed), vertical by display size after rotation (G4 closed), HDR flag, `PHONE` card per day with `device`, approximate wall-clock sync, sidecars and Live Photos. 54 plugin tests, 60 library tests.
- **2.1.3 (261113): iPhone VFR conformed; flagged, not transcoded.** Phone clips report measured rates like 28.945 or 26.48; `ppf_common.conform_fps()` snaps each to a standard rate (23.976 24 25 29.97 30 50 59.94 60), to the shoot day's dominant rate when within 15 % of it, before any sequence rate is chosen and again in both writers. Clips more than 0.5 % off are flagged `vfr` with `fps_measured`, listed in `plan["vfr_clips"]` and the report. Tested on the 14 measured Fox Field rates (102 synthetic clips, 32 sequences, all standard, written by prwrite); the real Fox Field build has not run on this branch yet.
- **Runs and verifies here:** 60 library tests, 54 plugin tests. A test drive of 22 clips builds to 21 bins, 9 sequences, 20 placed clips; v002 on the second run.
- **Organize (261113), runs here with MiniLM:** from the scan CSVs, 5 `BROLL_<topic>` sequences on day 260901 (aerial wide, interior room wide, wide, hands and detail, screen or text); without scan data the two DJI aerials group together; the note "drone over the house" puts a DJI aerial first (score 0.38). TAKES tested on a synthesized repeated line only: the sample scan has no speech. Plugin folder 25 MB (`du -sk` 25488 KB at 2.2.0, gate 25600).
- **Pre-flight (261113):** `prproj_extract.py check` reproduces the Mac findings on the real fixtures in `fixtures/prproj/` (the damaged file fails on exactly 21 orphaned payloads and the Python gzip header; the file that opened passes). Our own fresh build passes the same checks.
- **Not yet proven:** a Premiere application opening a `.prproj` this plugin wrote. Until that happens, treat every output as "written and verified by reparse, unopened in Premiere". First person to open one: please report whether it opened, and the offline clip count.
- **Known limits (tagged for the next round):** organize without scan data reads words, not pictures, so camera-named folders mostly regroup by card; the TF-IDF fallback cannot match "drone" to "aerial"; audio not linked to video in `.prproj`; no multicam for offset timecode (plain multi-track sync sequences instead); RED `.R3D` clips get bins but no string-out; drop-frame drift ignored in sync math.

## Gotchas learned on a real project (261112, PROJECT_A, Premiere 26.0.1)

These come from the first pruned `.prproj` this crew got Premiere to open. Read them before trusting any file this plugin writes.

1. **A reader passing is not proof.** py-premiere and `prwrite inspect` both read a file Premiere called DAMAGED without complaint. Only Premiere opening the file counts. Report every output as "written, checked, unopened" until a person has opened it.
2. **Every build starts from a Premiere-saved blank project.** Since 2.1.2 `new_project()` begins from `lib/prwrite/data/blank_project.prproj`, Diego's blank project from Premiere 26.0.1 with its 37 settings objects at fixed ObjectIDs 3..39. A fresh build now passes the same five pre-flight checks that the file Premiere opened passes (0 dangling refs, settings complete, Premiere gzip header). `--base` with a copy of your own project is still the way to add to an existing project.
3. **Binary payloads are stored once.** An element with `Encoding="base64" BinaryHash="H"` carries data the first time and is written empty every later time. If the first copy is dropped, every later empty element is an orphan and Premiere reports the file damaged. Any prune or extract must copy the data into the first kept element.
4. **The gzip header matters.** Premiere writes a 10-byte header: no filename, mtime 0, OS byte 0x13 or 0x0a. Python's default gzip header (with a filename field) was present in the damaged file. Our framing writes the Premiere header; do not re-gzip our output with another tool.
5. **Nested objects carry local ObjectIDs.** Inside the Project object, child elements use IDs 1..64+ that collide with top-level IDs. A reference is local only inside the subtree that defines it; everything else is top-level. Count a reference only when the attribute is on the element itself, never inside escaped text (export presets contain fake ones).
6. **A kept sequence needs its panel entry.** Sequence ← SequenceSource ← Clip ← MasterClip ← ClipProjectItem, plus every ancestor bin. Bins left empty are dropped; bin Item indexes renumber from 0.

7. **Measure the size budget after the tests, not before.** A test run alone can push the folder over the 25 MB cap through `__pycache__`. Run tests with `-B`, sweep caches, then measure. (From the guest book: the model-bundle builder, 261113 05:52.)

8. **A rate that is almost right is the dangerous kind.** "A rate that is almost right is the dangerous kind: 28.945 never snapped because it was too far from everything, and 30.000 now joins 29.97 because the rule is literal." (Joiner, 261113)

The full handoff with the method and the six pre-flight checks is `reports/261112-2340_mac_handoff_extract-one-sequence-prproj.md` in the repo.

## Inside

- `models/minilm/` the bundled MiniLM (MODELS.md: licence, revision, sha256).
- `skills/261112-premiere-project-file-assembly-v2/` the skill: `ppf.py` front door, ingest, plan, organize, setup, writers, verify, match; references for ops, bins, the xmeml shape and the `.prproj` recipe.
- `lib/prwrite/` our own project-file library (framing, document, model, parse, build, export, cli; `python3 -m prwrite inspect FILE`).
- `vendor/py-premiere/` the MIT reference library we studied, kept as fallback, with five section papers.
- the earlier interview-to-assembly flow is no longer shipped inside the plugin: it moved to `library/261112-premiere-project-file-assembly-v1/` (261114, plugin 2.3.0). Two skills with near-identical names each claiming to be THE builder cost ~10.5k tokens on invoke and left the picker a coin toss.

## Rules this plugin lives by

Changes happen to the project file, never to a preview. Every write is a new version. The verifier checks structure and stops; what looks right is the editor's call.
