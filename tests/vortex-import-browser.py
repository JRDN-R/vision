"""Exercise the built Vision UI with deterministic media-server responses."""
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://vision-vortex-import.test/'
HOOK = r'''
let fixtureAudio=false,fixtureSerial=0,fixtureFinished=false,fixtureHoldLookup=false;
const fixtureCalls=[],fixtureMeta={id:'vortex_fixture_project',revision:1,backendUrl:'https://fixture.ts.net'};
projectQueueSave=()=>{};projectBackup=async()=>{};projectPerformSave=async()=>fixtureMeta;
ensureRemoteProject=async()=>fixtureMeta;projectCapabilities=async()=>({});
youtubeAPI=async(path,options={})=>{
 fixtureCalls.push({path,method:options.method||'GET',body:options.body?JSON.parse(options.body):null});
 if(path==='vortex/capabilities')return{ready:true,visionImport:true};
 if(path==='vortex/jobs')return{id:'lookup-'+(++fixtureSerial)};
 if(path.startsWith('vortex/jobs/')){
  if(options.method==='DELETE')return{};
  if(fixtureHoldLookup)await new Promise(resolve=>window.fixtureReleaseLookup=resolve);
  return{status:'complete',media:{title:fixtureAudio?'Music fixture':'Video fixture',mediaType:fixtureAudio?'audio':'video'}};
 }
 if(path==='vortex/import')return{id:'import-'+fixtureSerial};
 if(path.startsWith('jobs/')){
  if(options.method==='DELETE')return{};
  if(!path.endsWith('/result'))return{status:fixtureFinished?'complete':'processing',phase:'Transcribing on FUPCJ Server',progress:80};
  const c=document.createElement('canvas');c.width=320;c.height=208;c.getContext('2d').fillRect(0,0,320,208);
  return{title:fixtureAudio?'Music fixture':'Video fixture',mediaType:fixtureAudio?'audio':'video',duration:2,hasAudio:true,
    frames:fixtureAudio?[]:[{name:'frame-1.jpg',timestamp:0,data:c.toDataURL('image/jpeg')}],transcription:{text:'[00:00:00.000] Fixture transcript'}};
 }
 throw new Error('Unexpected fixture request '+path);
};
window.vortexFixture={
 setup:async()=>{
  await accountReady;accountUpdateGate=()=>{};accountCanUseApp=()=>true;accountSignedIn=()=>true;
  for(const d of document.querySelectorAll('dialog[open]'))d.close();
  document.documentElement.classList.remove('account-locked','account-restoring','account-restore-failed');$('accountGate').hidden=true;
  document.querySelectorAll('[inert]').forEach(el=>el.inert=false);
  projectAccountSwitching=false;projectLoading=false;busy=false;ioBusy=false;
  cloudConfig={kind:'private-pc',backendUrl:fixtureMeta.backendUrl};cloudAuth={kind:'firebase-google',uid:'fixture',backendUrl:fixtureMeta.backendUrl};accountFirebase={currentUser:{uid:'fixture',email:'fixture@example.test',getIdToken:async()=> 'fixture-token'}};
  state.projectCloud=fixtureMeta;state.settings.transcriptionProvider='local';state.settings.includeSoundEvents=false;soundEventsSelected=()=>false;
  state.nodes=[];state.edges=[];state.youtubeImports=[];renderAll();
 },
 audio:()=>{fixtureAudio=true;fixtureFinished=false;},finish:()=>{fixtureFinished=true;},
 calls:()=>fixtureCalls,nodes:()=>state.nodes,queue:()=>state.youtubeImports,
 reopen:()=>{state.youtubeImports=normalizeYouTubeImports(JSON.parse(JSON.stringify(state.youtubeImports)));resumeYouTubeImports();},
 hold:()=>{fixtureHoldLookup=true;},switchAccount:()=>{accountAuthEpoch++;cancelVortexImportDialog();},
 select:id=>selectNode(id),validate:()=>validateAttachments(state.nodes[0].attachments)
};
'''


