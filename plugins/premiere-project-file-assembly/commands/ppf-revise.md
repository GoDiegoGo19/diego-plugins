---
description: Apply conversation changes (ops) to a built project plan and write the next version
argument-hint: <NAME_vNNN.plan.json> [--ops ops.json] [--base COPY.prproj] [--to prproj|xmeml]
allowed-tools: Bash, Read, Write, AskUserQuestion
---
Revise a Premiere project with the premiere-project-file-assembly skill (v2). Arguments: `$ARGUMENTS`

1. Read the plan JSON named in the arguments. If none was given, list `*.plan.json` in `~/ppf_out` and ask which version to start from.
2. Turn what the user asked for into ops, using the vocabulary in `${CLAUDE_PLUGIN_ROOT}/skills/261112-premiere-project-file-assembly-v2/references/ops.md`. To find clip ids from a description, run `ppf_match.py --text "<words>"` from the same scripts folder. Write the ops to a file beside the plan (`ops_<date>.json`) and show them to the user before running anything.
3. Changes happen to the project, never to a preview. If something cannot be expressed as an op on the project objects, say so and stop; do not fake it.
4. Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/261112-premiere-project-file-assembly-v2/scripts/ppf.py" revise NAME_vNNN.plan.json --ops ops_<date>.json [--base COPY.prproj] [--to prproj|xmeml]
```

5. Report the new version file (vNNN+1), what changed between the two plans, and the verify lines. Nothing is ever overwritten.
