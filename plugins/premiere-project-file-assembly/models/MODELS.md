# Models bundled in this plugin

## minilm/ — all-MiniLM-L6-v2, int8 ONNX (23 MB)

- **What:** a sentence-embedding model (384 dimensions). `ppf_organize.py` and `ppf_match.py` use it to group clips by topic, find every take of a line, and rank clips against your notes. It ranks and groups; the editor decides.
- **Licence:** Apache-2.0.
- **Source:** Hugging Face `Xenova/all-MiniLM-L6-v2`, revision `751bff37182d3f1213fa05d7196b954e230abad9` (as recorded in the scan plugin's `rules.json`, `vision.models.minilm`). Copied byte for byte from `plugins/scan-and-index-hard-drives/skills/scan-and-index-hard-drives/models/minilm/` on 261113.
- **Runs with:** onnxruntime + numpy (`scripts/ppf_setup.py` installs those two into `~/.ppf/venv`). Without them, the scripts fall back to TF-IDF and say so.

| file | bytes | sha256 |
|---|---|---|
| `minilm/onnx/model_quantized.onnx` | 22972370 | `afdb6f1a0e45b715d0bb9b11772f032c399babd23bfc31fed1c170afc848bdb1` |
| `minilm/tokenizer.json` | 711661 | `da0e79933b9ed51798a3ae27893d3c5fa4a201126cef75586296df9b4d2c62a0` |

Check them any time: `python3 skills/261112-premiere-project-file-assembly-v2/scripts/ppf_setup.py check`.

Lookup order in the scripts: this folder first, then the scan plugin's copy, then the TF-IDF fallback.
