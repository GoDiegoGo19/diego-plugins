---
description: The contact-sheet PDF only, for a clip or a folder of clips
argument-hint: <clip or folder> [--job NAME] [--per-page 6] [--cols 3]
allowed-tools: Bash, Read
---
Make the contact-sheet PDF for the footage in the arguments with the watch-video skill, nothing else. Arguments: `$ARGUMENTS`

1. If no clip or folder was given, ask Diego which one. No write-up, no lens questions: this is the sheet.
2. Run from the skill folder (one video: `detect_cuts.py` then frames; a folder: `pool_frames.py`, 2 frames per clip):

```bash
PY="$HOME/.scan-and-index/venv/bin/python"; [ -x "$PY" ] || PY=python3
S="${CLAUDE_PLUGIN_ROOT}/skills/261113-watch-video-v10"
"$PY" "$S/scripts/watch_state.py" init --state <JOB>/WATCH_STATE.json --video <clip> --job "<JOB>" --seq 101
"$PY" "$S/scripts/pool_frames.py" <clip or folder> --state <JOB>/WATCH_STATE.json
"$PY" "$S/scripts/witness.py" --state <JOB>/WATCH_STATE.json
"$PY" "$S/scripts/render_html.py" <JOB>/WATCH_STATE.json --out <JOB> [--per-page 6] [--cols 3]
```

Every cell carries its source clip filename (the skill's filename-per-cell law). If the existing `WATCH_STATE.json` is already there, skip straight to `render_html.py`.

3. Report the PDF path and its page count. If WeasyPrint or Jinja2 is missing, say so and give the one install line the skill names; do not fall back to loose JPGs (JPGs are not a sheet).
