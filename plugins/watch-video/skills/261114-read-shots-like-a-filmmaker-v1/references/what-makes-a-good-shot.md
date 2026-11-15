# What makes a good shot (mini white paper · 261114)

*Inside the read-shots skill. Sources: Palmer's research (`wp/261114-read-shots-like-a-filmmaker-v1/inputs/research_what_makes_a_good_shot.md`) and Sexton's fabricator design (`inputs/editors_eye_fabricator.md`), both on `main`. Decisions D028–D034, facts F023–F030 in the job ledgers. Weights are starting points to tune on Diego's blind picks, never measured truths.*

## 1 · The claim
Diego (261114): "a good shot is usually contrast, purpose, dynamism, layers" and "the good shot of the bird is really a byproduct of the script." So **good is script-relative**. There is no verdict. There is a **template** (what this ask means by good) read off **sliders** (numbers under the words on every totem pole). The first machine deconstructs each shot into a pole, once. A **second machine, the fabricator**, reassembles the pool to the ask, again and again, from text, never from pixels.

## 2 · What theory and the literature say (the short form)
| finding | source | consequence here |
|---|---|---|
| Contrast in any visual component (tone, colour, space, line, movement, rhythm) raises intensity; affinity calms | Bruce Block, *The Visual Story* | "dynamic" = contrast anywhere; a calm shot is high affinity by choice, not a fault |
| One centre of interest, the eye led to it | Mascelli, Arnheim, Murch (eye trace) | purpose = one clear point; measured as subject-vs-background clarity and saliency concentration |
| Depth: the three-dimensional field (overlap, perspective, haze, chiaroscuro) | Blain Brown | layers slider; flat frames read as record shots |
| Motivated light with direction | Brown; Deakins | light slider: ratio + backlight/edge tests (measured) + direction word (read) |
| Professional photos isolate the subject: subject-focused features separate pro from amateur 93 % vs 72 % | Luo and Tang, ECCV 2008 | measure contrast and sharpness **between subject and background**, not globally |
| Attribute importance "depends strongly on image content" (thirds and vivid colour matter for landscapes, not portraits) | Kong et al., AADB, ECCV 2016 | one fixed weighting is wrong by construction: weights move with the brief and the shot size |
| Rule of thirds: no general viewer preference | Amirshahi 2014; Hoh 2023 | framing geometry stays a tiny weight |
| Preference vs complexity is an inverted U | Berlyne and later | simplicity rewards the middle of the pool's clutter range, small weight |
| Rule of six: emotion 51, story 23, rhythm 10, eye trace 7, flat screen 5, depth 4 | Walter Murch | pixels get about a quarter of the vote; the sliders rank **inside** a brief, never above it |

## 3 · The sliders (0 to 1, computed by code from the row's closed values)
| slider | built from |
|---|---|
| **energy** | arc_energy, move_speed, subject_motion, arc_shape |
| **purpose** | framing point (thirds / phi / centred), clarity on subject, subject size, depth separation |
| **contrast** | lighting_ratio, levels (full range or protected highlights high; milky low), quality_of_light |
| **layers** | depth (layered / two-layer / flat), foreground occlusion, haze |
| **cinematic** | lighting_style (motivated, low key, naturalistic over flat broadcast), shallow DOF, colour_design, designed camera move |
| **believable** | believability, pv_performers, cliche_markers none |
| **production** | production_value with its four evidence slots, source_quality_class |
| **light** | key:fill, backlight / edge / silhouette tests (M), direction and motivation words (R) |
| **colour** | colourfulness, palette contrast (warm/cool, complementary), saturation in range |
| **calm** | inverse of energy, plus steadiness and level horizon |
| **clean** | no quality flags (soft, noisy, clipped, cast) |
| **simplicity** | edge spread and palette count, peak at mid-clutter |
| **fit** | the ask's subject / verb / place / time against the sentence slots |

**None of these is a count.** Detector counts live in the detector bin for search and never become a slider.

New cheap measures this needs (no weights): symmetry (mirror correlation), stillness (1 minus normalised flow), subject-vs-background contrast and sharpness ratios, edge spread. A monocular depth model would sharpen layers; that is a probe with a LICENSE to read first.

## 4 · The default template: best = most dynamic
Diego: "the best ones equal the most dynamic," on a high-end camera with high production value as the easy solve. Two chairs proposed weights independently; the fabricator file carries Sexton's ordering, Palmer's research is the check on it.

| slider | weight | why |
|---|---|---|
| energy | .30 | "the best ones equal the most dynamic" (Diego) |
| purpose | .20 | one clear central visual point |
| contrast | .15 | some level of contrast |
| layers | .15 | depth, foreground to background |
| cinematic | .10 | light and lens with intent |
| believable | .05 | an audience has to buy it |
| production | .05 | the easy solve counts, but least; Palmer's research weights it 20 for a stock-clean brief |

Switches: `cap_staged_obvious = true` (holds the shot in the bottom third, never drops it; off for ironic or playful briefs). Ties: emotion, believability, novelty in pool, handles, fewer flags, licence. Still tied → **TIE**. Two shuffled runs that disagree → **UNSTABLE**.

Brief overrides as examples: calm sets energy to 0 and adds symmetry and stillness; product/stock clean raises production and simplicity; documentary lowers production and raises light motivation, because polish reads as staged.

## 5 · The call templates (what an editor actually says)
All share the **base floor**: no hard faults, usable length at least the need, orientation matches the deliverable, licence allowed for the use.

