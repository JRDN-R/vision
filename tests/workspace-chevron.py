"""Real Lottie rendering: repeated idle cycles must never redraw or move the icon."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
right, left = [json.loads((ROOT / 'web/animations' / f'circle-chevron-{d}-gradient-shift.json').read_text()) for d in ('right','left')]
r, l = [data['layers'][0]['shapes'][0]['it'] for data in (right,left)]
assert r[0] == l[0]  # The circle's reveal direction stays original.
assert l[1]['ks']['k']['v'] == [[24-x,y] for x,y in r[1]['ks']['k']['v']]
assert r[2:] == l[2:]
assert right['layers'][0]['ks'] == left['layers'][0]['ks']
gradient = r[3]['g']['k']['k']
assert next(k['s'] for k in gradient if k['t']==108) == gradient[-1]['s']

with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True)
    for width in (390,1280):
        page=browser.new_page(viewport={'width':width,'height':844});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.route('**/*',lambda route:route.continue_() if route.request.url.startswith('file:') else route.abort())
        page.goto((ROOT/'web/animations/circle-chevron-preview.html').as_uri())
        page.wait_for_function('previews.every(p=>p.animation.isLoaded)')
        page.evaluate('''window.frames=[];window.loops=0;
          const a=previews[0].animation;
          a.addEventListener('loopComplete',()=>loops++);
          a.addEventListener('drawnFrame',()=>{if(a.firstFrame===108)frames.push({
            paths:[...document.querySelectorAll('#right path')].map(p=>p.getAttribute('d')),
            transforms:[...document.querySelectorAll('#right g')].map(g=>g.getAttribute('transform')),
            colors:[...document.querySelectorAll('#right stop')].map(s=>s.getAttribute('stop-color'))
          });});previews.forEach(p=>p.animation.setSpeed(12));replay();''')
        page.wait_for_function('loops>=3')
        frames=page.evaluate('frames');assert len(frames)>20
        assert all(f['paths']==frames[0]['paths'] and f['transforms']==frames[0]['transforms'] for f in frames)
        assert any(f['colors']!=frames[0]['colors'] for f in frames)
        page.emulate_media(reduced_motion='reduce')
        page.wait_for_function('previews.every(p=>p.animation.isPaused)')
        page.locator('#replay').click()
        assert page.evaluate('previews.every(p=>p.animation.isPaused && p.animation.currentFrame===108)')
        page.emulate_media(reduced_motion='no-preference')
        page.wait_for_function('previews.every(p=>p.animation.loop && !p.animation.isPaused)')
        assert not errors,errors
        print(f'{width}px: three seamless idle cycles, fixed geometry, changing color, replay and reduced motion passed')
        page.close()
    browser.close()
