"""Regression checks for real bottom-sheet geometry, floating menus and search focus."""
import importlib.util
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('belt_fixture',ROOT/'tests/toolbelt-browser.py')
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
where=fixture.shell.rfind('\n})();')
SHELL=fixture.shell[:where]+'\nwindow.floatingToast=toast;'+fixture.shell[where:]
SHOTS=ROOT/'tests/venture-screenshots'

def run():
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,**({'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}))
  for width,height in [(320,568),(390,844),(430,932),(760,844),(1280,844)]:
   context=browser.new_context(viewport={'width':width,'height':height},has_touch=width<=760)
   context.route('**/*',lambda r:r.fulfill(body=SHELL,content_type='text/html') if r.request.url==fixture.fixture.URL else r.abort())
   page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto(fixture.fixture.URL);page.wait_for_timeout(450);page.evaluate('navSignIn()');page.locator('#workspaceNews').wait_for(state='visible');page.locator('#workspaceNews button').click()
   page.evaluate('beltFixture()');page.wait_for_timeout(300)
   menu=page.locator('#workspaceMenuButton');details=page.locator('#sidebarToggle');plus=page.locator('#addNodeButton');sheet=page.locator('#inspectorPanel')
   if details.get_attribute('aria-expanded')=='true':details.click()
   mb=menu.bounding_box();b=page.locator('#board').bounding_box();db=details.bounding_box();pb=plus.bounding_box()
   assert db['y']>height-70 and pb['y']+pb['height']<db['y'],(pb,db,height)
   assert abs((pb['x']+pb['width']/2)-(db['x']+db['width']/2))<2
   assert plus.inner_text()=='' and plus.get_attribute('aria-label')=='Add a node'
   zoom=page.locator('.zoom').bounding_box();assert zoom['x']+zoom['width']<db['x'],(zoom,db)
   details.click();page.wait_for_timeout(280)
   if width<=760:
    sb=sheet.bounding_box();assert abs(sb['y']+sb['height']-height)<2 and sb['y']>height*.35,(sb,height)
    assert sb['x']==0 and abs(sb['width']-width)<1
    db=details.bounding_box();pb=plus.bounding_box();assert db['y']+db['height']<=sb['y'] and pb['y']+pb['height']<db['y']
    # Toast placement must not follow the relocated Details control off-screen.
    page.evaluate("floatingToast('Details stay at the bottom')");page.wait_for_timeout(80)
    tb=page.locator('#toast').bounding_box();assert tb['y']+tb['height']<sb['y'],(tb,sb)
    if width==390:page.screenshot(path=str(SHOTS/'bottom-details.png'))
    page.locator('#boardSheetSize').click();page.wait_for_timeout(100)
    expanded=sheet.bounding_box();assert expanded['height']>sb['height'] and abs(expanded['y']+expanded['height']-height)<2
    details.click();assert sheet.is_hidden()
    details.click();page.wait_for_timeout(250)
    page.mouse.click(b['x']+80,150);assert sheet.is_hidden(),'Outside click did not dismiss bottom sheet'
   else:
    sb=sheet.bounding_box();b=page.locator('#board').bounding_box();assert sb['x']>=b['x']+b['width']-1
    details.click()
   # Preserve the original add-node action while replacing only its presentation.
   before=page.locator('#world .node').count();plus.click();page.wait_for_function('count=>document.querySelectorAll("#world .node").length===count',arg=before+1)
   if width==390:
    page.locator('#toast').wait_for(state='hidden')
    if details.get_attribute('aria-expanded')=='true':details.click()
    page.wait_for_timeout(250)
    page.screenshot(path=str(SHOTS/'floating-board.png'))
   menu.click();page.locator('#workspaceMenu').wait_for(state='visible');page.wait_for_function("()=>document.getElementById('workspaceMenuGlyph').dataset.phase==='hidden'")
   assert page.locator('#workspaceMenuGlyph').evaluate("el=>getComputedStyle(el).opacity")=='0'
   assert menu.evaluate("el=>getComputedStyle(el).backgroundColor")=='rgba(0, 0, 0, 0)'
   assert page.evaluate('document.activeElement.id')=='workspaceMenu'
   assert page.locator('.workspace-menu-heading').inner_text()=='Vision'
   page.evaluate('document.fonts.ready');assert page.evaluate('document.fonts.check(\'30px "Lilita One"\')')
   assert page.locator('.workspace-menu-heading .workspace-brand-name').evaluate("el=>getComputedStyle(el).fontFamily").startswith('"Lilita One"')
   mw=page.locator('#workspaceMenu').bounding_box()['width']
   if width==390:page.screenshot(path=str(SHOTS/'floating-vision-menu.png'))
   page.keyboard.press('Escape');page.wait_for_function("()=>document.getElementById('workspaceMenuGlyph').dataset.phase==='visible'")
   assert menu.bounding_box()==mb and page.evaluate('document.activeElement.id')=='workspaceMenuButton'
   if width<=760:page.locator('#workspaceSwitchButton').click()
   else:menu.click();page.locator('#runProjectBtn').click()
   page.locator('#visionVenture').wait_for(state='visible');menu.click();page.wait_for_timeout(500)
   assert page.evaluate('document.activeElement.id')=='ventureSidebar','Opening Venture focused an input'
   assert page.locator('#ventureSidebar .workspace-brand-name').inner_text()=='Venture'
   assert abs(page.locator('#ventureSidebar').bounding_box()['width']-mw)<1
   assert menu.bounding_box()==mb
   if width==390:page.screenshot(path=str(SHOTS/'floating-venture-menu.png'))
   page.locator('#ventureSearch').click();assert page.evaluate('document.activeElement.id')=='ventureSearch'
   page.locator('#ventureScrim').click(position={'x':width-3,'y':200});assert page.locator('#ventureSidebar').evaluate('el=>el.inert')
   # Rapid toggles and reduced motion must settle to the correct visual state.
   for _ in range(3):menu.click();page.keyboard.press('Escape')
   page.wait_for_function("()=>document.getElementById('workspaceMenuGlyph').dataset.phase==='visible'")
   page.emulate_media(reduced_motion='reduce');menu.click()
   assert page.locator('#workspaceMenuGlyph').evaluate("el=>getComputedStyle(el).opacity")=='0'
   assert page.evaluate('document.activeElement.id')=='ventureSidebar'
   page.keyboard.press('Escape');assert page.locator('#workspaceMenuGlyph').evaluate("el=>getComputedStyle(el).opacity")=='1'
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1 && document.documentElement.scrollHeight<=innerHeight+1')
   assert not errors,errors
   print(f'{width}×{height}: bottom controls, sheet geometry, floating Lottie, bundled font, menu focus and reduced motion passed',flush=True)
   context.close()
  browser.close()

if __name__=='__main__':run()
