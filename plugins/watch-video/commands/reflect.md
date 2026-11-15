---
description: Rank a watched pool against an ask (the fabricator: templates, sliders, reasons)
---
Run the fabricator over a pool that `/read-shots` has already watched. It reads the ledger as text and never
re-watches footage.

```bash
S=plugins/watch-video/skills/261114-read-shots-like-a-filmmaker-v1/scripts
python3 $S/reflect.py <OUT_DIR>/ledger.jsonl --ask "$ARGUMENTS" --out <OUT_DIR>/answer.md
```

`$ARGUMENTS` is plain words: "the 7 most dynamic elephant clips in Thailand, cinematic high, no phone".
A superlative picks the template, a noun filters, "<slider> high/low" doubles or halves a weight, "by place"
groups, a number is how many to return.

Read the answer's first two lines before the ranking: the parse it acted on, and every word it did not use.
`--list-templates` shows the calls it knows. Templates are files a person edits in `../templates/`.
