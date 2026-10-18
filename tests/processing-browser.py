"""Portable-app settings and floating activity, at phone and desktop widths.
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
 open:()=>openProcessingSettings(),
 settings:()=>({...state.settings}),
 restore:async()=>{state=await validateProject({format:'Vision',version:4,...snapshot()});},
 activity:()=>{mediaActivity.push({title:'Screenshots',detail:'Creating frames',progress:50});renderActivity();},
 done:()=>{mediaActivity.forEach(task=>task.finished=true);renderActivity();}
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
  # Fixture bypasses account navigation only; real dialogs, CSS and handlers run.
  page.evaluate('document.querySelectorAll("dialog[open]").forEach(d=>d.close());processingFixture.open()')
  expect(page.locator('#processingDialog')).to_be_visible()
  page.locator('#projectDetail').select_option('maximum')
  expect(page.locator('#projectFrameEstimate')).to_contain_text('1200 screenshots')
  page.locator('#projectTranscription').select_option('gemini')
  page.locator('#projectSounds').check()
  page.locator('#saveProcessing').click()
  settings=page.evaluate('processingFixture.settings()')
  assert settings['screenshotDetail']=='maximum' and settings['transcriptionProvider']=='gemini' and settings['includeSoundEvents']
  page.evaluate('processingFixture.restore()')
  assert page.evaluate('processingFixture.settings().screenshotDetail')=='maximum'
  page.evaluate('processingFixture.activity()')
  indicator=page.locator('#queueIndicator');expect(indicator).to_be_visible()
  assert indicator.evaluate('(el)=>el.parentElement===document.body')
  for mode in ('vision','venture'):
   page.evaluate('(mode)=>{document.documentElement.dataset.workspacePage=mode;document.getElementById("appHeader").style.display="none"}',mode)
   box=indicator.bounding_box();assert box and box['x']>=0 and box['x']+box['width']<=width and box['y']+box['height']<844
   indicator.click();expect(page.locator('#activityDialog')).to_be_visible();page.locator('#closeActivity').click()
  page.evaluate('processingFixture.done()');expect(indicator).to_have_attribute('data-state','complete')
  page.wait_for_timeout(2200);expect(indicator).to_be_hidden()
  assert not errors,errors
  print(f'{width}px settings, saved settings, board/Venture activity, tap targets and completion passed')
  page.close()
 browser.close()
