# Install on the Mac (watch-video 2.0.0, read-shots 1.0) · 261114

Everything runs offline after this. Every download is pinned by revision and checked by sha256. Nothing here is paid. Written for the Intel iMac Pro (Python 3.12, onnxruntime 1.23.2); the Silicon pins are in the scan plugin's `rules.json`.

## One command, and it tells you yes or no

```bash
bash ~/Diego-work/plugins/watch-video/verify.sh
```

It stashes local edits to tracked files (kept in `git stash list`), pulls, picks the interpreter that actually has onnxruntime, hashes every weight against the pins, tries to open the reader's three graphs, and prints one of three verdicts:

- `WORKS` — every weight hashes clean and the reader loaded.
- `SEE PASS READY, READER NOT INSTALLED` — read-shots runs, every read slot stays unresolved. Step 3 below installs the reader.
- `NOT READY` — the lines above it marked MISSING or MISMATCH are the list.

Everything below is the long form, for a first install or when a verdict names something to fix.

## 0 · Get the code
```bash
cd ~ && git clone https://github.com/GoDiegoGo19/Diego-work.git || (cd Diego-work && git pull)
cd ~/Diego-work/plugins/watch-video
```
Or, inside Claude Code: `/plugin marketplace add GoDiegoGo19/Diego-work` then `/plugin install watch-video@diego-work`.

## 1 · What's already there (one command verifies every weight)
```bash
python3 skills/261113-watch-video-v10/scripts/watch_setup.py check
```
Reads: Python version, onnxruntime, **every weight by sha256** (bundled: DINOv2-small, YOLOX-S, MiniLM, YuNet, CLIP; downloaded: the SmolVLM reader), **and whether onnxruntime can actually open the reader's three graphs on this machine**, then ffprobe, ffmpeg, cv2, tesseract. Anything "MISSING" is fixed by step 2 or 3.

The reader's hashes are pinned in the script. They were taken from Diego's Intel iMac Pro on 261114 and then confirmed byte-identical against a fresh pinned download in a Linux container, so `ok` on that line means the file is the right file, not just a file that is present. The last reader line is the one that matters:

```
reader  smolvlm  load  ok, loaded decoder_model_merged_int8.onnx, embed_tokens_int8.onnx, vision_encoder_uint8.onnx
```

## 2 · Runtime and CLIP (asks before installing)
```bash
python3 skills/261113-watch-video-v10/scripts/watch_setup.py install
```
Prints the plan (the pinned pip packages and the CLIP weights, ~156 MB), asks y/n, then installs. If it says Python 3.12 is the pinned environment and you are on another version: `brew install python@3.12` and rerun with `python3.12`. If the scan plugin is already installed on this Mac, its venv at `~/.scan-and-index/venv` can be used instead: `PY="$HOME/.scan-and-index/venv/bin/python"`.

## 3 · The reader model (the one gear read-shots is missing)
Small, runs on the onnxruntime you already have:
```bash
mkdir -p models/smolvlm
hf download HuggingFaceTB/SmolVLM-256M-Instruct --local-dir models/smolvlm \
  config.json tokenizer.json onnx/vision_encoder_uint8.onnx onnx/embed_tokens_int8.onnx onnx/decoder_model_merged_int8.onnx
cd models/smolvlm && shasum -a 256 config.json tokenizer.json onnx/*.onnx && cd ../..
```
Then write the commit hash `hf download` printed and the sha256 lines into `skills/261114-read-shots-like-a-filmmaker-v1/references/vision-lane.md` (the table says PIN ON FIRST INSTALL). After that, always pass `--revision <that hash>`. `hf` is the Hugging Face CLI: `pip install -U huggingface_hub` if missing.

Probe it on one frame before trusting it:
```bash
S=skills/261114-read-shots-like-a-filmmaker-v1/scripts
ffmpeg -y -ss 2 -i "/path/to/a/clip.mp4" -update 1 -frames:v 1 /tmp/hero.jpg
python3 $S/shot_measure.py /tmp/hero.jpg --assume-transfer rec709 --out /tmp/m.jsonl
python3 $S/read_lane.py /tmp/hero.jpg --measured /tmp/m.jsonl --out /tmp/read.jsonl --slots lighting_style time_of_day mood
```
`path` in the output should read `logits`, followed in brackets by the file each part loaded. If it reads `none` with a reason, the reason is the thing to fix; the run still exits 0. **Do not use the `_int8` vision encoder on an Intel Mac**: it is a signed-int8 `ConvInteger` the CPU provider has no kernel for (261114 probe; `references/vision-lane.md`). The lane resolves each part separately, so a mixed set (uint8 vision encoder, int8 decoder) is correct, not a compromise.

## 4 · Optional: the time-of-day probe (176 MB, OpenRAIL-M)
Only after Diego's ruling that a trained classifier head is not "CLIP as a judge". Commands and licence text: `skills/261114-read-shots-like-a-filmmaker-v1/references/cinemanet.md`. It never overrules the measured time of day.

## 5 · Optional: a stronger reader via llama.cpp (already pinned by the scan plugin)
The scan plugin pins llama.cpp release b12207 for captions and already runs a SmolVLM2 there (`plugins/scan-and-index-hard-drives/.../rules.json`, `vision.llama_cpp`). If that is installed, Qwen3-VL-2B (Apache-2.0) in GGUF is the stronger reader to probe next; it is a 1.1 probe, not a 1.0 need. Ask before downloading (about 2 GB).

## 6 · Run it
```bash
S=skills/261114-read-shots-like-a-filmmaker-v1/scripts
python3 $S/read_shots.py /Volumes/DRIVE/pool_folder --out ~/read_shots_out --assume-transfer rec709
```
or `/read-shots /Volumes/DRIVE/pool_folder`. Writes `ledger.jsonl` (one row per shot), `sentences.md` (one line per shot, grouped by subject and place), `measures.jsonl` (the numbers, the decon passes), `read.jsonl`, `source.jsonl`. Never overwrites: the next run is `_v002`. Drop `--assume-transfer` for footage with a proper colour tag.

Read `sentences.md` first. Where a line says "indistinct: ask the vision lane", that is the grammar being honest: the measured half can't separate those two shots and the reader has not spoken yet.

## 7 · Tests on the Mac (optional, 2 minutes)
```bash
cd skills/261114-read-shots-like-a-filmmaker-v1
for t in tests/test_*.py; do python3 -I -B $t | tail -1; done
cd ../261113-watch-video-v10 && SIBLING_SKILL=../../../scan-and-index-hard-drives/skills/scan-and-index-hard-drives python3 -I -B -m unittest discover -s tests | tail -1
```

## What to send back
The first `sentences.md` from a real pool, the `path` line from step 3, and the `watch_setup.py check` output. Those three tell the hub what the next round fixes.
