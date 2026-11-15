#!/usr/bin/env python3
"""witness.py - run the machine witness (annotate_frames.py, engine) with the plugin's model lookup.

Same flags as annotate_frames.py. The only difference: the MobileNet-SSD weights come from
watch_models.resolve() (scan plugin first, then this skill's own copies), and the choice is
printed to stderr on the first line.

  python3 scripts/witness.py --state WATCH_STATE.json
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watch_models
import annotate_frames as af

m, p, label = watch_models.resolve()
af.SSD_M, af.SSD_P = m, p
af.SSD_URLS = [(u, m if path.endswith(".caffemodel") else p) for u, path in af.SSD_URLS]
print("watch models: %s (%s)" % (label, os.path.dirname(m)), file=sys.stderr)

if __name__ == "__main__":
    af.main()
