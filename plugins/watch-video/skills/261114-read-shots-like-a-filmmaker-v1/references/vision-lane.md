# The vision lane (P12): what reads the R fields, and how to install it

Code: `scripts/read_lane.py`. Tests: `tests/test_read_lane.py`. Sources: Palmer, `wp/261114-read-shots-like-a-filmmaker-v1/inputs/research_dimensions_and_vision_lane.md` Table B and finding 3; Sexton, `inputs/editors_eye_shot_grammar.md` section 2.

## The model
| item | value |
|---|---|
| repo | `HuggingFaceTB/SmolVLM-256M-Instruct` (Apache-2.0). `HuggingFaceTB/SmolVLM-500M-Instruct` drops in to the same folder if 256M is too weak on lighting style. |
| revision | `d039ab0d4b9311b617e8ffec1d06c8464364aeb3` (pinned 2026-11-14, first install on the Intel iMac Pro). Always pass `--revision` with it; never `main`. |
| runtime | onnxruntime (pinned 1.23.2 on the Intel Mac), `tokenizers`, Pillow, numpy. No torch. |
| folder | `plugins/watch-video/models/smolvlm/` (first), then `~/.scan-and-index/models/smolvlm/`, then `$WATCH_MODELS_DIR/smolvlm/`; or `--model DIR`. |
| files | `config.json`, `tokenizer.json`, and one file per part under `onnx/`. **Each part is resolved on its own**, so quantizations may be mixed. Wanted first: vision encoder `_uint8`, then `_q4`, `_fp16`, plain, `_quantized`, `_int8`; embed tokens and decoder `_int8` first. A file that exists but has no kernel on this machine is skipped and the next one is tried. |

