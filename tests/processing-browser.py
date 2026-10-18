"""Portable-app settings and node-owned activity, at phone and desktop widths.
Every network request is blocked; no account or provider calls are made.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
root=Path(__file__).resolve().parents[1]
html=(root/'Vision.html').read_text()
index=html.rfind('})();')
hooks='''
accountCanUseApp=()=>true;accountUpdateGate();
window.processingFixture={
 ready:async()=>{await accountReady;await projectRecoverStartup();},
 open:()=>openProcessingSettings(),
 settings:()=>({...state.settings}),
 restore:async()=>{state=await validateProject({format:'Vision',version:4,...snapshot()});},
 activity:()=>{mediaActivity.push({title:'Screenshots',detail:'Creating frames',progress:50});renderActivity();},
 done:()=>{mediaActivity.forEach(task=>task.finished=true);renderActivity();},
 nodes:async()=>{
  state.settings.autoNameProject=false;mediaActivity.length=0;
  await createModuleNode('node','Running node');await createModuleNode('node','Waiting node');
  state.nodes.forEach((n,i)=>{n.x=0;n.y=i*450;});state.view={x:40,y:120,scale:.75};setSidebar(false);renderAll();
  state.nodes.forEach((n,i)=>mediaActivity.push({nodeId:n.id,title:n.title,detail:i?'Queued':'Reading screenshots',status:i?'queued':'working'}));renderActivity();
 },
 move:()=>{const n=state.nodes[0];n.x+=30;n.y+=40;const el=$('node-'+n.id);el.style.left=n.x+'px';el.style.top=n.y+'px';},
 zoom:()=>{state.view.scale=.6;updateView();}

};
'''
html=html[:index]+hooks+html[index:]
with sync_playwright() as pw:
 browser=pw.chromium.launch(headless=True)
 for width in (390,1280):
  page=browser.new_page(viewport={'width':width,'height':844});errors=[]
  page.on('pageerror',lambda error:errors.append(str(error)))
  page.route('**/*',lambda route:route.fulfill(body=html,content_type='text/html') if route.request.url=='https://processing.test/' else route.abort())
  page.goto('https://processing.test/',wait_until='domcontentloaded')
  page.wait_for_function('!!window.processingFixture')
  page.evaluate('processingFixture.ready()')
  # Fixture bypasses account navigation only; real dialogs, CSS and handlers run.
  page.evaluate('document.querySelectorAll("dialog[open]").forEach(d=>d.close());processingFixture.open()')
  expect(page.locator('#processingDialog')).to_be_visible()
  page.locator('#projectDetail').select_option('maximum')
  expect(page.locator('#projectFrameEstimate')).to_contain_text('1200 screenshots')
  page.locator('#projectTranscription').select_option('gemini')
  page.locator('#projectSounds').check()
  page.locator('#projectAutoName').check()
  page.locator('#saveProcessing').click()
  settings=page.evaluate('processingFixture.settings()')
  assert settings['screenshotDetail']=='maximum' and settings['transcriptionProvider']=='gemini' and settings['includeSoundEvents'],settings
  page.evaluate('processingFixture.restore()')
  assert page.evaluate('processingFixture.settings().screenshotDetail')=='maximum'
  assert page.evaluate('processingFixture.settings().autoNameProject') is True
  page.evaluate('processingFixture.activity()')
  indicator=page.locator('#queueIndicator');expect(indicator).to_be_visible()
  assert indicator.evaluate('(el)=>el.parentElement===document.body')
  for mode in ('vision','venture'):
   page.evaluate('(mode)=>{document.documentElement.dataset.workspacePage=mode;document.getElementById("appHeader").style.display="none"}',mode)
   box=indicator.bounding_box();assert box and box['x']>=0 and box['x']+box['width']<=width and box['y']+box['height']<844
   indicator.click();expect(page.locator('#activityDialog')).to_be_visible();page.locator('#closeActivity').click()
  page.evaluate('processingFixture.done()');expect(indicator).to_have_attribute('data-state','complete')
  page.wait_for_timeout(2200);expect(indicator).to_be_hidden()
  page.evaluate('document.documentElement.dataset.workspacePage="vision";processingFixture.nodes()')
  badges=page.locator('.node-activity');expect(badges).to_have_count(2)
  expect(badges.nth(0)).to_have_attribute('data-state','working')
  expect(badges.nth(1)).to_have_attribute('data-state','awaiting')
  expect(indicator).to_be_hidden()
  for i in range(2):
   badge=badges.nth(i).bounding_box();node=page.locator('#world .node').nth(i).bounding_box()
   assert badge and node and abs(badge['x']-node['x'])<2 and badge['y']+badge['height']<node['y']
  before=badges.nth(0).bounding_box();page.evaluate('processingFixture.move()');after=badges.nth(0).bounding_box()
  assert abs(after['x']-before['x']-22.5)<2 and abs(after['y']-before['y']-30)<2
  page.evaluate('processingFixture.zoom()')
  badges.nth(0).click();expect(page.locator('#activityDialog')).to_be_visible()
  expect(page.locator('#activitySummary')).to_contain_text('Running node')
  page.locator('#closeActivity').click()
  plus=page.locator('#addNodeButton')
  if plus.count():
   p=plus.bounding_box()
   for b in badges.all():
    box=b.bounding_box()
    assert not p or box['x']+box['width']<=p['x'] or box['x']>=p['x']+p['width'] or box['y']+box['height']<=p['y'] or box['y']>=p['y']+p['height']
  page.evaluate('processingFixture.done()');expect(badges).to_have_count(0)
  assert not errors,errors
  print(f'{width}px settings, saved settings, board/Venture activity, tap targets and completion passed')
  page.close()
 browser.close()
