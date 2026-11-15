# Probes: what was not run here, and how to run it

## 1. py-premiere .prproj (tier 1)  [D here]
See prproj-recipe.md "Probe on the Mac". Pass = `PPF_RUN_PRPROJ=1 python3 tests/run_tests.py` shows the prproj
lines PASS, and Premiere opens NAME_v001.prproj with bins 00-05 and the sequences.

## 2. Tier 3 XML import  [D here]
Premiere: File > Import > NAME_v001.xml. Expect a bin named after the file holding 00_CUTS..05_STILLS and
the sequences; count offline clips. Run ppf_write_xmeml.py with `--path-map <linux root>=/Volumes/<DRIVE>`
first if the XML was written off the Mac. Known cost: duplicate media items.

## 3. MiniLM matching  [W here]
`python3 scripts/ppf_match.py OUT/NAME_objects.json --text "aerial drone shot" --segments <P6 segments CSV>`
First line says `engine: minilm` (onnxruntime + numpy importable) or `tfidf`. Model is read from
`plugins/scan-and-index-hard-drives/skills/scan-and-index-hard-drives/models/minilm/` (onnx/model_quantized.onnx
23 MB + tokenizer.json), never copied. Without the scan plugin, download by hand (pinned names):
`https://huggingface.co/Xenova/all-MiniLM-L6-v2/resolve/main/onnx/model_quantized.onnx` and `.../tokenizer.json`
into a folder with the same layout, then `--model-dir <folder>`.
