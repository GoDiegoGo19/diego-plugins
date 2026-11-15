# How the three fit: scan, watch, print

Three plugins in the diego-work marketplace, one job each, one set of IDs between them.

**Scan** (`scan-and-index-hard-drives`). Inventories a whole drive. It ledgers every file, sorts cards and shoot days, tags and groups the footage with local vision engines, makes it searchable, and ships one contact-sheet PDF per drive. Its question is breadth: what is on here, where, and how much. It carries the watch engine inside so it can look at footage at drive scale.

**Watch** (`watch-video`). Looks hard at one piece of footage: a clip, a cut, or a folder of clips. It writes one `WATCH_STATE.json` and renders from it: tags, segments (scenes and shots with in and out times), a tagline (the logline), a shot sheet, a contact-sheet PDF and a write-up. Its question is depth: what is in this, shot by shot. The engine inside is byte-identical to the scan plugin's (`engine_sync.py compare` proves it), and it uses the scan plugin's model weights when both are installed.

**Print** (`premiere-project-file-assembly`). Writes the Premiere project file. It reads scan data (or a media folder) and builds bins, string-outs per card per day, INTERVIEWS / BROLL and sync sequences, then a new numbered version each time you ask for a change. Its question is the edit: put this footage into a project I can open.

## The shared IDs

All three name footage the same way, so the output of one is the input of the next:

| ID | Means | Example |
|---|---|---|
| drive | the volume a scan ran on | `FOXFIELD_A` |
| card | one camera card / folder on that drive, by shoot day | `260901_A001` |
| clip | one media file on a card | `A001C003_260901.MP4` |
| segment | a scene or shot inside a clip or cut, with in and out | `SC01` / `101-3` |
| time | seconds or timecode inside the clip | `12.40` / `01:00:12:10` |

## Which plugin to reach for

| Question | Plugin | Command |
|---|---|---|
| "What is on this drive?" | scan | `/scan <drive>` |
| "What is in this clip / this cut / this folder of selects?" | watch | `/watch <clip or folder>` · `/contact-sheet <clip or folder>` |
| "Build or change the Premiere project." | print | `/ppf-build` · `/ppf-revise` |
