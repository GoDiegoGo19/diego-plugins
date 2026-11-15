#!/usr/bin/env python3
"""watch_state.py — WATCH_STATE.json cache manager (v3).

Everything renders FROM state; rendered files never get hand-edited.

Usage:
  python3 watch_state.py init --state WATCH_STATE.json --video V.mp4 --job "a client spot :30 v05" --seq 101 [--lens sfx]
  python3 watch_state.py set --state S.json --key macro.logline --value "..."
  python3 watch_state.py add-scene --state S.json --id SC01 --slug CAMPUS_AERIALS --in 0.0 --out 2.4
  python3 watch_state.py add-shot --state S.json --scene SC01 --json '{"id":"101-1","type":"WS",...}'
  python3 watch_state.py add-shots --state S.json --json SHOTS.json [--scene SC01]
      (bulk, one write: SHOTS.json is a list of shot objects, each with a "scene" key
       unless --scene is given; every scene must already exist, else nothing is written)
  python3 watch_state.py verify --state S.json --shot 101-1
  python3 watch_state.py coverage --state S.json --range 0.0-30.1 --level shot
  python3 watch_state.py ledger --state S.json --type sheet --file AIMS_Sheet_p01_slate.jpg
  python3 watch_state.py status --state S.json
"""
import json, sys, subprocess, datetime

def load(p):
    try: return json.load(open(p))
    except FileNotFoundError: return None
def save(p, s): json.dump(s, open(p,"w"), indent=2)
def arg(name, default=None):
    return sys.argv[sys.argv.index(name)+1] if name in sys.argv else default

def probe(video):
    out = subprocess.run(["ffprobe","-v","error","-show_entries",
        "format=duration,size:stream=r_frame_rate","-of","json",video],
        capture_output=True,text=True).stdout
    j = json.loads(out)
    dur = float(j["format"]["duration"]); size = int(j["format"]["size"])
    fr = j["streams"][0]["r_frame_rate"]; a,b = fr.split("/"); fps = round(float(a)/float(b),3)
    return {"path":video,"duration":round(dur,2),"fps":fps,"size":size}

def new_state(video, job="", seq="101", lens=""):
    v = probe(video)
    return {"job":job, "seq":seq, "lens":lens,
            "mode":"grid-first" if v["duration"] <= 120 else "detection-first",
            "video":v, "probe":{}, "macro":{}, "scenes":[], "frames":{},
            "coverage":[], "deliverables":[]}

def add_shots(s, items, scene=None):
    """Bulk add: file every shot under its scene in one pass. All-or-nothing: an unknown
    scene raises ValueError before anything is added. Returns the count added."""
    by = {x["id"]: x for x in s["scenes"]}
    plan = []
    for it in items:
        sh = dict(it); sid = sh.pop("scene", None) or scene
        if sid not in by: raise ValueError(f"no scene {sid} for shot {sh.get('id')}")
        sh.setdefault("verified", False); plan.append((by[sid], sh))
    for sc, sh in plan: sc["shots"].append(sh)
    return len(plan)

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h","--help","help"):
        print(__doc__); sys.exit(0 if len(sys.argv) >= 2 else 2)
    cmd = sys.argv[1]; sp = arg("--state","WATCH_STATE.json")
    if cmd == "init":
        s = new_state(arg("--video"), arg("--job",""), arg("--seq","101"), arg("--lens",""))
        v = s["video"]
        save(sp,s); print(json.dumps({"init":sp,"video":v,"mode":s["mode"]},indent=2)); return
    s = load(sp)
    if s is None: sys.exit(f"no state at {sp} — run init first")
    if cmd == "set":
        key = arg("--key"); val = arg("--value")
        try: val = json.loads(val)
        except (json.JSONDecodeError, TypeError): pass
        parts = key.split("."); ref = s
        for p in parts[:-1]: ref = ref.setdefault(p,{})
        ref[parts[-1]] = val; save(sp,s); print(f"set {key}")
    elif cmd == "add-scene":
        s["scenes"].append({"id":arg("--id"),"slug":arg("--slug",""),
            "tc_in":float(arg("--in",0)),"tc_out":float(arg("--out",0)),"shots":[]})
        save(sp,s); print(f"scene {arg('--id')} added ({len(s['scenes'])} scenes)")
    elif cmd == "add-shot":
        shot = json.loads(arg("--json")); shot.setdefault("verified", False)
        sc = next((x for x in s["scenes"] if x["id"]==arg("--scene")), None)
        if sc is None: sys.exit(f"no scene {arg('--scene')}")
        sc["shots"].append(shot); save(sp,s)
        n = sum(len(x["shots"]) for x in s["scenes"])
        print(f"shot {shot.get('id')} -> {sc['id']} ({n} shots total)")
    elif cmd == "add-shots":
        src = arg("--json")
        if not src: sys.exit("add-shots needs --json FILE")
        items = json.load(open(src))
        if isinstance(items, dict): items = items.get("shots", [])
        try: k = add_shots(s, items, arg("--scene"))
        except ValueError as e: sys.exit(str(e))
        save(sp,s)
        n = sum(len(x["shots"]) for x in s["scenes"])
        print(f"{k} shots added ({n} shots total)")
    elif cmd == "verify":
        sid = arg("--shot"); hit = False
        for sc in s["scenes"]:
            for sh in sc["shots"]:
                if sh.get("id")==sid: sh["verified"]=True; hit=True
        save(sp,s); print(f"verified {sid}" if hit else f"no shot {sid}")
    elif cmd == "coverage":
        s["coverage"].append({"range":arg("--range"),"level":arg("--level","shot")})
        save(sp,s); print("coverage logged")
    elif cmd == "ledger":
        s["deliverables"].append({"type":arg("--type"),"file":arg("--file"),
            "date":datetime.date.today().isoformat()})
        save(sp,s); print("ledgered")
    elif cmd == "status":
        shots=[sh for sc in s["scenes"] for sh in sc["shots"]]
        ver=sum(1 for sh in shots if sh.get("verified"))
        print(json.dumps({"job":s["job"],"mode":s["mode"],
            "duration":s["video"]["duration"],"scenes":len(s["scenes"]),
            "shots":len(shots),"verified":f"{ver}/{len(shots)}",
            "coverage":s["coverage"],"deliverables":s["deliverables"]},indent=2))
    else: sys.exit(f"unknown command {cmd}")

if __name__ == "__main__":
    main()
