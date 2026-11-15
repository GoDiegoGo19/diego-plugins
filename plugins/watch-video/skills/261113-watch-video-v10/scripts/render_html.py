#!/usr/bin/env python3
"""render_html.py v4 — the staging room. WATCH_STATE.json -> Jinja2 -> HTML/CSS
-> WeasyPrint PDF -> pdftoppm -> paged JPGs. A real layout engine does wrapping,
flow, and pagination — no hand-placed pixels, no collision fix-passes.

Usage:
  python3 render_html.py WATCH_STATE.json --out JOB [--per-page 6] [--cols 3]
                        [--thumb 472] [--dpi 150] [--scene SC03] [--range 15 21]
Emits: JOB_Sheet_p-1.jpg (slate), JOB_Sheet_p-2.jpg ... (boards). Keeps JOB_Sheet.pdf.
Accepts full WATCH_STATE (scenes[] wrap shots[]) or flat {"shots":[...]}.
A shot with no macros yet (or PENDING ones) prints PENDING chips; desc_stale is never printed.
(v10 scan copy: a shot's `facts` line, the ledger format line of a drive sheet, prints under the chips;
 relative frame paths resolve against the state file's folder, then the cwd.)
Frames are read from disk and base64-embedded so the HTML is self-contained.
"""
import json, sys, os, base64, subprocess
from jinja2 import Environment, FileSystemLoader

FONT_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fonts"))
TPL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates")

def arg(name, d=None):
    return sys.argv[sys.argv.index(name)+1] if name in sys.argv else d

def b64(path):
    with open(path,"rb") as f: return base64.b64encode(f.read()).decode()

def flatten(state):
    if "shots" in state and "scenes" not in state:
        return list(state["shots"])
    out=[]
    for sc in state.get("scenes",[]):
        for sh in sc["shots"]:
            sh=dict(sh); sh.setdefault("scene",sc.get("id")); out.append(sh)
    return out

def main():
    state = json.load(open(sys.argv[1]))
    base = (arg("--out","SHEET"))+"_Sheet"
    per = int(arg("--per-page",6)); thumb = int(arg("--thumb",472))
    dpi = int(arg("--dpi",150))
    cols = int(arg("--cols",3))
    page_w = 36*2 + cols*thumb + (cols-1)*22
    thumb_h = round(thumb*9/16)
    # page height: header + rows*(thumb+caption) generous; WeasyPrint flows, this just sizes the sheet
    page_h = 1500  # v6.1: header + 2 rows of tallest captioned cells; 1100 stranded board headers alone on blank pages (witnessed, AMEYALLI smiles pool 260806)

    shots = flatten(state)
    if "--scene" in sys.argv:
        shots=[s for s in shots if s.get("scene")==arg("--scene")]
    if "--range" in sys.argv:
        i=sys.argv.index("--range"); lo,hi=float(sys.argv[i+1]),float(sys.argv[i+2])
        shots=[s for s in shots if float(s.get("tc_in",0))<hi and float(s.get("tc_out",1e9))>lo]

    sdir = os.path.dirname(os.path.abspath(sys.argv[1]))  # v10: a relative frame path resolves against the STATE FILE's folder first
    for s in shots:
        fr = s.get("frame")
        cands = [fr] if fr and os.path.isabs(fr) else ([os.path.join(sdir, fr), fr, os.path.join("..", fr)] if fr else [])
        hit = next((c for c in cands if os.path.exists(c)), None)
        s["b64"] = b64(hit) if hit else ""
        if not s.get("tc") and s.get("tc_in") is not None:
            s["tc"]=f"{s['tc_in']}–{s.get('tc_out','')}"

    pages=[shots[i:i+per] for i in range(0,len(shots),per)] or [[]]
    grid = "KEYFRAMES BY FORMULA" if state.get("mode") in ("pool", "pool-sheet") else "TC ON 0.5s GRID"
    subline=f"SEQ {state.get('seq','101')} · {len(shots)} SHOTS · {len(pages)} BOARD PAGES · {grid}"

    dur = (state.get("video") or {}).get("duration")  # pool states carry video: null (v8)
    if dur:
        sec = round(dur)
        duration = f":{sec:02d}" if sec < 60 else f"{sec//60}:{sec%60:02d}"
    else:
        duration = ""

    env=Environment(loader=FileSystemLoader(TPL_DIR), autoescape=True)  # v8: filenames like "Tom & Jerry <final>.mov" must print as written
    html=env.get_template("sheet.html.j2").render(
        job=state.get("job",""), subline=subline, macro=state.get("macro"),
        board_pages=pages, closer=state.get("closer"), duration=duration,
        font_dir=FONT_DIR, page_w=page_w, page_h=page_h, thumb=thumb, thumb_h=thumb_h)

    from weasyprint import HTML
    pdf=f"{base}.pdf"
    HTML(string=html, base_url=TPL_DIR).write_pdf(pdf)
    # v10 fix: pdftoppm pads page numbers by page count (p-3 vs p-03), so a re-render with more
    # pages left the old JPGs beside the new ones. Clear this sheet's old pages first.
    stem = os.path.basename(base) + "_p-"
    for f in os.listdir(os.path.dirname(os.path.abspath(pdf)) or "."):
        if f.startswith(stem) and f.endswith(".jpg"):
            os.remove(os.path.join(os.path.dirname(os.path.abspath(pdf)) or ".", f))
    subprocess.run(["pdftoppm","-jpeg","-r",str(dpi),pdf,f"{base}_p"],check=True)
    outs=sorted(f for f in os.listdir(".") if f.startswith(os.path.basename(base)+"_p") and f.endswith(".jpg"))
    print(json.dumps({"pdf":pdf,"pages":outs}))

if __name__ == "__main__":
    main()
