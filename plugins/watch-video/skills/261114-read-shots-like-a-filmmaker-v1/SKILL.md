---
name: 261114-read-shots-like-a-filmmaker-v1
description: "Read every shot of a clip, a folder or a SEE watch.json the way a filmmaker logs it: one code-printed sentence per shot (size, angle, move, subject + variation, verb, manner, direction, place, time of day, light, source, and the head-mid-tail arc) over a strict ledger row of closed vocabularies, plus the pool grouped by subject + place with the variation as a diff. Use when Diego says read these shots, log the pool, compare the takes, which sunset couple, or hands over stock pulls to sort. Not for ranking against a brief yet; not for writing project files (the premiere-project-file-assembly plugin)."
---

# Read shots like a filmmaker · v1

*"I'm trying to share my knowledge as a professional filmmaker so the workers understand the dimensionality."* (Diego, 261114; `references/how-a-filmmaker-watches.md`)

**Kind:** Procedure. **Front door:** `scripts/read_shots.py`. **Not for:** ranking against a brief yet; writing project files.

## Run it

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/261114-read-shots-like-a-filmmaker-v1"
python3 -I -B "$S/scripts/read_shots.py" <clip|folder|watch.json> --out <DIR> [--assume-transfer rec709] [--notes notes.txt]
```

Writes into `<DIR>`: `ledger.jsonl` (one row per shot, validated), `sentences.md` (one line per shot, then the pool by
subject + place with the slot diff), `read_shots.json` (summary: shots, arc shapes, indistinct pairs, lanes called or
skipped, what stays unread), `measures.jsonl`. **Never overwrites:** the next run writes `ledger_v002.jsonl`, `sentences_v002.md`, ...

## What happens, in order

1. **Cuts.** A folder or clip goes through the watch skill's `detect_cuts.py` (imported, never edited); a `watch.json`
   hands over its segments. Proxies (`Sub/`, `PROXY/`) and audio-only files are skipped with a reason.
2. **Arc + measure per shot** (`shot_arc.py`): frames at 10 / 50 / 90 % of the window (five for holds over 8 s), plus one
   where a measured change spikes (subject-box size, flow, sharpness, difference from the head frame). Each frame runs
   `shot_measure.measure`. Out: `arc_frames`, `arc_head/mid/tail`, `arc_shape`, `arc_energy`, `direction`, `manner`, `sun_warmth`.
3. **The other lanes, when present:** `shot_motion.py` (camera_move, speed, manner, direction), `shot_light.py` (time of day,
   light quality), `shot_decon.py` (own CLI; no ledger slot yet), `read_lane.py` (the vision lane; R slots stay `unread`
   without its model). A missing lane is skipped with a note; a lane that adds `read_shot(video, t_in, t_out, measures)`
   is called through it.
4. **Ledger** (`shot_ledger.py build` + `validate`): every field `{value, origin M/R/S/P, trust}` from a closed list,
   `references/vocab.json` (nouns, verbs; a person extends it), or bounded text (manner, direction, place <= 4 words).
5. **Sentence** (`shot_sentence.py`): code prints the template; a model never writes it.

```
[size] [angle] [move, speed] on [subject: variation + noun] [verb] [manner] [direction] [place], [time of day] ([light]),
[source]; [arc shape]: [head] → [mid] → [tail]
```

Empty slots drop out cleanly. `row_text` = sentence + levels, lighting ratio, saturation, clarity, PV (four evidence slots),
believability, novelty in pool, licence. No counts, no tags (validate fails a digit beside an object noun).

## Rules

- **One dimension per question.** The vision lane fills a slot from its list with evidence; the code prints the sentence.
- **The poet must show the numbers.** A dedicated measured lane wins over a guess; a read slot is filled by a measure only while unread.
- **Casting is S or P only, never R.** `var_casting` comes from the stock listing or from Diego (`casting=` in notes.txt).
- **Source is a fact to weigh, never a rank.** `source_quality_class`: cinema-A / cinema-B / prosumer / drone / DSLR / phone, from the card path and ffprobe.
- **Variation is a diff.** Two shots with the same sentence are flagged `indistinct: true`: ask the vision lane for state, manner or place.
- **Unknown is an answer.** Untagged footage writes `unknown` levels unless `--assume-transfer` states the grade.

## Closed lists the grammar adds (full list in `scripts/shot_ledger.py` SCHEMA)

| slot | values |
|---|---|
| camera_angle (R; M for dutch) | eye level / low / high / overhead / dutch / POV / OTS |
| subject, verb (R) | one word from `references/vocab.json` |
| subject_mods, var_state (R) | <= 3 words, <= 2 words |
| var_form, var_age (R) | solo / couple / family / group / crowd · child / teen / young adult / middle-aged / older |
| manner, direction (M) | <= 4 words · left to right / right to left / toward camera / away from camera / across / static |
| place (R) | <= 4 words |
| sun_warmth (M) | sunset-red / warm / neutral / cool (R/B of the top 5 % luma) |
| source_camera, source_codec, source_quality_class (S) | as listed · as listed · the six classes above |
| arc_shape (M) | held / push / pull/reveal / pass-through / rack / soft / whip / follow / orbit / enters/leaves |
| arc_energy (M) | dynamic (two or more size steps, or a reveal) / modest / held |

## What stays `unread` until the vision lane runs with a model

camera_angle (except dutch), subject, subject_mods, var_form, var_age, var_state, verb, place, and the R fields of the row
(lighting style, colour design, depth, mood, tone, production value and its four slots, believability, clichés).

## Files

- `scripts/read_shots.py` front door · `scripts/shot_arc.py` arc sampler · `scripts/shot_sentence.py` the sentence + pool diff
- `scripts/shot_ledger.py` the row contract · `scripts/shot_measure.py` the measures
- `references/how-a-filmmaker-watches.md` (read first) · `ledger-row.md` · `measures.md` · `vocab.json`
- `references/what-makes-a-good-shot.md` (mini white paper: sliders, templates, the bird test, the fabricator) · `references/ascii-map.md` · `templates/*.json` (one editable file per editor's call; best = most dynamic)
- **the second machine**: `scripts/reflect.py` (`/reflect`) ranks a watched pool against an ask. It reads `ledger.jsonl` as text, builds one totem pole per shot, weighs the sliders by a template, and writes the ranking with a reason in the row's own fields. It never re-watches footage and never ranks by a count.
- Grammar source: `wp/261114-read-shots-like-a-filmmaker-v1/inputs/editors_eye_shot_grammar.md` (Sexton), `research_shot_grammar.md` (Palmer)

## Gates

```bash
cd "$S" && for t in measure_ledger grammar_arc motion_light_decon read_lane; do python3 -I -B tests/test_$t.py || break; done
```

**Needs:** Python 3.10+, ffmpeg / ffprobe, opencv-python-headless<5, numpy. The vision model is optional and installed separately.
scikit-image is NOT required.
**Command form:** `/read-shots <clip, folder or watch.json>` (`plugins/watch-video/commands/read-shots.md`).
**Status (1.0):** read slots stay `unread` until a vision model is installed; ranking (the reflection) is not in 1.0.