def main():
    html = (ROOT / 'Vision.html').read_text()
    position = html.rfind('renderAll();')
    html = html[:position] + HOOK + html[position:]
    shots = ROOT / '.qa' / 'vortex-import'
    shots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for width in (390, 1280):
            context = browser.new_context(viewport={'width': width, 'height': 844}, has_touch=width < 760, bypass_csp=True)
            context.route('**/*', lambda route: route.fulfill(body=html, content_type='text/html') if route.request.url == URL else route.abort())
            page = context.new_page()
            errors, downloads = [], []
            page.on('pageerror', lambda error: errors.append(error.stack))
            page.on('download', lambda download: downloads.append(download))
            page.goto(URL, wait_until='domcontentloaded')
            page.evaluate('vortexFixture.setup()')
            page.locator('#emptyAdd').click()
            expect(page.locator('#boardAddVortex')).to_be_visible()
            page.locator('#boardAddVortex').click()
            expect(page.locator('#vortexImportDialog')).to_be_visible()
            page.locator('#vortexImportURL').fill('https://youtu.be/abcdefghijk')
            page.locator('#vortexImportFind').click()
            expect(page.locator('#vortexImportName')).to_have_text('Video fixture')
            expect(page.locator('#vortexImportApply')).to_be_enabled()
            assert page.locator('#vortexImportDialog').evaluate('(el)=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&el.scrollWidth<=el.clientWidth+1}')
            page.screenshot(path=str(shots / f'vortex-import-{width}.png'))
            page.locator('#vortexImportApply').dblclick()
            expect(page.locator('#vortexImportDialog')).to_be_hidden()
            page.wait_for_function('vortexFixture.queue().length===1 && !!vortexFixture.queue()[0].remoteId')
            assert page.evaluate('vortexFixture.nodes().length') == 1
            assert len([call for call in page.evaluate('vortexFixture.calls()') if call['path'] == 'vortex/import']) == 1
            assert page.evaluate('vortexFixture.queue()[0].sourceKind') == 'vortex'
            page.evaluate('vortexFixture.reopen();vortexFixture.finish()')
            try:
                page.wait_for_function('vortexFixture.queue().length===0')
            except Exception:
                print(page.evaluate('({queue:vortexFixture.queue(),calls:vortexFixture.calls(),nodes:vortexFixture.nodes()})'))
                print(errors)
                raise
            assert page.evaluate("vortexFixture.nodes()[0].attachments.some(a=>a.role==='video-frame')")
            assert page.evaluate("vortexFixture.nodes()[0].attachments.some(a=>a.role==='video-transcript')")
            page.evaluate('vortexFixture.validate()')
            # Files-tab shortcut targets the selected module, rather than adding another.
            page.evaluate("vortexFixture.audio();document.getElementById('addAttachmentsVortex').click()")
            expect(page.locator('#vortexImportNewModule')).not_to_be_checked()
            page.locator('#vortexImportURL').fill('https://music.youtube.com/watch?v=abcdefghijk')
            page.locator('#vortexImportFind').click()
            expect(page.locator('#vortexImportName')).to_have_text('Music fixture')
            page.locator('#vortexImportApply').click()
            expect(page.locator('#vortexImportDialog')).to_be_hidden()
            page.evaluate('vortexFixture.finish()')
            page.wait_for_function("vortexFixture.nodes()[0].attachments.some(a=>a.role==='audio'&&a.status==='complete')")
            assert page.evaluate('vortexFixture.nodes().length') == 1
            page.evaluate('vortexFixture.validate()')
            assert not downloads, 'Applying media must never download a file on this device'
            # Slow lookup responses are discarded after an account change.
            page.evaluate("document.getElementById('addAttachmentsVortex').click();vortexFixture.hold()")
            page.locator('#vortexImportURL').fill('https://example.org/audio.mp3')
            page.locator('#vortexImportFind').click()
            page.wait_for_function('typeof fixtureReleaseLookup === "function"')
            page.evaluate('vortexFixture.switchAccount();fixtureReleaseLookup()')
            expect(page.locator('#vortexImportDialog')).to_be_hidden()
            expect(page.locator('#vortexImportSelection')).to_be_hidden()
            assert not errors, errors
            print(f'{width}px: board/Files entry, Find/Apply, duplicate click, video/audio, restore, no download and account switch passed')
            context.close()
        browser.close()


if __name__ == '__main__':
    main()