sha256 (fill on first install with `shasum -a 256 <file>`; a later mismatch means a different model, so re-probe):
| file | sha256 |
|---|---|
| config.json | `b70fb4bfde88df9eeebc9d8ff523733b8bf70d6c9b06c610325960e06ae9db52` |
| tokenizer.json | `5ece781dc8d2b2f3e2f289ca0ae50b17cfc27dd27bfe7971bb8241e0b964331a` |
| onnx/vision_encoder_uint8.onnx | `f82adc84246fbfe8651038470166385452682cc889538f5106544e622e5f1595` |
| onnx/vision_encoder_int8.onnx | `d534ab668d3563e6d1d809bbbf1ff18308ded48651178614dbcc4a1de13d16f7` (present on Diego's Mac; does not load, see below) |
| onnx/embed_tokens_int8.onnx | `4b919b829fe2bf225e42431230c56891116f0149e5170e36c99bcc0cfae0d2ef` |
| onnx/decoder_model_merged_int8.onnx | `306fad6cec3b5b7613600b74abf1e410fa50000fbc81292c0d083bd8d85b5fe2` |

## Install on the Mac (one time, ~260 MB, free)
`watch_setup.py` (261113-watch-video-v10) does not carry a SmolVLM entry yet; until it does, the install line is:
```
cd plugins/watch-video && mkdir -p models/smolvlm && \
hf download HuggingFaceTB/SmolVLM-256M-Instruct --revision <PIN> --local-dir models/smolvlm \
  config.json tokenizer.json onnx/vision_encoder_uint8.onnx onnx/embed_tokens_int8.onnx onnx/decoder_model_merged_int8.onnx
python3 -m pip install tokenizers   # only if `python3 -c "import tokenizers"` fails
```
Then probe: `python3 scripts/read_lane.py <hero.jpg> --measured measures.jsonl --out read.jsonl --slots lighting_style time_of_day mood` and read `path` in the output row (`logits` when the model ran).

## How a slot is read
1. One question per slot, by field name, with that slot's closed list (shot_ledger.py `SCHEMA`, references/vocab.json for subject and verb) and the measured facts that bear on it ("levels p50 22 IRE, key low").
2. **logits path** (SmolVLM): each allowed label is scored as the answer's continuation (mean log-prob per token); best wins; margin to the runner-up < 0.35 nats -> `unresolved`. **text path** (a model that only generates): the first allowed label in the output, else `unresolved`. **none**: no model -> every slot `unresolved`, note "model not installed", exit 0.
3. One evidence line, at most 25 words. Value + evidence are checked against the contradiction table ("low key" vs p50 > 45 IRE; "high key" vs p50 < 30; "sunset/golden" vs CCT > 6500 K; "static/locked" vs a moving camera; "night" vs p50 > 40). A contradiction -> `unresolved`, the rejected text kept in `note`.
4. `var_casting` is never asked (raises / exit 2). Mood, tone, colour design, production value and its four pv slots, believability: `trust: person`. The rest: `approximate`.
5. `--lane hosted` is a paid lane: refused unless `--yes-paid` and a keys file from `p0_env.keys_file()`; keys are never printed. It is a gate only: no call is wired.

## Known limits (honest)
- The ONNX adapter (`SmolVLM` class) is written to the published export layout (global 512 px image, 64 image tokens, no tiling) and was **not run** in the build sandbox (no weights). Its first Mac run is the probe; a load failure is reported as the reason and the run still exits 0.
- 256M is weak on abstract fields (Palmer); those are `trust: person` for that reason.

## Probe 1 on Diego's Mac (261114, Intel iMac Pro, Python 3.12, onnxruntime 1.23.2)

The int8 triplet downloaded and hashed clean, and the lane still read `path none`:

```
model at .../models/smolvlm failed to load: [ONNXRuntimeError] : 9 : NOT_IMPLEMENTED :
Could not find an implementation for ConvInteger(10) node with name
'/vision_model/embeddings/patch_embedding/Conv_quant'
```

**What it means.** The int8 vision encoder quantizes its patch-embedding convolution to a signed-int8 `ConvInteger`.
The onnxruntime CPU provider has no kernel for that, so the session cannot be built. The file is not corrupt; this
machine simply cannot run that operator. The decoder and the embedding table are `MatMulInteger` and load fine at int8.

**Two bugs this exposed, both fixed in 1.2.3.**
1. The loader required all three parts to share one quantization suffix, so a working decoder was discarded over the
   vision tower. Each part is now resolved independently, and a part that fails to build a session falls through to its
   next candidate on disk.
2. The reason string was cut at 160 characters, which removed the node name, the one piece of the error that identified
   the operator. Now 400.

**The fix on the machine:** download the `_uint8` vision encoder (unsigned, ~100 MB), which the CPU provider does have a
`ConvInteger` kernel for. Keep the int8 files; the loader prefers uint8 and ignores what it cannot run. If uint8 also
fails, `_fp16` (~190 MB, no integer operators at all) is the next one it tries, then plain fp32.

`read_lane` now prints which file each part loaded, in brackets after the path, so the next probe says what ran.

## Probe 2 on Diego's Mac (261114 00:26, same machine, 1.2.3)

With the uint8 vision encoder on disk the lane came up:

```
read_lane: 2/3 slot(s) read, path logits
[decoder_model_merged_int8.onnx, embed_tokens_int8.onnx, vision_encoder_uint8.onnx]
```

The mixed set is the point: a uint8 vision tower next to an int8 decoder and embedding table. Under 1.2.2, which
demanded one shared suffix across the three parts, this machine had no loadable combination at all. Frame: one
hero at 2 s from an Artlist HD stock clip (h264, 1920x1080, bt709 tagged, 25 fps, timecode 02:04:42:01).

Two of three slots resolved. The third is the margin rule doing its job or a prompt that needs work; probe 3
reads `read.jsonl` to tell those apart. Noted for 1.1: `ffmpeg` writing a single still wants `-update 1`, or it
warns about a missing image-sequence pattern. The file is still written, so this was cosmetic, and the docs now
pass the flag.
