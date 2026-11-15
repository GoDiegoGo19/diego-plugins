# Rules layer + shot objects · contract (v8, 261106)

This file is the contract between the scripts. Every script reads and writes exactly these shapes. Change the contract here first, then the code.

**The flow (Diego, 261106):** frames → machine witness captures everything → macros (rules) derive facts and say `resolved` or `unresolved` → objects are built and checked → Claude looks at pixels **only** for unresolved objects and failed checks. Declarative, never hand-derived: thresholds and keyframe formulas live in `rules.json`, nowhere else.

**Path rule.** A relative `frame` / `path` (shot, scene or `video.path`) in a state file is resolved against the **state file's directory**, never the cwd. `macros.py` and `objects.py` both apply it. **Shot ids** are full labels (`SM01-HEAD`); no script may depend on the id format. Pool shots also carry `src` and `t`.

## 0 · The PENDING placeholder (Diego's rule: every shot always has a complete record)

Nothing is ever silently skipped or left blank. When a step cannot produce a value it writes ONE explicit marker, never `null`, never a magic number:

```
{"value": "PENDING", "status": "needs_scan", "why": "<reason>", "scan_by": "any"}
```

- `annotate_frames.py`: a shot whose frame is missing, 0-byte or unreadable gets `machine = {"value":"PENDING","status":"needs_scan","why":...,"scan_by":"any"}` (a dict, so later steps can detect it) and stays in `missing_frames`. A real `machine` is never overwritten by a placeholder. Sidecar mode (`--frames dir`) records an unreadable image the same way and lists it under `unreadable`.
- `macros.py`: every M1-M8 key is written for every shot. A shot with no usable machine data (absent or PENDING) gets the placeholder in each macro (`why: "no witness data"`). Once a real `machine` exists the placeholders are recomputed.
- `objects.py fill` (below) is how a judgment replaces a placeholder.

Three statuses, three different meanings, three different people:

| status | meaning | who acts |
|---|---|---|
| `needs_scan` | no data yet | a scanner must look (re-run annotate/macros, or `fill`) |
| `unresolved` | data present, the rule can't settle it | judgment must look at the pixels |
| `not_run` | an optional engine is not installed (`why` names it) | install the engine, or ignore |

## 1 · `rules.json` (one place for every threshold and formula)

Lives in the skill's root next to `SKILL.md`. Scripts load it with `--rules <path>` (default: the skill's own `rules.json`). Keys:

| Key | Meaning |
|---|---|
| `keyframes.points` | list of `[label, fraction]`, default `[["HEAD",0.15],["MID",0.60]]` |
| `keyframes.tail` / `keyframes.tail_min_s` | `["TAIL",0.85]` added when the clip is at least `12` s |
| `M1_shot_size` | `face_cu` 0.30, `face_mcu` 0.12, `person_ms` 0.40 (fractions of frame height) |
| `M2_motion` | `static_max_diff` (mean abs grey difference, 0–255, on 64×36 thumbs), `min_shot_s` 0.6 (a shorter cut shot is `unresolved`), `sample_offset_s` 0.25 (midpoint ± this), `edge_pad_s` 0.05 (samples stay inside `[tc_in+pad, tc_out-pad]`) |
| `M3_speech` | `speech_min_pct` 40, `frame_ms` 30, `aggressiveness` 2 |
| `M4_role` | `sot_min_s` 20, `broll_max_s` 12 |
| `M5_light` | `night_max_brightness`, `day_min_brightness` (0–255) |
| `M6_text` | `lower_third_min_y` 0.62, `card_min_chars` 12, `slate_strong_words` (`scene`, `take`, `slate`: one hit makes a slate), `slate_words` (weak: `camera, cam, director, dir, prod, date, dp, sound, fps, roll`; needs `slate_min_weak` (2) distinct hits), `lower_frac` 0.70 (share of characters in the lower band for `lower_third`), `above_y` 0.45 (a box centred above this rules a lower third out), `card_max_lower_frac` 0.5 (a card has less than this share in the lower band) |
| `M7_tags` | `words` list, `min_score` (needs CLIP; otherwise `not_run`) |
| `M8_dupes` | `dup_max_hamming` 4, `near_max_hamming` 10 (64-bit dHash), `min_bits` 2 / `max_bits` 62 (a hash with fewer/more set bits is a flat frame) |
| `flat` | `min_busyness` 15: a frame below it is flat (black, white, single colour) and M5/M8 refuse to judge it |
| `witness` | `ssd_min_score` 0.45 (MobileNet-SSD detections), `ocr_min_conf` 60 (tesseract word confidence) |
| `W5_simulant_words` | words in a description that make a shot simulant-risk (default: manikin, mannequin, simulator, patient, infant, baby, classroom, clinic, hospital, turnout, scba, hazmat) |

