"""Real UI with deterministic account/server fixtures and blocked external traffic."""
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'Vision.html').read_text()
PROBE=r'''
window.upgradeFixture={
 boot:async()=>{
  await accountReady;await projectRecoverStartup();
  accountSignedIn=()=>true;accountCanUseApp=()=>true;ventureScope=()=> 'upgrade-fixture';
  accountFirebase={currentUser:{uid:'upgrade-fixture',displayName:'Jordan Rapp',email:'jordan@example.test',getIdToken:async()=> 'fixture-id-token'}};
  cloudAuth={kind:'firebase-google',uid:'upgrade-fixture'};window.fixtureApproval='unrequested';window.fixturePrefs={};
  // Keep projectRequest, cloudFetch and the Firebase token path real. Mock
  // only the network boundary so a project-only guard cannot be hidden again.
  window.fetch=async(url,options={})=>{
   const path=new URL(url).pathname.replace(/^\/api/,'');
   const headers=new Headers(options.headers);
   if(headers.get('Authorization')!=='Bearer fixture-id-token')throw new Error('Missing account authentication');
   if(path.startsWith('/account/')&&headers.has('X-Vision-Project-Key'))throw new Error('Account request leaked a project key');
   if(path==='/account/upgrade'){
    if(options.method==='POST'&&fixtureApproval==='unrequested')window.fixtureApproval='pending';
    return new Response(JSON.stringify({status:fixtureApproval,approved:fixtureApproval==='approved',canAdmin:true,preferences:{transcriptionProvider:fixtureApproval==='approved'?'gemini':'local',frameDescriptionEngine:fixtureApproval==='approved'?'auto':'local',describeFrames:true,includeSoundEvents:false,...fixturePrefs}}));
   }
   if(path==='/account/processing-preferences'){
    if(window.fixtureOffline)throw new Error('Fixture offline');
    Object.assign(fixturePrefs,JSON.parse(options.body));
    return new Response(JSON.stringify({preferences:{transcriptionProvider:'gemini',frameDescriptionEngine:'auto',describeFrames:true,includeSoundEvents:false,...fixturePrefs}}));
   }
   return new Response('{}');
  };
  ventureJSON=async path=>path==='/health'?{capabilities:{ventureV2:true}}:path==='/venture/preferences'?{settings:VENTURE_DEFAULTS}:path==='/venture/conversations'?{conversations:[]}:{};
  ventureIdentity=()=>{venture.uid='upgrade-fixture';};venture.ready=true;venture.uid='upgrade-fixture';workspace.uid='upgrade-fixture';
  navigationMarkSeen();document.querySelectorAll('dialog[open]').forEach(d=>d.close());
  document.querySelectorAll('[data-account-inert]').forEach(el=>{el.inert=false;delete el.dataset.accountInert;});document.documentElement.classList.remove('account-locked');$('accountGate').hidden=true;workspaceSyncSwitch();await upgradeRefresh(true);
 },
 approve:async()=>{window.fixtureApproval='approved';await upgradeRefresh(true);},
 resetApproval:async()=>{window.fixtureApproval='unrequested';await upgradeRefresh(true);},
 values:()=>({provider:transcriptionProvider(),...processingSettings()}),
 otherProject:()=>{state={...state,title:'Older project',settings:{...defaults(),transcriptionProvider:'local',frameDescriptionEngine:'local'}};renderAll();},
 newProject:()=>{state={...state,title:'Untitled timeline',settings:defaults()};renderAll();},
 openSettings:()=>openProcessingSettings(),
 venture:()=>workspaceSwitch('venture'),
 history:()=>{venture.history=Array.from({length:23},(_,i)=>({id:'chat-'+i,title:'Conversation '+i}));venturePaintHistory();},
 offlineChoices:async()=>{window.fixtureOffline=true;try{await saveAccountProcessing({describeFrames:true});}catch{}window.fixtureOffline=false;await saveAccountProcessing({includeSoundEvents:true});}
};
'''
i=HTML.rfind('\n})();');HTML=HTML[:i]+PROBE+HTML[i:]
NATIVE=(ROOT/'ios/App/WorkspaceScripts.swift').read_text()
def script(name):return re.search(r'static let '+name+r' = #"""([\s\S]*?)"""#',NATIVE)[1]
with sync_playwright() as pw:
 options={'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}
 browser=pw.chromium.launch(headless=True,**options)
 for width,height in [(320,568),(390,700),(430,874),(1280,900)]:
  context=browser.new_context(viewport={'width':width,'height':height},has_touch=width<760)
  context.route('**/*',lambda route:route.fulfill(body=HTML,content_type='text/html') if route.request.url=='https://upgrade.test/' else route.abort())
  page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto('https://upgrade.test/');page.evaluate('upgradeFixture.boot()')
  page.evaluate("window.webkit={messageHandlers:{vision:{postMessage:()=>Promise.resolve(true)}}}")
  page.evaluate(script('bridge'));page.evaluate(script('sidebar'))
  page.locator('#workspaceMenuButton').click();page.wait_for_timeout(300)
  expect(page.locator('#accountUpgradeButton')).to_be_visible()
  assert 'linear-gradient' in page.locator('#accountUpgradeButton').evaluate('e=>getComputedStyle(e).backgroundImage')
  for menu in ['workspaceMenu']:
   metrics=page.locator('#'+menu).evaluate('(e)=>({height:e.clientHeight,scroll:e.scrollHeight})')
   assert metrics['scroll']<=metrics['height']+1,metrics
   for button in page.locator('#'+menu+' button:visible, #'+menu+' a:visible').all():
    box=button.bounding_box();assert box['y']>=0 and box['y']+box['height']<=height+1,(button.inner_text(),box,height)
  order=page.locator('#workspaceMenu .top-actions').evaluate('(e)=>Array.from(e.children).filter(x=>!x.hidden).slice(0,4).map(x=>x.id)')
  assert order==['accountButton','accountUpgradeButton','newBtn','saveBtn'],order
  boxes=[page.locator('#'+ident).bounding_box() for ident in ['runProjectBtn','workspaceVortexLink']]
  assert abs(boxes[0]['y']-boxes[1]['y'])<1
  out=ROOT/'.qa/upgrade-screenshots';out.mkdir(parents=True,exist_ok=True)
  page.screenshot(path=str(out/f'menu-{width}.png'))
  page.locator('#accountUpgradeButton').click();page.locator('#requestAccountUpgrade').click()
  expect(page.locator('#accountUpgradeStatus')).to_contain_text('waiting')
  page.evaluate("document.getElementById('accountUpgradeDialog').close()")
  page.evaluate('upgradeFixture.approve()')
  expect(page.locator('#accountUpgradeButton')).to_be_hidden()
  for action in ['values','otherProject','newProject']:
   if action!='values':page.evaluate('upgradeFixture.'+action+'()')
   value=page.evaluate('upgradeFixture.values()');assert value['provider']=='gemini' and value['frameDescriptionEngine']=='auto' and value['describeFrames'],value
  page.evaluate('upgradeFixture.openSettings()')
  page.locator('#projectTranscription').select_option('local');page.locator('#projectFrameEngine').select_option('local');page.locator('#projectDescribe').uncheck();page.locator('#saveProcessing').click()
  expect(page.locator('#processingDialog')).to_be_hidden()
  page.evaluate('upgradeFixture.newProject()');value=page.evaluate('upgradeFixture.values()')
  assert value['provider']=='local' and value['frameDescriptionEngine']=='local' and not value['describeFrames'],value
  page.locator('#transcribeProvider select').evaluate("e=>{e.value='gemini';e.dispatchEvent(new Event('change'));}")
  page.wait_for_function("fixturePrefs.transcriptionProvider==='gemini'")
  page.evaluate('upgradeFixture.newProject()');assert page.evaluate('upgradeFixture.values().provider')=='gemini'
  page.evaluate('upgradeFixture.offlineChoices()');assert page.evaluate('fixturePrefs.describeFrames && fixturePrefs.includeSoundEvents')
  assert page.locator('#projectTitle').is_visible();page.mouse.wheel(0,500);assert page.locator('#projectTitle').is_visible()
  page.evaluate('upgradeFixture.resetApproval()')
  page.evaluate('upgradeFixture.venture()');page.locator('#workspaceMenuButton').click();page.wait_for_timeout(300)
  page.evaluate('upgradeFixture.history()');page.wait_for_timeout(100)
  sidebar=page.locator('#ventureSidebar')
  metrics=sidebar.evaluate('(e)=>({height:e.clientHeight,scroll:e.scrollHeight})');assert metrics['scroll']<=metrics['height']+1,metrics
  for button in sidebar.locator('button:visible, a:visible').all():
   box=button.bounding_box();assert box['y']>=0 and box['y']+box['height']<=height+1,(button.inner_text(),box,height)
  history=page.locator('#ventureHistory');assert history.evaluate('(e)=>e.scrollHeight<=e.clientHeight+1')
  first=history.inner_text();page.locator('#ventureHistoryNext').click();assert history.inner_text()!=first
  page.screenshot(path=str(out/f'venture-menu-{width}.png'))
  assert not errors,errors
  context.close();print(f'{width}x{height}: request, approval, global overrides and no-scroll native menu passed')
 browser.close()
