"""Cached Vortex starts with no static network dependencies; native menus fit."""
import importlib.util
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('vortex_fixture',ROOT/'tests/vortex-browser.py')
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
HTML=(ROOT/'vortex/offline.html').read_text().replace((ROOT/'web/vendor/firebase.js').read_text(),fixture.FIREBASE,1)
NATIVE=(ROOT/'ios/App/WorkspaceScripts.swift').read_text()
def script(name):return re.search(r'static let '+name+r' = #"""([\s\S]*?)"""#',NATIVE)[1]
with sync_playwright() as pw:
 options={'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}
 browser=pw.chromium.launch(headless=True,**options)
 for width,height in [(320,568),(390,700),(1280,900)]:
  api=fixture.Fixture();job=api.new_job(status='complete',phase='Ready',media={**fixture.VIDEO,'thumbnail':''})
  context=browser.new_context(viewport={'width':width,'height':height},has_touch=width<760)
  context.add_init_script("localStorage.setItem('__fixtureUID','alice');")
  static=[]
  def route(r):
   if r.request.url==fixture.URL:r.fulfill(body=HTML,content_type='text/html')
   elif r.request.url.startswith(fixture.BACKEND):api.route(r)
   else:static.append(r.request.url);r.abort()
  context.route('**/*',route);page=context.new_page();page.set_default_timeout(7000);errors=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto(fixture.URL);expect(page.locator('#application')).to_be_visible()
  expect(page.locator('#connectionStatus')).to_have_text('Connected to FUPCJ Server')
  page.evaluate("window.webkit={messageHandlers:{vision:{postMessage:()=>Promise.resolve(true)}}}")
  page.evaluate(script('bridge'));page.evaluate(script('sidebar'))
  page.locator('#menuButton').click();page.wait_for_timeout(300)
  menu=page.locator('#navigation');metrics=menu.evaluate('(e)=>({height:e.clientHeight,scroll:e.scrollHeight})')
  assert metrics['scroll']<=metrics['height']+1,metrics
  for b in menu.locator('button:visible,a:visible').all():
   box=b.bounding_box();assert box['y']>=0 and box['y']+box['height']<=height+1,(b.inner_text(),box,height)
  links=menu.locator('nav a');assert abs(links.nth(0).bounding_box()['y']-links.nth(1).bounding_box()['y'])<1
  assert menu.locator('nav').evaluate('(e)=>e===e.parentElement.lastElementChild')
  out=ROOT/'.qa/upgrade-screenshots';out.mkdir(exist_ok=True,parents=True);page.screenshot(path=str(out/f'vortex-menu-{width}.png'))
  page.locator('#openDownloadHistory').click();expect(page.locator('#downloadHistoryDialog')).to_be_visible()
  row=page.locator(f'#historyList [data-id="{job["id"]}"]');expect(row).to_be_visible();row.locator('.history-actions').click()
  expect(page.locator('#actionDialog')).to_be_visible();page.locator('#actionDialog .close-dialog').click();page.locator('#downloadHistoryDialog > button').click()
  page.mouse.wheel(0,600);assert page.locator('.topbar').bounding_box()['y']==0
  assert not static,static
  assert not errors,errors
  context.close();print(f'{width}x{height}: self-contained cached Vortex, native menu, history and fixed title passed',flush=True)
 browser.close()
