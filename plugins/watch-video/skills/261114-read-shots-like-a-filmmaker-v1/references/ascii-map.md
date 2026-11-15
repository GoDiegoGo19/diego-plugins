# The machine, in ASCII (Diego, 261114: "build me an ASCII map first")

One frame goes in. Gears deconstruct it. Every gear drops its output into a labelled bin. The bins stack into a totem pole for that shot. The pole is queried again and again; it is never rebuilt. Nothing is "good". Everything is a slider.

```
                       ONE FRAME (plus its neighbours for motion and arc)
                       ───────────────────────────────────────────────
                                            │
                                            ▼
            ┌───────────────────────────────────────────────────────────────┐
            │                        THE MACHINE                           │
            │     (gears: each takes the frame, returns one kind of fact)    │
            │                                                               │
            │  TECHNICAL gears (code, no weights, same frame → same number)  │
            │   ├─ levels         p1 / p50 / p99 IRE, clipping, transfer     │
            │   ├─ contrast       stops; lighting ratio on the face box      │
            │   ├─ colour         CCT, tint, saturation, colourfulness, 5-colour palette
            │   ├─ clarity        sharpness, sharp region, noise, blockiness │
            │   ├─ geometry       subject point vs thirds / golden, headroom, horizon tilt
            │   ├─ motion         axis rule: XY = pan/tilt or truck/pedestal (parallax decides)
            │   │                 Z  = push/pull (parallax) or zoom (none); speed; steadiness
            │   ├─ arc            head → mid → tail frames, sampled where change spikes
            │   └─ decon TOOLS    posterize · invert · black-and-white · difference matte · edges · depth
            │                     (simplifiers, used when a question needs them; never every time)
            │                                                               │
            │  MECHANICAL gears (the file and the source)                   │
            │   ├─ ffprobe        codec, chroma, bit depth, resolution, fps, timecode, transfer tag
            │   ├─ source         camera make/model, shot-on, quality class  │
            │   └─ licence        comp / licensed / screener, from the stock page or pull ledger
            │                                                               │
            │  DETECTOR gears (small weights, already installed)            │
            │   ├─ YOLOX          people and object boxes (count stays here, never reaches rank)
            │   ├─ YuNet          faces (for lighting ratio, size, headroom)  │
            │   ├─ DINOv2         look-alike families across the pool        │
            │   ├─ CLIP           tags for search only                        │
            │   └─ MiniLM         words → vectors for search only            │
            │                                                               │
            │  READER gear (a vision model, the one gear still missing)     │
            │   └─ one question per slot, closed list, measured facts in the prompt,
            │      evidence that must agree with the numbers, else "unresolved"
            │        subject · variation · verb · manner · place · angle · lighting style
            │        mood · tone · colour design · production value (4 evidence slots) · believability
            │                                                               │
            │  PERSON gear (Diego, on the top picks)                        │
            │   └─ casting · intent · interesting · the pick                 │
            └───────────────────────────────────────────────────────────────┘
                                            │
                                            ▼
      ┌─────────────────── THE BINS (one row per shot, 60+ fields, closed vocabularies) ───────────────────┐
      │                                                                                                   │
      │  TECHNICAL      MECHANICAL      CREATIVE: the film-school dimensions          ARC          SOURCE  │
      │  levels         codec           mise en scène   subject, variation, place, dressing   head  camera  │
      │  contrast       chroma          blocking        verb, manner, direction                mid   codec   │
      │  colour         bit depth       proxemics       size: EWS WS MWS MS MCU CU ECU         tail  class   │
      │  clarity        resolution      angle           eye / low / high / overhead / dutch    shape licence │
      │  geometry       fps             composition     thirds, golden, headroom, lead room    energy        │
      │  motion         timecode        camera          move + speed + steadiness                           │
      │  decon          transfer        lighting        key, ratio, direction, quality, motivation          │
      │                                 time of day     dawn / day / golden / sunset / night (from light)   │
      │                                 colour design   palette, cast, saturation                           │
      │                                 production      set, props, performers, staging (evidence each)     │
      │                                 believability   real / staged-obvious / stock-generic               │
      │                                                                                                   │
      │  every field: value · origin (M measured, R read, S source, P person) · trust word · evidence       │
      │  the SENTENCE is printed by code from the slots:                                                   │
      │  [size] [angle] [move] on [subject: variation + noun] [verb] [manner] [direction] [place],          │
      │  [time of day] ([light]), [source]; [arc shape]: [head] → [mid] → [tail]                           │
      └───────────────────────────────────────────────────────────────────────────────────────────────────┘
                                            │
                                            ▼
                  THE TOTEM POLE (one per shot; the pool is a row of poles)
                  ─────────────────────────────────────────────────────────
                      ┌──────────────┐
                      │  believable  │  high ───────────── low      ◄── sliders, not verdicts
                      ├──────────────┤
                      │  cinematic   │  high ───────────── low
                      ├──────────────┤
                      │  production  │  cinema-A ─ cinema-B ─ prosumer ─ drone ─ DSLR ─ phone
                      ├──────────────┤
                      │  energy      │  dynamic ─ modest ─ held
                      ├──────────────┤
                      │  light       │  sunset · golden · day · night ...  (+ the numbers under it)
                      ├──────────────┤
                      │  proxemics   │  EWS ... ECU
                      ├──────────────┤
                      │  subject     │  elephant · couple · doctor ...  (+ variation)
                      ├──────────────┤
                      │  place       │  Thailand river · beach · ward ...
                      ├──────────────┤
                      │  source      │  RED 444 · ARRI · DJI · iPhone ...
                      └──────────────┘
                  built once; read forever; every later question is text against these poles
                                            │
                                            ▼
   ┌───────────────────────────── THE FABRICATOR ─────────────────────────────┐
   │  the second machine: reads poles as text; never looks at pixels;         │
   │  never rebuilds a pole; reassembles the pool to the ask, again and again  │
   │                                                                          │
   │  THE ASK ──► PARSE ──► echo: "elephant · Thailand · best (cinematic ×2)   │
   │   (plain words)   │            · no phone · up to 7"  + ignored words    │
   │                   ├──► HARD FILTERS   floor · must / must-not · template  │
   │                   │                   unknown → "unconfirmed" tail       │
   │                   ├──► TEMPLATE       templates/<name>.json               │
   │                   │      best (= most dynamic, the default) · safest     │
   │                   │      most cinematic · most believable · most energy  │
   │                   │      calmest · establisher · detail · emotional beat │
   │                   │      b-roll under VO · transition                    │
   │                   │      same as this one but… · by place / time / source │
   │                   │      (the bird: silhouette · drama · reflection)     │
   │                   ├──► OVERRIDES      "<slider> high/low" → ×2 / ×½       │
   │                   └──► SWITCHES       cap_staged_obvious (on by default)  │
   │                                                                          │
   │  SCORE ──► weights × sliders ──► ties ──► two shuffled runs (UNSTABLE?)   │
   │  REASON ──► cites must_cite fields from the row; no counts; slot diff     │
   │  GROUP  ──► "by …" asks return groups, each ranked by the inner template  │
   └──────────────────────────────────────────────────────────────────────────┘
                   worked example: "the 7 clips with the most elephants-in-Thailand, cinematic high"
                        ├─ filter:   subject = elephant, place ~ Thailand        → 3 of 20 (unknown place → unconfirmed tail)
                        ├─ template: best, cinematic ×2
                        ├─ compare:  the 3 poles side by side; same noun, differ on:
                        │              1 tourist phone, handheld, midday, flat
                        │              2 professional drone, orbit, golden hour
                        │              3 cinema-A, slow push, motivated light, shallow
                        └─ answer:   3 found (never padded to 7), ranked with a reason citing ≥ 3 fields; counts never a reason
                                            │
                                            ▼
                  THE OUTPUTS (all read the same poles; none re-watches the footage)
                  ──────────────────────────────────────────────────────────
                   context sheet (PDF)   ·   selects per brief line   ·   assembly   ·   Premiere ops
                   search ("find me…")  ·   the next query, and the next, on the same poles
```

