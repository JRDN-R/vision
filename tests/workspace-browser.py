"""Chromium integration of real workspace/recovery modules against non-billable fixtures.
This is not an iOS-device, Windows-worker, or live-provider test.
Query input is injected because this environment does not permit browser navigation.
The URL parser is exercised; hosted navigation itself is not verified.
"""
from pathlib import Path
import importlib.util, json, os
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('venture_fixture',ROOT/'tests/venture-smoke.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
extra='''<header id="appHeader" class="top-actions"><button id="accountButton">Account</button><button id="newBtn">New</button><button id="openBtn">Projects</button><button id="saveBtn">Save project</button><button id="exportBtn">Export</button></header><button id="mobileHeaderToggle" hidden></button><button id="headerReveal" hidden></button><dialog id="accountDialog"></dialog><div id="accountGateSignIn">Sign in</div>'''
fixture=mod.fixture.replace('<body>','<body>'+extra).replace('</style>','\n'+(ROOT/'web/workspace.css').read_text()+'</style>',1).replace('</script></body>', '\n'+(ROOT/'web/workspace.js').read_text().replace('location.search',"window.fixtureQuery || ''")+'</script></body>')
CID='v-test-conversation-001'
settings=mod.settings
run=dict(runId='run-001',status='completed',model='gpt-6-astra',message='Create a report.',text='Saved report.',artifacts=[dict(id='a-001',name='report.html',mime='text/html',ready=True,size=50)],attachments=[],runOptions=settings['runOptions'],createdAt='2026-10-06T17:00:00Z',updatedAt='2026-10-06T17:01:00Z',sequence=2)
conv=dict(id=CID,title='Saved inspection report',settings=settings,revision=1,createdAt=1791320000,updatedAt=1791320000,boardProject=False)

def response(data,status=200,mime='application/json'):
    return dict(status=status,type=mime,body=json.dumps(data) if mime=='application/json' else data)

def main():
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,**({'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}))
  checks=[]
  try:
   for width in (390,1280):
    prefs=dict(launchView='vision',swipeNoticeVersion=0,sourcesV1=True);source_calls=[];patches=[]
    def api(url,method,raw):
     path=url.split('?')[0];body=json.loads(raw or '{}')
     if path=='/health':return response({'capabilities':{'ventureV1':True,'ventureV2':True}})
     if path=='/venture/workspace-preferences':
      if method=='PATCH':prefs.update(body);patches.append(body)
      return response(prefs)
     if path=='/venture/dictation':return response({'recoveryV2':True,'providers':{'whisper':{'ready':True}}})
     if path=='/venture/models':return response({'status':'available','models':[{'id':v} for v in ['gpt-6-astra','gpt-6.1-sol','gpt-4.1','sora-2']]})
     if path=='/venture/profile':return response({'hasAvatar':False})
     if path=='/venture/preferences':return response({'settings':body if method=='PUT' else settings})
     if path=='/venture/funding':return response({'status':'available','fraction':.75,'revision':1,'issues':[]})
     if path=='/venture/conversations':return response({'conversations':[conv],'nextCursor':None})
     if path.endswith('/sources'):
      source_calls.append(url)
      older='before=' in url
      files=[dict(id='a-'+str(i),kind='artifact',artifactId='a-001',runId='run-001',name='report.html' if i==0 else f'version-{i}.txt',mime='text/html' if i==0 else 'text/plain',size=50,ready=True,createdAt=1791321000-i) for i in (range(100,104) if older else range(100))]
      return response({'conversationId':CID,'files':files,'nextCursor':None if older else '[1791320901,"a-99"]'})
     if '/artifacts/' in path:return response('<h1>Saved report</h1><script>parent.hacked=true</script>',mime='text/html')
     if path==f'/venture/conversations/{CID}':return response({'conversation':conv,'runs':[run],'cursor':1791320060,'nextCursor':None})
     return response({'error':'Unexpected fixture request '+path},404)
    ctx=browser.new_context(viewport={'width':width,'height':844},has_touch=True,accept_downloads=True)
    p=ctx.new_page();p.set_default_timeout(5000);errors=[];p.on('pageerror',lambda error:errors.append(str(error)));p.expose_function('ventureTestAPI',api)
    p.route('**/*',lambda route:route.fulfill(body=fixture,content_type='text/html') if route.request.url.startswith('http://127.0.0.1/') else route.abort())
    p.set_content(fixture.replace('<script>', '<script>window.fixtureQuery="?view=venture";',1));p.wait_for_function('venture.open && venture.ready')
    assert p.locator('#visionVenture').is_visible()
    if width==390:
     assert p.locator('#workspaceSwipeNotice').is_visible();p.keyboard.press('Escape');assert p.locator('#workspaceSwipeNotice').is_visible()
     p.locator('#workspaceSwipeOK').click();p.wait_for_function('workspace.prefs.swipeNoticeVersion===1')
     assert prefs['swipeNoticeVersion']==1
     assert p.locator('#ventureBack').is_hidden();assert p.locator('#runProjectBtn').is_hidden()
    else:assert p.locator('#workspaceSwipeNotice').is_hidden()
    assert p.locator('#accountButton').get_attribute('aria-label')=='Account'
    assert p.evaluate('getComputedStyle(document.getElementById("accountButton"),"::before").maskImage.includes("data:image/svg+xml")')
    p.evaluate('ventureSelect('+json.dumps(CID)+')');p.wait_for_function('venture.current?.id==='+json.dumps(CID))
    p.locator('#workspaceSourcesButton').click();p.wait_for_function('workspace.files.size===100')
    assert p.locator('.venture-file-grid').is_hidden()
    p.locator('#workspaceSourcesMore').click();p.wait_for_function('workspace.files.size===104')
    p.locator('#workspaceSourcesRefresh').click();p.wait_for_timeout(150)
    assert p.locator('#workspaceSourcesList .workspace-source-row').count()==104
    assert p.locator('#workspaceSourcesMore').is_hidden()
    with p.expect_download() as dl:p.locator('.workspace-source-row').first.get_by_role('button',name='Download',exact=True).click()
    assert dl.value.suggested_filename=='report.html'
    # The existing preview is also a dialog and must keep opaque sandboxing.
    p.locator('.workspace-source-row').first.get_by_role('button',name='Preview',exact=True).click();p.wait_for_timeout(100)
    assert p.frame_locator('#consolePreviewBody iframe').locator('h1').inner_text()=='Saved report'
    assert p.locator('#consolePreviewBody iframe').get_attribute('sandbox')==''
    assert not p.evaluate('!!window.hacked');p.locator('#consolePreviewClose').click()
    p.screenshot(path=str(ROOT/'tests/venture-screenshots'/f'sources-{width}.png'))
    p.locator('#workspaceSourcesClose').click()
    assert p.evaluate("ventureSettings({model:'gpt-4.1',runOptions:{mode:'pro',effort:'max',verbosity:'high'}}).runOptions")==dict(mode='auto',effort='auto',verbosity='auto',webSearch=False,codeInterpreter=True)
    # Parameter controls hide for non-reasoning models and retain supported maxima.
    p.evaluate("venture.settings.model='gpt-4.1';ventureNormalizeParameterChoice();venturePaintSettings();ventureTogglePopover('settings')")
    assert p.locator('#ventureEffort').is_hidden();assert p.locator('#ventureVerbosity').is_hidden();assert p.locator('#ventureProRow').is_hidden()
    assert p.locator('#ventureMaxTokens').get_attribute('max')=='32768'
    p.locator('#ventureSettingsClose').click()
    # Actual retry sequence; accelerated waits record the production delays.
    # Paid/local providers and microphone acquisition are non-billable fixtures.
    p.evaluate(r'''window.dictationRequests=[];window.dictationOutcomes=[];window.receiptOutcomes=[];
    window.dictationWaits=[];window.holdDictationWait=false;
    const originalFetch=ventureFetch,originalPause=ventureDictationPause;
    ventureDictationPause=(rec,ms)=>{dictationWaits.push(ms);
      if(holdDictationWait)return new Promise(resolve=>{rec.retryResolve=resolve;});
      return originalPause(rec,1);};
    ventureFetch=async function(path,options={}){
      if(path.startsWith('/venture/dictation/')&&options.method!=='POST'){
        const outcome=receiptOutcomes.shift();if(!outcome)throw new Error('No saved result');
        return new Response(JSON.stringify(outcome),{status:200,headers:{'Content-Type':'application/json'}});
      }
      if(path==='/venture/dictation'&&options.method==='POST'){
        dictationRequests.push({id:options.body.get('requestId'),provider:options.body.get('provider')||'gemini',audio:options.body.get('audio').size});
        const outcome=dictationOutcomes.shift();if(outcome==='network')throw new Error('Connection interrupted');
        return new Response(JSON.stringify(outcome),{status:200,headers:{'Content-Type':'application/json'}});
      }return originalFetch(path,options);};
    window.startTestRecording=(outcomes,receipts=[])=>{ventureStopDictation();dictationRequests=[];dictationOutcomes=outcomes;receiptOutcomes=receipts;dictationWaits=[];
      const rec={epoch:venture.epoch,selection:venture.selection,phase:'processing',cancelled:false,provider:'gemini',requestId:ventureId(),form:new FormData()};
      rec.form.set('requestId',rec.requestId);rec.form.set('audio',new Blob(['retained audio'],{type:'audio/mp4'}),'dictation.m4a');ventureRecording=rec;void ventureUploadDictation(rec);};''')
    for outcomes,expected in [
      ([dict(status='completed',text='Gemini text')],['gemini']),
      ([dict(status='error',error='Gemini unavailable'),dict(status='completed',text='local text')],['gemini','whisper']),
      ([dict(status='error')]*2+[dict(status='completed',text='second local')],['gemini','whisper','whisper']),
      ([dict(status='error')]*3+[dict(status='completed',text='third local')],['gemini','whisper','whisper','whisper']),
    ]:
     p.evaluate('startTestRecording('+json.dumps(outcomes)+')');p.wait_for_function('ventureRecording===null')
     assert p.evaluate('dictationRequests.map(r=>r.provider)')==expected
     assert p.evaluate('new Set(dictationRequests.map(r=>r.id)).size')==len(expected)
     assert p.evaluate('dictationWaits')==[30000,60000][:max(0,len(expected)-2)]
     assert p.locator('#ventureMessage').input_value().endswith(outcomes[-1]['text'])
     assert not p.evaluate('venture.pending')
    assert p.locator('#ventureDictationRetry').count()==0
    assert p.locator('#ventureDictationWhisper').count()==0
    # An uncertain paid response is reconciled by GET, not another paid POST.
    p.evaluate('startTestRecording(["network"],[{status:"processing"},{status:"completed",text:"saved receipt"}])')
    p.wait_for_function('ventureRecording===null');assert p.evaluate('dictationRequests.length')==1
    assert p.evaluate('dictationWaits')==[1500]
    # No saved receipt: automatic local fallback, still exactly one Gemini POST.
    p.evaluate('startTestRecording(["network",{status:"completed",text:"local after outage"}])')
    p.wait_for_function('ventureRecording===null');assert p.evaluate('dictationRequests.map(r=>r.provider)')==['gemini','whisper']
    # Exhaustion is finite, red, downloadable, and cannot be restarted by reentry.
    p.evaluate('startTestRecording([{status:"error"},{status:"error"},{status:"error"},{status:"error"}])')
    p.wait_for_function('ventureRecording?.phase==="failed"')
    assert p.evaluate('dictationRequests.length')==4;assert p.evaluate('dictationWaits')==[30000,60000]
    assert 'failed' in p.locator('#ventureDictation').get_attribute('class')
    assert 'Transcription failed' in p.locator('#ventureDictationStatus').inner_text()
    assert p.locator('#ventureDictationSave').inner_text()=='Save audio (.m4a)'
    p.evaluate('ventureUploadDictation(ventureRecording)');assert p.evaluate('dictationRequests.length')==4
    with p.expect_download() as dl:p.locator('#ventureDictationSave').click()
    assert dl.value.suggested_filename.endswith('.m4a');assert Path(dl.value.path()).read_bytes()==b'retained audio'
    assert p.evaluate('!ventureDictationBusy()')
    p.screenshot(path=str(ROOT/'tests/venture-screenshots'/f'dictation-failed-{width}.png'))
    # Cancellation while waiting prevents all subsequent attempts and insertion.
    p.evaluate('holdDictationWait=true;startTestRecording([{status:"error"},{status:"error"},{status:"completed",text:"must not insert"}])')
    p.wait_for_function('ventureRecording?.phase==="waiting"');p.locator('#ventureDictationDiscard').click()
    p.wait_for_function('ventureRecording===null');assert p.evaluate('dictationRequests.length')==2
    p.evaluate('holdDictationWait=false');assert not p.locator('#ventureMessage').input_value().endswith('must not insert')
    # A full draft caches a successful transcript. Editing inserts it, without retry.
    p.evaluate("document.getElementById('ventureMessage').value='full';document.getElementById('ventureMessage').maxLength=8")
    p.evaluate('startTestRecording([{status:"completed",text:"result"}])');p.wait_for_function('ventureRecording?.phase==="awaiting-space"')
    p.locator('#ventureMessage').fill('');p.wait_for_function('ventureRecording===null')
    assert p.evaluate('dictationRequests.length')==1;assert p.locator('#ventureMessage').input_value()=='result'
    p.evaluate("document.getElementById('ventureMessage').maxLength=50000")
    # The requested auto-model placeholder cannot change model or settings.
    p.evaluate('ventureTogglePopover("settings")')
    assert p.locator('#ventureAutoModel').is_disabled();assert not p.locator('#ventureAutoModel').is_checked()
    assert p.locator('#ventureAutoModelHelp').inner_text()=='Coming soon'
    p.locator('#ventureSettingsClose').click()
    p.evaluate('workspaceSave({launchView:"venture"})');assert prefs['launchView']=='venture';assert prefs['swipeNoticeVersion']==(1 if width==390 else 0)
    # Synthetic touch events exercise browser listeners; not a physical-iOS claim.
    p.evaluate(r'''window.swipe=(x1,y1,x2,y2)=>{const host=document.body;
      const point=(x,y)=>new Touch({identifier:1,target:host,clientX:x,clientY:y});
      host.dispatchEvent(new TouchEvent('touchstart',{touches:[point(x1,y1)],bubbles:true,cancelable:true}));
      host.dispatchEvent(new TouchEvent('touchend',{touches:[],changedTouches:[point(x2,y2)],bubbles:true,cancelable:true}));};''')
    p.evaluate('swipe(90,20,240,20)');p.wait_for_timeout(100)
    if width==390:
     assert not p.evaluate('venture.open')
     p.evaluate('swipe(280,400,80,400)');assert not p.evaluate('venture.open'),'Body swipe switched workspaces'
     p.evaluate('swipe(280,20,80,70)');p.wait_for_timeout(100)
     assert p.evaluate('venture.open'),'Collapsed header swipe failed'
     p.evaluate('workspaceSwitch("vision");document.documentElement.classList.add("header-open")')
     p.evaluate('swipe(280,30,80,30)');p.wait_for_function('venture.open')
    else:assert p.evaluate('venture.open'),'Desktop swipe must not switch'
    # Logout clears retained content, Sources, and once-per-account state.
    p.evaluate('workspaceOpenSources()');p.wait_for_timeout(100)
    p.evaluate('testSignOut()');p.wait_for_function('!workspace.uid')
    assert p.locator('#workspaceSources').is_hidden();assert p.locator('#workspaceSourcesList').inner_text()==''
    assert not p.evaluate('ventureRecording');assert not errors,errors
    checks.append(f'{width}px: startup, icons, notice, Sources/pagination/download/preview, parameter controls, one Gemini attempt, three-attempt local fallback/30s/60s waits, cancellation, audio download, receipt reconciliation, cached insertion, disabled auto-model toggle, swipes, account reset')
    ctx.close()
   # Explicit Vision URL wins over account preference; no once-acknowledged notice.
   prefs.update(launchView='venture',swipeNoticeVersion=1)
   ctx=browser.new_context(viewport={'width':390,'height':844});p=ctx.new_page();p.expose_function('ventureTestAPI',api)
   p.route('**/*',lambda r:r.fulfill(body=fixture,content_type='text/html') if r.request.url.startswith('http://127.0.0.1/') else r.abort())
   p.set_content(fixture.replace('<script>', '<script>window.fixtureQuery="?view=vision";',1));p.wait_for_function('workspace.sources')
   assert not p.evaluate('venture.open');assert p.locator('#workspaceSwipeNotice').is_hidden();ctx.close()
   print('\n'.join(checks));print('Explicit URL precedence and acknowledged notice passed. No paid API calls made.')
  finally:browser.close()
if __name__=='__main__':main()
