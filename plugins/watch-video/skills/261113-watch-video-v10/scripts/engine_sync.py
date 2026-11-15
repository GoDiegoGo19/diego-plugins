#!/usr/bin/env python3
"""engine_sync.py — one watch engine, two standalone skills (Diego, 261107).

The scan skill and the watch skill install separately, but the watch ENGINE inside
them must be byte-for-byte the same: witness, macros, objects, pool frames, renderer,
sheet template, shot contract, fonts, model, and the engine's own tests. Each skill
carries ENGINE.json (a fingerprint of every engine file) so it can prove, standalone,
that its copy is the engine it claims to be.

  python3 scripts/engine_sync.py check                 # this skill's files match its ENGINE.json
  python3 scripts/engine_sync.py manifest              # rewrite ENGINE.json from the files (after an engine edit)
  python3 scripts/engine_sync.py compare <other_skill> # both skills carry the same engine + shared rules
  python3 scripts/engine_sync.py push <other_skill>    # copy the engine (+ shared rules keys) into the other skill

Rule: edit the engine in ONE skill, run `manifest`, then `push` to the other, then run
both skills' gates. Skill-only files (pipeline, p0..p5, drive_objects, SKILL.md, the
skill's own rules keys such as `red`, `lanes`, `transcribe_tag`) are never touched.
Exit codes: 0 ok, 1 drift found, 2 usage.
"""
import hashlib, json, os, shutil, sys

ENGINE_VERSION = "1"   # bump when the engine changes in a way callers must know about
ENGINE_FILES = [
    "scripts/annotate_frames.py", "scripts/macros.py", "scripts/objects.py",
    "scripts/pool_frames.py", "scripts/render_html.py", "scripts/watch_state.py",
    "scripts/extract_frames.py", "scripts/detect_cuts.py",
    "scripts/mnssd.prototxt", "scripts/mnssd.caffemodel", "scripts/engine_sync.py", "scripts/p0_machine.py",
    "templates/sheet.html.j2", "references/rules-and-objects.md",
    "react_lane/Cell.jsx", "react_lane/render_satori.mjs",
    "fonts/OstrichSans-Medium.otf", "fonts/Montserrat-Regular.ttf",
    "fonts/Montserrat-SemiBold.ttf", "fonts/Montserrat-Bold.ttf",
    "tests/test_macros.py", "tests/test_objects.py", "tests/test_witness_v8.py",
    "tests/test_engine.py", "tests/test_machine.py",
    # v17 / watch 1.1.0 (261113): the SEE lane (vision engines) and the bundled weights, carried by both skills
    "scripts/p0_env.py", "scripts/p3_see_models.py", "scripts/p3_see_segments.py",
    "scripts/p3_process_frames.py", "scripts/p3b_driver_red.py", "scripts/p4_sort_families.py",
    "scripts/p3_quick_premise.py",
    "models/dino_small/onnx/model_quantized.onnx", "models/dino_small/preprocessor_config.json",
    "models/yolox_s/yolox_s.onnx", "models/minilm/onnx/model_quantized.onnx", "models/minilm/tokenizer.json",
    "models/yunet/face_detection_yunet_2023mar.onnx",
]
# rules.json keys the engine reads; they must be equal in both skills.
SHARED_RULE_KEYS = ["keyframes", "M1_shot_size", "M2_motion", "M3_speech", "M4_role",
                    "M5_light", "M6_text", "M7_tags", "M8_dupes", "flat", "witness",
                    "W5_simulant_words", "vision"]

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the skill root


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 16), b""):
            h.update(blk)
    return h.hexdigest()


def fingerprints(root):
    out, missing = {}, []
    for rel in ENGINE_FILES:
        p = os.path.join(root, rel)
        if os.path.exists(p):
            out[rel] = sha(p)
        else:
            missing.append(rel)
    return out, missing


def shared_rules(root):
    p = os.path.join(root, "rules.json")
    try:
        with open(p) as f:
            r = json.load(f)
    except (OSError, ValueError) as e:
        return None, str(e)
    return {k: r.get(k) for k in SHARED_RULE_KEYS}, None


