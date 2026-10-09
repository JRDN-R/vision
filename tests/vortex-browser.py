"""Real Vortex page + modules with deterministic Firebase/backend fixtures.

No production auth, provider, or network calls are permitted. These Chromium
checks cover UI/API behavior and responsive layout; they are not an iPhone or
Windows-worker end-to-end test. Screenshots are written to ignored .qa/vortex/.
"""
import json
import mimetypes
import os
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / '.qa' / 'vortex'
URL = 'http://127.0.0.1:8899/vision/vortex/'
BACKEND = 'https://desktop-vjt2br2.tail385c9d.ts.net'
VIDEO = dict(title='Harbor at sunset', url='https://www.youtube.com/watch?v=fixture001', source='YouTube',
             mediaType='video', width=3840, height=2160, fps=60, vcodec='avc1.640033', acodec='mp4a.40.2',
             duration=180, thumbnail=URL.replace('vortex/', 'vortex_character.png'))
AUDIO = dict(title='Evening soundtrack', url='https://example.org/audio.flac', source='Archive',
             mediaType='audio', acodec='flac', abr=1411, asr=48000, audioChannels=2, duration=240)

FIREBASE = r'''
(()=>{
 const listeners=[];
 const identity=uid=>uid?{uid,email:uid+'@example.test',displayName:uid==='alice'?'Alice Example':'Bob Example',photoURL:null,
   getIdToken:async()=>{if(window.__holdToken)await new Promise(resolve=>window.__releaseToken=resolve);return 'fixture-'+uid;}}:null;
 const auth={currentUser:identity(localStorage.getItem('__fixtureUID')),authStateReady:async()=>{}};
 window.__switchUser=uid=>{auth.currentUser=identity(uid);if(uid)localStorage.setItem('__fixtureUID',uid);else localStorage.removeItem('__fixtureUID');listeners.forEach(fn=>fn(auth.currentUser));};
 window.__firebaseCalls=[];
 const login=async()=>{window.__switchUser('alice');return {user:auth.currentUser};};
 window.VisionFirebaseSDK={app:{getApps:()=>[],initializeApp:(config,name)=>{__firebaseCalls.push({config,name});return {name};}},auth:{
   getAuth:()=>auth,useDeviceLanguage:()=>{},onAuthStateChanged:(_,fn)=>{listeners.push(fn);return ()=>{};},
   setPersistence:async(_,value)=>{window.__persistence=value;},browserLocalPersistence:'LOCAL',
   GoogleAuthProvider:class{setCustomParameters(value){window.__providerOptions=value;}},
   signInWithPopup:login,signInWithEmailAndPassword:login,createUserWithEmailAndPassword:login,
   sendPasswordResetEmail:async()=>{},signOut:async()=>window.__switchUser(null)
 }};
})();
'''


