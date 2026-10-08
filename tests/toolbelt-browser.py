"""Bundled dock/gesture/tool tests with local fixtures and no external traffic."""
import importlib.util
import os
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('nav_fixture',ROOT/'tests/compact-navigation.py')
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
shell=fixture.SHELL
probe=r'''
window.beltFixture=()=>{
 const canvas=document.createElement('canvas');canvas.width=320;canvas.height=200;const c=canvas.getContext('2d');c.fillStyle='#435448';c.fillRect(0,0,320,200);
 state.nodes=[{id:'belt-reference',kind:'image',title:'Reference',prompt:'Inspect this image.',src:canvas.toDataURL(),width:320,height:200,x:30,y:120,caption:'Test reference',annotations:[],attachments:[]}];
 state.edges=[];state.view={x:20,y:40,scale:.55};renderAll();
};
window.beltSnapshot=()=>JSON.stringify({nodes:state.nodes,edges:state.edges,view:state.view});
window.beltProbe=()=>({side:toolbelt.side,mode:toolbelt.mode,open:toolbelt.open,armed:toolbelt.armed,tool,press:!!toolbelt.press,marks:state.nodes[0]?.annotations?.length||0});
'''
where=shell.rfind('\n})();');shell=shell[:where]+probe+shell[where:]
SHOTS=fixture.SHOTS

def run():
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,**({'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}))
  for width,height in [(320,844),(390,844),(430,844),(760,844),(1280,844),(390,568)]:
   context=browser.new_context(viewport={'width':width,'height':height},has_touch=width<=760)
   context.route('**/*',lambda r:r.fulfill(body=shell,content_type='text/html') if r.request.url==fixture.URL else r.abort())
   page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto(fixture.URL);page.wait_for_timeout(400);page.evaluate('navSignIn()');page.locator('#workspaceNews').wait_for(state='visible');page.locator('#workspaceNews button').click()
   page.evaluate('beltFixture()');page.wait_for_timeout(450)
   belt=page.locator('#toolbelt');toggle=page.locator('#toolbeltToggle');board=page.locator('#board')
   original=page.evaluate('beltSnapshot()')
   def edge(side):
    r=page.locator('#toolbeltRail').bounding_box();b=board.bounding_box();t=belt.bounding_box()
    assert t['x']>=r['x'] and t['x']+t['width']<=r['x']+r['width']+1,(r,t)
    assert (r['x']+r['width']<=b['x']+.5) if side=='left' else (r['x']>=b['x']+b['width']-.5),(side,r,b)
    assert b['width']>150 and t['y']>=0 and t['y']+t['height']<=height+1,(r,b,t)
   edge('left')
   # Board use alone cannot collapse an opened, unarmed belt.
   b=board.bounding_box();page.mouse.click(b['x']+b['width']/2,height-65)
   assert page.evaluate('beltProbe().open')
   page.locator('[data-tool="arrow"]').click();assert page.evaluate('beltProbe().armed')
   assert page.evaluate('beltProbe().open'),'Selecting a tool prematurely collapsed the belt'
   # Collapse occurs after the board gesture finishes, never in the middle of drawing.
   rect=page.locator('#node-belt-reference .canvas-wrap').bounding_box()
   x,y=rect['x']+rect['width']*.25,rect['y']+35
   page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+50,y+25)
   assert page.evaluate('beltProbe().open')
   page.mouse.up();page.wait_for_function('!beltProbe().open')
   assert page.evaluate('beltProbe().marks')==1,'Collapsing interrupted the drawing tool'
   page.wait_for_timeout(1800)
   assert page.locator('#toolbeltSlot svg').count()==1
   assert page.locator('#toolbeltSlot circle').count()==1,'Animation did not settle on gear'
   assert page.locator('#toolbeltBody').evaluate('el=>el.inert')
   assert page.evaluate('beltProbe().tool')=='arrow'
   assert abs(belt.bounding_box()['height']-50)<1
   edge('left')
   if width==390 and height==844:page.screenshot(path=str(SHOTS/'toolbelt-collapsed.png'))
   # Docking the collapsed control never floats it above board/node coordinates.
   saved=page.evaluate('beltSnapshot()');toggle.focus();page.keyboard.press('ArrowRight');page.wait_for_timeout(350);edge('right')
   assert page.evaluate('document.activeElement.id')=='toolbeltToggle','Docking lost keyboard focus'
   page.keyboard.press('ArrowRight');assert page.evaluate('document.activeElement.id')=='toolbeltToggle'
   page.keyboard.press('ArrowLeft');assert page.evaluate('beltProbe().side')=='left'
   page.keyboard.press('ArrowRight');page.wait_for_timeout(350);edge('right')
   assert page.evaluate('beltSnapshot()')==saved
   assert not page.evaluate('beltProbe().open')
   toggle.click();page.wait_for_timeout(350);assert page.evaluate('beltProbe().open')
   page.locator('#toolbeltPin').click();assert page.evaluate('beltProbe().mode')=='pinned'
   page.locator('[data-tool="select"]').click();b=board.bounding_box();page.mouse.click(b['x']+b['width']/2,height-65)
   assert page.evaluate('beltProbe().open')
   toggle.click();assert page.evaluate('beltProbe().open'),'Pinned belt collapsed'
   if width==390 and height==844:page.screenshot(path=str(SHOTS/'toolbelt-pinned-right.png'))
   # Hold then drag has a directional snap; ordinary short drags do not dock.
   handle=toggle.bounding_box();hx=handle['x']+handle['width']/2;hy=handle['y']+handle['height']/2
   page.mouse.move(hx,hy);page.mouse.down();page.mouse.move(hx-70,hy+8);page.mouse.up();assert page.evaluate('beltProbe().side')=='right'
   page.mouse.move(hx,hy);page.mouse.down();page.wait_for_timeout(330);page.mouse.move(hx-85,hy+8);page.mouse.up();page.wait_for_timeout(350)
   assert page.evaluate('beltProbe().side')=='left';edge('left')
   assert page.evaluate('beltSnapshot()')==saved
   # User preferences are per-account and survive a reload; another account gets defaults.
   if width==390 and height==844:
    page.reload();page.wait_for_timeout(400);page.evaluate('navSignIn()');page.wait_for_timeout(400)
    assert page.locator('#workspaceNews').is_hidden()
    assert page.evaluate('beltProbe().mode')=='pinned' and page.evaluate('beltProbe().side')=='left'
    page.evaluate('navSignIn("second-belt-user")');page.locator('#workspaceNews').wait_for(state='visible');page.locator('#workspaceNews button').click()
    assert page.evaluate('beltProbe().mode')=='auto' and page.evaluate('beltProbe().side')=='left'
    page.evaluate('navSignIn("fixture")');assert page.evaluate('beltProbe().mode')=='pinned'
   page.locator('#toolbeltPin').click();assert page.evaluate('beltProbe().mode')=='auto'
   page.emulate_media(reduced_motion='reduce');toggle.click();assert not page.evaluate('beltProbe().open')
   assert page.locator('#toolbeltSlot circle').count()==1
   assert page.locator('#toolbelt').evaluate('el=>el.getAnimations({subtree:true}).length')==0
   toggle.click();assert page.evaluate('beltProbe().open')
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
   assert not errors,errors
   print(f'{width}x{height}: reserved edges, post-use collapse, slot animation, pinning, drag docking, persistence, reduced motion passed',flush=True)
   context.close()
  browser.close()
if __name__=='__main__':run()