| template | extra hard filters | weights in order |
|---|---|---|
| best (default) | none | energy .30 · purpose .20 · contrast .15 · layers .15 · cinematic .10 · believable .05 · production .05 |
| safest | clean · licence clear · not staged-obvious | clean .30 · purpose .25 · believable .20 · fit .15 · contrast .10 |
| most cinematic | none | cinematic .35 · contrast .20 · layers .20 · purpose .15 · production .10 |
| most believable | performers not looking at lens | believable .40 · production .20 · layers .15 · purpose .15 · cinematic .10 |
| most energy | arc not held | energy .50 · purpose .20 · contrast .15 · layers .15 |
| calmest | still or slow move | calm .40 · purpose .20 · contrast .15 · layers .15 · cinematic .10 |
| establisher | EWS / WS / MWS | fit(place) .30 · purpose .20 · layers .20 · calm .15 · contrast .15 |
| detail | CU / ECU / insert, sharp on subject | purpose .35 · fit(subject) .25 · contrast .20 · cinematic .20 |
| emotional beat | face or hands present | believable .30 · purpose .25 · contrast .15 · cinematic .15 · layers .15 · **no auto BEST, three shown "for you"** |
| b-roll under VO | length ≥ VO · no lip-sync to lens · no burned-in text | fit .30 · calm .25 · layers .20 · believable .15 · clean .10 |
| transition | arc is pass-by / whip / cross / reveal / focus change | energy .30 · purpose .20 · contrast .20 · layers .15 · clean .15 |
| same as this one but… | same subject and place as the anchor | the named slot must change; rank by fewest other differences, then best |
| by place / time of day / source class | none | grouping, not ranking; each group ranked inside by best or the named template |

## 6 · The bird test: three different goods
One pool of bird shots, three profiles that inherit from best.

| profile | hard filters | weights | a correct winner reads like |
|---|---|---|---|
| **silhouette** | light silhouette / backlit; dawn to sunset | contrast .40 · purpose .30 · cinematic .15 · calm .15; crushed blacks are not a fault here | "WS, low angle, locked off on a heron, standing, against a low sun, sunset (silhouette)" |
| **drama** | arc not held; subject motion medium / fast | energy .35 · contrast .25 · purpose .20 · cinematic .20 | "MCU, low angle, track fast on an eagle taking off, hard side light; MCU → MS → WS" |
| **reflection** | place water; move locked / slow; horizon level | calm .35 · purpose .20 (centred-symmetric highest) · layers .20 · contrast .15 · cinematic .10 | "WS, eye level, locked off on a swan gliding slowly across a still lake, golden hour; static hold" |

Palmer's illustrative scores show the shape: under the default template drama 0.77, silhouette 0.69, reflection 0.52; under a calm brief reflection 0.75, silhouette 0.69, drama 0.54. Same three shots, reversed order, because the brief moved the weights. That is the sentence "the good shot of the bird is a byproduct of the script" in numbers.

How a brief line picks: the words pick first ("against the sky" → silhouette; "takes flight" → drama; "still lake" → reflection). If the words say nothing, the line type picks (action → drama; beat or transition → silhouette or reflection; establisher → reflection). If still open, run all three and put the top shot of each on the line's page as three different goods. Diego picks.

## 7 · Hard filter versus weight (the rule the design must get right)
- **Only these remove a shot:** hard faults; too short; a licence the use cannot take; what the ask names as must or must-not; the template's own definition (a detail is close, an establisher is wide).
- **Everything aesthetic is a weight.** Soft, noisy, flat, cheap camera, generic set: the shot stays, ranked lower, flag showing.
- A template may turn a weight into a filter **only when its name says so** (safest filters on clean; calmest on slow).
- A hard filter on a field the row reads `unknown` (Thailand is often invisible in pixels) does not drop the shot; it goes to an **unconfirmed** tail with the missing field named.

## 8 · Templates are files a person edits
One file per template in `templates/<name>.json`: name, says, inherits, hard, weights (ordered, sum 1), switches, tie_break, must_cite, a one-line **why** per weight, edited_by, date. A weight change with no why is refused. A new ask starts as a copy of an existing file. Tuned on Diego's blind picks, one weight at a time. Shipped here: `best.json`, `silhouette.json`, `drama.json`, `reflection.json`.

## 9 · The ask grammar: words → filter + template + overrides
"find the 7 most dynamic elephant clips in Thailand, cinematic high, phone footage excluded" parses to: **elephant · Thailand · best (cinematic ×2) · no phone · up to 7**.
- a superlative picks the template; a noun becomes a subject filter; "in / at" becomes place or time; "<slider> high / low" doubles or halves a weight; "excluded / no / only" makes a hard filter; "like P07 but…" calls same-as-this-one-but; "by …" groups; a number is the result count.
- **Echo the parse before the answer** and list every word not used ("ignored: clips"). Never drop a word silently.
- If only 3 pass, the answer says 3, never padded to 7.
- **"The most elephants" is never a count of elephants.** A herd is a filter (`var_form = group`), not a ranking by boxes.
- The reason for every rank cites at least three row fields from the template's must_cite list. Counts are never a reason.

## 10 · Where this lives
Not built yet: `reflect.py` (the fabricator) is the 1.1 build. The ASCII map's FABRICATOR box (`references/ascii-map.md`) is its blueprint; the templates folder is its data. The sheet shows each shot's top two sliders so Diego can disagree with a reason, not with a number.
