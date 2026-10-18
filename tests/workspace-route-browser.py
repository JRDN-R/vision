"""Mobile/desktop routes, isolated page rendering and session-preserving switches.
Real UI and Lottie code; all account/provider requests are non-billable fixtures.
"""
from pathlib import Path
import importlib.util, json, os
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('workspace_fixture',ROOT/'tests/workspace-browser.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
route_script=(ROOT/'web/workspace-route.js').read_text()
fixture=mod.fixture.replace('<head>', '<head><script>'+route_script+'</script>',1).replace('<body>', '<body><main class="app"><div id="board">Board stays in this session</div></main>',1)
# Keep the in-memory session observable without exposing account credentials.
fixture=fixture.replace('</script></body>', '\nwindow.routeFixture=()=>({ready:venture.ready,files:venture.files.length,board:state.title});window.attachFixture=()=>{venture.files=[new File(["fixture"],"test.txt")];venturePaintAttachments();};</script></body>')
def api(url,method,raw):
 path=url.split('?')[0]
 data={}
 if path=='/health':data={'capabilities':{'ventureV2':True}}
 elif path=='/venture/workspace-preferences':data={'launchView':'vision','sourcesV1':False}
 elif path=='/venture/preferences':data={'settings':mod.settings}
 elif path=='/venture/conversations':data={'conversations':[],'nextCursor':None}
 elif path=='/venture/models':data={'status':'available','models':[{'id':'gpt-6-astra'}]}
 elif path=='/venture/profile':data={'hasAvatar':False}
 elif path=='/venture/funding':data={'status':'available','fraction':.8,'revision':1,'issues':[]}
 return {'status':200,'type':'application/json','body':json.dumps(data)}
with sync_playwright() as pw:
 browser=pw.chromium.launch(headless=True,**({'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}))
 for width in (390,1280):
  context=browser.new_context(viewport={'width':width,'height':844});page=context.new_page();errors=[]
  page.on('pageerror',lambda e:errors.append(str(e)));page.expose_function('ventureTestAPI',api)
  page.route('**/*',lambda r:r.fulfill(body=fixture,content_type='text/html; charset=utf-8') if r.request.url.startswith('https://route.test/') else r.abort())
  page.goto('https://route.test/vision/venture/');page.wait_for_function('routeFixture().ready')
  expect(page.locator('#visionVenture')).to_be_visible();expect(page.locator('main.app')).to_be_hidden();expect(page.locator('#appHeader')).to_be_hidden()
  assert page.title()=='Venture — by Jordan R.'
  page.locator('#ventureMessage').fill('Keep my draft');page.evaluate('attachFixture()');page.evaluate('() => {window.sessionMarker=42;window.routeAnimations=[];const originalAnimate=Element.prototype.animate;Element.prototype.animate=function(frames,options){routeAnimations.push(options.duration);return originalAnimate.call(this,frames,options);};}')
  switch=page.locator('#workspaceSwitchButton');expect(switch).to_be_visible()
  expect(switch.locator('[data-direction="left"] svg')).to_be_visible()
  switch.click();expect(page).to_have_url('https://route.test/vision/?view=vision')
  expect(page.locator('main.app')).to_be_visible();expect(page.locator('#visionVenture')).to_be_hidden()
  switch.click();expect(page).to_have_url('https://route.test/vision/venture/')
  expect(page.locator('main.app')).to_be_hidden();expect(page.locator('#ventureMessage')).to_have_value('Keep my draft')
  assert page.evaluate('routeFixture().files')==1 and page.evaluate('sessionMarker')==42
  # A partially transparent entrance must never reveal the board beneath it.
  assert page.evaluate('getComputedStyle(document.querySelector("main.app")).display')=='none'
  assert page.evaluate('routeAnimations.includes(220)')
  page.go_back();expect(page.locator('main.app')).to_be_visible()
  page.go_forward();expect(page.locator('#visionVenture')).to_be_visible();expect(page.locator('main.app')).to_be_hidden()
  expect(page.locator('#ventureMessage')).to_have_value('Keep my draft')
  page.reload();page.wait_for_function('routeFixture().ready');expect(page.locator('#visionVenture')).to_be_visible();expect(page.locator('main.app')).to_be_hidden()
  assert not errors,errors
  print(f'{width}px: direct entry, chevron, page isolation, draft/files, Back/Forward and refresh passed')
  context.close()
 browser.close()
