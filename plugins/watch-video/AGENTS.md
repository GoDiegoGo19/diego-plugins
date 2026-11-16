# watch-video, for a coding agent (Codex, Cursor, or any shell agent)

No plugin system needed. Everything here is plain Python over ffmpeg and small ONNX models. Run the
scripts directly from a clone.

```bash
PY=python3                 # 3.10+; 3.12 is what the pins target
$PY -m pip install onnxruntime opencv-python-headless numpy tokenizers pillow av scenedetect
# ffmpeg must be on PATH:  brew install ffmpeg   (mac)   apt install ffmpeg   (linux)

W=plugins/watch-video/skills/261113-watch-video-v10/scripts
R=plugins/watch-video/skills/261114-read-shots-like-a-filmmaker-v1/scripts
```

## The three things it does

```bash
# 1. a whole drive or project folder -> a content ledger per subfolder
$PY -I -B $R/run_volume.py /path/to/clips --out /path/to/output --list   # identify only, then drop --list

# 2. one folder -> a measured ledger + one sentence per shot
$PY -I -B $R/read_shots.py /path/to/folder --out /path/to/output --assume-transfer rec709

# 3. rank an already-watched pool against a plain-words ask
$PY -I -B $R/reflect.py /path/to/output/ledger.jsonl --ask "the 5 best shots, cinematic high"
```

Check an install: `bash plugins/watch-video/verify.sh` prints WORKS, or names what is missing.

## What an agent should know before using it

- **It provisions itself.** Missing weights are fetched once on first run, pinned by revision and
  checked by sha256, into `plugins/watch-video/models/`. About 420 MB total. `WATCH_NO_AUTO_INSTALL=1`
  turns that off, and the run still completes with those columns thinner.
- **A measurement beats a guess.** Light, time of day, camera move and framing come from code over
  pixels. The vision model only fills what no measurement can reach: subject, action, place, mood.
  Where they disagree, the measured value wins and the model's answer is kept as evidence. Do not
  "improve" a measured field with a model answer.
- **Counts never rank.** Detector boxes are for search, never a criterion and never a reason.
- **An unresolved slot stays unresolved.** `unread` and `unknown` are the absence of a value. Do not
  fill them in from the file name or from a stock listing's title.
- **Nothing here starts an agent.** `run_volume.py` walks the tree itself. Do not fan work out to
  subagents; the models are the workers.
- **Speed:** roughly a minute per clip on an older Intel Mac, most of it the reader. Point it at one
  folder before a whole drive.
- **Outputs** per job: `ledger.jsonl` (one strict row per shot), `sentences.md`, `measures.jsonl`,
  and `<date>_<job>_content_ledger.csv`/`.md` with KEEP / MARGINAL / CUT and the reason.

Licence Apache-2.0. Bundled and downloaded models keep their own terms; see `NOTICE` at the repo root.
