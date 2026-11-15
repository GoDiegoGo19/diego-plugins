// Cell.jsx — the shot cell as a satori/React component (presentation lane).
// Same visual grammar as the WeasyPrint cell, but this one is a real React
// component: it renders through satori (Yoga flexbox -> SVG) for the sheet,
// AND drops straight into the SaaS production-tools stack as a live component.
// This is the bridge — the sheet cell and the product cell are one thing.

export function Cell({ shot }) {
  const S = { slate:'#2f5688', rose:'#c76a75', sage:'#6a9e7a', ink:'#1a1a1a' };
  const sndLine = (tag, val, color) =>
    val ? ({ type:'div', props:{ style:{ display:'flex', fontSize:16, marginTop:4 },
      children:[{ type:'span', props:{ style:{ color, fontWeight:700, marginRight:6 }, children:tag } },
                { type:'span', props:{ style:{ color:'#3c3c3c' }, children:val } }] } }) : null;

  return {
    type:'div',
    props:{
      style:{ display:'flex', flexDirection:'column', width:472 },
      children:[
        { type:'img', props:{ src:shot.b64src, width:472, height:265,
          style:{ objectFit:'cover', border:'1px solid #b4b4b4' } } },
        { type:'div', props:{
          style:{ display:'flex', flexDirection:'column', borderLeft:`5px solid ${S.slate}`,
                  paddingLeft:12, marginTop:8 },
          children:[
            { type:'div', props:{ style:{ fontSize:20, fontWeight:700, color:S.ink },
              children:`SHOT ${shot.id}${shot.type?` · ${shot.type}`:''}${shot.tc?` · ${shot.tc}`:''}` } },
            shot.desc && { type:'div', props:{ style:{ fontSize:18, color:'#3c3c3c', marginTop:3 },
              children:shot.desc } },
            sndLine('IN:', shot.snd_in, S.sage),
            sndLine('OFF:', shot.snd_off, S.slate),
            sndLine('BEAT:', shot.snd_beat, S.rose),
          ].filter(Boolean) } },
      ] } };
}
