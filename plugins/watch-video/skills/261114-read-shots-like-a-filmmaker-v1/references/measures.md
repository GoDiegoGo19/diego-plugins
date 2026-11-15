# The measures (shot_measure.py): formula, source, trust

Source for every row: Palmer, Table A, `wp/261114-read-shots-like-a-filmmaker-v1/inputs/research_dimensions_and_vision_lane.md`.
Each value in measures.jsonl is `{"value", "trust", "rule"[, "note"]}`; trust is trusted / approximate / person (Sexton's machine / machine~ / person).
`transfer` (Rec.709 / log / HLG / unknown) rides on every row: ffprobe `color_transfer`, else the camera sidecar XML gamma, else `--assume-transfer`
(recorded as assumed). On log or unknown, levels, contrast, CCT and lighting ratio write `"unknown"` with a note.

| measure | formula in one line | Palmer row | trust | status |
|---|---|---|---|---|
| levels | Rec.709 Y' = .2126R+.7152G+.0722B; p1/p50/p99 as IRE = (Y-16)/219*100; % at code <=16 and >=235; key low <30 / high >65 IRE p50 | Luma levels | trusted | built |
| contrast | RMS of Y'/255; log2(p95/p5) of linear luma (Y'/255)^2.2, in stops | Contrast | trusted | built |
| lighting_ratio | log2 of mean linear luma, brighter vs darker half (left/right or top/bottom, the larger split) of the largest YuNet face, else YOLOX person box; else unknown | Lighting ratio | approximate | built (face/person); eye-nose split line not built |
| cct | bright-neutral pixels (S<.25, V p40-97; else grey-world) -> linear sRGB -> XYZ -> xy -> McCamy cubic; Duv from Krystek's Planckian locus, + green / - magenta; `cast_estimate` | Colour temperature | approximate | built |
| saturation | HSV-S mean and p90; Hasler-Suesstrunk M = sqrt(std_rg^2+std_yb^2) + 0.3 sqrt(mean_rg^2+mean_yb^2) | Saturation + colourfulness | trusted | built |
| palette | cv2.kmeans k=5 on Lab at 160 px (seeded); share per colour; chroma-weighted circular std of hue | Palette | trusted | built |
| sharpness | variance of Laplacian over the frame and max over a 3x3 grid (`sharp_region`) | Sharpness | approximate | built |
| noise | Immerkaer 1996: sigma = sqrt(pi/2)/(6(W-2)(H-2)) * sum abs(I * [[1,-2,1],[-2,4,-2],[1,-2,1]]) | Noise (estimate_sigma stand-in; no skimage) | trusted | built |
| clipping | % pixels at 254+ in any / all channels, % at 0-1 | Clipping | trusted | built |
| motion | Farneback flow at 320 px; RANSAC partial affine = camera (dx, dy, zoom, rot); p90 residual = subject; % of width per second | Motion | approximate | built (needs two frames) |
| shake | mean change in camera translation between successive pairs | Shake | approximate | built (needs three frames); LK jitter frequency not built |
| subject | largest YuNet face, else YOLOX-S person, else spectral-residual saliency (Hou & Zhang 2007, numpy FFT, 95th-percentile weighted centroid) | Subject point | approximate | built |
| framing_geometry | distance from subject point to nearest thirds / phi (0.382, 0.618) intersection and to centre, % of diagonal; geometry only, never a score | Rule of thirds and golden points | approximate | built |
| headroom | gap from box top to frame top, % of box height | Headroom | approximate | built |
| lead room | facing direction from YuNet landmarks | Lead room | | probe, not built |
| horizon | Canny + HoughLinesP; length-weighted angle of lines within 20 deg of level; confidence from total length and spread | Horizon tilt | trusted | built |
| fidelity | ffprobe codec, size, fps, pix_fmt, bit depth, chroma, bits per pixel, color_transfer/primaries; blockiness = mean abs dY at 8-px column edges / elsewhere | Fidelity facts | trusted (facts) | facts built; blockiness probe; maybe_upscaled not built |

YuNet and YOLOX-S are reached only through the watch skill's engine helpers (`p3_see_models.mpath / session / V`). The engine's `people()`
returns count and height only, so shot_measure decodes boxes from the engine's own session with the same decode. Nothing in the ENGINE list is edited.