class Fixture:
    def __init__(self):
        self.jobs = {'alice': [], 'bob': []}
        self.calls = []
        self.unexpected = []
        self.held = None
        self.hold_list = False
        self.profile_version = 'saved-vision-avatar'
        self.hold_avatar = False
        self.held_avatar = None
        self.serial = 0
        self.source_visits = []
        self.compatible_exports = True

    def new_job(self, uid='alice', **fields):
        self.serial += 1
        now = time.time()
        job = dict(id=f'job-{self.serial:04d}', requestId=f'request-{self.serial}', kind='download', quality='balanced',
                   input=VIDEO['url'], engine='yt-dlp', status='queued', phase='Queued', progress=None, error=None,
                   media=None, results=[], title=None, source=None, thumbnail=None, format='', filename=None,
                   size=0, createdAt=now, updatedAt=now, completedAt=None, expiresAt=None, cancelRequested=False,
                   resultReady=False)
        job.update(fields)
        self.jobs[uid].insert(0, job)
        return job

    @staticmethod
    def respond(route, value, status=200):
        route.fulfill(status=status, body=json.dumps(value), content_type='application/json', headers={
            'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'Authorization,Content-Type',
            'Access-Control-Allow-Methods': 'GET,POST,DELETE,OPTIONS'})

    def route(self, route):
        request = route.request
        url = urlparse(request.url)
        if request.url.startswith(BACKEND + '/api/venture/profile'):
            if request.method == 'OPTIONS':
                self.respond(route, {})
                return
            token = request.headers.get('authorization', '')
            uid = token.removeprefix('Bearer fixture-')
            self.calls.append(dict(path=url.path, method=request.method, uid=uid, token=token, body={}))
            if uid not in self.jobs:
                self.respond(route, {'error':'Unauthorized'}, 401)
            elif url.path.endswith('/avatar'):
                if self.hold_avatar:
                    self.hold_avatar = False
                    self.held_avatar = route
                else:
                    route.fulfill(body=(ROOT/'vortex_character.png').read_bytes(), content_type='image/png', headers={'Access-Control-Allow-Origin':'*'})
            else:
                self.respond(route, dict(hasAvatar=uid=='alice', avatarVersion=self.profile_version if uid=='alice' else None))
            return
        if request.url.startswith(BACKEND + '/api/vortex/'):
            if request.method == 'OPTIONS':
                self.respond(route, {})
                return
            token = request.headers.get('authorization', '')
            uid = token.removeprefix('Bearer fixture-')
            if '/file' in url.path and url.query:
                uid = 'alice'
            self.calls.append(dict(path=url.path, method=request.method, uid=uid, token=token,
                                   body=json.loads(request.post_data or '{}')))
            if uid not in self.jobs:
                self.respond(route, {'error': 'Unauthorized'}, 401)
                return
            tail = url.path.removeprefix('/api/vortex/')
            if tail == 'capabilities':
                self.respond(route, dict(ready=True, videoFormats=['mp4','mov'], audioFormats=['m4a','mp3','wav'], searchPagination=True) if self.compatible_exports else dict(ready=True))
                return
            if tail == 'jobs':
                query = parse_qs(url.query)
                jobs = [job for job in self.jobs[uid] if not query.get('kind') or job['kind'] == query['kind'][0]]
                cursor = query.get('cursor', [''])[0]
                if cursor:
                    offset = next((index+1 for index, job in enumerate(jobs) if job['id'] == cursor), len(jobs))
                    jobs = jobs[offset:]
                result = dict(jobs=jobs[:100], nextCursor=jobs[99]['id'] if len(jobs)>100 else None, serverTime=time.time())
                if request.method == 'POST':
                    self.respond(route, self.new_job(uid, **json.loads(request.post_data)), 202)
                elif self.hold_list:
                    self.hold_list = False
                    self.held = (route, json.loads(json.dumps(result)))
                else:
                    self.respond(route, result)
                return
            pieces = tail.split('/')
            job = next((job for job in self.jobs[uid] if job['id'] == pieces[1]), None) if len(pieces) > 1 else None
            if not job:
                self.respond(route, {'error': 'Not found'}, 404)
            elif tail.endswith('/cancel'):
                job.update(status='cancelled', phase='Cancelled', progress=None, cancelRequested=True)
                self.respond(route, job)
            elif tail.endswith('/ticket'):
                self.respond(route, dict(url=BACKEND+'/api/vortex/jobs/'+job['id']+'/file?ticket=fixture', expiresAt=time.time()+300))
            elif tail.endswith('/file'):
                route.fulfill(body=b'fixture media bytes', content_type='video/mp4', headers={
                    'Content-Disposition': 'attachment; filename="Harbor.mp4"', 'Access-Control-Allow-Origin':'*'})
            elif request.method == 'DELETE':
                self.jobs[uid].remove(job)
                self.respond(route, dict(deleted=True, id=job['id']))
            else:
                self.respond(route, job)
            return
        if request.url.startswith('http://127.0.0.1:8899/vision/'):
            relative = url.path.removeprefix('/vision/') or 'index.html'
            relative = relative + 'index.html' if relative.endswith('/') else relative
            path = ROOT / relative
            if relative == 'web/vendor/firebase.js':
                route.fulfill(body=FIREBASE, content_type='application/javascript')
            elif path.is_file() and path.resolve().is_relative_to(ROOT.resolve()):
                route.fulfill(body=path.read_bytes(), content_type=mimetypes.guess_type(str(path))[0] or 'application/octet-stream')
            else:
                self.unexpected.append(request.url)
                route.abort()
            return
        if request.is_navigation_request() and request.url.startswith('https://www.youtube.com/watch?'):
            self.source_visits.append(request.url)
            route.fulfill(body='<title>Original media source</title>', content_type='text/html')
            return
        self.unexpected.append(request.url)
        route.abort()

    def release(self):
        route, result = self.held
        self.held = None
        try:
            self.respond(route, result)
        except Exception:
            pass  # Switching identities aborts the old account's fetch.


def refresh(page):
    page.locator('#refreshButton').click()
    page.wait_for_function('!document.querySelector("#refreshButton").disabled')


def submitted(page):
    page.wait_for_function('!document.querySelector("#sourceSubmit").disabled')


def next_job(page, api, previous):
    deadline = time.monotonic() + 7
    while api.jobs['alice'][0]['id'] == previous and time.monotonic() < deadline:
        page.wait_for_timeout(20)
    assert api.jobs['alice'][0]['id'] != previous, 'New media request was not submitted'
    submitted(page)
    return api.jobs['alice'][0]


def no_overflow(page):
    overflow = page.evaluate("""() => {
      const viewport = innerWidth, scroll = document.documentElement.scrollWidth;
      if (scroll <= viewport + 1) return null;
      const items = [...document.querySelectorAll('body *')].map(el => {
        const r = el.getBoundingClientRect();
        return {tag: el.tagName, id: el.id, className: typeof el.className === 'string' ? el.className.slice(0, 85) : '',
          left: Math.round(r.left), right: Math.round(r.right), width: Math.round(r.width)};
      }).filter(v => v.right > viewport + 1 || v.left < -10).slice(0, 18);
      return {viewport, scroll, items};
    }""")
    assert overflow is None, f'Horizontal page overflow: {overflow}'


