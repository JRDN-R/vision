"""Return from Vortex without flashing login or waiting forever on recovery.

Uses the built applications and actual navigation links. Firebase/backend I/O
is deterministic; this is not a live Google OAuth or physical iPhone test.
"""
import importlib.util
import base64
import json
import os
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('vortex_fixture', ROOT/'tests/vortex-browser.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class DelayedVision(mod.Fixture):
    def route(self, route):
        if '/Vision.html' in route.request.url:
            sdk = mod.FIREBASE.replace('authStateReady:async()=>{}', '''authStateReady:()=>
                window.__restoreReleased?Promise.resolve():new Promise(resolve=>{
                    document.documentElement.dataset.fixtureAuthWaiting='true';
                    window.__completeVisionRestore=()=>{window.__restoreReleased=true;resolve();};
                })''').replace('signInWithPopup:login', '''signInWithPopup:async(auth,provider,resolver)=>{
                    window.__popupResolver=resolver;window.__popupCount=(window.__popupCount||0)+1;return login();
                }''')
            route.fulfill(body=mod.VISION.replace(mod.FIREBASE, sdk, 1), content_type='text/html')
        else:
            super().route(route)


def enter_from_vortex(browser, api, *, view='vision', width=390, script=''):
    context = browser.new_context(viewport={'width':width, 'height':844}, has_touch=width<760)
    context.add_init_script("localStorage.setItem('__fixtureUID','alice');" + script)
    context.route('**/*', api.route)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(mod.URL)
    page.locator('#menuButton').click()
    page.locator('#navigation').get_by_role('link', name=view.title(), exact=True).click()
    page.wait_for_url(mod.URL.replace('vortex/', '?view='+view))
    return context, page, errors


def verify_delayed_return(browser):
    for width in (390, 1280):
        for view in ('vision', 'venture'):
            api = DelayedVision()
            context, page, errors = enter_from_vortex(browser, api, view=view, width=width)
            page.locator('html[data-fixture-auth-waiting=true]').wait_for()
            assert page.locator('#accountGateSignIn').is_hidden(), 'Returning account saw a login form before restore'
            assert page.locator('#accountGateEmailArea').is_hidden()
            assert page.locator('#accountGate').get_attribute('aria-busy') == 'true'
            assert not api.vision_calls, 'Private workspace requests ran before identity restoration'
            page.evaluate('__completeVisionRestore()')
            page.locator('#accountGate').wait_for(state='hidden')
            page.locator('#visionVenture').wait_for(state='visible' if view=='venture' else 'hidden')
            assert page.evaluate('window.__popupCount||0') == 0
            # Return through the real app menu, preserving the same identity.
            page.keyboard.press('Escape')
            page.locator('#workspaceMenuButton').click()
            page.locator('#ventureVortexLink' if view=='venture' else '#workspaceVortexLink').click()
            page.wait_for_url(mod.URL)
            page.locator('#accountEmail').get_by_text('alice@example.test', exact=True).wait_for(state='attached')
            assert page.locator('#authGate').is_hidden()
            assert not errors, errors
            context.close()
    print('Vision/Venture return: delayed sessions, no login flash, real menu round trips and identity retention passed', flush=True)


def verify_stalled_recovery(browser):
    for operation in ('open', 'read'):
        script = '''
        const open=indexedDB.open.bind(indexedDB);
        indexedDB.open=(name,...args)=>{
            if(name==='vision-projects-v1' && MODE==='open')return {};
            return open(name,...args);
        };
        const transaction=IDBDatabase.prototype.transaction;
        IDBDatabase.prototype.transaction=function(name,mode,...args){
            if(this.name==='vision-projects-v1' && MODE==='read' && mode!=='readwrite'){
                return {objectStore:()=>({get:()=>({})}),abort:()=>{window.__recoveryAborted=true;}};
            }
            return transaction.call(this,name,mode,...args);
        };
        '''.replace('MODE', repr(operation))
        api = mod.Fixture()
        context, page, errors = enter_from_vortex(browser, api, script=script)
        page.locator('html[data-fixture-auth-initialized=true]').wait_for(timeout=2000)
        assert page.locator('#accountGateSignIn').is_hidden(), 'Recovery exposed a redundant login'
        page.locator('#accountGate').wait_for(state='hidden', timeout=8000)
        assert page.evaluate("localStorage.getItem('__fixtureUID')") == 'alice'
        if operation == 'read':
            assert page.evaluate('window.__recoveryAborted') is True
        assert not errors, errors
        context.close()
    print('Vision return: hung recovery database opens/reads cannot block authentication or workspace access', flush=True)


def verify_restore_retry(browser):
    for signed_in in (True, False):
        api = DelayedVision()
        context = browser.new_context(viewport={'width':390, 'height':844})
        context.add_init_script('''
            const timeout=window.setTimeout;
            window.setTimeout=(fn,ms,...args)=>timeout(fn,ms===12000?200:ms,...args);
        ''' + ("localStorage.setItem('__fixtureUID','alice');" if signed_in else ''))
        context.route('**/*', api.route)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(mod.URL.replace('vortex/', '?view=vision'))
        page.locator('#accountGateRetry').wait_for(state='visible')
        assert page.locator('#accountGateSignIn').is_hidden()
        assert page.locator('#accountGate').get_attribute('aria-busy') == 'false'
        assert not api.vision_calls
        page.evaluate('__completeVisionRestore()')
        with page.expect_response(mod.BACKEND+'/api/account/openai-key'):
            page.locator('#accountGateRetry').click()
            if not signed_in:
                page.locator('#accountGateSignIn').wait_for(state='visible')
                page.locator('#accountGateSignIn').click()
        page.locator('#accountGate').wait_for(state='hidden')
        if not signed_in:
            assert page.evaluate('window.__popupCount') == 1, 'Google did not respond after recovery'
            assert page.evaluate('window.__popupResolver') == 'POPUP'
        assert not errors, errors
        context.close()
    print('Vision restore: bounded timeout, actionable retry, existing session and responsive Google button passed', flush=True)


def verify_real_sdk_return(browser, html=None):
    """Use the vendored Firebase SDK/persistence; intercept only HTTP services."""
    runtime = (ROOT/'web/vendor/firebase.js').read_text()
    html = html or (ROOT/'Vision.html').read_text()
    seed = mod.URL.replace('vortex/', 'session-fixture')
    now = int(time.time())
    encode = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    token = encode({'alg':'RS256'})+'.'+encode(dict(sub='alice', user_id='alice',
        aud='visionboard-api', iss='https://securetoken.google.com/visionboard-api',
        iat=now, exp=now+3600, auth_time=now, firebase={'sign_in_provider':'password'}))+'.fixture'
    account = dict(localId='alice', email='alice@example.test', emailVerified=True,
        displayName='Alice Example', createdAt=str(now*1000), lastLoginAt=str(now*1000),
        providerUserInfo=[dict(providerId='password', rawId='alice@example.test', email='alice@example.test')])

    class RealFirebase(mod.Fixture):
        returning = False

        def __init__(self):
            super().__init__()
            self.helper_requests = []

        def route(self, route):
            url = route.request.url
            if url == seed:
                route.fulfill(content_type='text/html', body='<script>'+runtime+'</script><script>'+'''
                    const sdk=VisionFirebaseSDK;
                    window.fixtureAuth=sdk.auth.initializeAuth(sdk.app.initializeApp({
                        apiKey:'AIzaSyDh1AHhi41cAXcSFnvFkfeZWmxc8gI0zSg',authDomain:'visionboard-api.firebaseapp.com',
                        projectId:'visionboard-api',appId:'1:150865729216:web:554423d0c7602d3a47bf25'
                    },'vision-account-login'),{persistence:sdk.auth.browserLocalPersistence});
                    fixtureAuth.authStateReady().then(()=>document.documentElement.dataset.seedReady='true');
                </script>''')
            elif url.startswith('https://identitytoolkit.googleapis.com/'):
                data = dict(users=[account]) if 'accounts:lookup' in url else dict(
                    localId='alice', email='alice@example.test', idToken=token,
                    refreshToken='fixture-refresh', expiresIn='3600', registered=True)
                self.respond(route, data)
            elif url.startswith('https://apis.google.com/') or 'visionboard-api.firebaseapp.com/__/auth/' in url:
                if self.returning:
                    self.helper_requests.append(url)  # Intentionally never finish this unrelated helper request.
                else:
                    route.abort()
            elif url.startswith(mod.BACKEND+'/api/vortex/'):
                self.respond(route, dict(ready=True, jobs=[], serverTime=time.time(), vortexVersion='1.0.2'))
            elif url.startswith(mod.BACKEND+'/api/venture/profile'):
                self.respond(route, dict(hasAvatar=False))
            elif url.endswith('/web/vendor/firebase.js'):
                route.fulfill(body=runtime, content_type='application/javascript')
            elif '/Vision.html' in url:
                route.fulfill(body=html, content_type='text/html')
            else:
                super().route(route)

    api = RealFirebase()
    context = browser.new_context(viewport={'width':390, 'height':844}, is_mobile=True,
        user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1')
    context.route('**/*', api.route)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(seed)
    page.locator('html[data-seed-ready=true]').wait_for()
    page.evaluate("VisionFirebaseSDK.auth.signInWithEmailAndPassword(fixtureAuth,'alice@example.test','fixture-password')")
    page.goto(mod.URL)
    page.locator('#menuButton').click()
    api.returning = True
    page.locator('#navigation').get_by_role('link', name='Vision', exact=True).click()
    page.wait_for_url(mod.URL.replace('vortex/', '?view=vision'))
    page.locator('#accountGate').wait_for(state='attached')
    page.locator('#accountGate').wait_for(state='hidden', timeout=10000)
    assert page.evaluate("VisionFirebaseSDK.auth.getAuth(VisionFirebaseSDK.app.getApps()[0]).currentUser.uid") == 'alice'
    assert page.evaluate("VisionFirebaseSDK.auth.getAuth(VisionFirebaseSDK.app.getApps()[0]).currentUser.email") == 'alice@example.test'
    assert not api.helper_requests, 'Saved-session restoration unnecessarily loaded the Google popup helper'
    assert not errors, errors
    context.close()
    print('Real Firebase SDK: persistent identity survives Vortex-to-Vision on the mobile code path without a popup helper', flush=True)


def run():
    with sync_playwright() as pw:
        options = {'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}
        browser = pw.chromium.launch(headless=True, **options)
        try:
            verify_delayed_return(browser)
            verify_stalled_recovery(browser)
            verify_restore_retry(browser)
            verify_real_sdk_return(browser)
        finally:
            browser.close()


if __name__ == '__main__':
    run()
