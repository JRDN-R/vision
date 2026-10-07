"""Build a standalone, offline preview using the same JSON and player as Vision."""
import json
from pathlib import Path

WEB = Path(__file__).resolve().parent
data = {d: json.loads((WEB / 'animations' / f'circle-chevron-{d}-gradient-shift.json').read_text()) for d in ('right', 'left')}
player = (WEB / 'vendor/lottie_svg.min.js').read_text()
controller = (WEB / 'workspace-chevron.js').read_text().replace('/* VISION_CHEVRONS */ {}', json.dumps(data, separators=(',', ':')))
html = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vision ↔ Venture · Animated chevrons</title>
<style>
:root{font-family:system-ui,-apple-system,sans-serif;color:#eef2e9;background:#111416;color-scheme:dark}*{box-sizing:border-box}body{margin:0;min-height:100dvh;display:grid;place-items:center;padding:28px 18px}main{width:min(740px,100%)}.eyebrow{color:#c4f48a;font-size:11px;letter-spacing:.18em}h1{font-size:clamp(25px,5vw,40px);letter-spacing:-.04em;font-weight:550;margin:14px 0 12px}p{color:#a7b2a5;font-size:14px;line-height:1.7;margin:0}.icons{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:30px 0 22px}.card{border:1px solid #344032;border-radius:22px;background:#191e1a;padding:28px 12px;text-align:center}.icon{width:104px;height:104px;margin:0 auto 26px}.small{width:28px;height:28px;margin:22px auto 0}h2{font-size:15px;font-weight:550;margin:0 0 6px}.card p{font-size:12px}.actions{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:22px}button{font:inherit;font-size:13px;padding:11px 16px;border-radius:11px;border:1px solid #4c5b44;background:#20281e;color:#dfead6;cursor:pointer;min-height:44px}button:first-child{background:#c4f48a;color:#24301b;border-color:#c4f48a}button:hover{filter:brightness(1.12)}button:focus-visible{outline:2px solid #c4f48a;outline-offset:3px}.note{font-size:12px;margin-top:22px}.legend{display:flex;gap:8px;align-items:center;margin-top:20px}.dot{width:7px;height:7px;border-radius:50%;background:#c4f48a}.dot:nth-child(2){background:#ba8af4}.dot:nth-child(3){background:#f49b8a}.legend span:last-child{font-size:11px;color:#95a18e;margin-left:4px}@media(max-width:400px){.icons{gap:9px}.card{padding:25px 8px}.icon{width:86px;height:86px}.card h2{font-size:13px}}
</style><main><div class="eyebrow">VISION / VENTURE · V1.0.2.3</div><h1>One place. Both directions.</h1><p>The original reveal plays once. The outlines settle while their colors drift.</p>
<div class="icons"><section class="card"><div id="right" class="icon" aria-hidden="true"></div><h2>Open Venture →</h2><p>Circle Chevron Right</p><div id="right-small" class="small" aria-hidden="true"></div></section><section class="card"><div id="left" class="icon" aria-hidden="true"></div><h2>← Return to Vision</h2><p>Circle Chevron Left</p><div id="left-small" class="small" aria-hidden="true"></div></section></div>
<div class="legend"><span class="dot"></span><span class="dot"></span><span class="dot"></span><span>Green first · 8-second color cycle</span></div>
<div class="actions"><button id="replay" type="button">Replay entrance</button><button id="download-right" type="button">Right JSON ↓</button><button id="download-left" type="button">Left JSON ↓</button></div>
<p class="note">Large previews above, actual 28px icons below. Reduced-motion preferences show the finished, stationary artwork.</p></main>
'''
script = '''
const previews = ['right','left','right-small','left-small'].map(id => VisionChevron.create(document.getElementById(id),id.split('-')[0]));
function replay(){previews.forEach(icon=>icon.start());}document.getElementById('replay').onclick=replay;replay();
const downloadData=DATA;
for(const direction of ['right','left'])document.getElementById('download-'+direction).onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(downloadData[direction])],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='circle-chevron-'+direction+'-gradient-shift.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
'''.replace('DATA',json.dumps(data,separators=(',',':')))
(WEB / 'animations' / 'circle-chevron-preview.html').write_text(html+'<script>'+player+'\n'+controller+'\n'+script+'</script></html>')
print('Built standalone chevron preview.')
