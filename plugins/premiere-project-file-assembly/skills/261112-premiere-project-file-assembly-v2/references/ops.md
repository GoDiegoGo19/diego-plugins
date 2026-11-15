# Operations: what the LLM writes from a conversation

The plan is a JSON object whose `ops` list the writers replay in order. A revision is a JSON
list of ops given to `ppf.py revise NAME_vNNN.plan.json --ops ops.json`; it writes vNNN+1.
Clip ids are objects.json `clip_id` values (`<drive>/<card column>/<file>`); find them with
`ppf_match.py objects.json --text "<what the user said>"`. Times are seconds.

| op | fields | does |
|---|---|---|
| add_bin | path | bin by slash path, parents made as needed |
| add_masterclip | clip, bin | put a clip in a bin (plan builder does every object) |
| add_sequence | name, bin?, fps?, width?, height?, clips?, track? | new sequence (defaults 01_SEQUENCES, 23.976, 1920x1080); with `clips`, they are placed back to back on V1 |
| place_clip | seq, clip, track ("V1"), at_s, in_s?, out_s? | one clip at a time position |
| append_clips | seq, clips [ids], track? | clips back to back after the sequence's last clip |
| remove_clip | seq, clip | takes that clip off that sequence |
| rename_sequence | name, to | renames (and its placements) |
| remove_sequence | name | drops the sequence and its placements |
| extract_sequence | name, out | copies ONE sequence (nested sequences, clips, master clips, media, owner chain, ancestor bins, settings 3..39) out of the user's .prproj into a new slim .prproj in `out`; runs `scripts/prproj_extract.py extract SRC "<name>" <out>` then `check` (documented here; ppf.py wiring is P04) |

Refused (exit, nothing written): unknown op, missing field, unknown clip id, a sequence that does not exist,
add_sequence with a name already used.

Example: "make me a cut called aerials in today's cut folder with both drone shots"
```json
[{"op": "add_sequence", "name": "261112_cut01_aerials", "bin": "00_CUTS/261112_cut01", "fps": 29.97},
 {"op": "append_clips", "seq": "261112_cut01_aerials",
  "clips": ["TEST_DRIVE_v1/260901_SHOOT_DAY_1/AERIAL_DJI/DJI_0001.MP4",
            "TEST_DRIVE_v1/260901_SHOOT_DAY_1/AERIAL_DJI/DJI_0002.MP4"]}]
```
Pre-cut: `place_clip` with `in_s`/`out_s` from the scan's segment t_in/t_out.

## extract_sequence (tool: scripts/prproj_extract.py, stdlib)

```
python3 scripts/prproj_extract.py extract SRC.prproj "SEQ NAME" OUT_DIR   # -> OUT_DIR/<stem>__<slug>_<YYMMDD-HHMM>.prproj
python3 scripts/prproj_extract.py check  OUT_DIR/<that file>.prproj        # 5 checks + "6 TODO open in Premiere"; last line PASS|FAIL n + counts
```
`{"op": "extract_sequence", "name": "PROJECT_A - v8 261112", "out": "~/ppf_out"}`.
SRC is only read; an existing output name is refused. Spec and the 22:53 DAMAGED / 22:57 OPENED story:
`reports/261112-2340_mac_handoff_extract-one-sequence-prproj.md`. A PASS means "written, checked, unopened"
until a person opens the file in Premiere. Test: `python3 -I -B -m unittest discover -s tests -p 'test_prproj_extract.py'`.
