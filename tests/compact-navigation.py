"""Real bundled UI, deterministic local identities, and no external/API traffic."""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / 'tests/venture-screenshots'
SHOTS.mkdir(exist_ok=True)
SHELL = (ROOT / 'Vision.html').read_text()
PROBE = r'''
window.navSignIn=(uid='fixture')=>{
 accountSignedIn=()=>!!uid;accountCanUseApp=()=>!!uid;ventureIdentity=()=>{venture.uid=uid;};ventureScope=()=>uid;
 accountFirebase={currentUser:uid?{uid,displayName:'Jordan Rapp',email:'jordan@example.test'}:null};
 ventureJSON=async path=>path==='/health'?{capabilities:{ventureV2:true}}:path==='/venture/preferences'?{settings:VENTURE_DEFAULTS}:path==='/venture/conversations'?{conversations:[]}:{};
 document.querySelectorAll('[data-account-inert]').forEach(el=>{el.inert=false;delete el.dataset.accountInert;});
 workspace.uid=uid;venture.uid=uid;document.documentElement.classList.toggle('account-locked',!uid);$('accountGate').hidden=!!uid;
 state.title='Script Write';$('projectTitle').value=state.title;$('saveState').textContent='Saved to FUPCJ Server';workspaceSyncSwitch();
};
window.navTest={header:()=>({open:document.documentElement.classList.contains('header-open'),inert:appHeader.inert}),
 showHeader:()=>showMobileHeader(),receipt:()=>navigationReceipt(),
 nextVersion:()=>{document.querySelector('meta[name="vision-version"]').content='9.9.9.9';navigationCheckNews();},
 protectExport:value=>{busy=value;}, checkNews:()=>navigationCheckNews(),
 unsupportedStorage:()=>{Object.defineProperty(window,'localStorage',{get(){throw new Error('Storage blocked');}});},
 locked:()=>!accountSignedIn()};
'''
where = SHELL.rfind('\n})();')
SHELL = SHELL[:where] + PROBE + SHELL[where:]
URL = 'http://127.0.0.1:8899/'

def outside(page, dialog):
    box = page.locator(dialog).bounding_box()
    page.mouse.click(2, max(2, box['y'] - 4))

