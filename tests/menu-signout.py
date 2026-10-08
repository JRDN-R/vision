"""Menu sign-out confirmation uses the existing auth action only after Yes."""
import importlib.util
import os
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('nav',ROOT/'tests/compact-navigation.py')
nav=importlib.util.module_from_spec(spec);spec.loader.exec_module(nav)
where=nav.SHELL.rfind('\n})();')
shell=nav.SHELL[:where]+"\nwindow.signOutCalls=0;accountGoogleSignOut=async()=>{window.signOutCalls++;navSignIn('');};"+nav.SHELL[where:]
with sync_playwright() as pw:
 browser=pw.chromium.launch(headless=True,**({'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}))
 for width in (390,1280):
  for workspace in ('workspace','venture'):
   context=browser.new_context(viewport={'width':width,'height':844})
   context.route('**/*',lambda r:r.fulfill(body=shell,content_type='text/html') if r.request.url==nav.URL else r.abort())
   page=context.new_page();page.goto(nav.URL);page.wait_for_timeout(450);page.evaluate('navSignIn()')
   page.locator('#workspaceNews').wait_for(state='visible');page.locator('#workspaceNews button').click()
   if workspace=='venture':
    if width<=760:page.locator('#workspaceSwitchButton').click()
    else:page.locator('#workspaceMenuButton').click();page.locator('#runProjectBtn').click()
   page.locator('#workspaceMenuButton').click()
   icon=page.locator('#'+workspace+'MenuSignOut');news=page.locator('#'+workspace+'MenuNews');dialog=page.locator('#workspaceSignOutDialog')
   ib=icon.bounding_box();nb=news.bounding_box();assert ib['x']>nb['x']+nb['width'] and abs(ib['y']-nb['y'])<5
   for cancel in ('no','escape','outside'):
    icon.click();assert dialog.is_visible();assert page.evaluate('document.activeElement.id')=='workspaceSignOutNo'
    if cancel=='no':page.locator('#workspaceSignOutNo').click()
    elif cancel=='escape':page.keyboard.press('Escape')
    else:nav.outside(page,'#workspaceSignOutDialog')
    assert dialog.is_hidden() and page.evaluate('signOutCalls')==0
    assert page.evaluate('document.activeElement.id')==workspace+'MenuSignOut'
   if width==390:
    page.screenshot(path=str(nav.SHOTS/(workspace+'-signout.png')))
    icon.click();page.screenshot(path=str(nav.SHOTS/(workspace+'-signout-confirm.png')));page.locator('#workspaceSignOutNo').click()
   icon.click();page.locator('#workspaceSignOutYes').click()
   assert dialog.is_hidden() and page.evaluate('signOutCalls')==1
   assert page.locator('#accountGate').is_visible()
   print(f'{width}px {workspace}: placement, No/Escape/outside cancellation, focus return and confirmed sign-out passed',flush=True)
   context.close()
 browser.close()
