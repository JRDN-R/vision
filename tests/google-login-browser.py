"""Hosted Google login with real Firebase persistence and mocked provider I/O.

No real Google account, credentials, consent, or production backend is used.
"""
import base64
import importlib.util
import json
import os
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('vortex_fixture', ROOT/'tests/vortex-browser.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
runtime = (ROOT/'web/vendor/firebase.js').read_text()
html = (ROOT/'Vision.html').read_text()
now = int(time.time())
encode = lambda v: base64.urlsafe_b64encode(json.dumps(v).encode()).decode().rstrip('=')
token = encode({'alg':'RS256'})+'.'+encode(dict(sub='alice', user_id='alice', aud='visionboard-api',
    iss='https://securetoken.google.com/visionboard-api', iat=now, exp=now+3600,
    auth_time=now, firebase={'sign_in_provider':'google.com'}))+'.fixture'
account = dict(localId='alice', email='alice@example.test', emailVerified=True,
    displayName='Alice Example', providerUserInfo=[dict(providerId='google.com', rawId='google-alice', email='alice@example.test')])


class DirectGoogle(mod.Fixture):
    def __init__(self):
        super().__init__()
        self.helpers = []
        self.exchanges = []
        self.scripts = 0

    def route(self, route):
        url = route.request.url
        if url == 'https://accounts.google.com/gsi/client':
            self.scripts += 1
            route.fulfill(content_type='application/javascript', body='''
                window.google={accounts:{oauth2:{initTokenClient:options=>({requestAccessToken(){
                    window.__googleCalls=(window.__googleCalls||0)+1;
                    window.__userActivation=navigator.userActivation.isActive;
                    if(window.__googleFailure)options.error_callback({type:window.__googleFailure});
                    else options.callback({access_token:'fixture-google-access-token'});
                }})}}};
            ''')
        elif url.startswith('https://identitytoolkit.googleapis.com/'):
            if 'accounts:signInWithIdp' in url:
                self.exchanges.append(route.request.post_data_json)
            self.respond(route, dict(users=[account]) if 'accounts:lookup' in url else dict(
                **account, idToken=token, refreshToken='fixture-refresh', expiresIn='3600'))
        elif 'visionboard-api.firebaseapp.com/__/auth/' in url:
            self.helpers.append(url)
            route.abort()
        elif url.endswith('/web/vendor/firebase.js'):
            route.fulfill(body=runtime, content_type='application/javascript')
        elif '/Vision.html' in url:
            route.fulfill(body=html, content_type='text/html')
        else:
            super().route(route)


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, **({'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}))
    try:
        for view in ('vision', 'venture', 'vortex'):
            api = DirectGoogle()
            context = browser.new_context(viewport={'width':390, 'height':844}, is_mobile=True)
            context.route('**/*', api.route)
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(mod.URL if view=='vortex' else mod.URL.replace('vortex/', '?view='+view))
            page.locator('#accountGateSignIn').wait_for(state='visible')
            page.wait_for_function('!!window.google?.accounts?.oauth2')
            assert api.scripts == 1, 'CSP must allow GIS to load on the shared gateway'
            assert page.evaluate('window.__googleCalls||0') == 0, 'No popup on startup'
            page.evaluate("window.__googleFailure='popup_closed'")
            page.locator('#accountGateSignIn').click()
            page.locator('#accountGateStatus').get_by_text('Google sign-in was closed.', exact=False).wait_for()
            assert not api.exchanges
            page.evaluate('window.__googleFailure=null')
            page.locator('#accountGateSignIn').click()
            if view=='vortex':
                page.wait_for_url(mod.URL)
                page.locator('#application').wait_for(state='visible')
            else:
                page.locator('#accountGate').wait_for(state='hidden')
                assert page.evaluate('window.__userActivation') is True
            assert len(api.exchanges) == 1
            body = api.exchanges[0]['postBody']
            assert 'access_token=fixture-google-access-token' in body and 'providerId=google.com' in body
            assert page.evaluate("VisionFirebaseSDK.auth.getAuth(VisionFirebaseSDK.app.getApps()[0]).currentUser.uid") == 'alice'
            # A direct link to another workspace restores the very same identity.
            page.goto(mod.URL.replace('vortex/', '?view=venture'))
            page.locator('#accountGate').wait_for(state='attached')
            page.locator('#accountGate').wait_for(state='hidden')
            assert page.evaluate("VisionFirebaseSDK.auth.getAuth(VisionFirebaseSDK.app.getApps()[0]).currentUser.uid") == 'alice'
            assert api.scripts == 1, 'Restoring a saved user must not load GIS'
            assert not api.helpers and not errors, (api.helpers, errors)
            context.close()
        print('All three entry routes: real Firebase credential exchange, cancellation/retry, shared durable identity, GIS CSP, no Firebase OAuth helper passed.')
    finally:
        browser.close()
