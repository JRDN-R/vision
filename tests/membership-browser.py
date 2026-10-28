"""Real bundled welcome page; mocked identity and API, no production traffic."""
import importlib.util
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('vortex_fixture',ROOT/'tests/vortex-browser.py')
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
SHOTS=ROOT/'.qa/membership';SHOTS.mkdir(parents=True,exist_ok=True)
class MembershipFixture(fixture.Fixture):
    status='pending'
    def route(self,route):
        if route.request.url==fixture.BACKEND+'/api/account/membership':
            if self.status=='offline':return route.abort()
            return self.respond(route,{'status':self.status,'approved':self.status=='approved','admin':False})
        return super().route(route)
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    for width,height in [(390,844),(320,568),(1440,1000)]:
        api=MembershipFixture();ctx=browser.new_context(viewport={'width':width,'height':height},reduced_motion='reduce')
        ctx.route('**/*',api.route);page=ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(fixture.URL.replace('vortex/',''))
        page.locator('#accountGateSignIn').wait_for(state='visible')
        assert page.locator('#accountGateEmailArea').is_hidden()
        assert page.locator('#accountGateTrial').is_hidden()
        assert page.locator('.account-gate-logo').evaluate('el=>el.complete&&el.naturalWidth>0')
        page.screenshot(path=str(SHOTS/f'welcome-{width}.png'))
        page.locator('#whyVisionButton').click();assert page.locator('#whyVisionDialog').is_visible()
        page.locator('#whyVisionDone').click();assert page.locator('#whyVisionDialog').is_hidden()
        page.locator('#accountGateEmailToggle').click();page.locator('#accountGateEmail').fill('new@example.test');page.locator('#accountGatePassword').fill('fixture-password')
        page.locator('#accountGateEmailCreate').click();page.locator('#accountMembership').wait_for(state='visible')
        assert page.locator('#accountGateEmailArea').is_hidden()
        assert page.locator('#accountGateSignIn').is_hidden()
        assert page.locator('main.app').evaluate('el=>el.inert')
        assert not any(path=='/api/account/openai-key' for path in api.vision_calls)
        page.screenshot(path=str(SHOTS/f'pending-{width}.png'))
        api.status='denied';page.locator('#accountMembershipRefresh').click();page.get_by_role('heading',name='Account request not approved').wait_for()
        api.status='offline';page.locator('#accountMembershipRefresh').click();page.wait_for_timeout(250);assert page.locator('main.app').evaluate('el=>el.inert')
        api.status='approved';page.locator('#accountMembershipRefresh').click();page.locator('#accountGate').wait_for(state='hidden')
        assert not page.locator('html').evaluate("el=>el.classList.contains('account-locked')")
        assert not errors,errors
        ctx.close()
    # A direct Vortex URL for a pending restored account returns to the welcome gate.
    api=MembershipFixture();ctx=browser.new_context(viewport={'width':390,'height':844});ctx.add_init_script("localStorage.setItem('__fixtureUID','alice')");ctx.route('**/*',api.route)
    page=ctx.new_page();page.goto(fixture.URL);page.locator('#accountMembership').wait_for(state='visible');assert 'continue=vortex' in page.url
    ctx.close();browser.close()
print('Welcome layouts, question-mark dialog, email signup, pending/denied/offline/approved states, and direct Vortex membership gate passed.')
