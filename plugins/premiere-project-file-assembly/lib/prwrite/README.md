# prwrite

Our own Premiere Pro project-file library (stdlib only, Python 3.12+). It reads, builds and writes `.prproj` files, and exports FCP7 xmeml as a fallback. Status: **runs here, unopened in Premiere.** No file it writes has been opened in Premiere yet.

## What it does

| module | in → out |
|---|---|
| `framing` | `.prproj` bytes (gzip or plain) ⇄ XML text, keeping Premiere's gzip header. Inflate is capped at 2 GB. |
| `document` | XML text ⇄ `Doc`: ElementTree plus one ObjectID/ObjectUID/reference index. Serialization is byte-faithful for untouched elements. |
| `serializer` | Premiere's spelling (tabs, LF, escaping) for new elements. |
| `model` | plain dataclasses (`Project`, `Bin`, `Media`, `MasterClip`, `Sequence`, `Track`, `TrackItem`, `Rate`, `Time`). Time is in ticks (254016000000/s). Converts to and from JSON. |
| `parse` | `Doc` → `Project` |
| `probe` | media file → `MediaInfo` (fps as a fraction, size, duration, audio channels and rate, timecode). Uses ffprobe when present, otherwise stdlib header readers for mp4/mov/wav/png/jpg. Never reads audio samples. |
| `build` | `new_project`, `add_bin`, `import_media`, `add_sequence` (full panel trio, V1–V3/A1–A3, stereo mixer graph), `place` (linked V+A, source in/out), `save_version` (next `_vNNN.prproj`, never overwrites, `.tmp` then replace). |
| `export_xmeml` | `Project` → FCP7 xmeml v5, with Rosetta rule ids cited |
| `cli` | `python3 -m prwrite inspect / compare / validate / peek FILE` |

Schema facts come from the vendored py-premiere (MIT, see `ATTRIBUTION.md`), cited `file:line` in the code. `SPEC.md` holds the shared facts.

The plugin's `ppf_write_prproj.py` uses prwrite as its first choice. It falls back to the vendored py-premiere for `--multicam` or when `PPF_PRPROJ_ENGINE=py-premiere` is set.

## What it does not do yet

- **Nothing it writes has been opened in Premiere.** Values marked `GUESS` in `build.py` are not confirmed by the reference:
  - timecode display format 100 for 24 fps
  - `SampleType` 7 for PCM-in-MOV audio
  - `MZ.ZeroPoint` position
  - `PlaybackSpeed` position
  - the preview format for sizes outside the presets
  - the H.264 profile used for codecs with no verified profile (MXF/MPEG-2, for example)
- Stills (their default duration is a Premiere preference), R3D, and audio other than 1, 2 or 6 channels: `import_media` raises, and the plugin skips the clip.
- Multicam, merged clips, captions, transitions, effects, markers, time remap.
- ffprobe is only used when it is installed. Without it, the stdlib reader leaves the start timecode of mp4/mov files empty, because the `tmcd` sample lives in `mdat`.

## Tests

```
python3 -I -m unittest discover -s plugins/premiere-project-file-assembly/lib/prwrite/tests
python3 plugins/premiere-project-file-assembly/skills/261112-premiere-project-file-assembly-v2/tests/run_tests.py
```

`test_build.py` builds from the TEST_DRIVE_v1 clips in `skills/261105-scan-and-index-hard-drives/test_drive/`. The repo holds no real Premiere-written `.prproj`, so every fixture is made by our own code.

## On the Mac (the remaining check)

1. `python3 plugins/premiere-project-file-assembly/skills/261112-premiere-project-file-assembly-v2/scripts/ppf.py build /Volumes/<DRIVE> --project NAME --out ~/Desktop/prwrite_test --to prproj`
2. Open `~/Desktop/prwrite_test/NAME_v001.prproj` in Premiere Pro.
3. Check that it opens without a repair or conversion dialog, that the bins and clips are present and online, and that the sequences play with the V and A halves linked. Then save in Premiere and keep the saved copy as our first real fixture.