## The four rules the map enforces
1. **Gears make facts; facts fill bins; the pole is built from bins.** Nothing downstream looks at pixels again. Pay for the eyes once.
2. **Nothing is good. Everything is a slider.** "Cinematic" is a position on a scale with numbers and evidence under it, not a verdict. The brief says where on each slider it wants to be.
3. **Counts never climb the pole.** Seven tagged elements live in the detector bin for search. They are not a criterion. One well-lit slow shot through a brook outranks them if the brief asks for calm.
4. **Good is script-relative.** The fabricator reads a template (what this ask means by good) off the sliders. Default template: best = most dynamic. Three good birds are three profiles; the brief chooses. See `references/what-makes-a-good-shot.md`.

## Where each piece lives today (261114)
| Piece | File | State |
|---|---|---|
| technical gears | `scripts/shot_measure.py`, `shot_motion.py`, `shot_light.py`, `shot_decon.py` | built, tested, thresholds uncalibrated |
| arc | `scripts/shot_arc.py` | built |
| bins + sentence | `scripts/shot_ledger.py`, `shot_sentence.py`, `references/ledger-row.md`, `vocab.json` | built; 22 grammar slots; zoom/truck words being added |
| reader gear | `scripts/read_lane.py` | built; waits for a vision model on the Mac (`references/vision-lane.md`) |
| one command | `scripts/read_shots.py`, `/read-shots` | built; TEST_DRIVE_v1 → 17 poles in 34 s |
| totem pole + fabricator | `scripts/reflect.py`, `/reflect`; templates in `templates/*.json`; design in `references/what-makes-a-good-shot.md` | **built 261114**: sliders from the row's closed words, 4 templates, parse echo, hard filter vs weight, unconfirmed tail, caps, ties, UNSTABLE check, written reasons; 25 tests. The bird test reproduces: best puts drama first, a calm template flips it to reflection |
| outputs | `reflect.py --out` writes the ranked answer; sheet (P19) and the Premiere hand-off (P20) | the ranked answer is built; sheet and hand-off not built: 1.1 |
| film-theory vocabulary | `vocab.json` (nouns, verbs) + closed lists in the schema | first lists; grow by a person, never by a model |
