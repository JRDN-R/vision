"""No live login, no API keys and no paid requests: exercise the actual HTML and Run UI."""
from pathlib import Path
import json, os, re
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[1]
def read(name): return (ROOT/name).read_text()
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True, **({'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}))
    try:
        # Fully bundled shell renders without any remote JavaScript downloads.
        page=browser.new_page();errors=[];remote=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        shell=read('Vision.html')
        def shell_route(route):
            if route.request.url.startswith('file:'): route.continue_()
            elif route.request.url=='https://vision.test/': route.fulfill(status=200,content_type='text/html',body=shell)
            else:
                remote.append((route.request.resource_type,route.request.url));route.abort()
        page.route('**/*',shell_route)
        if os.environ.get('VISION_TEST_FILE')=='1':
            page.goto((ROOT/'Vision.html').as_uri())
        else:
            # Container browser policy can block file:; CI covers the literal file URL.
            shell=shell.replace("location.protocol==='file:'",'true').replace("location.protocol!=='file:'",'false')
            page.set_content(shell,wait_until='load')
        page.wait_for_timeout(900)
        assert page.evaluate('!!window.VisionFirebaseSDK'), 'Firebase runtime is missing'
        assert page.locator('#accountGateEmailArea').is_visible(), 'Local email sign-in is hidden'
        assert page.locator('#accountGateSignIn').is_visible(), 'Local Google sign-in is hidden'
        assert not any(t in ('script','stylesheet','font') for t,_ in remote), remote
        assert not errors, errors
        page.close()
        base=read('web/base.js');download=base[base.index('function download('):].split('\n')[0]
        code="""
const $=id=>document.getElementById(id),state={title:'Test',nodes:[],settings:{},consoleSession:null};let busy=false,ioBusy=false;
const R={safeFilename:n=>n};function escapeHTML(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function bytesFromDataURL(s){return Uint8Array.from(atob(s.split(',')[1]),c=>c.charCodeAt(0));}
function markDirty(){}function toast(s){window.testToast=s;}function checkpoint(){}function refreshExport(){}function renderAll(){renderConsole();}
"""+download+'\n'+read('web/console.js')+'\n'+read('web/run-tools.js')+"""
window.seed=()=>{state.consoleSession=normalizeConsoleSession({runOptions:{mode:'pro',effort:'max',verbosity:'high',webSearch:true},runs:[{runId:'r',status:'completed',model:'custom-model-id',text:'[Open answer](sandbox:/mnt/data/answer.html)',artifacts:[{id:'a',name:'answer.html',mime:'text/html',data:'data:text/html;base64,'+btoa('<h1>Test preview</h1><'+'script>window.ran=true;try{parent.stolen=true}catch(e){}<'+ '/script>'),ready:true}]}]});consoleRestoreRunOptions();renderConsole();};
window.getOptions=()=>state.consoleSession.runOptions;
"""
        # Escape embedded script terminators in the JavaScript fixture's artifact payload.
        fixture='<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>'+read('web/styles.css')+read('web/console.css')+read('web/run-tools.css')+'</style></head><body><button id="runProjectBtn">Run</button><input id="screenshotMode" hidden><section id="visionConsole"></section><script>'+code+'</script></body></html>'
        for width in [390,1280]:
            context=browser.new_context(viewport={'width':width,'height':844},accept_downloads=True)
            context.route('**/*',lambda r:r.fulfill(status=200,content_type='text/html',body=fixture) if r.request.url=='https://run.test/' else r.abort())
            p=context.new_page();errors=[];p.on('pageerror',lambda e:errors.append(str(e)));p.set_content(fixture,wait_until='load');assert not errors,errors;p.evaluate('seed()')
            p.locator('#consoleSettingsToggle').click();assert p.locator('#consoleMode').input_value()=='pro';assert p.locator('#consoleEffort').input_value()=='max';assert p.locator('#consoleWebSearch').is_checked()
            p.locator('#consoleVerbosity').select_option('low');assert p.evaluate('getOptions().verbosity')=='low'
            p.locator('#consoleSettingsToggle').click();p.locator('.console-artifact-link').click()
            frame=p.frame_locator('#consolePreviewBody iframe');assert frame.locator('h1').inner_text()=='Test preview'
            assert p.locator('#consolePreviewBody iframe').get_attribute('sandbox')==''
            p.locator('#consolePreviewScripts').check();assert p.locator('#consolePreviewBody iframe').get_attribute('sandbox')=='allow-scripts'
            p.wait_for_timeout(100);assert not p.evaluate('!!window.stolen'), 'Preview accessed parent'
            with p.expect_download() as result:p.locator('#consolePreviewDownload').click()
            assert result.value.suggested_filename=='answer.html'
            assert '<h1>Test preview</h1>' in Path(result.value.path()).read_text()
            p.locator('#consolePreviewClose').click();p.locator('#consoleFileBagButton').click();assert 'answer.html' in p.locator('#consoleFileBagList').inner_text()
            assert not errors,errors
            assert p.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1'), 'Run UI overflows mobile viewport'
            context.close()
        print('Bundled local startup, Run preferences, file-bag preview/download and frame isolation passed at mobile and desktop widths.')
    finally:browser.close()
