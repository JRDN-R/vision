"""Vortex integration in the real built Vision/Venture shell, without network.

Run after ``python web/build.py``. Uses deterministic accounts and intercepts
only navigation's final location assignment to check the actual save sequence.
"""
import importlib.util
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / '.qa' / 'vortex'
spec = importlib.util.spec_from_file_location('nav_fixture', ROOT / 'tests/compact-navigation.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
PROBE = r'''
window.vortexNavCalls=[];
window.vortexSaveFixture=(mode='success')=>{
  vortexNavCalls=[]; dirty=true; projectPending=true; busy=false; ioBusy=false;
  projectBackup=async()=>{vortexNavCalls.push('backup');};
  flushProjectSave=async()=>{
    vortexNavCalls.push('save');
    if(mode==='failure')throw new Error('Fixture save failure');
    if(mode==='switch')navSignIn('second-account');
  };
};
'''
SHELL = fixture.SHELL.replace('location.assign(navigationVortexURL());', 'vortexNavCalls.push(navigationVortexURL());')
where = SHELL.rfind('\n})();')
SHELL = SHELL[:where] + PROBE + SHELL[where:]


def run():
    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        options = {'executable_path': os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}
        browser = pw.chromium.launch(headless=True, **options)
        for width in (320, 390, 1280):
            context = browser.new_context(viewport={'width': width, 'height': 844}, has_touch=width < 760)
            context.route('**/*', lambda route: route.fulfill(body=SHELL, content_type='text/html') if route.request.url == fixture.URL else route.abort())
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(fixture.URL)
            page.wait_for_timeout(300)
            page.evaluate('navSignIn()')
            page.locator('#workspaceNews').wait_for(state='visible')
            page.keyboard.press('Escape')
            page.locator('#workspaceMenuButton').click()
            page.wait_for_timeout(350)
            link = page.locator('#workspaceVortexLink')
            assert link.is_visible()
            assert link.locator('img.workspace-vortex-logo').count() == 1
            assert link.locator('img.workspace-vortex-logo').evaluate("el => el.complete && el.naturalWidth > 0 && el.getAttribute('src').startsWith('data:image/png;base64,')")
            assert link.get_attribute('href') == fixture.URL + 'vortex/'
            assert link.evaluate("el=>el.previousElementSibling.textContent") == 'Help'
            assert link.evaluate("el=>el.previousElementSibling.previousElementSibling.textContent") == 'Processing activity'
            assert link.evaluate('el=>el===el.parentElement.lastElementChild')
            assert link.evaluate('el=>getComputedStyle(el).color') != page.locator('#exportBtn').evaluate('el=>getComputedStyle(el).color')
            page.screenshot(path=str(SHOTS / f'vision-menu-{width}.png'))
            page.evaluate('vortexSaveFixture()')
            link.click()
            page.wait_for_function('vortexNavCalls.length===3')
            assert page.evaluate('vortexNavCalls') == ['backup', 'save', fixture.URL + 'vortex/']
            assert link.get_attribute('aria-busy') is None
            # A failed save or account change cannot navigate away with stale data.
            page.evaluate("vortexSaveFixture('failure')")
            link.click()
            page.wait_for_timeout(80)
            assert page.evaluate('vortexNavCalls') == ['backup', 'save']
            page.evaluate("vortexSaveFixture('switch')")
            link.click()
            page.wait_for_timeout(80)
            assert page.evaluate('vortexNavCalls') == ['backup', 'save']
            page.evaluate('navSignIn();vortexSaveFixture()')
            page.keyboard.press('Escape')
            if width < 760:
                page.locator('#workspaceSwitchButton').click()
            else:
                page.locator('#workspaceMenuButton').click()
                page.locator('#runProjectBtn').click()
            page.locator('#visionVenture').wait_for(state='visible')
            page.locator('#workspaceMenuButton').click()
            page.wait_for_timeout(250)
            venture = page.locator('#ventureVortexLink')
            assert venture.is_visible()
            assert venture.locator('img.workspace-vortex-logo').count() == 1
            assert venture.locator('img.workspace-vortex-logo').evaluate("el => el.complete && el.naturalWidth > 0 && el.getAttribute('src').startsWith('data:image/png;base64,')")
            assert venture.evaluate("el=>el.nextElementSibling.classList.contains('venture-sidebar-foot')")
            assert venture.evaluate("el=>!el.closest('#ventureHistory')")
            page.evaluate('''document.querySelector('#ventureHistory').innerHTML=Array.from({length:80},(_,i)=>'<div class="venture-history-row"><button class="venture-history-open">Conversation '+i+'</button></div>').join('')''')
            before = venture.bounding_box()
            assert page.locator('#ventureHistory').evaluate('el=>el.scrollHeight>el.clientHeight')
            page.locator('#ventureHistory').evaluate('el=>el.scrollTop=el.scrollHeight')
            assert venture.bounding_box() == before, 'Vortex moved with scrolling conversation history'
            assert before['y'] + before['height'] < 844
            page.locator('#toast').wait_for(state='hidden')
            page.screenshot(path=str(SHOTS / f'venture-menu-{width}.png'))
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            assert not errors, errors
            print(f'{width}px: Vortex nav placement, color, fixed footer and safe-save navigation passed', flush=True)
            context.close()
        browser.close()


if __name__ == '__main__':
    run()