def manifest(root):
    fp, missing = fingerprints(root)
    rules, err = shared_rules(root)
    m = {"engine_version": ENGINE_VERSION, "files": fp,
         "shared_rules_sha": hashlib.sha256(json.dumps(rules, sort_keys=True).encode()).hexdigest() if rules else None}
    with open(os.path.join(root, "ENGINE.json"), "w") as f:
        json.dump(m, f, indent=1, sort_keys=True)
        f.write("\n")
    return m, missing


def check(root):
    """Standalone proof: the files on disk are the ones ENGINE.json fingerprints."""
    p = os.path.join(root, "ENGINE.json")
    if not os.path.exists(p):
        return ["ENGINE.json missing — run: engine_sync.py manifest"]
    with open(p) as f:
        m = json.load(f)
    fp, missing = fingerprints(root)
    bad = ["missing engine file: " + r for r in missing]
    for rel, h in m["files"].items():
        if fp.get(rel) != h:
            bad.append("changed since manifest: " + rel)
    for rel in fp:
        if rel not in m["files"]:
            bad.append("not in manifest: " + rel)
    rules, err = shared_rules(root)
    if err:
        bad.append("rules.json unreadable: " + err)
    elif m.get("shared_rules_sha") != hashlib.sha256(json.dumps(rules, sort_keys=True).encode()).hexdigest():
        bad.append("shared rules.json keys changed since manifest")
    return bad


def compare(a, b):
    bad = []
    fa, ma = fingerprints(a)
    fb, mb = fingerprints(b)
    for rel in ENGINE_FILES:
        if fa.get(rel) != fb.get(rel):
            bad.append("engine file differs: " + rel)
    ra, _ = shared_rules(a)
    rb, _ = shared_rules(b)
    for k in SHARED_RULE_KEYS:
        if (ra or {}).get(k) != (rb or {}).get(k):
            bad.append("shared rules key differs: " + k)
    return bad


def push(src, dst):
    if not os.path.isdir(dst) or not os.path.exists(os.path.join(dst, "SKILL.md")):
        print("USAGE: push target is not a skill folder: " + dst)
        sys.exit(2)
    for rel in ENGINE_FILES:
        s = os.path.join(src, rel)
        if os.path.exists(s):
            os.makedirs(os.path.dirname(os.path.join(dst, rel)), exist_ok=True)
            shutil.copy2(s, os.path.join(dst, rel))
    rs, _ = shared_rules(src)
    dp = os.path.join(dst, "rules.json")
    with open(dp) as f:
        rd = json.load(f)
    for k in SHARED_RULE_KEYS:
        if rs and k in rs:
            rd[k] = rs[k]
    with open(dp, "w") as f:
        json.dump(rd, f, indent=2)
        f.write("\n")
    manifest(dst)


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0 if a else 2)
    cmd = a[0]
    if cmd == "manifest":
        m, missing = manifest(HERE)
        print("ENGINE.json written: %d files%s" % (len(m["files"]),
              ("; MISSING " + ", ".join(missing)) if missing else ""))
        sys.exit(1 if missing else 0)
    if cmd == "check":
        bad = check(HERE)
        print("engine: OK (%d files match ENGINE.json)" % len(ENGINE_FILES) if not bad else "engine: DRIFT\n  " + "\n  ".join(bad))
        sys.exit(1 if bad else 0)
    if cmd in ("compare", "push"):
        if len(a) < 2:
            print("USAGE: engine_sync.py %s <other_skill_folder>" % cmd)
            sys.exit(2)
        other = os.path.abspath(a[1])
        if cmd == "push":
            manifest(HERE)
            push(HERE, other)
        bad = compare(HERE, other)
        print("engine: IDENTICAL in both skills" if not bad else "engine: DIFFERS\n  " + "\n  ".join(bad))
        sys.exit(1 if bad else 0)
    print("USAGE: check | manifest | compare <skill> | push <skill>")
    sys.exit(2)


if __name__ == "__main__":
    main()
