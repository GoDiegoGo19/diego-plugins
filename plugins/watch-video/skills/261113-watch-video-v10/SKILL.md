---
name: 261113-watch-video-v10
description: "Watch a video or a folder of clips for the edit: tags, segments, tagline, shot sheet, contact-sheet PDF, and a markdown write-up, all rendered from one WATCH_STATE.json. Use whenever Diego hands over a spot, cutdown, reel, reference, pull or pool, or says watch this, contact sheet, shot sheet, break down the cut, storyboard it, map the shots (SFX pulls, music cues, notes, reference breakdowns, revisions). Runs on any clip or folder with no drive scan. Machine witness + rules layer (macros M1-M8, checked shot objects); Claude looks at pixels only for what the facts cannot settle. Not for drive inventory (what is on this drive: the scan-and-index-hard-drives plugin); not for writing Premiere project files (the premiere-project-file-assembly plugin). Read ADDITIONAL CONTEXT first."
---

# Watch Video → Contact Sheet + Write-Up · v10 (watch-video plugin 1.0.0)

**v10 (261113) is the spin-off release:** the v9 skill, unchanged in body and engine, now its own plugin (`plugins/watch-video`). New outside the engine: `scripts/watch_models.py` + `scripts/witness.py` (the witness looks for the scan plugin's MobileNet-SSD weights first, then its own copies, and prints which it used). Run the witness through `scripts/witness.py`; it takes the same flags as `annotate_frames.py`.

## Where this sits

Three plugins, one set of IDs. **Scan** (`scan-and-index-hard-drives`) inventories a whole drive. **Watch** (this one) looks hard at one piece of footage: a clip, a cut, a folder of clips. **Print** (`premiere-project-file-assembly`) writes the Premiere project file. All three speak the same IDs: drive, card, clip, segment, time. See `HOW_THE_THREE_FIT.md` at the plugin root.

## ADDITIONAL CONTEXT (full descriptor — nothing trimmed)

Watch like a director first, technician second. The watch is ONE capability; the deliverables are lenses on it — SFX pulls, music cues, Vimeo note tracking, reference breakdowns for agencies, revision passes. Every watch produces a canonical **WATCH_STATE.json** cache (nothing lives in anyone's head, nothing gets redone by hand, any session resumes from the file) and ships **two deliverables**: (1) a paged contact-sheet set — page one is a full-bleed, text-heavy **title slate** (bold-labeled LOGLINE / SYNOPSIS / INTENT / STRUCTURE / PACING / VISUALS / CREDITS + EXISTING TRACK), followed by storyboard pages of dense captioned cells with the sound world per shot; and (2) a **standalone markdown write-up** — its own independently valid piece, everything in the video expanded. Per shot the sound world is explicit: in-frame / off-frame / beat actions, ×3 options each — **what's OUTSIDE the frame is as important as what's in it.** Fill the sheet with info: it is the handoff artifact downstream LLMs ingest, and LLMs respond best to visual info.

**v9 is the shared-engine release (261107).** **Shared engine (Diego, 261107).** The watch engine inside this skill is byte-for-byte the same as in its sibling skill (`261105-scan-and-index-hard-drives-v13` ⇄ `261106-watch-video-shot-contact-sheet-v9`): witness, macros, objects, pool frames, renderer, sheet template, shot contract, fonts, model and the engine's tests. `ENGINE.json` fingerprints every engine file; `python3 scripts/engine_sync.py check` proves this copy is intact on its own. To change the engine: edit it in one skill, run `engine_sync.py manifest`, then `engine_sync.py push <the other skill>`, then both skills' gates; `tests/test_engine.py` fails if the copies drift. Each skill still installs and runs on its own. From the scan skill's real Cowork runs the engine also brings: an OCR noise filter (sofas and walls no longer read as LOWER THIRD), long clips' audio SAMPLED instead of decoded whole, audio opt-in (`macros.py --no-audio`, `--audio-only F1|F2`), stale sheet pages cleared on re-render, frame paths resolved against the state file's folder, and a `facts` line under the chips. Two Gemini-review fixes: M4 uses the M2/M3 a shot keeps; `source_of` reads `src`. Gates: `cd tests && python3 -m unittest test_witness_v8 test_macros test_objects test_engine`. Supersedes `261106-watch-video-shot-contact-sheet-v8`.

**v8 is the rules-layer release (261106).** Built from the scan-and-index work, kept independent: this skill runs on any video or folder of clips with no drive scan. Diego's direction: *facts first, pixels only for what facts can't settle; declarative, never hand-derived.* Looking at a frame costs ~1,500 tokens; a row of facts costs ~60. So the witness now captures everything it sees (boxes, faces, OCR words with position, colour stats, a hash), macros turn that into facts by rules kept in one file (`rules.json`), and `objects.py` builds checked shot objects that Claude, other models and other skills can call. Claude's look goes where an object says `unresolved` or a check failed. Pool-sheet keyframes are a formula in `rules.json` (`pool_frames.py`), not a hand pick. Contract: `references/rules-and-objects.md`.

**v4 is the staging-room release.** Prior versions hand-placed pixels with PIL and burned fix-passes on collisions and text wrapping. v4 routes rendering through a real layout engine — the engine does wrapping, flow, and pagination — the same move as the headless HTML→Chromium→Playwright pipeline, but browserless. Detection also upgrades to PySceneDetect AdaptiveDetector (two-pass rolling average, mitigates handheld false positives), with an ffmpeg fallback and the same JSON contract.

**v6.1 revision record (2026-08-06):** revised per `/260730-revise-a-skill-package-v1` after the AMEYALLI ACT V smiles-pool run. Added POOL-SHEET MODE (folder-of-clips as first-class case, 2 frames/clip, register-flag duty), fixed the stranded-header pagination (page_h 1100→1500, witnessed as 8-pages-for-6), and recorded the JPGs-are-not-a-sheet rule. Gates run: py_compile on all scripts, live render smoke against the run's real WATCH_STATE (6 pages, no strays), citation checks, in-archive verify.

**v6 is the second-witness release.** Root cause from the a client spot run: the pipeline's failure class was never structural (cuts were right) — it was **semantic** (content described wrong: a robot-baby ECE beat read as a senior patient-actor, and the error propagated downstream because the watch is a high-upstream source). v6 answers with the MACHINE WITNESS: a blind machine-annotation pre-pass whose captions must agree with the watch before any shot verifies. Same architecture as cut detection — machines propose, eyes dispose — pointed at content instead of boundaries. This also makes the skill portable to small local LLMs: perception lives in the pre-scripts, not the model, so a baby LLM inherits machine eyes and only does language work.

## THE SCHEMA — GOAL FIRST, THEN CASE-USE

1. **GOAL** — what form is the piece:
   1. **SHORT FORM (≤2:00)** → run the full pipeline: Stages 0–E, two deliverables, state cache.
   2. **LONG FORM (film/TV)** → do not run short-form; go to **LONG-FORM RUNWAY** at the bottom.

2. **CASE-USE** — what the watch is FOR, which picks the renderer (decided at intake):
   1. **INGEST / INTERMEDIARY** — feeds another pipeline (SFX pull, notes tracker, reference breakdown; downstream reads and moves on) → **Python / WeasyPrint**. The default, ~99% of runs. Portable, sandbox-native, fast.
   2. **PRESENTATION / STANDALONE ARTIFACT** — begins something that gets modified and tweaked (pitch-deck pull, app build, a living document or product surface) → **React / satori**. The rare path; doubles as the first brick of the SaaS production-tools stack.

Default is WeasyPrint unless the case-use is presentation. Everything above the renderer (state, stages, density, deliverables) is identical across lanes — only the last mile swaps.

## FRAME — rules of engagement

- Diego speaks via voice-to-text. Parse for intent; correct obvious STT garble before locking any term.
- One layer at a time. Smallest scope that serves the stage. No expansion, no reframing, no volunteered improvements.
- Hard gate: if target, scope, or output is unclear, ask ONE specific question and wait.
- Usage is cash. Small proofs before full runs. Format proof (one page) before a full set on any new job.
- On correction: name what was wrong, fix it, move on.

## STAGE 0 · INTAKE

1. Probe the video (`ffprobe`) and **confirm the duration back to Diego**.
2. Declare the GOAL fork (short/long) and the CASE-USE fork (ingest/presentation → renderer).
3. Propose a density from the band; **Diego sets images-per-sheet** and pages before anything renders.
4. `watch_state.py init` — cache exists before the first frame.
5. File > ~30MB upload cap? Proxy: 720p H.264, CRF ~28 — reads identically for composition.

## STAGE A · MACRO WATCH — what IS this thing

Logline · narrative structure · significant content (genre, cinematography, striking visuals, people/product/location/outdoors) · intent + end use (brand, client, agency, director, platform, goal type) · pacing · synopsis. Log the **existing/present track**. Credits unknown → ask once or mark unknown, never invent. → state `macro`.

## STAGE B · STRUCTURAL READ

Shots · act structure with TCs · cadence (`detect_cuts.py`, AdaptiveDetector — scores propose, eyes dispose) · standout visuals · editing style + **proxemics** (what's in frame relative to what, how close the camera lives, how it shifts).

## STAGE C · INFERENCE TREE + CHEAT SHEET

**GOAL (phylum) → GENRE (class) → USE/PLATFORM (order) → SCENE (family) → SHOT (species) → SOUND.** Each level narrows the next; the tree predicts, the frames decide.

| IF | THEN LIKELY |
|---|---|
| genre = sports | faster cuts, impact SFX, crowd beds, low angles |
| goal = aspirational | faces forward, rising structure, warm build, big type |
| goal = awareness / PSA | direct address, sparser sound, held shots, restraint |
| genre = luxury / auto beauty | slower cuts, longer lenses, shallow DOF, tone-forward |
| use = social cutdown | hook ≤2s, type-driven, sound-off legible |
| style = docu / testimonial | handheld, real walla, ambient beds |
| genre = food / product tabletop | macro proximity, foley-forward |
| genre = comedy | held beats, reaction cuts, silence as a tool |
| structure = program montage | one graphics family reused, stat hits, riser spine |
| camera = handheld throughout | rooms alive, holds breathe, nothing dead-static |

## STAGE C.5 · MACHINE WITNESS — structural pre-pass (v6)

Runs after frames are pulled, **before** the shot-territory watch. Blind: the witness never sees the watch's notes. All lanes verified to run in the claude.ai sandbox (weights bundled/repo-committed, apt, pypi — no HF hub, no GitHub release binaries, no keys required).

- **Structural witness (default, zero-key) — counts, on-screen text, gross objects. It does NOT resolve roles, actions, or identify simulated people.** MobileNet-SSD person/object detection (23MB, bundled) · haar face count (in the wheel) · tesseract OCR (apt) · brightness/busyness stats. Emits a template caption per frame. Its real jobs: person-count floor, catching on-screen graphics the watch missed or misplaced (it reads stat cards and tags straight off the frame), twin-shot sanity.
- **THE DISAGREE RULE (count-based):** `--diff` parses the people-count implied by the watch's description and flags when the machine sees **more** people than described (+1 tolerance — SSD counts are a floor, not truth). It also flags OCR-vs-description graphic conflicts. It does NOT compare words to words — a template caption and natural prose share no vocabulary, so lexical overlap carries no signal.
- **THE SEMANTIC GATE lives in Stage D, not here.** Role and simulant errors (manikin, infant simulator, patient-actor — the a client spot ECE class) are meaning errors; a counter cannot see them, because both the right and wrong reading of that shot contain the same two adults. The gate that closes on them is the mandatory blind re-look (Stage D). Shots that never received it ship `semantic_check: "not_run"` — never silently passed.
- **Haiku lane (dormant until keyed):** if `ANTHROPIC_API_KEY` exists, frames are additionally captioned by claude-haiku via api.anthropic.com and the witness upgrades to a true independent semantic witness; `--ask` unlocks pointed interrogation of flagged shots. **Open-egress note:** on unrestricted machines (local rig, baby-LLM setup), Florence-2 base is the free neural witness. Neither is ever claimed present when absent.
- Output per shot: `machine:{caption, regions[], ocr[], tags[], counts, stats}` — same JSON shape from every lane.

**Small-LLM portability (the lingua franca contract):** the smaller the LLM running this skill, the more perception must live in the pre-scripts. Claude-class: full watch, witness as verification. Local baby LLM: witness annotations become primary perception; the LLM does language work only. The state contract doesn't care what wrote it.

Script: `annotate_frames.py` (structural witness + dormant Haiku lane; `--diff` = count rule, `--ask` needs a key). Deps: `pip3 install 'opencv-python-headless<5' numpy` · `apt install tesseract-ocr` (Mac: `brew install tesseract`). **OpenCV 5 removed the Caffe and Haar detectors the witness uses; pin `<5` (v8 finding, 261106).** v8 adds per frame: `detections` and `faces` boxes, word-level `ocr_boxes` with position, `warmth`/`saturation`, a 64-bit `dhash`, `size` — one tesseract call per frame. Tested on the four a client spot reference frames: the witness read NURSE/TEACHER tags off the walkway frame and correct counts on clean shots; it read 2 people on the ECE frame — which matches the wrong description too, proving the role error is invisible to counting; it missed the turnout-gear firefighter (0 persons) and invented a "bottle" near the simulator. Counts are a floor. That test is the honest spec of this tier.

## STAGE C.6 · RULES LAYER — facts before pixels (v8)

Runs right after the witness, still before shot territory. Three commands, all local, free, repeatable:

```
python3 scripts/witness.py --state WATCH_STATE.json          # witness: boxes, faces, OCR boxes, stats, dhash
python3 scripts/macros.py --state WATCH_STATE.json [--video V.mp4]   # M1–M8 → shot.macros, each resolved / unresolved / not_run
python3 scripts/objects.py build --state WATCH_STATE.json            # checks W1–W5 → <job>_objects.json
```

**No gaps — the PENDING rule (Diego, 261106).** Every shot always has a complete record. When a step gets no data (missing frame, unreadable file, witness never ran), it writes a placeholder instead of skipping: `{"value": "PENDING", "status": "needs_scan", "why": "...", "scan_by": "any"}`. One marker only; never a blank, a null or a magic number. Three states, never confused: **needs_scan** = no data yet, a scanner must look · **unresolved** = data present, the rule can't settle it, judgment must look · **not_run** = an optional engine isn't installed. Anyone can fill a placeholder: a Claude instance, another of Diego's tools, or a rerun — `objects.py fill --state S.json --shot ID --macro M1 --value MCU --by claude` writes the value with `filled_by`, and the objects rebuild.

Then **read the objects, not the frames**: `objects.py summary`, then `objects.py pending` (needs a scan), then `objects.py unresolved` (needs judgment). Every resolved fact (shot size, static/moving, day/night, slate / lower third / card, SOT / b-roll, duplicates) goes into the cell as given. Claude looks at full width **only** at the shots `unresolved` lists and at failed checks; that look writes the description and sound world as before. The macros never replace the watch's judgment of subject, action, role or sound: they settle the countable, so the eyes go where the judgment is.

| Macro | Settles | Engine |
|---|---|---|
| M1 shot size | WS / MS / MCU / CU (no person → unresolved) | witness boxes |
| M2 motion | static / moving | two frames of the clip |
| M3 speech | speech % | webrtcvad (`pip3 install webrtcvad-wheels`, ~100 KB) |
| M4 role | SOT / b-roll per clip | M2 + M3 + faces + duration |
| M5 light | day / night | brightness + warmth |
| M6 on-screen text | slate / lower third / card | OCR word boxes |
| M7 tags | open tags with scores | CLIP if installed; otherwise `not_run` |
| M8 duplicates | dup / near-dup of another clip | 64-bit dHash |

**A state made by v7** already has a v7 `machine` on each shot: run the witness once with `--force` so the v8 boxes exist, or M1, M6 and M8 stay unresolved. A shot the witness or macros never reached is `unresolved` (check W6), never verified.

Thresholds start as guesses and get tuned on Diego's real footage — **in `rules.json` only**. Change a rule there and rerun with `--force`; never edit a fact by hand. Checks W1–W5 (frame on disk, TC order, witness flag, filename per pool cell, semantic re-look on simulant-risk shots) set each object's status: `verified`, `flagged` or `unresolved`. Status at a glance: `objects.py check`. Find: `objects.py find --size MCU --motion static --text lower_third`.

## STAGE D · SHOT TERRITORY — sound world explicit

Per shot: brief description (subject + action + graphic landing there), then the sound world — **×3 options each**:
- **IN** — sounds visibly happening.
- **OFF** — the world beyond the edge (the law: outside the frame builds the scene — the surfer gets gulls, beach ambient, wave beds, and the boat you never see).
- **BEAT** — key actions and their sounds (hands from the huddle, bag hit foreground, shoes skidding behind).
Speculative flavor marked optional; off-frame environment-plausible, never invented plot. SND lines seed the SFX schematic downstream (IN+BEAT → framing/support/roofing; OFF → foundation/flourishes).

Mechanics: 0.5s grid extraction · 0.25s refine windows at ambiguous cuts · full-width views for content ID · describe what's on screen, never assert what wardrobe can't prove · TC on the 0.5 grid, 0.25 where refined · WS/MS/MCU/CU/INSERT, travel gets the arrow · INSERTs flagged.

**THE SEMANTIC GATE — mandatory blind re-look.** The watcher is the neural witness; genre-priming at thumb size is the enemy; full width is where priming breaks. Any shot matching a simulant-risk trigger gets a second look at full width BEFORE it can verify, framed as a question, not a confirmation: "is each figure a real person — what is the pixel evidence?" Triggers: adult + small figure · care/medical/classroom context · props that simulate people (manikin, infant simulator, patient-actor) · occluding gear (turnout, SCBA, hazmat) · any `witness_flag` from C.5. Record the outcome per shot: `semantic_check: "passed" | "adjudicated" | "not_run"`. `verified: true` requires the count-based disagree rule cleared AND `semantic_check` ≠ `not_run` on trigger shots. A shot that never got its re-look ships marked, never silently passed.

## POOL-SHEET MODE (v6.1 — first-class case, worked on the AMEYALLI smiles pool 260806)

The subject is a FOLDER OF CLIPS (a pull, an act's stock bin, an audition pool), not one cut. Same pipeline, three adaptations:

- **Structure:** one SCENE per clip (id `SM01`…, slug = clip title, filmmaker carried), **2 shots per clip default** — head (~15%) and mid (~60%) — a third cell only for clips ≥12s. **v8: that is a formula in `rules.json` (`keyframes`), applied by `python3 scripts/pool_frames.py <clips or folders> --state WATCH_STATE.json`**, which probes each clip, pulls the frames (`ffmpeg -nostdin -ss <t> -frames:v 1 -vf scale=640:-2`) and writes scenes and shots with the filename on each. Change the formula in one place; every sheet follows. Then run Stage C.5 + C.6.
- **REGISTER-FLAG DUTY (the pool sheet's whole point):** every clip whose frames contradict its filename or the pool's stated register gets the flag written INTO its desc — `REGISTER FLAG` / `WORLD FLAG` — plus a slate-synopsis summary naming every flagged clip. On the source run the filenames hid: a "Thoughtful" clip with no smile, an eyes-only macro, a meditation clip, a hands INSERT, and an unmistakable Eiffel Tower. The sheet exists to catch exactly these before a pick exists.
- **Slate reads as a POOL:** intent = what the pool auditions for (act + beat); structure = clip×frame math; pacing = n/a at pool stage (rhythm belongs to the cut); existing track = MOS for stock.

Downstream contract: this sheet IS the `sheet_ref` source the artgrid-pull skill's pick-point gate demands (`<sheet>.pdf#p<N>` per clip). A pool without a sheet is a pool nobody has seen.

## DENSITY — Diego's comfort band

Cells ≈ **duration × 1.0–1.3** (:30 → 30–40; :60 → 60–78 across pages). Floor: one per shot, never merged. Slow cutters pad: holds ≥2.5s get a mid cell, ≥5s head/mid/tail — continuations keep the number with a letter (`101-8·b`, caption `hold · mid`). Ceiling ≈ ×1.35. Sub-0.5s inserts get a cell at 0.25 precision.

## WATCH_STATE — the cache (source of truth)

```
{ job, seq, lens, case_use: "ingest" | "presentation", renderer: "weasyprint" | "react",
  mode: "grid-first" | "detection-first",
  video:{path,duration,fps,size}, probe:{cuts[],cadence,band,target},
  macro:{logline,synopsis,structure,intent,pacing,visual,credits,existing_track},
  scenes:[ {id,slug,tc_in,tc_out, shots:[ {id,type,tc_in,tc_out,desc,snd_in,snd_off,snd_beat,frame,verified,
    machine:{caption,regions[],ocr[],tags[],counts,stats, detections[],faces[],ocr_boxes[],dhash,size},
    macros:{M1..M8:{value,status,why}}, witness_flag, semantic_check, file} ]} ],
  coverage:[ {range,level} ], deliverables:[ {type,file,date} ] }
```

**The rule that holds it all: everything renders FROM state; rendered files never get hand-edited.** Scenes wrap shots even for a handful — the hierarchy is the long-form runway. Scripts: `watch_state.py` (init/set/add-scene/add-shot/verify/coverage/ledger/status) · `extract_frames.py` (idempotent puller) · `detect_cuts.py` (AdaptiveDetector + ffmpeg fallback) · `annotate_frames.py` (composed structural witness + dormant Haiku lane) · `pool_frames.py` (pool keyframes by formula) · `macros.py` (M1–M8) · `objects.py` (checked objects + calls) · `rules.json` (every threshold and formula) · `render_html.py` (WeasyPrint lane) · `react_lane/render_satori.mjs` (React lane).

## STAGE E · RENDER — the two deliverables

**Deliverable 1 — the paged contact sheet.** Pick the lane by CASE-USE:

Two pages, cleanly separated — this is a firm rule:
- **TITLE PAGE (page 1)** carries ALL the read: the macro block (bold-labeled LOGLINE / SYNOPSIS / INTENT / STRUCTURE / PACING / VISUALS / CREDITS / EXISTING TRACK) AND the MUSIC DIRECTION closer (from genre + goal). Everything that isn't a shot lives here.
- **BOARD PAGES** carry ONLY storyboarded shot cells — thumb + ID line + **SOURCE CLIP FILENAME** + description + IN/OFF/BEAT. **The filename-per-cell law (v7, paid for):** every cell states the clip's actual filename (e.g. `MY_AB_Apple_0429.gif`). A pool-organization job proved bare grids force index math everywhere and made 102 media pairs unjudgeable without the drive — the filename in the cell is what lets any later pass (dedup, swap, ledger, relink) address the media by sight. This applies to EVERY sheet this skill or any quick-montage variant emits: even a scratch `montage` grid gets `-label` with the filename. No macro repeat, no music direction, no closer strip. Diego trusts that lives on the title page; do not double it up on the board.

- **WeasyPrint lane (default, ingest).** `python3 scripts/render_html.py <state.json> --out <JOB> [--per-page 6] [--cols 3] [--dpi 150]`. Pipeline: state → Jinja2 (`templates/sheet.html.j2`) → HTML/CSS → WeasyPrint PDF → pdftoppm → paged JPGs. CSS Paged Media does wrapping/flow/pagination; `break-inside:avoid` keeps cells and blocks whole. `closer` in state renders on the title slate, not the board. Slicing for long form: `--scene SC03`, `--range 15 21`. Deps: `weasyprint`, `scenedetect`, `pdftoppm` (poppler). **Fonts are bundled in `fonts/` — Ostrich Sans Medium for display headlines (the tall-neck project title / headers) and Montserrat for all body/label text (chosen for fast scanning); the renderer points at the bundle, so no system-font install needed.**
- **React lane (presentation).** `npm i satori @resvg/resvg-js` then `node react_lane/render_satori.mjs <state.json> <outDir>`. state → React nodes (`react_lane/Cell.jsx`) → satori (Yoga flexbox → SVG) → resvg → PNG. The `Cell` component is the same brick the SaaS production-tools stack uses — build it here, reuse it in the product. Scaffolded for the ~1% path; the WeasyPrint lane is the fully-smoke-tested default.

**Board density: 6 cells per page is the locked default** (3 across × 2 rows). Reasoning: Diego is the primary reader most of the time, with the occasional collaborator share, and 6 keeps the page near the ~1568px width so AI agents ingesting it still read full detail — the text captions (description + IN/OFF/BEAT) carry the semantic load and offset any thumbnail ambiguity. If a human ever needs it to read bigger, the lever is fewer-per-page (4), never larger thumbs.

Output **JPG/PNG** (cheap downstream ingest). Filenames: `<JOB>_Sheet_p-1.jpg` (title), `_p-2.jpg`…

**Deliverable 2 — the standalone write-up (markdown).** Its own independently valid piece: per-shot prose of what's happening; the audio reasoning behind every IN/OFF/BEAT; music direction from genre + goal (aspirational vs awareness close differently — say which); the existing track logged and read. Authored by Claude from state; `<JOB>_Watch_Writeup_YYMMDD.md`.

Log both to the state `deliverables` ledger. QA-view one board page + the slate before delivering.

## LONG-FORM RUNWAY (film / TV — identify only, do not build here)

Schema carries it: `scenes[]` wrap shots, `mode` flips to **detection-first** (one AdaptiveDetector pass, one rep frame per shot — no full grid), `coverage[]` tracks watched ranges by level. Pathways: sheets render as **views** (`--scene`, `--range`) not a full set · macro pass on a sparse grid first, shot-level only where a lens needs it · in a pinch: reels — ~10-min proxy chunks fed in sequence, state accumulating · true home is **Cowork** (file never uploads; ffmpeg local) · narrative/beat analysis against the story-structure skill belongs to the dedicated spin-off skill. Stop there.

## AUDIT (before done)

- Intake ran: duration confirmed, both forks declared, Diego set density, renderer picked by case-use.
- Machine witness ran blind before shot territory; count-based disagree rule cleared on every `verified: true`; every simulant-risk shot carries `semantic_check` ≠ `not_run`; open `witness_flag`s adjudicated, none silently dropped.
- Rules layer ran (v8): macros on every shot, `objects.py build` clean or its flags adjudicated; Claude's full-width looks went to `unresolved` shots and failed checks first; no fact edited by hand (changes go through `rules.json` + `--force`).
- State is source of truth; every caption traces to a viewed frame; boundaries verified/refined at 0.25s; verified flags set.
- Both deliverables shipped and ledgered; slate present; write-up stands alone.
- Every cell carries IN/OFF/BEAT; off-frame plausible; speculative marked; existing track logged.
- Density in band; continuations and inserts labeled; house style holds (Ostrich Sans Medium headlines, Montserrat body, slate stripes, paper, dark header, rose accent).

## GOTCHAS (seeded from the a client spot :30 run — promote to rules at 3+)

- **The ECE misread (the ECE trigger):** adult + small figure in a clinical/classroom context is a semantic trap — genre primes the wrong story (senior patient-actor vs robot-baby simulator). It is a ROLE error: both readings contain the same countable adults, so no counter can see it. The gate that closes on it is the Stage D blind re-look, not the machine witness. Upstream description errors poison every downstream deliverable — the watch is the highest-upstream source in the pipeline.
- SSD misses people in occluding gear (turnout, SCBA — read 0 persons on a firefighter) and invents small objects (a "bottle" near the baby simulator). Treat structural counts as a floor, not truth; the disagree rule's +1 tolerance exists for this.
- Similar shots merge at small thumb size (the two aerials). Verify twins at width.
- A stat card can span two shots (the 91% card). TC the shots, not the graphic.
- A stretch nobody has notes on is a stretch nobody has watched — verify the whole timeline.
- WeasyPrint flows content honestly: if a board page overflows, lower `--per-page` or raise page height — don't hand-pack. That honest flow is the point; the PIL collision class is gone.
- **Stranded headers (fixed v6.1):** page_h 1100 left board headers alone on blank pages whenever 2 rows of tall-captioned cells overflowed — 8 pages rendered where 6 belonged. page_h now 1500. If it recurs at denser captions: raise page_h, never trim captions to fit.
- **A folder of JPGs is not a contact sheet.** Frame grabs are pipeline INPUT. The deliverable is state + witness + viewing + the RENDERED paged sheet — shortcutting to raw grabs is the blind-casting failure wearing a lab coat (the correction that forced pool-sheet mode into this skill).
- Vision downscales past ~1568px long edge: keep board pages near that width so machines read full detail; mega-canvas is human-only.
- Paged delivery costs ~1 image budget per page (5 pages ≈ 5× a single strip) but every token carries signal; a tall strip crushes the whole doc into one budget and reads as mush. Pages when the receiver must read; strip only for a glance.
- Upload cap ~30MB: broadcast-bitrate :60s need the proxy spec.
- AdaptiveDetector beats raw ffmpeg on handheld/quad-splits; if scenedetect is unavailable the script falls back to ffmpeg automatically (logged in `engine`).
- Off-frame sound is world-building, not fiction — a distant boat, yes; a named character's speedboat, no.
- **Macros settle counts, not meaning (v8).** M1 says MCU from a face box; it cannot say whose face or why. A resolved macro is a fact about pixels, never a reading of the story. The ECE class still belongs to the Stage D re-look.
- **Keep thresholds untuned until real footage says otherwise.** The `rules.json` numbers are starting guesses (261106); tune them on Diego's drives and note the date in `_note`.
- Skill descriptions cap at 1024 characters — overflow lives in ADDITIONAL CONTEXT.
- `cd` chains with `&&` into extraction loops so the working directory persists.
