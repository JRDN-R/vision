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
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'), 'Horizontal page overflow'


def run():
    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        options = {'executable_path': os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}
        browser = pw.chromium.launch(headless=True, **options)
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
            page.locator('input[name=quality][value=max]').check()
            assert page.locator('input[name=quality][value=max]+span').evaluate('el=>getComputedStyle(el).backgroundColor') == 'rgb(209, 186, 162)'
            page.wait_for_function("document.querySelector('#selection').getAttribute('aria-busy')==='true'")
            quality_inspect = next_job(page, api, inspected['id'])
            assert quality_inspect['kind'] == 'inspect' and quality_inspect['quality'] == 'max'
            assert page.locator('#selection').is_visible(), 'Changing quality hid selected media'
            assert '3840 × 2160' in page.locator('#mediaInfo').inner_text()
            assert page.locator('#processing').is_hidden(), 'Background quality lookup exposed the full processing display'
            assert page.locator('#downloadButton').is_disabled()
            quality_inspect.update(status='complete', phase='Media ready', progress=None, media=VIDEO, results=[VIDEO])
            refresh(page)
            assert page.locator('#selection').get_attribute('aria-busy') is None

            # Failed refreshes retain the prior metadata and selected quality.
            page.locator('input[name=quality][value=small]').check()
            failed_quality = next_job(page, api, quality_inspect['id'])
            failed_quality.update(status='error', error='Fixture provider temporarily unavailable')
            refresh(page)
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
            page.locator('#downloadButton').click()
            page.locator('#activityList .activity-item').wait_for()
            downloaded = api.jobs['alice'][0]
            assert downloaded['kind'] == 'download' and downloaded['quality'] == 'max'
            assert downloaded['requestId'] != inspected['requestId']
            downloaded.update(status='processing', phase='Downloading source media', progress=27, media=VIDEO)
            refresh(page)
            assert page.locator('#progressPercent').inner_text() == '27%'
            page.locator('#terminalLine').get_by_text('Downloading source media', exact=True).wait_for()
            page.wait_for_timeout(350)
            no_overflow(page)
            page.screenshot(path=str(SHOTS/f'vortex-processing-{width}.png'), full_page=True)
            # Recovery is fetched from the server, with the same shared account session.
            page.reload()
            page.locator('#activityList .activity-item').wait_for()
            assert page.locator('#authGate').is_hidden()
            assert page.locator('#activityList').get_by_text(VIDEO['title'], exact=True).count() == 1
            assert page.locator('#selectionTitle').inner_text() == VIDEO['title']
            assert page.locator('#progressPercent').inner_text() == '27%'
            downloaded.update(status='complete', phase='Ready', progress=100, filename='Harbor.mp4', size=24,
                              format='mp4', resultReady=True, completedAt=time.time(), expiresAt=time.time()+5*86400)
            refresh(page)
            row = page.locator(f'[data-id="{downloaded["id"]}"]')
            assert row.locator('.item-status').inner_text() == 'Ready to save'
            assert row.locator('.item-expiry').inner_text().startswith('Deletes in ')
            assert '5d' in row.locator('.item-expiry').inner_text() or '4d' in row.locator('.item-expiry').inner_text()
            page.screenshot(path=str(SHOTS/f'vortex-ready-{width}.png'), full_page=True)
            row.locator('button').click()
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
                row.locator('button').click()
                page.locator('#shareFile').wait_for(state='visible')
                page.locator('#shareFile').click()
                page.get_by_role('button', name='Share file', exact=True).wait_for()
                assert not page.evaluate('!!window.__shared'), 'Native share must wait for a fresh user gesture'
                page.locator('#shareFile').click()
                assert page.evaluate('__shared') == dict(name='Harbor.mp4', size=19, title=VIDEO['title'])
                page.locator('#actionDialog .close-dialog').click()
                page.evaluate("Object.defineProperty(navigator,'canShare',{configurable:true,value:()=>false})")
                row.locator('button').click()
                page.locator('#shareFile').click()
                page.locator('#actionStatus').get_by_text('This file cannot be shared by your browser. Use Save file instead.').wait_for()
                assert page.locator('#saveFile').is_visible()
                page.locator('#actionDialog .close-dialog').click()

            # Long press is a discoverable alternative to the same overflow actions.
            if width < 760:
                row.dispatch_event('pointerdown', {'pointerType':'touch','clientX':80,'clientY':400})
                page.locator('#actionDialog').wait_for(state='visible')
                row.dispatch_event('pointerup', {'pointerType':'touch','clientX':80,'clientY':400})
            else:
                row.locator('button').click()
            page.locator('#deleteJob').click()
            page.locator('#confirmNo').click()
            assert row.count() == 1
            page.locator('#deleteJob').click()
            page.locator('#confirmYes').click()
            row.wait_for(state='detached')
            assert downloaded not in api.jobs['alice']

            # Cancel active work; completed/expired work exposes different actions.
            page.locator('#downloadButton').click()
            page.locator('#activityList .activity-item').wait_for()
            cancelled = api.jobs['alice'][0]
            page.locator(f'[data-id="{cancelled["id"]}"] button').click()
            assert page.locator('#deleteJob').is_hidden()
            page.locator('#cancelJob').click()
            page.locator('#actionDialog').wait_for(state='hidden')
            assert api.jobs['alice'][0]['status'] == 'cancelled'
            assert page.locator(f'[data-id="{cancelled["id"]}"] .item-status').inner_text() == 'Cancelled'
            expired = api.new_job(status='expired', phase='File expired', media={**VIDEO, 'title':'Expired archive'}, expiresAt=time.time()-1)
            refresh(page)
            page.locator(f'[data-id="{expired["id"]}"] button').click()
            assert page.locator('#saveFile').is_hidden() and page.locator('#cancelJob').is_hidden()
            assert page.locator('#retryJob').is_visible() and page.locator('#deleteJob').is_visible()
            page.locator('#actionDialog .close-dialog').click()

            # Search results are server data, rendered as text, then re-inspected.
            page.locator('#sourceInput').fill('evening music')
            page.locator('#sourceInput').press('Enter')
            submitted(page)
            search = api.jobs['alice'][0]
            assert search['input'] == 'evening music' and search['kind'] == 'inspect'
            search.update(status='complete', phase='Search complete', results=[{**AUDIO, 'title':'Evening <img onerror=alert(1)> soundtrack'}])
            refresh(page)
            assert page.locator('#resultList .search-result').count() == 1
            assert page.locator('#resultList .item-title img').count() == 0
            page.locator('#resultList .search-result').click()
            submitted(page)
            audio = api.jobs['alice'][0]
            assert audio['input'] == AUDIO['url'] and audio['kind'] == 'inspect'
            audio.update(status='complete', phase='Media ready', media=AUDIO, results=[AUDIO])
            refresh(page)
            assert page.locator('#selectionTitle').inner_text() == AUDIO['title']
            assert page.locator('#qualityControls').is_hidden() and page.locator('#originalAudio').is_visible()
            for value in ('flac', '1411 kbps', '48 kHz', 'Stereo'):
                assert value in page.locator('#mediaInfo').inner_text()
            page.locator('#downloadButton').click()
            page.wait_for_function('!document.querySelector("#downloadButton").disabled')
            assert api.jobs['alice'][0]['quality'] == 'max'

            # Menu traps focus, shares links, closes with Escape, and respects motion settings.
            page.locator('#menuButton').click()
            page.wait_for_timeout(350)
            assert page.locator('#navigation a').all_text_contents() == ['Vision', 'Venture', 'Vortex']
            assert page.locator('#navigation a').nth(1).get_attribute('href') == '../?view=venture'
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
            page.keyboard.press('Escape')

            # Download history remains reachable beyond the first 100 records.
            if width == 390:
                for index in range(102):
                    api.new_job(status='cancelled', phase='Cancelled', media={**VIDEO, 'title':f'Older media {index}'})
                refresh(page)
                assert page.locator('#loadOlder').is_visible()
                expected = len([job for job in api.jobs['alice'] if job['kind'] == 'download'])
                page.locator('#loadOlder').click()
                page.wait_for_function('count=>document.querySelectorAll("#activityList .activity-item").length===count', arg=expected)
                assert page.locator('#loadOlder').is_hidden()
                refresh(page)
                assert page.locator('#activityList .activity-item').count() == expected, 'Refresh collapsed loaded history'

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
