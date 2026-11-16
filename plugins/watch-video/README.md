# 261114 · Watch Video (go Diego go) · plugin v2.2.0 (skills 261113-watch-video-v10, 261114-read-shots-like-a-filmmaker-v1)

**Verify an install with one command:** `bash plugins/watch-video/verify.sh` prints WORKS, SEE PASS READY / READER NOT INSTALLED, or NOT READY with the list.

Hand it a clip, a cut or a folder of clips; it watches like a director and gives back the watch as files.

How this plugin fits with scan and print (shared IDs, which to reach for): see `HOW_THE_THREE_FIT.md`.

## Read shots like a filmmaker (1.2.0, second skill)

`/read-shots <clip or folder>` writes one ledger row per shot in a filmmaker's language and prints one sentence per shot from closed slots: `[size] [angle] [move, speed] on [subject: variation + noun] [verb] [manner] [direction] [place], [time of day] ([light]), [source]; [arc shape]: [head] → [mid] → [tail]`. Under every term sits the data: measured (levels, contrast, lighting ratio, colour cast, saturation, sharpness, noise, camera move from optical flow, framing geometry, Diego's deconstruction passes: posterize, invert, black-and-white, difference matte), read (a vision lane asked one slot at a time from a closed list, with the measured facts in the prompt so it may not contradict them; unresolved when no model is installed), or source (camera, codec, licence). Counts are never merit; the sentence is printed by code, never by a model; two shots with the same sentence are flagged indistinct. Status 261114: runs end to end on the test drive (17 shots from 10 clips); the vision model and the time-of-day probe are unrun here and install on the Mac; thresholds are first guesses. Read `skills/261114-read-shots-like-a-filmmaker-v1/references/how-a-filmmaker-watches.md` first.

## What it does

1. **Machine witness first.** Cuts (PySceneDetect / ffmpeg), keyframes, then boxes, faces, on-screen text, stats and a perceptual hash per frame (OpenCV MobileNet-SSD, Haar, Tesseract).
2. **Facts before pixels.** Macros M1-M8 from `rules.json` (shot size, motion, speech, role, light, on-screen text, tags, duplicates) settle what they can and mark the rest unresolved; `objects.py` builds checked shot objects.
3. **Claude looks only where the facts stop**, writes the read (logline = the tagline, synopsis, structure, intent, pacing, music direction) into `WATCH_STATE.json`.
4. **Renders from state:** tags, segments (scenes and shots with in and out), the shot sheet, a paged contact-sheet PDF (WeasyPrint; React / satori for presentation pieces) and a markdown write-up. A folder of clips gets POOL-SHEET MODE, 2 frames per clip, every cell naming its source file.

## What's inside (engines and weights)

The vision engine is the scan plugin's, carried byte-for-byte (ENGINE.json; `engine_sync.py compare` IDENTICAL).

| Engine | Job | Weights | In the plugin? |
|---|---|---|---|
| PySceneDetect AdaptiveDetector | cuts | none | code (`p3_see_segments.py`) |
| PyAV | real frames at real times | none | code |
| DINOv2-small (ONNX, int8) | fingerprints, segments inside a shot (ruptures PELT), look-alike families (`p4_sort_families.group`) | `models/dino_small` 24 MB | yes |
| YOLOX-S (ONNX) | people per hero frame | `models/yolox_s` 35 MB | yes |
| all-MiniLM-L6-v2 (ONNX, int8) | text vectors over tags + facts (search) | `models/minilm` 23 MB | yes |
| YuNet (OpenCV zoo) | face boxes (`--bracket full`, with SFace) | `models/yunet` 0.2 MB | yes |
| CLIP ViT-B/32 | tags (shot, content, light ...) | ~156 MB | no: installs on first run (`p3_see_models.py install core`) |
| MobileNet-SSD (OpenCV DNN) | witness objects | `scripts/mnssd.*` 23 MB | yes |

Pins, licences and sha256: `skills/261113-watch-video-v10/models/MODELS.md` (from `rules.json -> vision.models`). If the scan plugin has installed models (`~/.cache/scan-and-index-models`), those are used first.
`watch_setup.py check` reports python, onnxruntime, each weight's sha256, ffprobe, cv2, tesseract; `watch_setup.py install [--yes]` installs the scan's pins (Python 3.12; onnxruntime 1.23.2 on an Intel Mac) after asking.