Every section has built-in fallback defaults in `macros.py` (the same numbers, including the M2 and M6 keys above): a rules file missing a section or key still runs.

## 2 · Machine witness output (per frame) — `shot.machine`

v7 keys are kept unchanged (`caption, regions, ocr, tags, counts, stats, witness`). v8 adds, all boxes normalised 0–1 with `x,y` = top-left:

```
detections: [{label, score, x, y, w, h}]      # MobileNet-SSD, score > 0.45
faces:      [{x, y, w, h}]                    # Haar
ocr_boxes:  [{text, conf, x, y, w, h}]        # tesseract TSV, word level, conf >= 60
stats:      {brightness, busyness, warmth, saturation}   # warmth = mean(R) - mean(B)
dhash:      "16 hex chars"                    # 64-bit difference hash of the frame
size:       [width, height]
```

## 3 · Macros — `shot.macros`

`scripts/macros.py --state WATCH_STATE.json [--rules rules.json] [--video V] [--force]` writes, for every shot:

```
macros: { "M1": {"value": "MCU", "status": "resolved", "why": "largest face 18% of height"}, ... }
```

`status` is one of `resolved`, `unresolved`, `not_run` (an engine is missing; `why` names it) or `needs_scan` (the §0 placeholder: no data yet). A macro never guesses: if the rule can't settle it, `unresolved`. Re-running skips a macro that is already `resolved`/`unresolved`/`not_run` (including one written by `objects.py fill`, which carries `filled_by`) and computes only missing keys and `needs_scan` placeholders; `--force` recomputes everything.

| # | Value set | Inputs |
|---|---|---|
| M1 shot size | WS / MS / MCU / CU / INSERT | largest face or person box height ÷ frame height |
| M2 motion | static / moving | two frames of the same shot or clip (pool cells sharing `file`; or, in a cut, two frames pulled from the video INSIDE the shot at midpoint ± 0.25 s, both clamped as in the `M2_motion` rules; a shot shorter than `min_shot_s` is `unresolved`). A pool frame pulled by the `-ss 0` fallback (`seek_fallback: true`) is not at its keyframe time, so M2 is `unresolved` when either frame used has it |
| M3 speech | speech / no_speech, with `pct` | webrtcvad over the clip or shot's audio (`file` or `--video`); only the percentage is cached per source/slice, never the decoded audio; `not_run` if webrtcvad or audio is missing |
| M4 role | sot / broll | duration, M2, M3, face count. **Pool** (shots have `file`): per clip. **Single video / cut** (no `file`): per SHOT, from that shot's own M3 slice, M2 and faces; one role is never stamped over the whole video. `sot` = duration ≥ `sot_min_s`, static, speech, 1-2 faces. `broll` = duration ≤ `broll_max_s`, no_speech, AND (moving OR zero faces). Anything else is `unresolved` |
| M5 light | day / night / mixed | brightness + warmth; `unresolved` for a flat frame (busyness < `flat.min_busyness`) |
| M6 on-screen text | slate / lower_third / card / timecode / none | `ocr_boxes` position + word lists. `timecode` = a burned-in `\d\d:\d\d:\d\d[:;.]\d\d` box (the frame separator may be `:`, `;` or `.`) (resolved, chip-worthy; excluded from the other rules). `slate` = a strong word or ≥ `slate_min_weak` distinct weak words. `lower_third` = ≥ `lower_frac` of text characters in boxes centred at or below `lower_third_min_y` AND no box centred above `above_y`. `card` = < `card_max_lower_frac` of characters in the lower band and ≥ `card_min_chars`. Any mix of these is `unresolved` |
| M7 tags | list of words with scores | CLIP if installed; else `not_run` |
| M8 duplicates | `dup_of` / `near_of` a shot id, or none | dHash Hamming distance across all shots; `unresolved` ("flat frame") when the shot's busyness < `flat.min_busyness` or its hash popcount is outside `min_bits`..`max_bits`; flat earlier shots are not compared |

## 4 · Checks (true before it is an object)

| # | Check | Fails as |
|---|---|---|
| W1 | every shot has a frame on disk | `NO FRAME` |
| W2 | shot timecodes inside a scene run in order without overlap | `TC OVERLAP` |
| W3 | witness disagree rule (v6 count rule) is clear | `WITNESS FLAG` |
| W4 | pool mode: every cell names its source file | `NO FILENAME` |
| W5 | simulant-risk shots have `semantic_check` other than `not_run` | `SEMANTIC NOT RUN` |
| W6 | every shot has `machine` (without `error`) and all of M1-M8. PENDING placeholders count as present (W6 passes) but make the shot `needs_scan` | `NOT ANALYSED` (check result `fail`, but the shot is **unresolved**, not flagged, with unresolved reason `no witness` / `no macros` / `no macros (missing M2,...)`) |

