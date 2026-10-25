"""Real iPhone broker JS + bundled Firebase + signed-token Flask/PKCE exchange.

Google account UI and provider HTTP responses are fixtures. All other traffic
is blocked; no real accounts, credentials or production server are used.
"""
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import parse_qs, urlparse
from unittest.mock import Mock, patch

import jwt
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'vision-pc'))
import server
from test_ios_companion import IOSCompanionTests

ORIGIN = 'https://jrdn-r.github.io'
BACKEND = 'https://desktop-vjt2br2.tail385c9d.ts.net'
IOSCompanionTests.setUpClass()
fixture = IOSCompanionTests()
fixture.setUp()


class Broker:
    def __init__(self):
        self.uid = 'alice'
        self.mode = 'fresh'
        self.approvals = []
        self.refreshes = 0
        self.exchanges = 0

    def token(self, refreshed=False):
        claims = jwt.decode(fixture.headers(self.uid)['Authorization'][7:], options={'verify_signature': False})
        now = int(time.time())
        claims.update(iat=now+30, auth_time=now+30)
        if self.mode == 'expired' and not refreshed:
            claims.update(iat=now-3600, auth_time=now-3600, exp=now-1)
        if self.mode == 'clock':
            claims.update(iat=now+120, auth_time=now+120)
        return jwt.encode(claims, fixture.signing_key, algorithm='RS256', headers={'kid': 'ios-test'})

    def route(self, route):
        request = route.request
        url = request.url
        if url == 'https://accounts.google.com/gsi/client':
            return route.fulfill(content_type='application/javascript', body='''
              window.google={accounts:{oauth2:{initTokenClient:options=>({requestAccessToken(){
                window.__googleCalls=(window.__googleCalls||0)+1;
                window.__selectionPrompt=options.prompt;
                window.__activated=navigator.userActivation.isActive;
                if(window.__cancelGoogle)options.error_callback({type:'popup_closed'});
                else options.callback({access_token:'fixture-google-access-token'});
              }})}}};
            ''')
        if url.startswith('https://identitytoolkit.googleapis.com/'):
            account = dict(localId=self.uid, email=self.uid+'@example.test', emailVerified=True,
                providerUserInfo=[dict(providerId='google.com', rawId='google-'+self.uid)])
            if 'accounts:lookup' in url:
                return route.fulfill(json={'users': [account]})
            self.exchanges += 1
            body = request.post_data_json
            assert 'providerId=google.com' in body['postBody']
            assert 'access_token=fixture-google-access-token' in body['postBody']
            return route.fulfill(json=dict(**account,
                idToken=self.token(), refreshToken='fixture-refresh', expiresIn='3600'))
        if url.startswith('https://securetoken.googleapis.com/'):
            self.refreshes += 1
            return route.fulfill(json=dict(access_token=self.token(refreshed=True), id_token=self.token(refreshed=True),
                refresh_token='fixture-refresh', expires_in='3600', user_id=self.uid, project_id='visionboard-api'))
        if url == BACKEND+'/api/ios/auth/approve':
            headers = request.all_headers()
            assert headers['origin'] == ORIGIN
            assert 'x-vision-project-key' not in headers
            subject = 'someone-else' if self.mode == 'mismatch' else 'google-'+self.uid
            google = Mock(status_code=200)
            google.json.return_value = {'sub': subject}
            with patch('ios_companion.requests.get', return_value=google):
                response = fixture.client.post('/api/ios/auth/approve', data=request.post_data, headers=headers)
            self.approvals.append(response.status_code)
            return route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.get_data())
        if url.startswith(ORIGIN+'/vision/'):
            relative = urlparse(url).path.removeprefix('/vision/')
            if relative in ('ios-signin.html', 'web/google-login.js', 'web/ios-signin.js', 'web/vendor/firebase.js'):
                return route.fulfill(body=(ROOT/relative).read_bytes(),
                    content_type='text/html' if relative.endswith('.html') else 'application/javascript')
        route.abort()


try:
    with sync_playwright() as pw:
        options = {'executable_path': os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}
        browser = pw.chromium.launch(headless=True, **options)
        context = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True)
        api = Broker()
        context.route('**/*', api.route)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def open_broker(uid='alice', mode='fresh'):
            api.uid, api.mode = uid, mode
            api.approvals = []
            api.refreshes = 0
            started = fixture.start()
            # Native reconnect creates a new authentication document, while
            # retaining the browser's cookies. A fragment change alone doesn't.
            page.goto('about:blank')
            page.goto(started['authURL'])
            expect(page.locator('#connect')).to_be_enabled()
            assert page.evaluate('window.__googleCalls||0') == 0
            assert page.evaluate('location.hash') == ''
            return started

        def finish(started):
            expect(page.locator('#return')).to_be_visible()
            params = parse_qs(urlparse(page.locator('#return').get_attribute('href')).query)
            assert params['id'] == [started['id']] and params['state'] == [fixture.state]
            assert set(params) == {'id', 'state', 'code'}, 'Never put credentials in the callback URL'
            body = dict(id=started['id'], state=fixture.state, code=params['code'][0], verifier=fixture.verifier)
            result = fixture.client.post('/api/ios/auth/exchange', json=body)
            assert result.status_code == 200, result.json
            assert result.json['uid'] == api.uid
            assert result.json['googleAccessToken'] == 'fixture-google-access-token'
            assert fixture.client.post('/api/ios/auth/exchange', json=body).status_code == 401
            assert page.evaluate('window.__selectionPrompt') == 'select_account'
            assert page.evaluate('window.__activated') is True

        # Cancel the provider popup, then finish with a fresh token issued ahead
        # of the PC clock. Closing/reopening the broker can select another user.
        started = open_broker()
        page.evaluate('window.__cancelGoogle=true')
        page.locator('#connect').click()
        expect(page.locator('#status')).to_contain_text('could not finish')
        expect(page.locator('#connect')).to_be_enabled()
        assert not api.approvals
        page.evaluate('window.__cancelGoogle=false')
        page.locator('#connect').click()
        finish(started)
        assert api.approvals == [200]
        started = open_broker('bob')
        page.locator('#connect').click()
        finish(started)
        assert api.approvals == [200]
        print('PASS: cancellation/retry, explicit chooser, fresh signed tokens, account switch and single-use native handoff')

        started = open_broker(mode='expired')
        page.locator('#connect').click()
        finish(started)
        assert api.approvals == [401, 200] and api.refreshes == 1
        assert page.evaluate('window.__googleCalls') == 1
        print('PASS: expired Firebase token refreshes once without another Google popup')

        for mode, message, approvals in [('clock', 'clock does not match Google', [401]),
                                         ('mismatch', 'does not match the signed-in Vision account', [401, 401])]:
            started = open_broker(mode=mode)
            page.locator('#connect').click()
            expect(page.locator('#status')).to_contain_text(message)
            expect(page.locator('#connect')).to_be_enabled()
            expect(page.locator('#return')).to_be_hidden()
            assert api.approvals == approvals
            assert 'google_access' not in fixture.service.pending[started['id']]
        assert not errors, errors
        print('PASS: large clock errors and mismatched accounts fail closed with useful errors')
        context.close()
        browser.close()
finally:
    fixture.tearDown()