## Install

```
/plugin marketplace add GoDiegoGo19/Diego-work
/plugin install watch-video@diego-work
```

Needs Python 3.10+, `ffmpeg`, `pip3 install 'opencv-python-headless<5' numpy jinja2 weasyprint scenedetect`, `tesseract`. The MobileNet-SSD weights (23 MB) ship inside; if the scan plugin is installed, its copy is used first (the witness prints `watch models: ...` naming which).

## Commands

| Command | Does |
|---|---|
| `/watch <clip or folder> [--notes "..."]` | The full watch: tags, segments, tagline, shot sheet, contact-sheet PDF, write-up, and one paragraph of what it saw |
| `/see <clip or folder> [--bracket quick\|standard\|full]` | The SEE pass alone: `watch_see.py` -> segments.csv, families.csv, tags.csv, hero frames, shot sheet, `watch.json` (shared IDs for the Premiere plugin) |
| `/contact-sheet <clip or folder>` | The contact-sheet PDF only |

## Status (honest)

- **1.1.0 (261113): the vision engines and weights ride inside** (~105 MB plugin). `watch_see.py` runs the scan's SEE engine standalone; seven SEE modules (`p0_env`, `p3_see_models`, `p3_see_segments`, `p3_process_frames`, `p3b_driver_red`, `p4_sort_families`, `p3_quick_premise`) and the four weight folders joined the shared ENGINE list in both skills (38 files, `compare` IDENTICAL both ways). Proven here on TEST_DRIVE_v1 (10 clips, 17 segments, 7 families, quick bracket, ~6 s; `tests/test_see.py`, 8 tests). **Not proven:** tags (CLIP not installed in this sandbox), the shot-sheet PDF from `watch_see.py` (WeasyPrint missing here; `watch.json` records the reason), faces/text (SFace not bundled), the Mac. **Known false positive:** YOLOX-S counts the mandrill face in `GH5S_A/P1000002.MOV` as one person; every other test clip says 0. `people_min_score` is a shared key, left at 0.45.

- **1.0.0 (261113): a spin-off, not a rewrite.** The skill is v9 (`library/261106-watch-video-shot-contact-sheet-v9`) copied whole and renamed `261113-watch-video-v10`; body unchanged apart from the frontmatter, a "Where this sits" section and the witness command.
- **Engine:** brought level with the scan plugin's newer engine by `engine_sync.py push` from the scan skill (adds `p0_machine.py` and `tests/test_machine.py`); `compare` says IDENTICAL both ways, 25 files.
- **Tests here:** 103 run, OK, 31 skipped for missing cv2 / tesseract / ffmpeg / jinja2 in this sandbox. The skipped ones are the pixel tests; they have to pass on the Mac before this counts as proven.
- **Not yet run:** a real watch of a real clip from the plugin location. `/watch` and `/contact-sheet` are written to the skill's stages, unrun end to end.
- **Gotcha:** "The detector swore the baboon was a person. Write down what the machine got wrong next to what it got right; a clean test that hides a false positive is a lie with a green tick." (Mandrill, 261113)

## Inside

```
.claude-plugin/plugin.json
commands/watch.md, commands/see.md, commands/contact-sheet.md
HOW_THE_THREE_FIT.md
skills/261113-watch-video-v10/
  SKILL.md               the method (stages 0-E, pool-sheet mode, density, audit, gotchas)
  ENGINE.json            fingerprints of the 38 shared engine files (watch engine + SEE engine + weights)
  rules.json             macro rules M1-M8, keyframes, witness thresholds
  scripts/               engine (annotate_frames, macros, objects, pool_frames, render_html, watch_state,
                         extract_frames, detect_cuts, p0_machine, engine_sync, mnssd.*) + plugin-only
                         watch_models.py (model lookup) and witness.py (witness with that lookup);
                         SEE engine (p0_env, p3_see_models, p3_see_segments, p3_process_frames, p3b_driver_red,
                         p4_sort_families, p3_quick_premise) + plugin-only watch_see.py and watch_setup.py
  models/                dino_small, yolox_s, minilm, yunet + MODELS.md
  templates/ fonts/ react_lane/ references/ tests/
```

Engine rule: never hand-edit an engine file. Edit in one skill, `engine_sync.py manifest`, `engine_sync.py push <other skill>`, run both test suites.
