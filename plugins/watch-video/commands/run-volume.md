---
description: Point at a volume; the local models identify, scan and write a content ledger per folder. No agents.
---
One command for a whole drive or project folder. The models on this machine do the work.

```bash
S=plugins/watch-video/skills/261114-read-shots-like-a-filmmaker-v1/scripts
python3 -I -B $S/run_volume.py "$ARGUMENTS" --out <OUT_DIR>
```

Add `--list` to identify the volume and its media and stop. Add `--only ACT1` to run one job.

What it does, in this order:
1. Identifies the volume and every clip in it, grouping them into jobs, one per leaf folder (an act, a card, a day).
2. Routes each job through the measured lanes and the local reader, fetching anything missing once, pinned and hash-checked.
3. Runs entirely on this machine.
4. Writes, per job: `ledger.jsonl`, `sentences.md`, `<date>_<job>_content_ledger.csv` and `.md` with one row per clip
   (cell, clip_id, title, filmmaker, site, duration, shot sentence, size, move, light, time of day, what is on screen,
   KEEP / MARGINAL / CUT and the reason), plus the CUT and MARGINAL lists and any repeated title or shoot.
   A volume summary sits at the top level.
5. Starts no agents and no workers.

A CUT is only called on something observable: on-screen text or a logo, a performer looking at the lens, or flat
bright light with lifted blacks. Anything the reader merely guesses, such as a cliche, moves a clip to MARGINAL and
names what it saw. Titles are never read as evidence.
