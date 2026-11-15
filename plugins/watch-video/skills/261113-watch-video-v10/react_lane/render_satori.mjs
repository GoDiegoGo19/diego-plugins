// render_satori.mjs — presentation lane. WATCH_STATE.json -> React nodes ->
// satori (Yoga flexbox -> SVG) -> resvg (-> PNG). Browserless, real layout engine.
// Doubles as the first brick of the SaaS production-tools stack: the Cell here
// is the same component the product uses.
//
// SETUP (only when the case-use is PRESENTATION — the ~1% path):
//   npm i satori @resvg/resvg-js
//   node render_satori.mjs WATCH_STATE.json OUT_DIR
//
// Fonts: reads IBM Plex TTFs from the system path. resvg rasterizes to PNG.
// This lane is scaffolded for the presentation/product case; the WeasyPrint
// Python lane is the default for ingest and is the fully-smoke-tested path.

import fs from 'node:fs';
import satori from 'satori';
import { Resvg } from '@resvg/resvg-js';
import { Cell } from './Cell.jsx';

const FONT = '/usr/share/fonts/truetype/ibm-plex';
const state = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const outDir = process.argv[3] || '.';

const fonts = [
  { name:'Plex', data:fs.readFileSync(`${FONT}/IBMPlexSans-Regular.ttf`), weight:400, style:'normal' },
  { name:'Plex', data:fs.readFileSync(`${FONT}/IBMPlexSans-SemiBold.ttf`), weight:600, style:'normal' },
  { name:'Plex', data:fs.readFileSync(`${FONT}/IBMPlexSans-Bold.ttf`), weight:700, style:'normal' },
];

const shots = (state.scenes || []).flatMap(sc => sc.shots.map(s => ({ ...s, scene:sc.id })))
  .concat(state.shots || []);
for (const s of shots) {
  if (s.frame && fs.existsSync(s.frame))
    s.b64src = `data:image/jpeg;base64,${fs.readFileSync(s.frame).toString('base64')}`;
  if (!s.tc && s.tc_in != null) s.tc = `${s.tc_in}–${s.tc_out ?? ''}`;
}

const cols = 3, thumb = 472, gap = 22, pad = 36;
const pageW = pad*2 + cols*thumb + (cols-1)*gap;
const board = {
  type:'div',
  props:{ style:{ display:'flex', flexWrap:'wrap', gap, padding:pad, background:'#fff', width:pageW,
                  fontFamily:'Plex' },
          children: shots.map(shot => Cell({ shot })) } };

const svg = await satori(board, { width:pageW, fonts });
const png = new Resvg(svg, { background:'#ffffff' }).render().asPng();
fs.mkdirSync(outDir, { recursive:true });
fs.writeFileSync(`${outDir}/${state.job || 'sheet'}_board_react.png`.replace(/[^\w.\/-]/g,'_'), png);
console.log('rendered react board ->', outDir);
