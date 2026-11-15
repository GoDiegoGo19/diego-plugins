# Diego's Claude Code plugins

Two plugins for editors and filmmakers. Both run locally; the vision models are small and offline.

```
/plugin marketplace add GoDiegoGo19/diego-plugins
/plugin install watch-video@diego-plugins
/plugin install premiere-project-file-assembly@diego-plugins
```

## watch-video
Watch a clip or a folder for the edit: cuts, segments, look-alike families, people, hero frames, a shot
sheet and a contact-sheet PDF. Then read every shot like a filmmaker: one sentence per shot printed from a
measured ledger (levels, light, colour, motion, framing, arc), and `/reflect` to rank a pool against a
plain-words ask over templates you can edit. The SEE weights ride inside (~83 MB). The reader model
(SmolVLM-256M ONNX) downloads once, pinned and hash-checked.

Verify an install: `bash plugins/watch-video/verify.sh`

## premiere-project-file-assembly
Writes Premiere project files from a conversation, a new version each time. A media folder or scan data
becomes bins, per-card string-outs, INTERVIEWS and BROLL, and timecode-synced sequences. A small model
inside (MiniLM) groups B-roll by topic and finds every take of a line. Writes `.prproj` directly with its
own library; FCP7 xmeml as a last resort.

Verify an install: `bash plugins/premiere-project-file-assembly/verify.sh`

Status: writing a `.prproj` is verified by reparsing what it wrote. Opening those files in Premiere is
proven for the engine lane and not yet re-proven for the new writer.