A scene with zero shots is `flagged` with `NO FRAME`. A state with zero shots is never `verified`: video status `unresolved`, `video.note` = `no shots`.

## 5 · Objects — `scripts/objects.py`

`objects.py build --state WATCH_STATE.json` writes `<state dir>/<state file stem>_objects.json` (`WATCH_STATE.json` -> `WATCH_STATE_objects.json`; the job slug plays no part, so two states in one folder never share a file):

```
{ "schema": "watch-objects/1", "job", "state_path", "state_mtime", "built_utc", "rules_version",
  "video":  {...state.video, "status"},
  "scenes": [{id, slug, tc_in, tc_out, file?, shots: [shot ids], status, failed: []}],
  "shots":  {id: {id, scene, type, tc_in, tc_out, file, frame, desc, macros, counts, ocr, status, failed: [], unresolved: [macro ids], needs_scan: [keys], needs_scan_why: {key: why}}},
  "checks": [{check, target, result: "pass"|"fail", note}] }
```

`status` precedence **flagged > needs_scan > unresolved > verified**: `flagged` (a check failed), `needs_scan` (a `machine` or macro placeholder, or a `desc_stale`: see §7), `unresolved` (checks pass, a macro is unresolved), `verified` (all checks pass, W6 included, nothing pending or unresolved). `needs_scan` keys are `machine`, `M1`..`M8`, and `desc` (the shot carries `desc_stale`, why `frame changed since description`). With `--state`, calls rebuild the objects file when it is missing, its `state_path` differs from the state file, its `state_mtime` differs from the state file's mtime, or `rules.json` is newer than it. Shot objects carry `frame_abs`, the resolved frame path. Calls (one-line text by default, `--json` for JSON):

| Call | Returns |
|---|---|
| `show <shot id or filename>` | one line of facts per shot, ~60 tokens |
| `summary [scene id]` | counts by shot size, motion, light, text, role; a `needs_scan` count; flags. An empty scope (no shots) is `unresolved`, never `verified` |
| `unresolved` | only shots needing a judgment look (unresolved or flagged; needs_scan shots are in `pending`), with the (resolved) frame path and why |
| `pending` | every shot with any `needs_scan` key (machine, M1-M8, desc): id, status, frame path, the pending keys and their reasons |
| `fill --state S.json --shot ID --macro M1 --value MCU [--by claude] [--why "..."]` | writes `shot.macros[M1] = {value, status: "resolved", why, filled_by}` into the state (the ONE place `objects.py` writes it; atomic temp-file write; allowed over any status so a judgment can override; missing macro keys are completed with placeholders; refuses an unknown shot or macro, exit 1; a `[`/`{` value is parsed as JSON for M7), then rebuilds the objects file |
| `fill --state S.json --shot ID --desc "text" [--by claude]` | sets `shot.desc`, `shot.desc_filled_by`, drops `desc_stale`, rebuilds |
| `find --size MCU --motion static --light night --text lower_third --role sot --tag drone` | matching shot ids |
| `check` | every check, pass/fail |

## 6 · On the sheet

Each board cell shows one chip line under the filename: `MCU · static · day · LOWER THIRD · dup of 101-3`. An unresolved macro shows as `?`; a `needs_scan` macro shows as `PENDING` (not for M8); `not_run` macros are omitted; `desc_stale` is never printed; M6 `timecode` shows as a chip. On the title page, Claude puts the one-line `objects.py summary` counts into STRUCTURE (no renderer change).

## 7 · Pool frames (`pool_frames.py`) rerun rules

- A shot whose frame **changed** on rerun (new keyframe time, label set or frames dir) drops `machine`/`macros`, sets `verified=False`, `semantic_check="not_run"`, clears `witness_flag`, and moves `desc` to `desc_stale` (text kept, out of the live field). `objects.py` then marks it `needs_scan` (`desc`) until `fill --desc` (or a new description) replaces it. An unchanged shot keeps everything; `--force` re-pulls and drops machine/macros but does not stale the description.
- If the `-ss <t>` pull yields nothing, a second pull at 0 s is used: `seek_fallback: true`, and `t`/`tc_in`/`tc_out` record the time actually pulled (0.0) while `t_want` keeps the asked-for time (so reruns don't re-pull forever). M2 treats such a frame as unresolved.
- A zero-byte or corrupt file is skipped with reason `unreadable or corrupt (...)`; `no video stream (audio-only)` is reserved for readable files with no video stream.