def verify_delayed_auth_restore(browser):
    """A shared Vision session must never flash Vortex's sign-in form."""
    slow_sdk = FIREBASE.replace(
        'authStateReady:async()=>{}',
        'authStateReady:()=>new Promise(resolve=>window.__completeAuthRestore=resolve)'
    )
    assert slow_sdk != FIREBASE, 'Firebase fixture no longer supports delayed hydration'

    class DelayedAuthFixture(Fixture):
        def route(self, route):
            if route.request.url == URL.replace('vortex/', 'web/vendor/firebase.js'):
                route.fulfill(body=slow_sdk, content_type='application/javascript')
            else:
                super().route(route)

    for existing_session in (True, False):
        api = DelayedAuthFixture()
        context = browser.new_context(viewport={'width': 390, 'height': 844})
        if existing_session:
            context.add_init_script("localStorage.setItem('__fixtureUID', 'alice')")
        context.route('**/*', api.route)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(URL)
        page.wait_for_function("typeof window.__completeAuthRestore === 'function'")
        assert page.locator('#authGate').is_hidden(), 'Sign-in flashed before restoration completed'
        assert page.locator('#application').is_visible(), 'Vortex shell was hidden during restoration'
        assert page.locator('#application').evaluate("el=>el.inert && el.getAttribute('aria-busy')==='true'")
        assert page.locator('#authRestoring').is_visible()
        assert not api.calls, 'Private API ran before Firebase restored the session'

        page.evaluate('__completeAuthRestore()')
        if existing_session:
            page.wait_for_function("!document.querySelector('#application').inert")
            assert page.locator('#authGate').is_hidden()
            assert page.locator('#authRestoring').is_hidden()
            assert page.locator('#application').get_attribute('aria-busy') is None
            page.wait_for_function("document.querySelector('#accountEmail').textContent === 'alice@example.test'")
        else:
            page.locator('#authGate').wait_for(state='visible')
            assert page.locator('#application').is_hidden()
            assert page.locator('#authRestoring').is_hidden()
            assert not api.calls, 'Signed-out user accessed a private endpoint'
        assert not errors, errors
        context.close()

    # Broken SDK still exposes a usable sign-in screen and a meaningful error.
    class BrokenSDKFixture(Fixture):
        def route(self, route):
            if route.request.url == URL.replace('vortex/', 'web/vendor/firebase.js'):
                route.fulfill(body='', content_type='application/javascript')
            else:
                super().route(route)

    api = BrokenSDKFixture()
    context = browser.new_context()
    context.route('**/*', api.route)
    page = context.new_page()
    page.goto(URL)
    page.locator('#authGate').wait_for(state='visible')
    assert 'Sign-in could not load' in page.locator('#authStatus').inner_text()
    assert page.locator('#application').is_hidden()
    assert page.locator('#authRestoring').is_hidden()
    assert not api.calls
    context.close()
    print('Vortex auth hydration: no login flash, no premature access, signed-out and SDK failure paths passed', flush=True)


