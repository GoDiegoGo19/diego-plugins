# xmeml shapes (tier 3 fallback): what ppf_write_xmeml.py emits

Tier 3 because Premiere's XML import creates duplicate media (Diego 261112). FCP7 xmeml version 5.

```
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="5"><project><name>NAME</name><children> bins... </children></project></xmeml>
```

**Bin** (uuid = uuid5(fixed namespace, "bin|<path>"), so the same plan gives the same file):
```
<bin><uuid>...</uuid><name>02_FOOTAGE</name><children> bins | clips | sequences </children></bin>
```

**Master clip** (id `masterclip-<sha1(clip_id)[:10]>`; `mastercomment1` = the clip_id, traceable to objects.json):
```
<clip id="masterclip-a11a357ce4"><uuid/><masterclipid>masterclip-a11a357ce4</masterclipid>
 <ismasterclip>TRUE</ismasterclip><duration>192</duration><rate><timebase>24</timebase><ntsc>TRUE</ntsc></rate>
 <name>P1000001.MOV</name><labels><label2>Forest</label2></labels>
 <comments><mastercomment1>TEST_DRIVE_v1/260901_SHOOT_DAY_1/GH5S_A/P1000001.MOV</mastercomment1></comments>
 <media><video><track><clipitem id="masterclip-a11a357ce4-v0"> masterclipid name rate start end in out <file id="file-a11a357ce4"/></clipitem></track></video>
        <audio><track><clipitem id="masterclip-a11a357ce4-a1"> ... <sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack></clipitem></track></audio></media></clip>
```
Labels: interview Iris, broll Forest, other Lavender.

**File**, defined in full at its FIRST occurrence in document order, bare `<file id="..."/>` after:
```
<file id="file-a11a357ce4"><name>P1000001.MOV</name>
 <pathurl>file://localhost/abs/path/P1000001.MOV</pathurl> (percent-encoded; --path-map OLD=NEW swaps the prefix)
 <rate/><duration>192</duration>
 <timecode><rate/><string>12:00:00:00</string><frame>1036800</frame><displayformat>NDF</displayformat></timecode>
 <media><video><samplecharacteristics><rate/><width>320</width><height>180</height><pixelaspectratio>square</pixelaspectratio></samplecharacteristics></video>
        <audio><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics><channelcount>1</channelcount></audio></media></file>
```

**Sequence** (id `sequence-<sha1("seq|"+name)[:10]>`):
```
<sequence id="sequence-0a06878bab"><uuid/><name>260901_01_AERIAL_DJI_stringout</name><duration>660</duration><rate/>
 <timecode><rate/><string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>
 <media><video><format><samplecharacteristics><rate/><width/><height/><pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format>
   <track> clipitems </track> ... </video><audio><track> clipitems </track> ... </audio></media></sequence>
```

**Clip item on a timeline** (ids `clipitem-N`, in the sequence's timebase; start/end on the timeline, in/out in the source):
```
<clipitem id="clipitem-3"><masterclipid>masterclip-...</masterclipid><name/><enabled>TRUE</enabled>
 <duration/><rate/><start>0</start><end>192</end><in>0</in><out>192</out><file id="file-..."/>
 <link><linkclipref>clipitem-3</linkclipref><mediatype>video</mediatype><trackindex>1</trackindex><clipindex>1</clipindex></link>
 <link><linkclipref>clipitem-4</linkclipref><mediatype>audio</mediatype><trackindex>1</trackindex><clipindex>1</clipindex></link>
</clipitem>
```
Audio clip items add `<sourcetrack><mediatype>audio</mediatype><trackindex>k</trackindex></sourcetrack>`.
Up to 2 audio channels per clip, on A(2v-1), A(2v) for video track v (SYNC: one camera per V track).
