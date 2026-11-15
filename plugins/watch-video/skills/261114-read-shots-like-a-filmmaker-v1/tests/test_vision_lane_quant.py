"""261114: the vision lane resolves each ONNX part on its own (Diego Mac probe 1: int8 vision encoder = ConvInteger, no CPU kernel)."""
import json,sys,tempfile,pathlib,importlib.util
S=pathlib.Path(__file__).resolve().parent.parent/"scripts"
spec=importlib.util.spec_from_file_location("rl",S/"read_lane.py"); rl=importlib.util.module_from_spec(spec); spec.loader.exec_module(rl)
fail=0
def ck(c,m):
    global fail
    print(("ok   " if c else "FAIL ")+m)
    if not c: fail+=1
d=pathlib.Path(tempfile.mkdtemp())/"smolvlm"; (d/"onnx").mkdir(parents=True)
(d/"config.json").write_text("{}"); (d/"tokenizer.json").write_text("{}")
# Diego's Mac: all three parts at int8 only
for p in rl.ONNX_PARTS: (d/"onnx"/(p+"_int8.onnx")).write_bytes(b"x")
dd,c=rl.find_model(str(d))
ck(dd is not None,"int8-only export is found, not rejected")
ck([f.name for f in c["vision_encoder"]]==["vision_encoder_int8.onnx"],"one candidate when only int8 is there")
# add the uint8 vision encoder: it must come first, decoder stays int8
(d/"onnx"/"vision_encoder_uint8.onnx").write_bytes(b"x")
dd,c=rl.find_model(str(d))
ck([f.name for f in c["vision_encoder"]][0]=="vision_encoder_uint8.onnx","uint8 vision encoder is preferred over int8 (ConvInteger has no CPU kernel)")
ck([f.name for f in c["decoder_model_merged"]][0]=="decoder_model_merged_int8.onnx","decoder keeps int8: parts are resolved independently")
# mixed suffixes across parts must still resolve (the 1.2.2 bug)
d2=pathlib.Path(tempfile.mkdtemp())/"smolvlm"; (d2/"onnx").mkdir(parents=True)
(d2/"config.json").write_text("{}"); (d2/"tokenizer.json").write_text("{}")
(d2/"onnx"/"vision_encoder_fp16.onnx").write_bytes(b"x"); (d2/"onnx"/"embed_tokens_int8.onnx").write_bytes(b"x"); (d2/"onnx"/"decoder_model_merged_q4.onnx").write_bytes(b"x")
dd,c=rl.find_model(str(d2))
ck(dd is not None,"mixed quantizations across parts resolve (1.2.2 required one shared suffix)")
# a part with no file names that part
d3=pathlib.Path(tempfile.mkdtemp())/"smolvlm"; (d3/"onnx").mkdir(parents=True)
(d3/"config.json").write_text("{}"); (d3/"tokenizer.json").write_text("{}")
(d3/"onnx"/"vision_encoder_int8.onnx").write_bytes(b"x")
dd,why=rl.find_model(str(d3))
ck(dd is None and "embed_tokens" in why and "decoder_model_merged" in why,"an incomplete export names the missing parts: "+why[:70])
# nothing installed
dd,why=rl.find_model(str(pathlib.Path(tempfile.mkdtemp())/"nope"))
ck(dd is None and "not installed" in why,"no export at all still says not installed")

# ---- 261114 probe 2 on Diego's Mac: provenance and the interior gate
class FakeScorer:
    """Scores labels from a fixture dict; stands in for SmolVLM so the rules are testable without weights."""
    def __init__(self, table): self.table = table
    def score(self, frame, prompt, labels):
        key = "indoors" if "indoors or outdoors" in prompt else "slot"
        return [self.table.get(key, {}).get(l, -9.0) for l in labels]
    def generate(self, frame, prompt, n): return "a man sits at a lit monitor"

ck(rl.ans("serene", "mood", "r")["trust"] == "approximate",
   "a model answer on a person slot is approximate, never person trust")
ck(rl.ans("serene", "mood", "r").get("awaits_person") is True,
   "a person slot is flagged as awaiting Diego")
ck("awaits_person" not in rl.ans("day", "time_of_day", "r"),
   "a machine slot carries no awaits_person flag")
ck(all(rl.ans("x", s, "r")["trust"] == "approximate" for s in rl.PERSON),
   "every one of the %d person slots reads approximate from the lane" % len(rl.PERSON))

m = FakeScorer({"indoors": {"indoors": -0.2, "outdoors": -2.0}})
r = rl.read_slot(m, "f.jpg", "time_of_day", {})
ck(r["value"] == "interior" and "gate" in r["rule"],
   "indoors ends the time-of-day question: value interior, not blue hour")
m = FakeScorer({"indoors": {"indoors": -1.0, "outdoors": -1.1}})
r = rl.read_slot(m, "f.jpg", "time_of_day", {})
ck(r["value"] == rl.U and "low margin on indoors" in (r["note"] or ""),
   "indoors and outdoors within the margin leaves time of day unresolved")
print(("%d failed" % fail) if fail else "0 failed")
sys.exit(1 if fail else 0)
