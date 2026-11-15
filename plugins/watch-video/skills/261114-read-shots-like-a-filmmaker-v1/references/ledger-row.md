# The ledger row (Sexton, part 1): the contract every later tool reads

Source: `wp/261114-read-shots-like-a-filmmaker-v1/inputs/editors_eye_ledger_and_reflection.md` part 1. Code: `scripts/shot_ledger.py` (`SCHEMA`).

One row per shot, keyed `segment_id` = `<clip_id>#S<nn>`, `clip_id` = `<drive>/<card column>/<file>` (HOW_THE_THREE_FIT.md, watch_see.py).
Every field is `{"value", "origin", "trust"[, "evidence"]}`. Every value comes from a **closed list, plus `unknown`**. A free word is a bug:
`shot_ledger.py validate` fails it and names the field and the allowed list. A bare number, a missing origin, or a count of people/objects
as a value or evidence also fails.

- **Origin**: **M** measured from pixels (shot_measure.py) · **R** read by the vision lane from the hero frame, with `evidence` · **S** source fact (stock site, pull ledger, ffprobe) · **P** pool fact.
- **Trust**: **machine** trust it · **machine~** trust it with the known failure below · **person** the machine proposes, a person confirms on the top 3.
  (shot_measure.py writes trusted / approximate / person; the ledger maps them to machine / machine~ / person.)
- **R fields are `unread`** until the read lane (P12) lands. `unread` is allowed only on R fields.

## Source facts (S)

| field | closed vocabulary | on set it means | trust |
|---|---|---|---|
| shot_on | cinema / prosumer / DSLR-mirrorless / phone / drone | the budget and the crew | machine, from site metadata; never inferred from pixels |
| native_res | `WxH` as listed | grading room headroom | machine |
| fps | number as listed | | machine |
| bit_depth | 8-bit / 10-bit / 12-bit / 16-bit | how far it can be pushed | machine |
| transfer | Rec.709 / log / HLG | whether levels and colour can be read at all (Palmer) | machine |
| licence | comp / licensed / screener / editorial-only | can it air | machine (pull ledger) |
| source · filmmaker · collection | as listed (one line of text) | curated sets are shot to a standard | machine |

## Measured from pixels (M)

| field | closed vocabulary | on set it means | trust |
|---|---|---|---|
| levels | crushed blacks / full range / lifted (milky) / clipped highlights / protected highlights | where the DP put the exposure | machine |
| lighting_ratio | 1:1 flat / 2:1 soft / 4:1 modelled / 8:1+ hard chiaroscuro | key against fill | machine~: true key:fill only on a face box; person box is cruder; none = unknown |
| colour_temperature | warm / neutral / cool / mixed | practicals and time of day | machine~: a real sunset reads warm, never a fault |
| colour_cast | none / green / magenta / blue | fluorescents, cheap white balance | machine~ |
| saturation | muted / natural / rich / oversaturated | a grade choice; stock pop is oversaturated | machine |
| fidelity | clean / banding / macroblocking / aliasing | encode and sensor | machine (banding, aliasing not measured yet) |
| clarity | critical on subject / shallow DOF (subject sharp) / soft overall / motion blur | focus puller and lens | machine~: sharp_region against the subject box |
| noise | clean / fine grain / noisy | exposure discipline | machine |
| camera_move | locked / pan / tilt / push in / pull out / track / handheld / gimbal float / crane-jib / drone orbit / drone fly-through / zoom in / zoom out / truck left / truck right / pedestal up / pedestal down (decided by shot_motion.py by axis) | the grip department | machine~ |
| move_speed · subject_motion | still / slow / medium / fast | the shot's internal rhythm | machine |
| capture_speed | real time / slow motion | intent | machine (from fps) |
| shot_size | EWS / WS / MWS / MS / MCU / CU / ECU / insert | the grammar unit | machine~: inserts with no face need R |
| framing | subject on thirds / on phi point / centred-symmetric / off-balance | the operator's eye, as geometry (never a score) | machine~ |
| headroom | tight / right / excess | | machine~ |
| lead_room | right / short | looking into the edge | machine~ (probe; unknown this round) |
| horizon | level / tilted / dutch (over 8 degrees) | the operator | machine |

## Read by the vision lane (R): each must carry `evidence`

| field | closed vocabulary | trust |
|---|---|---|
| lighting_style | high key / low key / naturalistic / motivated practical / mixed / flat broadcast | machine~ (cross-checked against M) |
| quality_of_light | hard / soft / backlit-rim / silhouette / top-light | machine~ |
| time_of_day | dawn / golden hour / day / overcast / blue hour / night / interior | machine~ |
| colour_design | complementary (teal-orange) / analogous warm / analogous cool / monochrome palette / neutral / accidental mixed | person |
| depth | layered (fg / mg / bg) / two-layer / flat against a wall | machine~ |
| mood | serene / tense / intimate / melancholy / joyful / clinical / ominous / triumphant / neutral | person |
| tone | sincere / documentary / aspirational / corporate / playful / dramatic / ironic | person |
| production_value | high / mid / low | person on top 3 |
| pv_set | dressed, specific / generic / bare seamless | person |
| pv_props | real, used, period-right / new plastic / none | person |
| pv_performers | natural behaviour / posed for camera / looking at lens | person |
| pv_staging | blocked in depth, everything in its place / flat line-up | person |
| believability | believable / generic stock / staged-obvious | person |
| cliche_markers | none / smile at lens / arms folded at lens / thumbs up / handshake to camera / pointing at screen / laughing at nothing / stethoscope draped / clipboard nod / high-five team / lightbulb idea | machine~ |

## Pool fact (P): never alone (D007)

| field | vocabulary | means | trust |
|---|---|---|---|
| novelty_in_pool | unique / 1 sibling / common idea | how far this shot's measured vector sits from the pool mean (z-units, RMS over the measures): >= 1.5 unique, >= 0.9 1 sibling, else common idea. Pools under 3 rows: unknown | machine. Stand-in until DINOv2 families feed it (P14 method step 3) |

## The row as text (what the ranker reads)

`row_text` joins the known values in the order above, without source/filmmaker/collection, element tags or counts. Sexton's example:

`P07 · MCU OTS · low key, motivated practical (bedside lamp) · 4:1 face · warm · muted · protected highlights · shallow DOF on subject · locked · slow · thirds left, lead room right · layered · intimate · sincere · PV high (...) · believable · clichés none · cinema (ARRI) 4K 10-bit · comp · unique in pool`
