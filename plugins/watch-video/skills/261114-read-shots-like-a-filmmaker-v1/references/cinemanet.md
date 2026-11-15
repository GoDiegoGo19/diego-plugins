# CinemaCLIP (CinemaNet heads): the time-of-day probe

A probe, never the call. `shot_light.py --cinemanet DIR` runs the `shot.time-of-day` head and writes `time_of_day_probe`
with `agrees_with_measured`; the measured `time_of_day` is never overwritten. Disagreement is a flag for a person (unresolved).
The lane runs without the model: the probe then writes `"not installed"` and points here.

## What it is (read from the model card, 261114)
- OZU-Technology/CinemaCLIP, fine-tune of laion/CLIP-ViT-B-32-256x256-DataComp-s34B-b86K, ~0.2B, 23 classifier heads.
- ONNX input: `Image`, 1x3x256x256 float RGB in [0,1], bicubic resize, **no mean/std normalisation**. Outputs `(emb, probs)`;
  label positions come from `CinemaNetSchema.json["probabilities_labels"]` (`shot.time-of-day.{dawn-dusk, day, night, sunrise-sunset, na}`).
- Card's own eval: Shot Time of Day 87.6 (their column), not checked by us.
- Licence: OpenRAIL-M (card metadata `license: openrail`, `LICENSE.md`). Diego still rules whether D008 ("CLIP is not a judge")
  covers a trained classifier head; until then it stays a probe.

## Pinned download (on the Mac, not in the sandbox)
Revision `9121dccaffab7813c9db23dee13cabec75bd8587` (lastModified 2026-05-23). ~176 MB for the fp16 encoder.

```bash
REV=9121dccaffab7813c9db23dee13cabec75bd8587
D=~/models/cinemaclip-$REV && mkdir -p "$D" && cd "$D"
B=https://huggingface.co/OZU-Technology/CinemaCLIP/resolve/$REV
curl -L -o ImageEncoder_fp16.onnx  "$B/ImageEncoder_fp16.onnx"
curl -L -o CinemaNetSchema.json    "$B/CinemaNetSchema.json"
curl -L -o LICENSE.md              "$B/LICENSE.md"
echo "2473197c6afd57d51c9688fd0fa3f4b2a3642664a182b93a96df9ba418987a50  ImageEncoder_fp16.onnx" | shasum -a 256 -c
# full precision instead (352 MB): ImageEncoder.onnx  sha256 054fa4ff2b850d0e15e9b6cc277b89aef4c4c7e96bb9e8cbf127286f311bc60d
shot_light.py frame.jpg --cinemanet "$D" --out light.jsonl
```
The schema and licence are small git files (no LFS sha256 on the card); pin them by the revision. The text encoder is not needed.
Requires the pinned `onnxruntime` already in the engine's lock; no torch.

## Use restrictions (LICENSE.md, Attachment A, quoted)
"You agree not to use the Artifact or its Derivatives in any of the following ways:
1. Discrimination (a) To discriminate or exploit individuals or groups based on legally protected characteristics and/or vulnerabilities.
(b) For purposes of administration of justice, law enforcement, immigration, or asylum processes, such as predicting that a natural person will commit a crime or the likelihood thereof.
2. Military (a) For weaponry or warfare.
3. Legal (a) To engage or enable fully automated decision-making that adversely impacts a natural person's legal rights without expressly and intelligibly disclosing the impact to such natural person and providing an appeal process.
(b) To engage or enable fully automated decision-making that creates, modifies or terminates a binding, enforceable obligation between entities; whether these include natural persons or not.
4. Disinformation (a) To create, present or disseminate verifiably false or misleading information for economic gain or to intentionally deceive the public, including creating false impersonations of natural persons.
(b) To synthesize or modify a natural person's appearance, voice, or other individual characteristics, unless prior informed consent of said natural person is obtained.
(c) To autonomously interact with a natural person, in text or audio format, unless disclosure and consent is given prior to interaction that the system engaging in the interaction is not a natural person.
5. Privacy (a) To utilize personal information to infer additional personal information about a natural person, including but not limited to legally protected characteristics, vulnerabilities or categories; unless informed consent from the data subject to collect said inferred personal information for a stated purpose and defined duration is received.
6. Health (a) To provide medical advice or make clinical decisions without necessary (external) accreditation of the system; [...]"

Paragraph 4 makes these mandatory and they must pass to anyone we give the model or a derivative to. For this skill: time of day
on stock frames is fine; never point any head at a person's traits (5a), which is why the lane reads only `shot.time-of-day`.
