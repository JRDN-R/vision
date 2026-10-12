"""Authenticated image-row and lightbox UI with real pixels, no model calls."""
import base64
import io
import os
from pathlib import Path
import runpy

from PIL import Image
from playwright.sync_api import expect, sync_playwright

ROOT=Path(__file__).resolve().parents[1]
fixture=runpy.run_path(str(ROOT/'tests/venture-smoke.py'),run_name='images_fixture')['fixture']
buffer=io.BytesIO()
with Image.open(ROOT/'tests/fixtures/local-visual/hopper.jpg') as image:
    image.resize((720,320)).save(buffer,'JPEG')
pixels=base64.b64encode(buffer.getvalue()).decode()
shots=ROOT/'tests/venture-screenshots';shots.mkdir(exist_ok=True)
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,**({'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}))
    for width in (390,1280):
        context=browser.new_context(viewport=dict(width=width,height=844))
        context.route('**/*',lambda route:route.abort())
        page=context.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.set_content(fixture,wait_until='load')
        page.evaluate('''pixels=>{
          window.imageRequests=[];window.imageConcurrent=0;window.imageMaxConcurrent=0;
          cloudFetch=async(path,options={})=>{
            if(!path.startsWith('/venture/conversations/image-chat/uploads/image-run/'))throw Error('Unexpected request '+path);
            imageRequests.push(path);imageConcurrent++;imageMaxConcurrent=Math.max(imageMaxConcurrent,imageConcurrent);
            await new Promise(resolve=>setTimeout(resolve,30));imageConcurrent--;
            return new Response(bytesFromDataURL('data:image/jpeg;base64,'+pixels),{headers:{'Content-Type':'image/jpeg'}});
          };
          ventureSchedule=()=>{};venture.uid='test-user';venture.ready=true;venture.open=true;
          venture.current={id:'image-chat',title:'Describe these photos'};
          venture.settings=ventureSettings({model:'qwen3.8-local',maxOutputTokens:2048,runOptions:{webSearch:false,codeInterpreter:false}});
          venture.localCapability={enabled:true,capabilities:{vision:true}};
          const run={runId:'image-run',ventureId:'image-chat',status:'in_progress',model:'qwen3.8-local',phase:'Inspecting the selected images',
            message:'Describe these photos.',text:'',sequence:1,artifacts:[],attachments:Array.from({length:8},(_,i)=>({name:'Photo '+(i+1)+'.jpg',mime:'image/jpeg',size:1234})),
            localQueue:{requestsAhead:0,waitingSeconds:2,processingSeconds:30,estimateStatus:'estimated',estimatedRemainingRange:[45,150],estimateSamples:5}};
          venture.runs=[run];$('visionVenture').hidden=false;ventureSidebar(false);venturePaintSettings();venturePaint();
        }''',pixels)
        page.wait_for_function("document.querySelector('.venture-photo img').naturalWidth>0")
        gallery=page.locator('.venture-image-row')
        assert gallery.evaluate('(el)=>el.scrollWidth>el.clientWidth')
        tile=page.locator('.venture-photo').first
        dimensions=tile.bounding_box();assert abs(dimensions['width']-dimensions['height'])<1
        assert tile.locator('img').evaluate("el=>getComputedStyle(el).objectFit")=='cover'
        assert tile.locator('img').get_attribute('src').startswith('blob:')
        assert gallery.bounding_box()['y']<page.locator('.venture-user').bounding_box()['y']
        expect(page.locator('.venture-progress-estimate')).to_contain_text('About 45s–2m 30s remaining')
        assert page.get_by_role('progressbar',name='Local answer in progress').get_attribute('aria-valuenow') is None
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(shots/f'venture-image-row-{width}.png'))
        tile.click();page.wait_for_function("document.querySelector('.venture-image-stage img').naturalWidth>0")
        viewer=page.locator('.venture-image-viewer')
        expect(viewer).to_be_visible();expect(viewer.locator('.venture-image-caption')).to_have_text('Photo 1.jpg')
        assert viewer.locator('img').get_attribute('src').startswith('blob:')
        assert viewer.locator('img').evaluate('el=>el.naturalWidth/el.naturalHeight')==720/320
        page.screenshot(path=str(shots/f'venture-image-viewer-{width}.png'))
        viewer.locator('[data-image-in]').click();expect(viewer.locator('[data-image-fit]')).to_have_text('140%')
        viewer.locator('[data-image-fit]').click();expect(viewer.locator('[data-image-fit]')).to_have_text('100%')
        page.keyboard.press('+');expect(viewer.locator('[data-image-fit]')).to_have_text('140%')
        page.keyboard.press('Escape');expect(viewer).to_have_count(0)
        tile.click();expect(page.locator('.venture-image-viewer')).to_be_visible()
        page.mouse.click(8,8);expect(page.locator('.venture-image-viewer')).to_have_count(0)
        gallery.evaluate('el=>el.scrollLeft=el.scrollWidth')
        page.wait_for_function("document.querySelectorAll('.venture-photo img')[7].naturalWidth>0")
        assert page.evaluate('imageMaxConcurrent')<=2
        page.evaluate("venture.runs[0].status='completed';venture.runs[0].text='A completed image description.';venture.runs[0].sequence++;venturePaintTurns()")
        expect(page.locator('.venture-assistant > .venture-local-progress')).to_have_count(0)
        info=page.locator('.venture-response-info')
        assert info.locator('svg').count()==1
        answer=page.locator('.venture-assistant > p').first
        before=answer.bounding_box()
        info.click();expect(page.locator('.venture-response-popover')).to_be_visible()
        expect(page.locator('.venture-response-popover')).to_contain_text('Processing 30s')
        after=answer.bounding_box();assert before==after,'Timing popover moved the answer'
        assert page.locator('.venture-response-popover').bounding_box()['width']<=width-24
        page.screenshot(path=str(shots/f'venture-response-info-{width}.png'))
        page.mouse.click(8,8);expect(page.locator('.venture-response-popover')).to_have_count(0)
        info.click();page.keyboard.press('Escape');expect(page.locator('.venture-response-popover')).to_have_count(0)
        page.locator('.venture-photo').last.click();expect(page.locator('.venture-image-viewer')).to_be_visible()
        page.evaluate('testSignOut()')
        expect(page.locator('.venture-image-viewer')).to_have_count(0)
        assert page.evaluate('ventureImageState.cache.size')==0
        assert not errors,errors
        context.close()
    browser.close()
print('Square gallery, scroll, authenticated blobs, zoom, close and sign-out passed at 390px and 1280px.')
