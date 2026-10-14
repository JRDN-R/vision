"""Bundled layout checks with a local test identity; every external request is blocked."""
from pathlib import Path
import os
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1]
shots=root/'tests/venture-screenshots';shots.mkdir(exist_ok=True)
shell=(root/'Vision.html').read_text()
probe='''window.checkNavigation=()=>{
accountSignedIn=()=>true;accountCanUseApp=()=>true;ventureIdentity=()=>{venture.uid='fixture';};ventureScope=()=> 'fixture';
ventureJSON=async (path)=>path==='/health'?{capabilities:{ventureV2:true}}:path==='/venture/preferences'?{settings:VENTURE_DEFAULTS}:path==='/venture/conversations'?{conversations:[]}:{};
document.querySelectorAll('[data-account-inert]').forEach(el=>{el.inert=el.dataset.accountInert==='true';delete el.dataset.accountInert;});workspace.uid='fixture';document.documentElement.classList.remove('account-locked');document.getElementById('accountGate').hidden=true;workspaceSyncSwitch();
};'''
where=shell.rfind('\n})();');shell=shell[:where]+probe+shell[where:]
with sync_playwright() as p:
 b=p.chromium.launch(headless=True,**({'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}))
 for width in (320,390,430,760,761,1024,1280):
  page=b.new_page(viewport={'width':width,'height':844},has_touch=True);errs=[];page.on('pageerror',lambda e:errs.append(str(e)));page.route('**/*',lambda r:r.abort());page.set_content(shell);page.wait_for_timeout(400);page.evaluate('checkNavigation()');page.wait_for_timeout(900)
  if page.locator('#workspaceNews').is_visible():page.locator('#workspaceNews button').click()
  switch=page.locator('#workspaceSwitchButton')
  assert switch.is_visible(), f'Workspace switch hidden at {width}px'
  assert switch.get_attribute('aria-label')=='Open Venture'
  assert switch.locator('[data-direction="right"]').is_visible()
  start=switch.bounding_box();assert start['width']==44 and start['height']==44
  if width<=760:
   assert switch.evaluate('el=>el.parentElement===document.body')
   panel=page.locator('#sidebarToggle');assert panel.is_visible();panel.click();assert panel.get_attribute('aria-expanded')=='true';panel.click()
   page.screenshot(path=str(shots/f'fixed-switch-vision-{width}.png'))
   page.locator('#headerReveal').click();page.wait_for_timeout(250)
   assert switch.bounding_box()==start
  else:
   assert switch.evaluate('el=>el.parentElement.id==="appHeader"')
   assert page.locator('.workspace-switch-slot').is_hidden()
   assert start['x']>=width-110, start
   page.screenshot(path=str(shots/f'inline-switch-vision-{width}.png'))
  switch.click();page.wait_for_timeout(900)
  assert switch.is_visible() and switch.get_attribute('aria-label')=='Return to Vision'
  assert switch.locator('[data-direction="left"]').is_visible()
  if width<=760:
   assert switch.bounding_box()==start
   # The mobile switch sits in its reserved header slot, away from other buttons.
   slot=page.locator('.workspace-switch-slot').bounding_box();assert abs(start['x']-slot['x'])<2,(start,slot)
   for ident in ('ventureNewCompact','workspaceSourcesButton','ventureAvatar'):
    r=page.locator('#'+ident).bounding_box();assert r['x']>=0
    assert r['x']+r['width']<=start['x']+1 or r['x']>=start['x']+start['width']-1,(ident,r,start)
   page.screenshot(path=str(shots/f'fixed-switch-venture-{width}.png'))
  else:
   assert switch.evaluate('el=>!!el.closest(".venture-header")')
   current=switch.bounding_box();avatar=page.locator('#ventureAvatar').bounding_box()
   assert current['x']>=avatar['x']+avatar['width']-1,(current,avatar)
   page.screenshot(path=str(shots/f'inline-switch-venture-{width}.png'))
  switch.click();page.wait_for_timeout(300)
  assert switch.is_visible() and switch.get_attribute('aria-label')=='Open Venture'
  assert page.evaluate('!document.querySelector("main.app").inert')
  if width<=760:assert switch.bounding_box()==start
  else:assert switch.evaluate('el=>el.parentElement.id==="appHeader"')
  assert not errs,errs
  print(width,'px: responsive placement, preserved animations and round trip passed');page.close()
 b.close()
