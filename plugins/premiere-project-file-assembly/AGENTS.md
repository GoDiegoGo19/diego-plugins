# For any model working in this folder (Codex, Claude, others)

This folder is a plugin that writes Adobe Premiere project files from a conversation. You do not need Claude Code or slash commands; every script runs with plain `python3`. Read `README.md` for what it does, then `skills/261112-premiere-project-file-assembly-v2/SKILL.md` for the method.

## The three calls

```bash
S=skills/261112-premiere-project-file-assembly-v2/scripts

# 1. build a project from a media folder (or a scan clips CSV) -> NAME_v001.prproj + NAME_v001.plan.json
python3 $S/ppf.py build /path/to/MEDIA --project NAME --out ~/ppf_out [--base COPY_OF_USERS.prproj]

# 2. the user says what to change -> you write ops.json (vocabulary: $S/../references/ops.md) -> v002
python3 $S/ppf.py revise ~/ppf_out/NAME_v001.plan.json --ops ops.json

# 0. once per machine: venv with onnxruntime + numpy for the bundled MiniLM (asks y/n; --yes skips)
python3 $S/ppf_setup.py && python3 $S/ppf_setup.py check

# build also organizes by content: BROLL_<topic>, TAKES_<line>; with --notes notes.txt, SELECTS_<note>
# + NAME_vNNN_selects_report.md. Its first line says the engine: "engine: minilm (...)" or "engine: tfidf (fallback ...)".

# 3. look at what was written without Premiere
python3 $S/ppf_prproj_peek.py ~/ppf_out/NAME_v001.prproj --names
python3 -m prwrite inspect ~/ppf_out/NAME_v001.prproj      # run from lib/
```

## Rules

- **Never write to the user's own project file.** `--base` must be a copy. Every output is a new `_vNNN`; nothing is overwritten.
- **Changes happen to the project, never to a preview.** If the user's request cannot be expressed as an op on project objects, say so and stop.
- **Say what is proven.** The writer runs and verifies by reparse. As of 261113 no Premiere application has opened one of these files. Report outputs as "written, verified by reparse, unopened in Premiere" until someone has.
- **Find clips by words** with `python3 $S/ppf_match.py --objects ~/ppf_out/NAME_objects.json --text "drone over the house"`; use the returned `clip_id` values in ops.
- Requirements: Python 3.10+ (3.12 on Intel Macs), `ffprobe` recommended. No network at run time, nothing paid. The only install is `ppf_setup.py` (onnxruntime + numpy into `~/.ppf/venv`, after a y/n); `ppf.py` and `ppf_match.py` re-exec under that venv when it exists (`PPF_NO_REEXEC=1` turns it off). Report the engine line the scripts print.
- **Phone footage (2.2.0):** read `date_source`, `device`, `orientation`, `hdr`, `slomo` on each object before saying where a clip went. `_SYNC_approx_` sequences are wall-clock guesses: always say "check against audio". The report (`NAME_vNNN_selects_report.md`) lists HDR clips, approx sync groups, vertical stringouts, Live Photos and ignored sidecars.
- **Size gate:** the plugin folder must stay <= 25 MB (`du -sh`); it sits at ~24.8 MB with the model. Run tests with `PYTHONDONTWRITEBYTECODE=1` so `__pycache__` does not push it over.

## Layout

`skills/…-v2/` the skill and scripts · `models/minilm/` the bundled MiniLM (MODELS.md) · `lib/prwrite/` our project-file library (tests: `python3 -I -B -m unittest discover -s lib/prwrite/tests`) · `vendor/py-premiere/` the MIT reference, fallback only · `commands/` Claude Code slash commands (optional).
