# How a filmmaker watches (read this before you build or prompt anything in this skill)

*Diego, 261114, in his words: "I'm trying to share my knowledge as a professional filmmaker so the workers understand the dimensionality. We're adding truth and validity in a contextual language framework. I'm not coming up with this on the fly; it's what you learn in film school."*

## The problem this skill exists to fix
Vision models describe a shot the way someone who has never studied film would: *couple walks on beach*. Noun, verb, done. That is a two-dimensional view. Diego: "If I tell mom and dad, hey, tell me how to teach a thing to watch video, they'll say: just describe what's on screen." A person who has spent years with cinema sees the same frame in many dimensions at once, and each dimension is already documented in a hundred years of film study. The weights the models carry can answer in those dimensions; nobody has been asking them to. This skill asks, one dimension at a time, with the vocabulary film school already uses.

## The dimensions (the vocabulary is old; only the strictness is new)
| Dimension | Film-school term | What the row records | Who fills it |
|---|---|---|---|
| What is in the frame | mise en scène (setting, props, costume, bodies) | subject noun + its variation (young / old / solo / couple / group), place, dressing, wardrobe | vision lane, closed lists; a person for casting |
| What they do | action, blocking | verb + manner + direction | vision lane for the verb; measured motion for manner |
| How near we are | proxemics, shot size | EWS / WS / MWS / MS / MCU / CU / ECU | measured from face or body height; vision lane when no person |
| Where the camera stands | angle, height | eye level / low / high / overhead / dutch | vision lane; horizon tilt measured |
| How the frame is built | composition, framing, geometry | subject point vs thirds and golden points (geometry only, never a score), headroom, lead room, lines, depth | measured |
| How the camera moves | camera movement | static / pan / tilt / dolly / track / handheld / drone orbit, with speed and steadiness | measured from optical flow (open vision models score at chance on this: CameraBench) |
| What the light is doing | lighting: key, ratio, direction, quality, motivation | key high / low; ratio; hard / soft; direction; motivated practical / flat broadcast | ratio and levels measured; style read with evidence that must agree with the numbers |
| When | time of day inferred from light | dawn / day / golden hour / sunset / night, with the colour temperature and luma under it | measured cast + a small local classifier as a probe; vision lane with evidence |
| What it was made with | source, fidelity | camera, codec, chroma, bit depth, resolution, licence; a quality class weighed, never ranked on alone | ffprobe + the stock page + sidecars |
| How it changes | the arc of the shot | head → mid → tail, sampled where the measured change spikes; arc shape from a closed list (held, push, pull/reveal, pass-through, rack, soft, whip) | measured sizes and focus at three points; vision lane for what entered or left |
| What it is for | intent, believability, interest | whether it reads as real, whether it is interesting, what beat it could serve | a person, on the top picks; the model may propose, never decide |

## The rules that fall out of this
1. **One dimension per question.** Never ask a model "describe this shot." Ask it the size, then the angle, then the subject, each from a closed list with one line of evidence. The sentence is printed by code from the slots (Sexton's grammar); the model never writes it freely.
2. **The poet must show the numbers.** A read value may not contradict a measured one. "Low key" needs a dark midpoint in the levels.
3. **Counts are not merit.** Seven tagged elements never outrank one well-lit slow shot. The ranker reads the dimensions, never the element list.
4. **Unknown is an answer.** Untagged or log footage writes "unknown" for levels and colour rather than a confident wrong number.
5. **Variation is a diff.** Five sunset couples come out as five sentences that differ on named slots, so the reflection can say "same noun, differ on age, verb, size, arc, source."
6. **The encyclopedic layer is welcome but separate.** "This framing is common to that filmmaker's era; this is a Casablanca reference" belongs in a notes field a person or a large model may fill. It never drives the rank.

## Where this came from
Diego's words in `wp/261114-read-shots-like-a-filmmaker-v1/ledgers/quotes.jsonl` (Q009-Q013); Sexton's grammar in `inputs/editors_eye_shot_grammar.md`; Palmer's survey of fourteen published schemas in `inputs/research_shot_grammar.md` (ShotBench's vocabulary, CinemaNet's time of day, CameraBench's movement primitives; nobody had written down the subject's variation or the shot's arc as fields, so those two are Diego's).

## Known limits at 1.0 (261114), tagged for 1.1
- The read slots (subject, verb, place, mood, production value...) are `unread` until a vision model is installed (SmolVLM, see `references/vision-lane.md`); on the test drive 7 of 17 shots are therefore indistinct.
- The motion lane reads "push in" on most test-drive shots while the arc reads "held": the measured zoom sits in the 2-8 %/s band on those tiny clips. Real stock decides whether that is motion or flow noise (Spinner review; `scripts/shot_motion.py:141`).
- Orbit vs follow is decided by "DJI" in the path (`scripts/shot_arc.py:161`). Pan vs truck and push vs zoom carry the alternative in the note.
- Every threshold is a first guess until calibrated on real footage. The CinemaCLIP time-of-day probe is unrun here. Ranking (reflection), the sheet and the hand-off are 1.1.
