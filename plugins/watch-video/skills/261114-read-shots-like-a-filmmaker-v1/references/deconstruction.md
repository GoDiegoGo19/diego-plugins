# The deconstruction passes (shot_decon.py)

*Diego, Q015: "Take a keyframe, posterize it, invert it, make it black and white, do a difference matte, and you get value out
of it. I'm deconstructing the image within frameworks that are consistent across the board."*

A painter squints, flips the canvas, looks at it in a mirror, turns it to greyscale. Each trick throws away most of the picture
so one thing can be seen. A musician does the same with a chord: name the notes, and the chord is the same chord in any room.
These passes are those notes. They are deterministic (no seeds, no model): the same frame in gives the same number out, on any
machine, so two shots can be compared on the same scale. Every value is `{value, trust: "measured", rule}`.

| pass | what it does | what the row gets | why an editor cares |
|---|---|---|---|
| **posterize** | luma cut into k equal bands (default 4) | levels used, share of the largest band, where its biggest flat area sits (3x3) | the shapes under the picture: a frame that reads as two big masses (sky / ground) is a graphic frame; one that uses every band is busy |
| **invert** | the negative; the blown areas become the dark shapes | count of highlight structures (top-5% blobs), blown share, blown shape (band / blob / ragged) | what the eye goes to first is the light; a band of blown sky reads differently from a blown face |
| **black and white** | the luma histogram | shape word: bimodal / low-key / high-key / flat / full-range; tonal range in stops | the mood without the colour: low-key and bimodal frames cut together; flat ones need a grade |
| **difference matte** | first arc frame minus last, then again after taking the camera out | changed share, where it changed (3x3), attribution camera / subject / whole frame | whether the shot is about a move of the camera or a move in front of it; a held frame with a local change is a subject beat |
| **edges** | Canny + Hough lines | edge density, line shares, geometry words: horizon / verticals / diagonals / radial | the frame's skeleton: horizons calm, diagonals push, radial lines pull the eye to a point |
| **depth cue** | sharpness by 3x3 region | where focus sits, shallow / medium / deep | where the lens wants you to look, and whether the background was separated |

What these are not: a score of beauty. They are inputs the ranker and the sentence can read, the same way a key is not a judgement
of a song. Rule lines and thresholds live in the script next to each pass; changing a threshold changes every row, so it is a
versioned decision, not a per-shot tweak.
