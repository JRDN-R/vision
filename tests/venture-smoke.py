"""Actual Venture modules in Chromium, with deterministic non-billable service fixtures."""
from pathlib import Path
import json
import os
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
def read(name):return (ROOT/name).read_text()
base=read('web/base.js');download=base[base.index('function download('):].split('\n')[0]
setup=r'''
if(!crypto.randomUUID)crypto.randomUUID=()=>('10000000-1000-4000-8000-'+String(Date.now()).padStart(12,'0'));
const $=id=>document.getElementById(id), state={title:'Unchanged board',nodes:[],settings:{},consoleSession:null};let busy=false,ioBusy=false;
const R={safeFilename:n=>n};function escapeHTML(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function bytesFromDataURL(s){return Uint8Array.from(atob(s.split(',')[1]),c=>c.charCodeAt(0));}
function markDirty(){}function toast(s){window.testToast=s;}function checkpoint(){}function refreshExport(){}function renderAll(){renderConsole();}
let accountFirebase={currentUser:{uid:'test-user',displayName:'Jordan Rapp',email:'jordan@example.test'}};
function accountSignedIn(){return !!accountFirebase.currentUser;}function accountUpdateGate(){}function accountGoogleSignOut(){accountFirebase.currentUser=null;accountUpdateGate();}
function openAccountDialog(){}function openCloudSettings(){}function trialActive(){return false;}
async function cloudFetch(path,opts={}){const result=await window.ventureTestAPI(path,opts.method||'GET',typeof opts.body==='string'?opts.body:null);return new Response(result.body,{status:result.status,headers:{'Content-Type':result.type}});}
'''
code=setup+download+'\n'+read('web/console.js')+'\n'+read('web/run-tools.js')+'\n'+read('web/venture.js')+r'''
window.testState=()=>({board:state.title,settings:venture.settings,current:venture.current?.id,pending:!!venture.pending});
window.testSignOut=()=>accountGoogleSignOut();
'''
fixture='<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>'+''.join(read('web/'+f) for f in ['styles.css','console.css','run-tools.css','venture.css'])+'</style></head><body><button id="runProjectBtn">Run</button><input id="screenshotMode" hidden><section id="visionConsole" hidden></section><script>'+code+'</script></body></html>'
settings={'model':'gpt-6-astra','maxOutputTokens':16000,'runOptions':{'mode':'auto','effort':'auto','verbosity':'auto','webSearch':False,'codeInterpreter':True}}
shots=ROOT/'tests/venture-screenshots';shots.mkdir(exist_ok=True)
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,**({'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}))
    try:
        for width in [390,1280]:
            conversations=[dict(id='v-test-conversation-001',title='Cowling inspection report',settings=settings,revision=1,createdAt=1791320000,updatedAt=1791320000,boardProject=False)]
            runs=[dict(runId='run-001',status='completed',model='gpt-6-astra',message='Create an inspection report and save the HTML.',text='## Your inspection report\n\nThe measurements are organized by panel and location.\n\n[Download the report](sandbox:/mnt/data/report.html)',artifacts=[dict(id='a-001',name='report.html',mime='text/html',ready=True,size=50)],attachments=[],runOptions=settings['runOptions'],createdAt='2026-10-06T17:00:00Z',updatedAt='2026-10-06T17:01:00Z',sequence=2)]
            funding=dict(provider='estimate',status='available',fraction=.76,revision=1,updatedAt=1791320000,issues=[])
            submitted=[]
            def api(url,method,raw):
                if url.endswith('/artifacts/a-001'):
                    return {'status':200,'type':'text/html','body':'<h1>Retained report</h1><script>parent.hacked=true</script>'}
                path=url.split('?')[0];body=json.loads(raw or '{}');data={}
                if path=='/health':data={'capabilities':{'ventureV1':True}}
                elif path=='/venture/preferences':data={'settings':body if method=='PUT' else settings}
                elif path=='/venture/funding':
                    if method=='POST':funding.update(fraction=1,revision=funding['revision']+1)
                    data=funding
                elif path=='/venture/conversations':
                    if method=='POST':
                        new=dict(conversations[0],id=body['id'],title='New venture',settings=body['settings']);conversations.append(new);data={'conversation':new}
                    else:data={'conversations':conversations,'nextCursor':None}
                elif path.startswith('/venture/conversations/'):
                    cid=path.split('/')[-1];item=next(c for c in conversations if c['id']==cid)
                    if method=='PATCH':
                        item.update({k:v for k,v in body.items() if k!='revision'});item['revision']+=1
                    data={'conversation':item,'runs':runs if cid=='v-test-conversation-001' else [],'cursor':1791320060,'nextCursor':None}
                elif path.endswith('/runs') and method=='POST':
                    submitted.append(raw);data=dict(runs[0],runId='run-002',status='queued',text='',artifacts=[],clientRequestId='accepted')
                else:return {'status':404,'type':'application/json','body':json.dumps({'error':'Unexpected fixture request '+path})}
                return {'status':202 if method=='POST' and path.endswith('/runs') else 200,'type':'application/json','body':json.dumps(data)}
            context=browser.new_context(viewport={'width':width,'height':844},accept_downloads=True)
            context.route('**/*',lambda r:r.abort());p=context.new_page();p.expose_function('ventureTestAPI',api);errors=[];p.on('pageerror',lambda e:errors.append(str(e)))
            p.set_content(fixture,wait_until='load');assert not errors,errors
            p.locator('#runProjectBtn').click();p.wait_for_timeout(400)
            assert p.locator('#visionVenture').is_visible()
            if width<761:p.locator('#ventureHistoryToggle').click()
            p.locator('[data-conversation="v-test-conversation-001"]').first.click();p.wait_for_timeout(200)
            assert 'Your inspection report' in p.locator('#ventureTurns').inner_text()
            assert p.evaluate('testState().board')=='Unchanged board'
            p.screenshot(path=str(shots/f'venture-{width}.png'))
            p.locator('#ventureSettingsToggle').click();p.wait_for_timeout(2200)
            assert not p.locator('#ventureSettings').is_visible(),'Idle settings did not close'
            p.locator('#ventureSettingsToggle').click();p.locator('#ventureModel').focus();p.wait_for_timeout(2200)
            assert p.locator('#ventureSettings').is_visible(),'Settings closed while editing'
            p.locator('#ventureVerbosity').fill('3');p.locator('#ventureSettingsClose').click();p.wait_for_timeout(800)
            assert p.evaluate('testState().settings.runOptions.verbosity')=='high'
            p.locator('#ventureAvatar').click();assert 'Estimated API balance' in p.locator('#ventureAccount').inner_text()
            assert '$' not in p.locator('#ventureAccount').inner_text()
            assert p.locator('#ventureBattery').get_attribute('aria-valuenow')=='76'
            p.locator('#ventureCalibrate').click();p.locator('#ventureBalanceAmount').fill('25');p.locator('#ventureBalanceForm [type=submit]').click();p.wait_for_timeout(200)
            assert p.locator('#ventureBattery').get_attribute('aria-valuenow')=='100',p.locator('#ventureBalanceStatus').inner_text()
            p.locator('#ventureAccountClose').click()
            # Original file link routes to the authorized retained server copy.
            p.locator('.console-artifact-link').click();p.wait_for_timeout(100)
            assert p.frame_locator('#consolePreviewBody iframe').locator('h1').inner_text()=='Retained report'
            assert p.locator('#consolePreviewBody iframe').get_attribute('sandbox')==''
            assert not p.evaluate('!!window.hacked')
            with p.expect_download() as dl:p.locator('#consolePreviewDownload').click()
            assert dl.value.suggested_filename=='report.html'
            p.locator('#consolePreviewClose').click()
            if width<761:p.locator('#ventureHistoryToggle').click()
            p.locator('[data-rename="v-test-conversation-001"]').click();p.locator('#ventureRenameValue').fill('My adventure');p.locator('#ventureRenameForm [type=submit]').click();p.wait_for_timeout(200)
            assert p.locator('#ventureTitle').inner_text()=='My adventure'
            if width<761:p.locator('#ventureHistoryClose').click()
            assert p.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            p.evaluate('testSignOut()');assert not p.locator('#visionVenture').is_visible()
            assert p.locator('#ventureTurns').inner_text()==''
            assert not errors,errors
            context.close()
        print('Venture: history, rename, model settings, two-second dismissal, calibration, durable download, sandbox isolation and account reset passed at 390px and 1280px.')
    finally:browser.close()
