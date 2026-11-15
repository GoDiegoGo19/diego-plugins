---
description: The SEE pass alone on a clip or a folder (cuts, segments, look-alike families, people, tags when CLIP is installed, hero frames, watch.json)
argument-hint: <clip or folder> [--out DIR] [--bracket quick|standard|full]
allowed-tools: Bash, Read
---
Run the SEE lane of the watch-video skill on `$ARGUMENTS` (no Claude watch, no write-up).

```bash
PY="$HOME/.scan-and-index/venv/bin/python"; [ -x "$PY" ] || PY=python3
S="${CLAUDE_PLUGIN_ROOT}/skills/261113-watch-video-v10"
"$PY" "$S/scripts/watch_setup.py" check          # read-only; if something is MISSING, ask Diego before `watch_setup.py install`
"$PY" "$S/scripts/watch_see.py" <clip or folder> --out <OUT> --bracket quick
```

Default `--out` is `<JOB>_SEE` beside the job (never inside the media folder). Report: the tagline line, each premise line,
the counts line (segments, families, hero frames, sheet), and the paths `watch.json`, `segments.csv`, `families.csv`,
`tags.csv`, `hero/`, the sheet PDF. If it printed `tags: CLIP not installed`, say so and offer
`python3 "$S/scripts/p3_see_models.py" install core` (about 156 MB; ask first). Exit 4 means the engine cannot run here:
show the `watch_setup.py check` output.
