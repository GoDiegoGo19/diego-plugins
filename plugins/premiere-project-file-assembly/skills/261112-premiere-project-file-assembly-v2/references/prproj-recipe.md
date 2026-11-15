# .prproj recipe (tier 1): the exact py-premiere calls

Library: **py-premiere**, package `py_premiere` 0.1.0, MIT (Aurore Delaunay), Pouf/py-premiere @ **adb9e57**
(the commit the engine skill pinned for a client project/PROJECT_A). Vendored unpatched at
`plugins/premiere-project-file-assembly/vendor/py-premiere/` (no dependencies; Python >= 3.7).
Source tree is `vendor/py-premiere/py_premiere/...` (no `src/`). Line numbers below are into that copy.
Status: **[D] here** (the sandbox may not execute it); the calls are the ones engine v2 marks [W] on PROJECT_A.

## Load

```python
sys.path.insert(0, "<plugin>/vendor/py-premiere"); import py_premiere
```
`ppf_write_prproj.load_lib()` does this and, in memory only, adds 23.976/24 to
`media_import.TIMECODE_FORMATS` (media_import.py:70 knows 25/29.97/30 only; engine v2 gotcha):
`(10594584000, False): 110`, `(10584000000, False): 100`.

## Calls, in the order the adapter makes them

| step | call | where |
|---|---|---|
| open user's project | `py_premiere.parse(path, *, preferences_path=None) -> Application` | `__init__.py:29` |
| or a fresh one | `py_premiere.new(*, preferences_path=None) -> Application` | `__init__.py:49` |
| project | `Application.project` (property) | `models/application.py:18` |
| bin tree root | `Project.root_item -> ProjectItem` | `models/project.py:240` |
| children | `ProjectItem.children -> NamedList[ProjectItem]`, `.name` | `models/project_item.py:822`, `:572` |
| make bin | `ProjectItem.add_bin(name: str) -> ProjectItem` | `models/project_item.py:603` |
| import media | `Project.import_files(paths: list, target_bin: ProjectItem|None=None) -> list[ProjectItem]` | `models/project.py:306` |
| new sequence | `Project.add_sequence(name, preset="1080p23976", video_tracks=None) -> Sequence` | `models/project.py:602` |
| presets | `sequence_builder.FORMATS`: 1080p23976, 1080p25, 1080p2997, 1080p50, 1080p5994, 2160p*, square/portrait, broadcast4/8mono | `models/sequence_builder.py:212` |
| file the sequence | `Sequence.project_item` then `ProjectItem.move_to(new_parent)` | `models/sequence.py:388`, `models/project_item.py:687` |
| tracks | `Sequence.video_tracks`, `Sequence.audio_tracks` | `models/sequence.py:630`, `:635` |
| place | `Track.add_clip(project_item, start: Time|None=None) -> TrackItem` (video track places the VideoClip half, audio track the AudioClip half) | `models/track.py:842` |
| trim | `TrackItem.in_point = Time`, `TrackItem.out_point = Time` | `models/track_item.py:595`, `:605` |
| time | `Time.from_seconds(seconds)`, `TICKS_PER_SECOND = 254016000000` | `models/time.py:85`, `:10` |
| multicam (opt.) | `Project.create_multicam_clip(items, name=None) -> ProjectItem` | `models/project.py:1024` |
| save | `Project.save(path)`: new file only, `FileExistsError` on overwrite, atomic via temp sibling | `models/project.py:1642` |
| never | `Project.save_in_place()` (overwrites the parsed file) | `models/project.py:1657` |
| reparse | `py_premiere.parse(saved).project.sequences` (`project.py:261`) | |

## The file, the ids

- A .prproj is gzip-compressed XML: root `<PremiereData>`, a flat list of objects linked by
  `ObjectID`/`ObjectRef` (integers) and `ObjectUID`/`ObjectURef` (GUIDs). py-premiere writes gzip OS byte 10
  (gzip_io.py), like current Premiere; older builds wrote 19.
- py-premiere allocates fresh ObjectIDs and GUIDs for everything it adds (`_splice_fragment`); we never
  write ids by hand. Our traceability lives in the plan: `<project>_vNNN.plan.json` beside the .prproj
  keeps each clip_id (drive/card/file) and the op that placed it.
- Media is read on import (headers only). Supported at adb9e57: H.264/H.265/ProRes/DNxHR in MP4/MOV
  (with or without AAC), MPEG-2 in MXF, WAV/AIFF/M4A/MP3, common stills. Not: R3D, BRAW, RAW stills,
  MPEG-4 Part 2. The adapter imports one file per call and lists every SKIP.
- Sync: SYNC sequences are ordinary sequences, one video track per camera, `start = TC start - group start`
  (engine doc: "one subtraction per angle"). `create_multicam_clip` syncs at clip STARTS and needs exactly
  one audio angle, so `--multicam` builds one only when every angle starts at offset 0.

## Probe on the Mac (closes the [D])

1. `cd <repo>/plugins/premiere-project-file-assembly/skills/261112-premiere-project-file-assembly-v2`
2. `PPF_RUN_PRPROJ=1 python3 tests/run_tests.py` : the prproj lines must PASS (v001, v002, more bins and
   sequences than the base). The vendored samples are Git LFS pointer stubs, so the test uses `new()`.
3. Real run on a copy of a project Premiere saved: `python3 scripts/ppf.py build /Volumes/DRIVE --project NAME --out ~/ppf_out --base ~/COPY.prproj`
4. `python3 scripts/ppf_prproj_peek.py ~/ppf_out/NAME_v001.prproj --names` (stdlib, no py-premiere) lists bins and sequences.
5. Open `NAME_v001.prproj` in Premiere: bins 00-05, stringouts, INTERVIEWS/BROLL; note offline clips. Record PASS/FAIL.
6. If a call fails: the error names a py_premiere file; compare with the table above and fix the adapter, not the library.
