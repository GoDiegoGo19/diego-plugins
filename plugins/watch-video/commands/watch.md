---
description: Watch a clip or a folder of clips for the edit (tags, segments, tagline, shot sheet, contact-sheet PDF)
argument-hint: <clip or folder> [--notes "what the watch is for"] [--job NAME]
allowed-tools: Bash, Read, AskUserQuestion
---
Watch the footage in the arguments with the watch-video skill (`skills/261113-watch-video-v10/SKILL.md`; read it first, it is the method). Arguments: `$ARGUMENTS`

1. If no clip or folder was given, ask Diego which one. If `--notes` was given, they are the LENS (SFX pull, music cue, notes, reference breakdown, revision); otherwise ask the one intake question the skill's STAGE 0 asks and wait.
2. Pick the mode: one video (a spot, a cut) runs the short-form stages; a folder of clips runs POOL-SHEET MODE. Over 2:00 is long form: stop and say so (LONG-FORM RUNWAY).
3. Run the engine from the skill folder (S=`${CLAUDE_PLUGIN_ROOT}/skills/261113-watch-video-v10`). Use the scan plugin's Python if it is set up, else python3:

```bash
PY="$HOME/.scan-and-index/venv/bin/python"; [ -x "$PY" ] || PY=python3
S="${CLAUDE_PLUGIN_ROOT}/skills/261113-watch-video-v10"
"$PY" "$S/scripts/watch_see.py" <clip or folder> --out <JOB>/SEE --bracket quick   # SEE first: segments, families, people, tags, watch.json
"$PY" "$S/scripts/watch_state.py" init --state <JOB>/WATCH_STATE.json --video <clip> --job "<JOB>" --seq 101
"$PY" "$S/scripts/detect_cuts.py" <clip>                                   # one video
"$PY" "$S/scripts/pool_frames.py" <folder> --state <JOB>/WATCH_STATE.json  # a folder of clips instead
"$PY" "$S/scripts/witness.py" --state <JOB>/WATCH_STATE.json     # prints which model weights it used
"$PY" "$S/scripts/macros.py"  --state <JOB>/WATCH_STATE.json [--video <clip>]
"$PY" "$S/scripts/objects.py" build --state <JOB>/WATCH_STATE.json
"$PY" "$S/scripts/render_html.py" <JOB>/WATCH_STATE.json --out <JOB>
```

Read `<JOB>/SEE/watch.json` before the staged watch: its segments (in/out, family, people) and the tagline are facts the stages start from; if it printed `tags: CLIP not installed`, say so once. If `watch_see.py` exits 4, run `watch_setup.py check`, report it, and carry on with the staged watch (the SEE pass is additive).

Then do the Claude part the skill describes: read the objects first, look at pixels only for unresolved shots and failed checks, write the macro block (logline = the tagline) and the shot descriptions INTO the state with `watch_state.py set` / `add-shot`, and render again. Never hand-edit a rendered file.

4. Report the paths: `SEE/watch.json` and `SEE/segments.csv`, `WATCH_STATE.json`, the shot sheet / contact-sheet PDF and its page JPGs, `<JOB>_objects.json` (tags = M7, segments = scenes and shots with in/out times), the write-up markdown, and the tagline itself. Report the `watch models:` line. Then ONE paragraph of what it saw: what the piece is, how it is built, what stands out, and what the facts could not settle.
