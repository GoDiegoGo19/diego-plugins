---
description: Build a Premiere project file (new version) from a media folder or a scan clips CSV
argument-hint: <media folder | P6 clips.csv> --project NAME [--out DIR] [--base COPY.prproj] [--notes notes.txt] [--top 5] [--engine auto|minilm|tfidf] [--no-organize] [--to prproj|xmeml] [--media-root /Volumes/DRIVE]
allowed-tools: Bash, Read, AskUserQuestion
---
Build a Premiere project with the premiere-project-file-assembly skill (v2). Arguments: `$ARGUMENTS`

1. If no media folder or CSV was given, list `/Volumes/*` and ask which drive or which scan clips CSV to use.
2. If `--project` was not given, ask for the project name. If `--out` was not given, use `~/ppf_out`.
3. Never point `--base` at the user's live project. Ask them to duplicate it first and pass the copy; the plugin reads the base and writes a new numbered file.
4. If the user has notes (client notes, a shot list, lines they want), save them one per line and pass `--notes FILE`; the plugin builds one SELECTS sequence per note with the small model and writes a report beside the project. First time on a machine, run `ppf_setup.py check` from the same scripts folder and, if it says the runtime is missing, offer `ppf_setup.py install` (it asks before installing).
5. Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/261112-premiere-project-file-assembly-v2/scripts/ppf.py" build <media_or_csv> --project NAME --out DIR [--base COPY.prproj] [--notes FILE] [--top N] [--to prproj|xmeml] [--media-root R]
```

6. Report the file it wrote (`NAME_vNNN.prproj` or `.xml`), the plan beside it (`NAME_vNNN.plan.json`), the verify lines, and the bins and sequences count from:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/261112-premiere-project-file-assembly-v2/scripts/ppf_prproj_peek.py" DIR/NAME_vNNN.prproj --names
```

7. Say plainly: open the file in Premiere and count offline clips. Until Premiere has opened it, report it as "written and verified by reparse, unopened in Premiere".
