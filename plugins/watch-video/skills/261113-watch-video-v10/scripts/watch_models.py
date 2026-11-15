#!/usr/bin/env python3
"""watch_models.py - where the watch finds its MobileNet-SSD weights (watch-video plugin, 261113).

Not an engine file: annotate_frames.py (engine) keeps its own default beside itself.
This resolver looks at the scan plugin's installed weights FIRST, so a machine with
both plugins uses one copy, then falls back to this skill's own copies.

Order:
  1. $WATCH_MODELS_DIR (if set)
  2. ~/.scan-and-index/  (and its models/, models/mnssd/)
  3. the scan plugin beside this one in the repo:
     plugins/scan-and-index-hard-drives/skills/scan-and-index-hard-drives/{models,scripts}/
  4. the scan plugin in Claude Code's plugin cache:
     ~/.claude/plugins/cache/*/scan-and-index-hard-drives/*/skills/scan-and-index-hard-drives/{models,scripts}/
  5. this skill's own scripts/ (always present; shipped inside)

  python3 scripts/watch_models.py      # prints which pair would be used, and where each vision weight resolves

1.1.0 (261113): the SEE engines (DINOv2-small, YOLOX-S, MiniLM, YuNet) also ship in this skill's models/.
vision_models() reports, per bundled weight, the scan plugin's installed copy first
(~/.cache/scan-and-index-models or $SCAN_MODELS, via p0_env.model_file), then this skill's models/.
"""
import glob, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))           # .../skills/261113-watch-video-v10/scripts
SKILL = os.path.dirname(HERE)
PLUGINS = os.path.dirname(os.path.dirname(os.path.dirname(SKILL)))  # .../plugins
NAMES = ("mnssd.caffemodel", "mnssd.prototxt")
SCAN_SKILL = os.path.join("scan-and-index-hard-drives", "skills", "scan-and-index-hard-drives")


def candidates():
    out = []
    if os.environ.get("WATCH_MODELS_DIR"):
        out.append(("WATCH_MODELS_DIR", os.environ["WATCH_MODELS_DIR"]))
    home = os.path.expanduser("~/.scan-and-index")
    for d in (home, os.path.join(home, "models"), os.path.join(home, "models", "mnssd")):
        out.append(("scan plugin (~/.scan-and-index)", d))
    repo_scan = os.path.join(PLUGINS, SCAN_SKILL)
    for sub in ("models", "scripts"):
        out.append(("scan plugin (repo)", os.path.join(repo_scan, sub)))
    for root in sorted(glob.glob(os.path.expanduser(
            "~/.claude/plugins/cache/*/scan-and-index-hard-drives/*/skills/scan-and-index-hard-drives")), reverse=True):
        for sub in ("models", "scripts"):
            out.append(("scan plugin (installed)", os.path.join(root, sub)))
    out.append(("watch plugin (own copy)", HERE))
    return out


def resolve():
    """Return (caffemodel, prototxt, label). Own copy is the last resort."""
    for label, d in candidates():
        m, p = (os.path.join(d, n) for n in NAMES)
        if os.path.isfile(m) and os.path.isfile(p) and os.path.getsize(m) > 1_000_000:
            return m, p, label
    return os.path.join(HERE, NAMES[0]), os.path.join(HERE, NAMES[1]), "watch plugin (own copy, will fetch if missing)"


def vision_models():
    """{key/file: path or None}: scan's installed copy first, then this skill's bundled models/."""
    import json
    sys.path.insert(0, HERE)
    import p0_env
    models = json.load(open(os.path.join(SKILL, "rules.json")))["vision"]["models"]
    out = {}
    for key in ("dino_small", "yolox_s", "minilm", "yunet", "clip"):
        for f in models[key]["files"]:
            out["%s/%s" % (key, f["file"])] = p0_env.model_file(key, f["file"], f["bytes"])
    return out


if __name__ == "__main__":
    m, p, label = resolve()
    print("watch models: %s\n  %s\n  %s" % (label, m, p))
    for k, v in vision_models().items():
        print("  %-45s %s" % (k, v or "not installed"))
    sys.exit(0)
