# Bundled vision weights (watch-video 1.1.0, 261113)

Byte copies of the scan plugin's `models/` (`plugins/scan-and-index-hard-drives/skills/scan-and-index-hard-drives/models/`).
Pins and checksums are `rules.json -> vision.models` (shared key, identical in both skills). These files are ENGINE
files: `scripts/engine_sync.py check` proves they match ENGINE.json; `compare` proves both plugins carry the same bytes.
Lookup order (p0_env.model_file): the installed copy in `~/.cache/scan-and-index-models` (or `$SCAN_MODELS`) first, then these.

| folder | what it does here | licence | source · revision | file | sha256 | size |
|---|---|---|---|---|---|---|
| `dino_small/` | DINOv2-small fingerprints: segments inside a shot, look-alike families | Apache-2.0 | Xenova/dinov2-small @ c2bb04a51fab207c420665f1946016107bffc701 | onnx/model_quantized.onnx | 3afdc8bc63b50558d6e5770f5b799bb82455c2311183a2de43803f343a29d917 | 24.5 MB |
| | | | | preprocessor_config.json | 14e780d86fa1861f8751f868d7f45425b5feb55c38ca26f152ca5097ab30f828 | 436 B |
| `yolox_s/` | YOLOX-S people count per hero frame | Apache-2.0 | Megvii-BaseDetection/YOLOX release 0.1.1rc0 | yolox_s.onnx | c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063 | 35.9 MB |
| `minilm/` | all-MiniLM-L6-v2 text vectors over each segment's tags + facts (search) | Apache-2.0 | Xenova/all-MiniLM-L6-v2 @ 751bff37182d3f1213fa05d7196b954e230abad9 | onnx/model_quantized.onnx | afdb6f1a0e45b715d0bb9b11772f032c399babd23bfc31fed1c170afc848bdb1 | 23.0 MB |
| | | | | tokenizer.json | da0e79933b9ed51798a3ae27893d3c5fa4a201126cef75586296df9b4d2c62a0 | 0.7 MB |
| `yunet/` | YuNet face detector (faces_text group; used by `--bracket full` when SFace is installed too) | MIT (OpenCV zoo) | opencv/face_detection_yunet @ 3cc26e7f1014a5ee5d74a42acee58bafc9d0a310 | face_detection_yunet_2023mar.onnx | 8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4 | 0.2 MB |

Not bundled (too big, or optional): CLIP ViT-B/32 (tags, ~156 MB, core group) installs on first run with
`python3 scripts/p3_see_models.py install core` (or `watch_setup.py install`); SigLIP 2, TransNetV2, SFace, SmolVLM2 and
Whisper are the scan plugin's eyes / faces_text / captions / speech groups, installed the same way when wanted.
MobileNet-SSD (`scripts/mnssd.*`, 23 MB) is the watch engine's object detector, unchanged.
