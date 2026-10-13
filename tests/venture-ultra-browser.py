"""Real Venture controls/branding in Chromium with an explicit benchmark fixture."""
import json
import os
from pathlib import Path
import runpy
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
fixture=runpy.run_path(str(ROOT/'tests/venture-smoke.py'),run_name='ultra_fixture')['fixture']
controller=runpy.run_path(str(ROOT/'web/build.py'))['qwen_animation_script']()
fixture=fixture.replace('<script>', '<script>'+(ROOT/'web/vendor/lottie_svg.min.js').read_text()+controller,1)
shots=ROOT/'tests/venture-screenshots';shots.mkdir(exist_ok=True)
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,**({'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}))
    for width in (390,1280):
        settings=dict(model='qwen3.8-local',maxOutputTokens=2048,memoryEnabled=False,
                      runOptions=dict(mode='auto',effort='auto',verbosity='auto',webSearch=False,codeInterpreter=False))
        profiles={};revision=0;notice_due=True;notice_posts=0
        perf={k:dict(threads=t,gpuLayers=g,benchmark=dict(loadSeconds=10,promptTokensPerSecond=80,generationTokensPerSecond=4)) for k,t,g in [('auto',12,4),('cpu',8,0)]}
        ultra=dict(id='qwen-ultra-local',enabled=True,contextTokens=8192,maxOutputTokens=4096,
                   contexts=[4096,8192],performance=perf,capabilities=dict(vision=False))
        local=dict(id='qwen3.8-local',enabled=True,contextTokens=4096,maxOutputTokens=2048,
                   capabilities=dict(vision=True),ultra=ultra)
        def api(url,method,raw):
            global settings,profiles,revision,notice_due,notice_posts
            path=url.split('?')[0];body=json.loads(raw or '{}')
            if path=='/venture/models':data=dict(status='local-only',models=[local,ultra])
            elif path=='/venture/ultra/notice':
                if method=='POST':notice_due=False;notice_posts+=1
                data=dict(due=notice_due)
            elif path=='/venture/preferences':
                if method=='PUT':
                    profiles[settings['model']]=settings;settings=body.get('settings',body)
                    profiles[settings['model']]=settings;revision+=1
                data=dict(settings=settings,profiles=profiles,preferenceRevision=revision,defaultEpoch='',disclosureVersion='qwen-local-vision-v2',disclosureAcknowledged=True)
            elif path=='/venture/conversations':data=dict(conversations=[],nextCursor=None)
            elif path=='/venture/profile':data=dict(hasAvatar=False)
            elif path=='/venture/funding':data=dict(status='local',noApiTokenCharge=True)
            elif path=='/health':data=dict(capabilities=dict(ventureV1=True,ventureV2=True))
            else:data={}
            return dict(status=200,type='application/json',body=json.dumps(data))
        context=browser.new_context(viewport=dict(width=width,height=900))
        context.route('**/*',lambda route:route.abort())
        page=context.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.expose_function('ventureTestAPI',api);page.set_content(fixture)
        page.locator('#runProjectBtn').click();page.wait_for_function('venture.ready && venture.models?.length===2')
        page.locator('#ventureSettingsToggle').click();page.locator('#ventureModel').click()
        page.locator('[data-model="qwen-ultra-local"]').click()
        page.locator('.venture-ultra-notice').wait_for(state='visible')
        assert 'significantly longer' in page.locator('#ventureUltraNoticeCopy').inner_text()
        assert 'ETA' not in page.locator('.venture-ultra-notice').inner_text()
        page.wait_for_function("document.querySelector('.venture-ultra-coffee').naturalWidth>0")
        page.screenshot(path=str(shots/f'qwen-ultra-notice-{width}.png'))
        page.locator('.venture-ultra-notice [data-accept]').click()
        page.wait_for_function("venture.settings.model==='qwen-ultra-local'")
        assert notice_posts==1
        assert 'Qwen Ultra' in page.locator('#ventureModelLabel').inner_text()
        assert page.locator('#ventureModelLabel .model-qwen-ultra').count()==1
        assert page.locator('#ventureUncensoredRow').is_hidden()
        assert page.locator('#ventureUltraControls').is_visible()
        assert page.locator('#ventureUltraThinking').is_checked()
        assert page.locator('#ventureMaxTokens').get_attribute('max')=='4096'
        assert page.locator('#ventureUltraContext option').all_text_contents()==['4,096 tokens','8,192 tokens']
        assert page.locator('#ventureWeb').is_disabled() and page.locator('#ventureCode').is_disabled()
        assert page.locator('#ventureUltraWait').count()==0
        page.locator('#ventureUltraThinking').uncheck()
        page.locator('#ventureUltraContext').select_option('4096')
        page.locator('#ventureUltraPerformance').select_option('cpu')
        assert page.locator('#ventureMaxTokens').get_attribute('max')=='3520'
        assert '3,520 output tokens within 4,096 context tokens' in page.locator('#ventureParameterDetails').text_content()
        assert '8 CPU threads · 0 GPU layers' in page.locator('#ventureUltraHardware').inner_text()
        page.locator('#ventureUltraControls summary').click()
        page.locator('#ventureUltra_temperature').fill('0.25')
        page.locator('#ventureUltraSeed').fill('731')
        page.wait_for_function('venture.savedSettingsVersion===venture.settingsVersion')
        saved=page.evaluate('venture.settings.qwenUltra')
        assert saved['temperature']==.25 and saved['seed']==731 and saved['thinking'] is False
        page.locator('#ventureModel').click();page.locator('[data-model="qwen3.8-local"]').click()
        assert page.locator('#ventureUltraControls').is_hidden()
        page.locator('#ventureModel').click();page.locator('[data-model="qwen-ultra-local"]').click()
        page.wait_for_function("venture.settings.model==='qwen-ultra-local'")
        assert page.locator('.venture-ultra-notice').count()==0 and notice_posts==1
        assert page.evaluate('venture.settings.qwenUltra')==saved
        page.wait_for_function("document.querySelector('#ventureModelLabel venture-qwen-symbol')?.ready")
        assert page.locator('#ventureModelLabel .venture-model-name').evaluate('(el)=>getComputedStyle(el).animationName')=='qwen-ultra-shimmer'
        page.emulate_media(reduced_motion='reduce')
        assert page.locator('#ventureModelLabel .venture-model-name').evaluate('(el)=>getComputedStyle(el).animationName')=='none'
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        box=page.locator('#ventureSettings').bounding_box();assert box['x']>=0 and box['x']+box['width']<=width
        page.locator('#ventureSettings').evaluate('(el)=>el.scrollTop=0')
        page.screenshot(path=str(shots/f'qwen-ultra-{width}.png'))
        page.locator('#ventureSettingsClose').click()
        page.screenshot(path=str(shots/f'qwen-ultra-coffee-{width}.png'))
        # A later server-side expiry prompts again, regardless of the browser clock.
        notice_due=True
        page.evaluate('void ventureLocalNotice()')
        page.locator('.venture-ultra-notice [data-accept]').click()
        page.locator('.venture-ultra-notice').wait_for(state='detached')
        assert notice_posts==2
        page.evaluate('''() => {
          const run={runId:'ultra-progress',model:'qwen-ultra-local',status:'in_progress',text:'',phase:'Thinking',message:'Explain gravity',attachments:[],artifacts:[],runOptions:{},sequence:1,localQueue:{processingSeconds:0,estimatedTotalSeconds:100,estimatedCompletionAt:Date.now()/1000+100,estimateStatus:'estimated',estimateBasis:'benchmark'}};
          venture.runs=[run];venturePaint();window.ultraRun=run;
        }''')
        bar=page.locator('.venture-progress-track.is-ultra')
        assert bar.get_attribute('aria-valuenow')=='0'
        assert 'Estimated completion' in page.locator('.venture-progress-estimate').inner_text()
        page.evaluate('ultraRun.localQueue.processingSeconds=40;venturePaintTurns()')
        assert bar.get_attribute('aria-valuenow')=='40'
        assert bar.locator('span').evaluate('(e)=>getComputedStyle(e).animationName')=='none'
        page.evaluate('ultraRun.localQueue.processingSeconds=20;venturePaintTurns()')
        assert bar.get_attribute('aria-valuenow')=='40'
        page.evaluate("ultraRun.localQueue.processingSeconds=150;ultraRun.localQueue.estimateStatus='overrun';venturePaintTurns()")
        assert bar.get_attribute('aria-valuenow')=='97'
        assert 'Taking longer' in page.locator('.venture-progress-estimate').inner_text()
        assert '%' not in page.locator('.venture-ultra-loading').inner_text()
        assert page.locator('#ventureStop').is_visible()
        page.screenshot(path=str(shots/f'qwen-ultra-progress-{width}.png'))
        page.evaluate("ultraRun.status='completed';ultraRun.text='Gravity attracts objects.';venturePaint()")
        assert bar.get_attribute('aria-valuenow')=='100'
        assert page.locator('#ventureStop').is_hidden()
        assert not errors,errors
        context.close()
    browser.close()
print('Ultra: gold animated logo, reduced motion, mobile layout, bounded controls, preference restoration daily popup, monotonic progress and clock ETA passed.')