def run():
    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        options = {'executable_path': os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}
        browser = pw.chromium.launch(headless=True, **options)
        verify_delayed_auth_restore(browser)
        for width in (320, 390, 1280):
            api = Fixture()
            context = browser.new_context(viewport={'width':width, 'height':844}, has_touch=width < 760, accept_downloads=True)
            context.route('**/*', api.route)
            page = context.new_page()
            page.set_default_timeout(7000)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(URL)
            page.locator('#authStatus').get_by_text('Sign in to open your private media history.').wait_for()
            assert page.locator('#application').is_hidden()
            assert page.locator('#application').evaluate('el=>el.inert')
            assert not api.calls, 'Unauthenticated page accessed private backend'
            config = page.evaluate('__firebaseCalls[0]')
            assert config['name'] == 'vision-account-login'
            assert config['config']['projectId'] == 'visionboard-api'
            assert config['config']['appId'] == '1:150865729216:web:554423d0c7602d3a47bf25'
            no_overflow(page)
            page.locator('#googleSignIn').click()
            page.locator('#application').wait_for(state='visible')
            page.locator('#connectionStatus').get_by_text('Connected to FUPCJ Server').wait_for()
            assert page.evaluate('__persistence') == 'LOCAL'
            assert page.evaluate('__providerOptions.prompt') == 'select_account'
            page.wait_for_function("document.querySelector('#profileImage').src.startsWith('blob:') && document.querySelector('#profileImage').naturalWidth > 0")
            assert any(call['path']=='/api/venture/profile/avatar' and call['uid']=='alice' for call in api.calls)
            assert page.locator('#selection').is_hidden() and page.locator('#processing').is_hidden()
            assert page.locator('#mediaInfo').inner_text() == ''
            assert page.locator('.brand img').get_attribute('src') == '../vortex_character.png'
            assert page.locator('.brand span').evaluate('el=>getComputedStyle(el).fontFamily').startswith('"Lilita One"')
            assert page.locator('html').evaluate('el=>getComputedStyle(el).backgroundColor') == 'rgb(16, 19, 20)'
            # The moving silver finish follows the transparent mascot, not a rectangle.
            page.wait_for_function("document.querySelector('.brand img').naturalWidth > 0")
            shimmer = page.locator('.brand').evaluate("""element => {
                const effect = getComputedStyle(element, '::after');
                return {animation:effect.animationName, mask:effect.webkitMaskImage || effect.maskImage,
                        width:effect.width, pointerEvents:effect.pointerEvents};
            }""")
            assert shimmer['animation'] == 'vortex-character-shimmer'
            assert 'vortex_character.png' in shimmer['mask']
            assert shimmer['pointerEvents'] == 'none'
            assert shimmer['width'] == ('38px' if width <= 380 else '40px' if width <= 600 else '44px')

            # URL inspection is a persistent server job; unknown progress has no number.
            page.locator('#sourceInput').fill(VIDEO['url'])
            page.locator('#sourceInput').press('Enter')
            page.wait_for_function("document.querySelector('#processing').hidden===false")
            inspected = api.jobs['alice'][0]
            assert inspected['kind'] == 'inspect' and inspected['input'] == VIDEO['url']
            assert page.locator('#progressPercent').inner_text() == ''
            assert page.locator('#progressTrack').get_attribute('aria-valuenow') is None
            assert page.locator('#selection').is_hidden()
            inspected.update(status='processing', phase='Reading source metadata', progress=42.5)
            refresh(page)
            assert page.locator('#progressPercent').inner_text() == '43%'
            assert page.locator('#progressTrack').get_attribute('aria-valuenow') == '42.5'
            inspected.update(status='complete', phase='Media ready', progress=None, media=VIDEO, results=[VIDEO])
            refresh(page)
            assert page.locator('#selectionTitle').inner_text() == VIDEO['title']
            for value in ('16:9', '3840 × 2160', '60 fps', 'H.264', 'AAC'):
                assert value in page.locator('#mediaInfo').inner_text()
            assert page.locator('#processing').is_hidden()
            assert page.locator('#outputFormat').input_value() == 'mp4'
            assert page.locator('#outputFormat option').all_text_contents() == ['MP4', 'MOV']
            assert page.locator('#selectionSourceLink').get_attribute('href') == VIDEO['url']
            assert page.locator('#selectionSourceLink').get_attribute('rel') == 'noopener noreferrer'
            with page.expect_popup() as popup:
                page.locator('#selectionSourceLink').click()
            popup.value.wait_for_load_state()
            assert api.source_visits[-1] == VIDEO['url']
            popup.value.close()
            page.locator('input[name=downloadMode][value=audio]').check()
            assert page.locator('#qualityControls').is_hidden()
            assert page.locator('#outputFormat option').all_text_contents() == ['M4A', 'MP3', 'WAV']
            page.locator('#outputFormat').select_option('mp3')
            assert page.locator('#selection').is_visible()
            page.locator('input[name=downloadMode][value=video]').check()
            page.locator('#outputFormat').select_option('mov')
            no_overflow(page)
            page.screenshot(path=str(SHOTS/f'vortex-formats-{width}.png'), full_page=True)
            page.locator('input[name=quality][value=max]').check()
            assert page.locator('input[name=quality][value=max]+span').evaluate('el=>getComputedStyle(el).backgroundColor') == 'rgb(209, 186, 162)'
            page.wait_for_function("document.querySelector('#selection').getAttribute('aria-busy')==='true'")
            quality_inspect = next_job(page, api, inspected['id'])
            assert quality_inspect['kind'] == 'inspect' and quality_inspect['quality'] == 'max'
            assert page.locator('#selection').is_visible(), 'Changing quality hid selected media'
            assert '3840 × 2160' in page.locator('#mediaInfo').inner_text()
            assert page.locator('#processing').is_hidden(), 'Background quality lookup exposed the full processing display'
            assert page.locator('#downloadButton').is_enabled(), 'Background quality checks must not gray out Download'
            assert 'Checking Max quality' in page.locator('#downloadStatus').inner_text()
            quality_inspect.update(status='complete', phase='Media ready', progress=None, media=VIDEO, results=[VIDEO])
            refresh(page)
            assert page.locator('#selection').get_attribute('aria-busy') is None

            # Failed refreshes retain the prior metadata and selected quality.
            page.locator('input[name=quality][value=small]').check()
            failed_quality = next_job(page, api, quality_inspect['id'])
            page.locator('#downloadButton').click()
            page.locator('#downloadButton').click()
            assert 'Download queued' in page.locator('#downloadStatus').inner_text()
            assert not [job for job in api.jobs['alice'] if job['kind'] == 'download'], 'A queued tap must wait for the quality lookup'
            failed_quality.update(status='error', error='Fixture provider temporarily unavailable')
            refresh(page)
            assert not [job for job in api.jobs['alice'] if job['kind'] == 'download'], 'Failed quality lookup must not fall back to Balanced'
            assert 'not started' in page.locator('#downloadStatus').inner_text()
            assert page.locator('#downloadButton').is_enabled()
            assert page.locator('#selection').is_visible()
            assert page.locator('input[name=quality][value=max]').is_checked()
            assert '3840 × 2160' in page.locator('#mediaInfo').inner_text()

            # Rapid quality changes cancel superseded lookups; only the latest applies.
            page.locator('input[name=quality][value=small]').check()
            superseded = next_job(page, api, failed_quality['id'])
            page.locator('input[name=quality][value=balanced]').check()
            page.locator('input[name=quality][value=max]').check()
            latest_quality = next_job(page, api, superseded['id'])
            assert superseded['status'] == 'cancelled'
            assert latest_quality['quality'] == 'max'
            latest_quality.update(status='complete', phase='Media ready', media=VIDEO, results=[VIDEO])
            refresh(page)
            assert page.locator('input[name=quality][value=max]').is_checked()
            assert page.locator('#selection').is_visible()

            # Tap immediately after changing quality. The enabled button should
            # remember the clicked format, submit once after inspection, and
            # start the browser download once the conversion finishes.
            page.locator('input[name=quality][value=balanced]').check()
            assert page.locator('#downloadButton').is_enabled()
            page.locator('#downloadButton').click()
            page.locator('#downloadButton').click()
            assert 'Download queued' in page.locator('#downloadStatus').inner_text()
            preparing = next_job(page, api, latest_quality['id'])
            assert preparing['kind'] == 'inspect' and preparing['quality'] == 'balanced'
            assert not [job for job in api.jobs['alice'] if job['kind'] == 'download']
            preparing.update(status='complete', phase='Media ready', media=VIDEO, results=[VIDEO])
            refresh(page)
            queued_download = next_job(page, api, preparing['id'])
            assert queued_download['kind'] == 'download' and queued_download['quality'] == 'balanced'
            assert queued_download['downloadMode'] == 'video' and queued_download['videoFormat'] == 'mov'
            page.locator('#downloadStatus').get_by_text('Saving automatically when ready', exact=False).wait_for()
            page.locator('#downloadButton').click()
            assert len([job for job in api.jobs['alice'] if job['kind'] == 'download']) == 1, 'Repeated taps duplicated queued jobs'
            queued_download.update(status='processing', phase='Converting media', progress=36)
            refresh(page)
            assert '36%' in page.locator('#downloadStatus').inner_text()
            queued_download.update(status='complete', phase='Ready', progress=100, filename='Harbor.mp4', size=24,
                                   format='mp4', resultReady=True, completedAt=time.time(), expiresAt=time.time()+5*86400)
            with page.expect_download() as auto_event:
                refresh(page)
            assert auto_event.value.suggested_filename == 'Harbor.mp4'
            page.locator('#downloadFallback').wait_for(state='visible')
            assert 'File ready' in page.locator('#downloadStatus').inner_text()
            ticket_calls = len([call for call in api.calls if call['path'].endswith('/ticket')])
            refresh(page)
            assert len([call for call in api.calls if call['path'].endswith('/ticket')]) == ticket_calls, 'Auto-save requested a second ticket'
            with page.expect_download() as manual_event:
                page.locator('#downloadFallback').click()
            assert manual_event.value.suggested_filename == 'Harbor.mp4'
            before_manual = len(api.jobs['alice'])
            with page.expect_download() as retry_event:
                page.locator('#downloadButton').click()
            assert retry_event.value.suggested_filename == 'Harbor.mp4'
            assert len(api.jobs['alice']) == before_manual, 'Saving again should not re-encode a ready file'
            page.locator('input[name=quality][value=max]').check()
            restore_max = next_job(page, api, queued_download['id'])
            restore_max.update(status='complete', phase='Media ready', media=VIDEO, results=[VIDEO])
            refresh(page)
            assert page.locator('#downloadFallback').is_hidden()

            api.compatible_exports = False
            before = len(api.jobs['alice'])
            page.locator('#downloadButton').click()
            page.locator('#notice').get_by_text('Update Vision PC on FUPCJ Server', exact=False).wait_for()
            assert len(api.jobs['alice']) == before, 'An outdated worker must not silently return MKV'
            api.compatible_exports = True
            page.locator('#downloadButton').click()
            downloaded = next_job(page, api, restore_max['id'])
            page.locator(f'#activityList [data-id="{downloaded["id"]}"]').wait_for()
            assert downloaded['kind'] == 'download' and downloaded['quality'] == 'max'
            assert downloaded['downloadMode'] == 'video' and downloaded['videoFormat'] == 'mov'
            assert downloaded['requestId'] != inspected['requestId']
            downloaded.update(status='processing', phase='Downloading source media', progress=27, media=VIDEO)
            refresh(page)
            assert page.locator(f'#activityList [data-id="{downloaded["id"]}"] .source-thumbnail').get_attribute('href') == VIDEO['url']
            assert page.locator('#progressPercent').inner_text() == '27%'
            page.locator('#terminalLine').get_by_text('Downloading source media', exact=True).wait_for()
            page.wait_for_timeout(350)
            no_overflow(page)
            page.screenshot(path=str(SHOTS/f'vortex-processing-{width}.png'), full_page=True)
            # Accepted work remains in the sidebar archive after a page reload.
            page.reload()
            page.locator(f'#historyList [data-id="{downloaded["id"]}"]').wait_for(state='attached')
            assert page.locator('#authGate').is_hidden()
            assert page.locator('#activityList .activity-item').count() == 0, 'Past sessions must not appear in current activity'
            assert page.locator('#historyList').get_by_text(VIDEO['title'], exact=True).count() == 1
            assert page.locator('#selectionTitle').inner_text() == VIDEO['title']
            assert page.locator('#processing').is_hidden(), 'Past session progress must not show on main page'
            downloaded.update(status='complete', phase='Ready', progress=100, filename='Harbor.mp4', size=24,
                              format='mp4', resultReady=True, completedAt=time.time(), expiresAt=time.time()+5*86400)
            refresh(page)
            row = page.locator(f'#historyList [data-id="{downloaded["id"]}"]')
            assert row.locator('.history-item-meta').inner_text().startswith('Ready')
            assert row.locator('.history-source').get_attribute('target') == '_blank'
            assert row.locator('.history-source').get_attribute('href') == VIDEO['url']
            page.screenshot(path=str(SHOTS/f'vortex-ready-{width}.png'), full_page=True)
            page.locator('#menuButton').click()
            row.locator('.history-actions').click()
            page.locator('#saveFile').wait_for(state='visible')
            assert page.locator('#shareFile').is_hidden(), 'Unavailable native sharing must not be advertised'
            assert page.locator('#saveFile').get_attribute('href').startswith(BACKEND+'/api/vortex/jobs/'+downloaded['id']+'/file?ticket=')
            assert 'save to Files' in page.locator('#actionStatus').inner_text()
            with page.expect_download() as event:
                page.locator('#saveFile').click()
            assert event.value.suggested_filename == 'Harbor.mp4'
            page.locator('#actionDialog .close-dialog').click()
            if width == 390:
                # Browser API stubs exercise two-tap native sharing without an OS UI.
                page.evaluate('''Object.defineProperty(navigator,'canShare',{configurable:true,value:()=>true});
                    Object.defineProperty(navigator,'share',{configurable:true,value:async value=>{window.__shared={name:value.files[0].name,size:value.files[0].size,title:value.title};}});''')
                row.locator('.history-actions').click()
                page.locator('#shareFile').wait_for(state='visible')
                page.locator('#shareFile').click()
                page.get_by_role('button', name='Share file', exact=True).wait_for()
                assert not page.evaluate('!!window.__shared'), 'Native share must wait for a fresh user gesture'
                page.locator('#shareFile').click()
                assert page.evaluate('__shared') == dict(name='Harbor.mp4', size=19, title=VIDEO['title'])
                page.locator('#actionDialog .close-dialog').click()
                page.evaluate("Object.defineProperty(navigator,'canShare',{configurable:true,value:()=>false})")
                row.locator('.history-actions').click()
                page.locator('#shareFile').click()
                page.locator('#actionStatus').get_by_text('This file cannot be shared by your browser. Use Save file instead.').wait_for()
                assert page.locator('#saveFile').is_visible()
                page.locator('#actionDialog .close-dialog').click()

            # Every past item exposes a three-dot action menu, including on mobile.
            row.locator('.history-actions').click()
            page.locator('#deleteJob').click()
            page.locator('#confirmNo').click()
            assert row.count() == 1
            page.locator('#deleteJob').click()
            page.locator('#confirmYes').click()
            row.wait_for(state='detached')
            assert downloaded not in api.jobs['alice']
            assert page.locator(f'#historyList [data-id="{downloaded["id"]}"]').count() == 0
            page.locator('#closeMenu').click()

            # Cancel active work; completed/expired work exposes different actions.
            page.locator('input[name=downloadMode][value=audio]').check()
            page.locator('#outputFormat').select_option('mp3')
            previous_job = api.jobs['alice'][0]['id']
            page.locator('#downloadButton').click()
            cancelled = next_job(page, api, previous_job)
            assert cancelled['downloadMode'] == 'audio' and cancelled['audioFormat'] == 'mp3'
            assert cancelled['quality'] == 'max'
            page.locator(f'#activityList [data-id="{cancelled["id"]}"] .icon-button').click()
            assert page.locator('#deleteJob').is_visible(), 'Queued jobs can be deleted permanently'
            assert page.locator('#cancelJob').is_visible(), 'Cancelled jobs remain in history'
            page.locator('#cancelJob').click()
            page.locator('#actionDialog').wait_for(state='hidden')
            assert api.jobs['alice'][0]['status'] == 'cancelled'
            assert page.locator(f'#activityList [data-id="{cancelled["id"]}"] .item-status').inner_text() == 'Cancelled'
            page.locator('input[name=downloadMode][value=video]').check()
            page.locator(f'#activityList [data-id="{cancelled["id"]}"] .icon-button').click()
            page.locator('#retryJob').click()
            assert page.locator('#reexportPanel').is_visible()
            assert page.locator('#reexportMode').input_value() == 'audio'
            assert page.locator('#reexportFormat').input_value() == 'mp3'
            page.locator('#reexportFormat').select_option('wav')
            page.locator('#reexportSubmit').click()
            retried = next_job(page, api, cancelled['id'])
            assert retried['downloadMode'] == 'audio' and retried['audioFormat'] == 'wav', 'New format must be applied'
            assert cancelled['audioFormat'] == 'mp3', 'Original export must remain unchanged'
            # Deleting a queued re-export removes both the history record and
            # the pending automatic save instead of leaving a ghost download.
            page.locator('#menuButton').click()
            new_row = page.locator(f'#historyList [data-id="{retried["id"]}"]')
            new_row.locator('.history-actions').click()
            assert page.locator('#cancelJob').is_visible() and page.locator('#deleteJob').is_visible()
            page.locator('#deleteJob').click()
            page.locator('#confirmYes').click()
            new_row.wait_for(state='detached')
            assert retried not in api.jobs['alice']
            page.locator('#closeMenu').click()
            expired = api.new_job(status='expired', phase='File expired', media={**VIDEO, 'title':'Expired archive'}, expiresAt=time.time()-1)
            refresh(page)
            assert page.locator(f'#activityList [data-id="{expired["id"]}"]').count() == 0
            page.locator('#menuButton').click()
            page.locator(f'#historyList [data-id="{expired["id"]}"] .history-actions').click()
            assert page.locator('#saveFile').is_hidden() and page.locator('#cancelJob').is_hidden()
            assert page.locator('#retryJob').is_visible() and page.locator('#deleteJob').is_visible()
            page.locator('#actionDialog .close-dialog').click()
            page.locator('#closeMenu').click()

            # Search results are server data, rendered as text, then re-inspected.
            page.locator('#sourceInput').fill('evening music')
            page.locator('#sourceInput').press('Enter')
            submitted(page)
            search = api.jobs['alice'][0]
            assert search['input'] == 'evening music' and search['kind'] == 'inspect'
            matches = [{**AUDIO, 'title':'Evening <img onerror=alert(1)> soundtrack', 'thumbnail':VIDEO['thumbnail']}]
            matches += [{**VIDEO, 'title':f'Video result {i}', 'url':f'https://www.youtube.com/watch?v=video{i:06}'} for i in range(15)]
            search.update(status='complete', phase='Search complete', results=matches, searchNextPage=1)
            refresh(page)
            assert page.locator('#resultList .search-result').count() == 16
            assert page.locator('#resultList .item-title img').count() == 0
            page.locator('#resultList .search-result').first.scroll_into_view_if_needed()
            page.wait_for_function("document.querySelector('#resultList img').naturalWidth>0")
            page.locator('#loadMoreResults').scroll_into_view_if_needed()
            more = next_job(page, api, search['id'])
            assert more['searchPage'] == 1 and more['input'] == search['input']
            assert page.locator('#resultList .search-result').count() == 16
            assert page.locator('#searchResults').is_visible()
            more.update(status='complete', results=[matches[-1], {**VIDEO, 'title':'A later result', 'url':'https://www.youtube.com/watch?v=later000001'}], searchNextPage=2)
            refresh(page)
            assert page.locator('#resultList .search-result').count() == 17, 'Later pages must append without duplicates'
            page.locator('#loadMoreResults').scroll_into_view_if_needed()
            failed_more = next_job(page, api, more['id'])
            failed_more.update(status='error', error='Search temporarily unavailable')
            refresh(page)
            assert page.locator('#searchStatus').inner_text() == 'Search temporarily unavailable'
            assert page.locator('#resultList .search-result').count() == 17
            page.locator('#loadMoreResults').click()
            last = next_job(page, api, failed_more['id'])
            assert last['searchPage'] == 2
            last.update(status='complete', results=[], searchNextPage=None)
            refresh(page)
            assert page.locator('#loadMoreResults').is_hidden()
            assert page.locator('#resultList .search-result').count() == 17
            no_overflow(page)
            page.locator('#resultList .search-result').first.scroll_into_view_if_needed()
            page.screenshot(path=str(SHOTS/f'vortex-search-{width}.png'), full_page=True)
            page.locator('#resultList .search-result').first.click()
            submitted(page)
            audio = api.jobs['alice'][0]
            assert audio['input'] == AUDIO['url'] and audio['kind'] == 'inspect'
            audio.update(status='complete', phase='Media ready', media=AUDIO, results=[AUDIO])
            refresh(page)
            assert page.locator('#selectionTitle').inner_text() == AUDIO['title']
            assert page.locator('#qualityControls').is_hidden() and page.locator('#originalAudio').is_visible()
            for value in ('flac', '1411 kbps', '48 kHz', 'Stereo'):
                assert value in page.locator('#mediaInfo').inner_text()
            before_audio_job = api.jobs['alice'][0]['id']
            page.locator('#downloadButton').click()
            audio_download = next_job(page, api, before_audio_job)
            assert audio_download['kind'] == 'download' and audio_download['quality'] == 'max'

            # Menu traps focus, shares links, closes with Escape, and respects motion settings.
            page.locator('#menuButton').click()
            page.wait_for_timeout(350)
            assert page.locator('#navigation nav a').all_text_contents() == ['Vision', 'Venture']
            assert page.locator('#navigation nav a').nth(0).get_attribute('href') == '../?view=vision'
            assert page.locator('#navigation nav a').nth(1).get_attribute('href') == '../?view=venture'
            assert page.locator('#navigation nav svg').count() == 0, 'App links should be text-only'
            assert page.locator('#navigation nav [aria-current]').count() == 0, 'Vortex should not link to itself'
            assert page.locator('#navigation .nav-brand-name').inner_text() == 'Vortex'
            assert page.locator('#navigation .nav-brand-head img').get_attribute('src') == '../vortex_character.png'
            head = page.locator('#navigation .nav-brand-head')
            # Animated transforms temporarily alter the rendered box; the base
            # character dimensions must remain 33×45 CSS pixels.
            dims = head.evaluate("el=>({width:getComputedStyle(el).width,height:getComputedStyle(el).height})")
            assert dims == {'width':'33px','height':'45px'}, dims
            assert head.evaluate("el=>getComputedStyle(el).animationName") == 'vortex-head-enter'
            font = page.locator('#navigation .nav-brand-name').evaluate(
                "el=>({family:getComputedStyle(el).fontFamily,size:getComputedStyle(el).fontSize,color:getComputedStyle(el).color})")
            assert 'Lilita One' in font['family'] and font['size'] == '30px', font
            assert font['color'] == page.locator('.brand span').evaluate('el=>getComputedStyle(el).color')
            assert page.locator('#mainContent').evaluate('el=>el.inert')
            no_overflow(page)
            page.screenshot(path=str(SHOTS/f'vortex-menu-{width}.png'), full_page=True)
            page.locator('#menuSignOut').focus()
            page.keyboard.press('Tab')
            assert page.evaluate('document.activeElement.id') == 'closeMenu'
            page.keyboard.press('Escape')
            assert page.locator('#navigation').is_hidden()
            assert page.evaluate('document.activeElement.id') == 'menuButton'
            page.emulate_media(reduced_motion='reduce')
            assert page.locator('#progressFill').evaluate('el=>getComputedStyle(el).animationName') == 'none'
            assert page.locator('.brand').evaluate("el=>getComputedStyle(el,'::after').animationName") == 'none'
            page.locator('#menuButton').click()
            assert head.evaluate("el=>getComputedStyle(el).animationName") == 'none'
            page.keyboard.press('Escape')

            # Download history remains reachable beyond the first 100 records.
            if width == 390:
                for index in range(102):
                    api.new_job(status='cancelled', phase='Cancelled', media={**VIDEO, 'title':f'Older media {index}'})
                refresh(page)
                page.locator('#menuButton').click()
                assert page.locator('#loadOlder').is_visible()
                expected = len([job for job in api.jobs['alice'] if job['kind'] == 'download'])
                page.locator('#loadOlder').click()
                page.wait_for_function('count=>document.querySelectorAll("#historyList .history-item").length===count', arg=expected)
                assert page.locator('#loadOlder').is_hidden()
                page.locator('#closeMenu').click()
                refresh(page)
                page.locator('#menuButton').click()
                assert page.locator('#historyList .history-item').count() == expected, 'Refresh collapsed loaded history'
                assert page.locator('#activityList .activity-item').count() < expected, 'Older history leaked into main activity'
                page.locator('#closeMenu').click()

            # An in-flight Alice history result must not reappear after switching to Bob.
            # The same boundary must protect a delayed private Vision avatar.
            api.profile_version = 'changed-avatar'
            api.hold_avatar = True
            page.locator('#profileButton').click()
            deadline = time.monotonic() + 7
            while api.held_avatar is None and time.monotonic() < deadline:
                page.wait_for_timeout(20)
            assert api.held_avatar is not None
            page.locator('#accountDialog .close-dialog').click()
            api.hold_list = True
            # A scheduled refresh may consume the held request and disable the
            # button first. Dispatch without waiting for actionability: either
            # request exercises the same response/account isolation boundary.
            page.locator('#refreshButton').dispatch_event('click')
            deadline = time.monotonic() + 7
            while api.held is None and time.monotonic() < deadline:
                page.wait_for_timeout(20)
            assert api.held is not None
            page.evaluate("__switchUser('bob')")
            api.release()
            try:
                api.held_avatar.fulfill(body=(ROOT/'vortex_character.png').read_bytes(), content_type='image/png', headers={'Access-Control-Allow-Origin':'*'})
            except Exception:
                pass  # The old identity's request was aborted.
            page.wait_for_timeout(150)
            assert page.locator('#activityList .activity-item').count() == 0
            assert page.locator('#historyList .history-item').count() == 0, 'Previous account history leaked'
            assert page.locator('#selection').is_hidden() and page.locator('#searchResults').is_hidden()
            assert page.locator('#sourceInput').input_value() == ''
            assert page.locator('#accountEmail').inner_text() == 'bob@example.test'
            assert page.locator('#profileImage').is_hidden(), 'Alice profile image leaked into Bob account'
            assert page.locator('#profileImage').get_attribute('src') is None
            assert not page.locator('#notice').inner_text(), 'Old-account errors leaked into new account'
            page.locator('#profileButton').click()
            page.locator('#accountSignOut').click()
            page.locator('#confirmYes').click()
            page.locator('#authGate').wait_for(state='visible')
            assert page.locator('#application').is_hidden()
            assert not errors, errors
            assert not api.unexpected, api.unexpected
            assert all(call['token'].startswith('Bearer fixture-') for call in api.calls if not (call['path'].endswith('/file') and not call['token']))
            print(f'{width}px: auth, inspection/search, metadata, quality, real progress, recovery, files, cancellation, deletion, expiry, account race and layout passed', flush=True)
            context.close()
        browser.close()


if __name__ == '__main__':
    run()