def run():
    with sync_playwright() as pw:
        options = {'executable_path': os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}
        browser = pw.chromium.launch(headless=True, **options)
        for width in (320, 390, 430, 760, 1280):
            context = browser.new_context(viewport={'width': width, 'height': 844}, has_touch=width<=760)
            context.route('**/*', lambda r: r.fulfill(body=SHELL, content_type='text/html') if r.request.url==URL else r.abort())
            page = context.new_page(); errors=[]
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(URL); page.wait_for_timeout(450)
            assert page.locator('#workspaceNews').is_hidden(), 'Must not show before sign-in'
            page.evaluate('navSignIn()'); page.locator('#workspaceNews').wait_for(state='visible')
            assert page.locator('#workspaceNews li').count()>=1
            assert page.evaluate('localStorage.getItem(navTest.receipt())')=='seen'
            if width==390: page.screenshot(path=str(SHOTS/'compact-whats-new.png'))
            outside(page,'#workspaceNews'); page.locator('#workspaceNews').wait_for(state='hidden')
            toggle=page.locator('#workspaceMenuButton'); anchor=toggle.bounding_box()
            chevron=page.locator('#workspaceSwitchButton'); start=chevron.bounding_box()
            header=page.locator('#appHeader').bounding_box()
            assert header['height']==(65 if width<=760 else 76), header
            if width<=760:
                assert page.evaluate('navTest.header().open')
                page.wait_for_timeout(4700 if width==390 else 100)
                assert page.evaluate('navTest.header().open'), 'Idle time hid header'
                assert start['width']==44 and start['height']==44
                assert page.locator('#projectTitle').bounding_box()['width']>=25
                if width==390: page.screenshot(path=str(SHOTS/'compact-header.png'))
                page.mouse.click(width/2,400)
                assert not page.evaluate('navTest.header().open')
                assert chevron.bounding_box()==start and toggle.bounding_box()==anchor
                page.locator('#headerReveal').click();assert page.evaluate('navTest.header().open')
                page.locator('#appHeader .brand').dispatch_event('pointerdown', {'pointerId':7,'pointerType':'touch','clientX':80,'clientY':42,'bubbles':True})
                page.dispatch_event('body','pointermove',{'pointerId':7,'pointerType':'touch','clientX':81,'clientY':9,'bubbles':True})
                assert not page.evaluate('navTest.header().open'), 'Upward swipe failed'
                page.locator('#headerReveal').click()
            else: assert chevron.is_hidden()
            toggle.click();page.locator('#workspaceMenu').wait_for(state='visible');page.wait_for_timeout(240)
            menu_width=page.locator('#workspaceMenu').bounding_box()['width']
            for ident in ('accountButton','newBtn','openBtn','saveBtn','runProjectBtn','exportBtn'):
                assert page.locator('#workspaceMenu #'+ident).is_visible()
                assert page.locator('#'+ident+' .workspace-nav-label').inner_text()
            if width==390: page.screenshot(path=str(SHOTS/'compact-vision-menu.png'))
            # Nested dialog goes back to the menu. Clicking inside stays open.
            page.locator('#workspaceMenuNews').click();page.locator('#workspaceNewsTitle').click()
            assert page.locator('#workspaceNews').is_visible()
            outside(page,'#workspaceNews');assert page.locator('#workspaceMenu').is_visible()
            assert page.evaluate('document.querySelector("main.app").inert')
            page.keyboard.press('Escape');assert page.locator('#workspaceMenu').is_hidden()
            assert page.evaluate('document.activeElement.id')=='workspaceMenuButton'
            toggle.click();page.locator('#workspaceMenuScrim').click(position={'x':width-5,'y':400})
            assert page.locator('#workspaceMenu').is_hidden()
            assert not page.evaluate('document.querySelector("main.app").inert')
            # Existing original Projects action still opens its actual dialog.
            toggle.click();page.locator('#openBtn').click();assert page.locator('#projectsDialog').is_visible()
            outside(page,'#projectsDialog');assert page.locator('#projectsDialog').is_hidden()
            if page.locator('#workspaceMenu').is_visible():page.keyboard.press('Escape')
            # Shared button and animation stay anchored in Venture, even with history open.
            if width<=760:chevron.click()
            else:toggle.click();page.locator('#runProjectBtn').click()
            page.locator('#visionVenture').wait_for(state='visible');page.wait_for_timeout(350)
            assert toggle.bounding_box()==anchor
            toggle.click();page.wait_for_timeout(240)
            assert abs(page.locator('#ventureSidebar').bounding_box()['width']-menu_width)<1,(width,menu_width,page.locator('#ventureSidebar').bounding_box())
            if width<=760:assert chevron.is_visible() and chevron.bounding_box()==start
            if width==390:page.screenshot(path=str(SHOTS/'compact-venture-menu.png'))
            page.locator('#ventureMenuNews').click();outside(page,'#workspaceNews')
            assert page.locator('#visionVenture').evaluate('el=>el.classList.contains("history-open")')
            page.locator('#ventureSearch').focus();page.keyboard.press('Escape')
            assert page.evaluate('document.activeElement.id')=='workspaceMenuButton'
            toggle.click();page.locator('#ventureMenuNews').focus();page.keyboard.press('Tab')
            assert page.evaluate('document.activeElement.id')=='ventureProfileButton'
            page.keyboard.press('Tab')
            expected='workspaceSwitchButton' if width<=760 else 'ventureNew'
            assert page.evaluate('document.activeElement.id')==expected
            page.locator('#ventureScrim').click(position={'x':width-5,'y':400})
            assert not page.locator('#visionVenture').evaluate('el=>el.classList.contains("history-open")')
            page.locator('#ventureSettingsToggle').click();assert page.locator('#ventureSettings').is_visible()
            page.mouse.click(width/2,760);assert page.locator('#ventureSettings').is_hidden()
            if width<=760:chevron.click();assert chevron.bounding_box()==start
            else:page.locator('#ventureBack').click()
            assert not page.evaluate('document.querySelector("main.app").inert')
            # Shared dialog dismissal honors the existing export busy guard.
            page.evaluate('document.querySelector("#exportDialog").showModal();navTest.protectExport(true)')
            outside(page,'#exportDialog');assert page.locator('#exportDialog').is_visible()
            page.evaluate('navTest.protectExport(false)');outside(page,'#exportDialog');assert page.locator('#exportDialog').is_hidden()
            # Native dialog drags crossing the edge cannot close a menu.
            page.locator('#workspaceHeaderNews').click()
            page.locator('#workspaceNewsTitle').hover();page.mouse.down();page.mouse.move(2,2);page.mouse.up()
            assert page.locator('#workspaceNews').is_visible();page.keyboard.press('Escape')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            assert not errors,errors
            if width==390:
                page.reload();page.wait_for_timeout(450);page.evaluate('navSignIn()');page.wait_for_timeout(400)
                assert page.locator('#workspaceNews').is_hidden(),'Receipt did not survive reload'
                page.evaluate('navSignIn("");navSignIn("second-user")');page.locator('#workspaceNews').wait_for(state='visible')
                page.keyboard.press('Escape');page.evaluate('navSignIn("fixture")');page.wait_for_timeout(400)
                assert page.locator('#workspaceNews').is_hidden(),'Accounts shared the wrong receipt'
                page.evaluate('document.querySelector("#helpDialog").showModal();navTest.nextVersion()');page.wait_for_timeout(400)
                assert page.locator('#workspaceNews').is_hidden(),'Notes covered another open dialog'
                page.keyboard.press('Escape');page.locator('#workspaceNews').wait_for(state='visible');page.keyboard.press('Escape')
                page.evaluate('navTest.unsupportedStorage();navSignIn("blocked-storage-user")');page.locator('#workspaceNews').wait_for(state='visible');page.keyboard.press('Escape')
                page.evaluate('navSignIn("");navSignIn("blocked-storage-user");navTest.checkNews()');page.wait_for_timeout(400)
                assert page.locator('#workspaceNews').is_hidden(),'Storage failure caused repeated notices'
                assert not errors,errors
            print(f'{width}px: compact header, menu parity, anchored controls, dismissal and release notes passed')
            context.close()
        browser.close()

if __name__=='__main__': run()
