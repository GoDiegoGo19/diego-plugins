# Default project structure and naming

```
00_CUTS/<project date YYMMDD>_cut<NN>      cuts by project date and cut number (cut01 made at build)
01_SEQUENCES/<shoot day YYMMDD>/           the breakdown:
    <YYMMDD>_<NN>_<CARD>_stringout         every master clip of one card, back to back, path order
    <YYMMDD>_INTERVIEWS                    kind = interview (scan transcribe = likely)
    <YYMMDD>_BROLL                         kind = broll
    <YYMMDD>_SYNC_<nn>                     only when 2+ cards' timecode ranges overlap that day
    <YYMMDD>_SYNC_approx_<n>               no timecode: phone clips whose creation_time ranges overlap >= 2 s
                                           (offset by creation_time difference; approx: true; check against audio)
    <YYMMDD>_VERTICAL_stringout            every vertical clip of the day, 1080x1920, day's dominant rate
02_FOOTAGE/<shoot day>/<NN>_<CARD>/        master clips; /_proxy and /_sidecar sub-bins for proxies and P2 audio;
                                           /_vertical for clips taller than wide (after the rotation tag)
03_AUDIO/<shoot day>/                      .wav .aif .mp3 .m4a ...
04_GRAPHICS/<shoot day>/                   .aep .psd .ai .svg, anything under a GRAPHICS folder, alpha video
05_STILLS/<shoot day>/                     .jpg .png .tif .heic .heif .dng ...
05_STILLS/<shoot day>/_live/               Live Photo clips: a .MOV under 3.5 s with a same-stem .HEIC/.HEIF
(not imported)                             .AAE .THM .XMP sidecars: listed in objects["sidecars"] and the report
```

- Shoot day: the QuickTime creation date (`com.apple.quicktime.creationdate`, which carries the local offset,
  else `creation_time` converted to this machine's zone); else the `YYMMDD_` folder above the card; else the
  file's mtime. Each clip records `date_source`: creation_time | folder | mtime.
- PHONE: a video with phone make/model tags, an `IMG_####`/`VID_`/`PXL_` name, or no card folder gets card
  `PHONE` (one per day, so every phone that day is in `<YYMMDD>_<NN>_PHONE_stringout`); `device` keeps the model,
  `card_folder` the folder it sat in.
- Card: the folder under the day (`FX6_A001`); `NN` = its order that day (01, 02...), sorted by name.
  Diego said "card 01..."; the name keeps both: `260901_02_FX6_A001_stringout`.
- Sequence format: most common fps/size among its clips (stringout = the card's own). Sequence rates are always
  standard (conformed); slo-mo (>= 90 fps) keeps 119.88/120/240 as its CLIP rate and votes for the day's dominant
  rate, so a 120 fps clip sits in a 29.97 sequence at its real duration.
- Vertical clips are only in `_VERTICAL_stringout`: not in card stringouts, INTERVIEWS, BROLL or organize topics.
- HDR (`color_transfer` arib-std-b67 = HLG, smpte2084 = PQ) is a flag: `hdr`, `color_space`, and a report section
  "HDR clips (set sequence color to Rec.709 or work in HLG)". Nothing is converted.
- Clips with no duration (RED .R3D unread) get a bin, no sequence.
