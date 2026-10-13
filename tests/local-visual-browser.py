"""Combined model UI in a real browser; the backend is a non-billable fixture."""
import base64
import json
import os
from pathlib import Path
import runpy
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = runpy.run_path(str(ROOT/'tests/venture-smoke.py'), run_name='visual_fixture')['fixture']
photo = base64.b64encode((ROOT/'tests/fixtures/local-visual/hopper.jpg').read_bytes()).decode()
shots = ROOT/'tests/venture-screenshots'
shots.mkdir(exist_ok=True)
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, **({'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}))
    for width in (390, 1280):
        state = {'vision':False, 'ack':False}
        def api(url, method, raw):
            path = url.split('?')[0]
            prefs = dict(settings=dict(model='qwen3.8-local', maxOutputTokens=2048, runOptions=dict(webSearch=False,codeInterpreter=False)),
                         preferenceRevision=0,defaultEpoch='',disclosureVersion='qwen-local-vision-v2',disclosureAcknowledged=state['ack'])
            if path == '/venture/models':
                data = dict(status='local-only',models=[dict(id='qwen3.8-local',contextTokens=4096,maxOutputTokens=2048,capabilities=dict(vision=state['vision']))])
            elif path == '/venture/local/disclosure':
                assert json.loads(raw)['version'] == 'qwen-local-vision-v2'
                state['ack'] = True;data = {**prefs,'disclosureAcknowledged':True}
            elif path == '/venture/preferences':data = prefs
            elif path == '/venture/conversations':data = dict(conversations=[],nextCursor=None)
            elif path == '/venture/profile':data = dict(hasAvatar=False)
            elif path == '/venture/funding':data = dict(status='local',noApiTokenCharge=True)
            elif path == '/health':data = dict(capabilities=dict(ventureV1=True,ventureV2=True))
            else:data = {}
            return dict(status=200,type='application/json',body=json.dumps(data))
        context = browser.new_context(viewport=dict(width=width,height=844))
        context.route('**/*',lambda route:route.abort())
        page = context.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.expose_function('ventureTestAPI',api)
        page.set_content(fixture,wait_until='load')
        page.locator('#runProjectBtn').click()
        page.wait_for_function("venture.models?.includes('qwen3.8-local')")
        page.evaluate('venturePaintModelPicker()')
        assert page.locator('#ventureModel').inner_text().startswith('Qwen3.8 Local')
        assert 'vision not ready' in page.locator('#ventureModel').inner_text()
        state['vision'] = True
        page.evaluate('ventureLoadModels(true)')
        assert '9B Distill + Vision' in page.locator('#ventureModel').inner_text()
        page.evaluate('void ventureLocalNotice()')
        page.locator('.venture-local-notice').wait_for(state='visible')
        assert 'run in sequence' in page.locator('.venture-local-notice').inner_text()
        assert page.locator('.venture-local-notice').bounding_box()['width'] <= width
        page.screenshot(path=str(shots/f'qwen-local-notice-{width}.png'))
        page.locator('.venture-local-notice [data-accept]').click()
        page.wait_for_function('venture.localAcknowledged===true')
        page.evaluate('''photo=>{
          const old=cloudFetch;cloudFetch=async(path,options)=>path.endsWith('/local-visual/0')?new Response(bytesFromDataURL('data:image/jpeg;base64,'+photo),{headers:{'Content-Type':'image/jpeg'}}):old(path,options);
          window.visualTestRun={runId:'fixture-run',projectId:'fixture-project',localVisualEvidence:[{id:0,name:'photo.jpg',location:'Original attachment',crop:'whole image',finding:'Protocol fixture'}]};
          void ventureOpenLocalVisual(window.visualTestRun,0);
        }''', photo)
        page.locator('.venture-local-image').wait_for(state='visible')
        page.wait_for_function("document.querySelector('.venture-local-image img').naturalWidth>0")
        page.screenshot(path=str(shots/f'qwen-local-source-{width}.png'))
        assert page.locator('.venture-local-image').bounding_box()['width'] <= width
        page.locator('.venture-local-image button').click()
        assert not errors, errors
        context.close()
    browser.close()
print('Qwen Local disclosure, conditional subtitle and source views passed at 390px and 1280px.')
