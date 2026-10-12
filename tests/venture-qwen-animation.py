"""Real bundled Lottie playback: order, visibility, reduced motion and cleanup."""
import os
import runpy
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
read = lambda name: (ROOT / name).read_text()
controller = runpy.run_path(str(ROOT / 'web/build.py'))['qwen_animation_script']()
script = '\n'.join([
    read('web/vendor/lottie_svg.min.js'), controller,
    "const venture={};function escapeHTML(s){return String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');}",
    read('web/venture-models.js'), read('web/venture-local.js'),
    "function paint(){document.querySelectorAll('[data-badge]').forEach(el=>el.innerHTML=ventureModelBadge('qwen3.8-local'));}paint();",
])
fixture = '''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>''' + read('web/styles.css') + read('web/venture.css') + '''
body{padding:24px;background:#111314}.venture-root{position:static;display:block;padding:20px;border:1px solid #394231;border-radius:18px;max-width:500px}
.preview-row{margin:20px 0}.venture-model-select{max-width:350px}
</style><section class="venture-root" id="preview">
<div class="preview-row"><button class="venture-model-button" data-badge></button></div>
<div class="preview-row"><button class="venture-model-select" data-badge></button></div>
<div class="preview-row venture-response-meta" data-badge></div>
</section><script>''' + script + '</script>'

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, **({'executable_path': os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}))
    for width in (390, 1280):
        page = browser.new_page(viewport={'width': width, 'height': 844})
        errors, requests = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/*', lambda route: (requests.append(route.request.url), route.abort()))
        page.set_content(fixture)
        page.wait_for_function("[...document.querySelectorAll('venture-qwen-symbol')].every(el=>el.ready && el.visible)")
        page.evaluate("window.symbol=document.querySelector('venture-qwen-symbol');window.order=[];symbol.players.forEach((p,i)=>{p.setSpeed(20);p.addEventListener('complete',()=>order.push(i+1));});")
        page.wait_for_function('order.length>=4')
        assert page.evaluate('order.slice(0,4)') == [1, 2, 1, 2]
        assert page.evaluate('symbol.players.every(p=>!p.loop)')
        assert page.locator('.venture-model-name').first.inner_text() == 'Qwen3.8 Local'
        assert page.locator('.venture-model-name').first.evaluate('(el)=>getComputedStyle(el).color') == 'rgb(72, 125, 255)'
        local = page.locator('.venture-model-local').first
        assert local.evaluate('(el)=>[getComputedStyle(el).color,getComputedStyle(el).fontWeight,getComputedStyle(el).fontStyle]') == ['rgb(196, 137, 44)', '400', 'italic']
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')

        # Hidden workspaces/pickers pause without dropping their place in the sequence.
        page.locator('#preview').evaluate('(el)=>el.hidden=true')
        page.wait_for_function('symbol.players.every(p=>p.isPaused)')
        page.locator('#preview').evaluate('(el)=>el.hidden=false')
        page.wait_for_function('!symbol.players[symbol.current].isPaused')
        page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));")
        assert page.evaluate('symbol.players.every(p=>p.isPaused)')
        page.evaluate("delete document.hidden;document.dispatchEvent(new Event('visibilitychange'));")
        page.wait_for_function('!symbol.players[symbol.current].isPaused')

        page.emulate_media(reduced_motion='reduce')
        page.wait_for_function('symbol.current===0 && symbol.players.every(p=>p.isPaused && p.currentFrame===0)')
        shots = ROOT / 'tests/venture-screenshots'
        shots.mkdir(exist_ok=True)
        page.screenshot(path=str(shots / f'qwen-animation-{width}.png'))
        page.emulate_media(reduced_motion='no-preference')
        page.wait_for_function('!symbol.players[0].isPaused')

        # Repainting model labels must release old players, including a rapid repaint during load.
        page.evaluate('paint();paint();')
        page.wait_for_function("[...document.querySelectorAll('venture-qwen-symbol')].every(el=>el.ready)")
        assert page.evaluate('lottie.getRegisteredAnimations().length') == 6
        page.locator('#preview').evaluate('(el)=>el.remove()')
        assert page.evaluate('lottie.getRegisteredAnimations().length') == 0
        assert not requests, requests
        assert not errors, errors
        print(f'{width}px: p1/p2/p1/p2, offline images, label styling, visibility, reduced motion and cleanup passed')
        page.close()
    browser.close()
