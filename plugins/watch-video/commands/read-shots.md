---
description: Read every shot like a filmmaker (one code-printed sentence per shot over a strict ledger row, the pool as a variation diff)
argument-hint: <clip, folder or watch.json> [--out DIR] [--assume-transfer rec709] [--notes notes.txt]
allowed-tools: Bash, Read
---
Run the read-shots front door of the watch-video plugin on `$ARGUMENTS`.

```bash
PY="$HOME/.scan-and-index/venv/bin/python"; [ -x "$PY" ] || PY=python3
S="${CLAUDE_PLUGIN_ROOT}/skills/261114-read-shots-like-a-filmmaker-v1"
"$PY" -I -B "$S/scripts/read_shots.py" <clip, folder or watch.json> --out <OUT> [--assume-transfer rec709]
```

Default `--out` is `<JOB>_READ` beside the job (never inside the media folder). It never overwrites: a second run writes
`ledger_v002.jsonl`, `sentences_v002.md`, `read_shots_v002.json`. Report: the last line (shots, clips, valid), the first
five lines of `sentences.md`, the `indistinct` list and the `lanes` notes from `read_shots.json`, and the paths. If levels
read `unknown`, the footage is untagged: ask Diego for the grade and rerun with `--assume-transfer`. If `read_lane` says no
model is installed, say which slots stay `unread`. Do not rank the shots against a brief: this pass reads, it does not choose.
