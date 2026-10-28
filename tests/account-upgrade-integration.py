"""Real browser request helpers -> real Flask routes and temporary SQLite.

Only Firebase credentials and network delivery are fixtures. No live server,
user data, provider API or project request helper is mocked or contacted.
"""
import json
import os
from pathlib import Path
import sys

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'vision-pc'))
import server
from test_ios_companion import IOSCompanionTests

PAGE = 'https://jrdn-r.github.io/vision/'
BACKEND = 'https://desktop-vjt2br2.tail385c9d.ts.net'
PROBE = r'''
window.upgradeIntegration={
 boot:async(uid,token)=>{
  await accountReady;await projectRecoverStartup();
  accountAuthEpoch++;accountGateLocked=false;
  accountFirebase={currentUser:{uid,email:uid+'@example.test',getIdToken:async()=>token}};
  cloudConfig={kind:'private-pc',backendUrl:BACKEND,publicAccess:true};
  cloudAuth={kind:'firebase-google',uid,backendUrl:BACKEND};
  await accountCheckMembership(accountFirebase.currentUser);
  projectAccountSwitching=false;delete state.projectCloud;
  document.querySelectorAll('[data-account-inert]').forEach(el=>{el.inert=false;delete el.dataset.accountInert;});
  document.documentElement.classList.remove('account-locked');$('accountGate').hidden=true;
  await upgradeRefresh(true);
 },
 open:()=>openAccountUpgrade(), refresh:()=>upgradeRefresh(true),
 prefs:patch=>saveAccountProcessing(patch),
 values:()=>({provider:accountProcessingPreference('transcriptionProvider','missing'),describe:accountProcessingPreference('describeFrames',null)}),
 foreignProject:()=>{state.projectCloud={id:'project-foreign-account',key:'a'.repeat(64),revision:1,ownerUid:'someone-else',backendUrl:'https://another-server.ts.net'};projectEpoch++;},
 blankProject:()=>{delete state.projectCloud;projectEpoch++;},
 hasProject:()=>!!state.projectCloud
};
'''.replace('BACKEND', json.dumps(BACKEND))
html = (ROOT / 'Vision.html').read_text()
offset = html.rfind('\n})();')
assert offset > 0
html = html[:offset] + PROBE + html[offset:]

IOSCompanionTests.setUpClass()
fixture = IOSCompanionTests()
fixture.setUp()
try:
    owner = fixture.headers('owner')
    (server.app.config['DATA_DIR'] / 'activity-admins.json').write_text(json.dumps({'uids': ['firebase:owner']}))
    calls = []

    def route_request(route):
        request = route.request
        if request.url == PAGE:
            return route.fulfill(body=html, content_type='text/html')
        if request.url.startswith(BACKEND + '/api/'):
            path = request.url[len(BACKEND):]
            headers = request.all_headers()
            calls.append((request.method, path, headers))
            if path.startswith('/api/account/'):
                assert 'x-vision-project-key' not in headers
            response = fixture.client.open(path, method=request.method, headers=headers, data=request.post_data)
            return route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.get_data())
        route.abort()

    def dashboard():
        response = fixture.client.get('/api/admin/gemini', headers=owner)
        assert response.status_code == 200, response.json
        return response.json

    def decide(row, decision):
        result = fixture.client.post('/api/admin/gemini/access', headers=owner,
            json={'userId': row['userId'], 'decision': decision})
        assert result.status_code == 200, result.json

    def boot(page, uid):
        token = fixture.headers(uid)['Authorization'].removeprefix('Bearer ')
        page.goto(PAGE)
        page.evaluate('([uid,token])=>upgradeIntegration.boot(uid,token)', [uid, token])

    with sync_playwright() as pw:
        options = {'executable_path': os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}
        browser = pw.chromium.launch(headless=True, **options)
        context = browser.new_context(viewport={'width': 390, 'height': 844})
        context.route('**/*', route_request)
        page = context.new_page()
        boot(page, 'alice')
        assert not page.evaluate('upgradeIntegration.hasProject()')
        page.evaluate('upgradeIntegration.open()')
        page.locator('#requestAccountUpgrade').click()
        expect(page.locator('#accountUpgradeStatus')).to_contain_text('waiting')
        expect(page.locator('#accountUpgradeButton')).to_have_text('Request awaiting approval')
        expect(page.locator('#requestAccountUpgrade')).to_be_hidden()
        pending = dashboard()
        assert pending['pendingCount'] == 1, pending
        alice = next(row for row in pending['accessRequests'] if row['status'] == 'pending')
        assert not page.evaluate('upgradeIntegration.hasProject()'), 'Account request created a project'
        with server.connect_db() as db:
            assert db.execute('SELECT COUNT(*) FROM projects').fetchone()[0] == 0
        print('PASS blank board request reaches the real admin pending list without a project')

        boot(page, 'alice')
        expect(page.locator('#accountUpgradeButton')).to_have_text('Request awaiting approval')
        assert dashboard()['pendingCount'] == 1
        decide(alice, 'approved')
        page.evaluate('upgradeIntegration.refresh()')
        expect(page.locator('#accountUpgradeButton')).to_be_hidden()
        assert page.evaluate('upgradeIntegration.values()') == {'provider': 'gemini', 'describe': True}
        page.evaluate('upgradeIntegration.foreignProject()')
        page.evaluate("upgradeIntegration.prefs({transcriptionProvider:'local',describeFrames:false})")
        page.evaluate('upgradeIntegration.blankProject()')
        page.evaluate('upgradeIntegration.refresh()')
        assert page.evaluate('upgradeIntegration.values()') == {'provider': 'local', 'describe': False}
        boot(page, 'alice')
        assert page.evaluate('upgradeIntegration.values()') == {'provider': 'local', 'describe': False}
        assert dashboard()['pendingCount'] == 0
        print('PASS admin approval and global choices survive project changes and reopening')

        boot(page, 'bob')
        expect(page.locator('#accountUpgradeButton')).to_have_text('Account Upgrade')
        assert page.evaluate('upgradeIntegration.values()') == {'provider': 'local', 'describe': True}
        page.evaluate('upgradeIntegration.foreignProject()')
        page.evaluate('upgradeIntegration.open()')
        page.locator('#requestAccountUpgrade').click()
        expect(page.locator('#accountUpgradeStatus')).to_contain_text('waiting')
        assert dashboard()['pendingCount'] == 1
        assert len(dashboard()['accessRequests']) == 2
        print('PASS another account can request with an unrelated project open; preferences stay isolated')
        assert not any(path.startswith('/api/projects') for _, path, _ in calls), calls
        context.close()
        browser.close()
finally:
    fixture.tearDown()
