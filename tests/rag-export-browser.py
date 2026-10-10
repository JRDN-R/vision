"""Check the built app's export chooser, readable RAG, and original ZIP route.

All network and source content are fixtures. No account or provider is contacted.
"""
from pathlib import Path
import zipfile

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent.parent
URL = 'https://vision-rag-export.test/'
HOOK = r'''
let ragFixtureMode='complete';
const ragFixtureMeta={id:'project_rag_fixture_1',revision:8,key:'fixture-project-secret',ownerUid:'alice',backendUrl:'https://processor.test.ts.net'};
projectCapabilities=async()=>({capabilities:{intelligentContextV1:ragFixtureMode!=='old'}});
ensureRemoteProject=async()=>state.projectCloud;
projectIsTemporary=()=>false;
projectQueueSave=()=>{};projectBackup=async()=>{};
projectRequest=async(path,options={})=>{
 if(ragFixtureMode==='pending')return new Response(JSON.stringify({revision:8,status:'updating',ready:false}));
 if(!path.endsWith('/search'))return new Response(JSON.stringify({revision:8,status:'ready',ready:true}));
 const partial=ragFixtureMode==='partial';
 return new Response(JSON.stringify({ready:true,status:'ready',manifest:{projectId:ragFixtureMeta.id,revision:8},items:[{kind:'record',text:'OPN 0060, 0.380, complete source note.'}],text:'VISION LOCAL CONTEXT MANIFEST\nEVIDENCE\nOPN 0060, 0.380, complete source note.'+(partial?'\nRETRIEVAL LIMITATIONS\nImage pixels are not included.':''),complete:!partial,warnings:partial?['Image pixels are not included.']:[]}));
};
window.ragFixture={
 setup:async()=>{
  await accountReady;
  accountUpdateGate=()=>{};accountShowWelcome=()=>{};accountCanUseApp=()=>true;
  for(const d of document.querySelectorAll('dialog[open]'))d.close();
  document.documentElement.classList.remove('account-locked','account-restoring','account-restore-failed');$('accountGate').hidden=true;
  document.querySelectorAll('[inert]').forEach(el=>el.inert=false);
  projectPending=false;projectAccountSwitching=false;
  state.projectCloud={...ragFixtureMeta};state.title='RAG browser fixture';state.mainPrompt='Find operation 0060 and preserve its exact bore size.';
  const canvas=document.createElement('canvas');canvas.width=80;canvas.height=50;canvas.getContext('2d').fillRect(0,0,80,50);
  state.nodes=[{id:'node-fixture',kind:'node',title:'Reference',prompt:'Preserve 0.380 exactly.',caption:'',src:canvas.toDataURL(),width:80,height:50,x:0,y:0,annotations:[],attachments:[{id:'source-fixture',name:'evidence.txt',mime:'text/plain',data:'data:text/plain;base64,T1BOIDAwNjAgfCAwLjM4MCB8IGNvbXBsZXRlIHNvdXJjZSBub3RlLg==',size:42}]}];
  state.edges=[];renderAll();
 },mode:value=>ragFixtureMode=value
};
'''


def main():
    html = (ROOT / 'Vision.html').read_text(encoding='utf-8')
    position = html.rfind('renderAll();')
    assert position > 0
    html = html[:position] + HOOK + html[position:]
    shots = ROOT / 'tests' / 'venture-screenshots'
    shots.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for width in (390, 1280):
                context = browser.new_context(viewport={'width': width, 'height': 844}, accept_downloads=True)
                context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=html)
                              if route.request.url == URL else route.abort())
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(URL, wait_until='domcontentloaded')
                page.evaluate('ragFixture.setup()')
                # The existing menu invokes this same main Export button.
                page.evaluate('document.getElementById("exportBtn").click()')
                dialog = page.locator('#exportChoiceDialog')
                expect(dialog).to_be_visible()
                expect(page.locator('#downloadRag')).to_have_text('Download RAG (recommended)Compact text context for AI, prepared on FUPCJ.')
                assert dialog.evaluate('(el)=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&el.scrollWidth<=el.clientWidth+1;}')
                assert page.locator('#downloadRag small').evaluate('(el)=>parseFloat(getComputedStyle(el).fontSize)<parseFloat(getComputedStyle(el.parentElement).fontSize)')
                page.screenshot(path=str(shots / f'rag-export-{width}.png'))
                with page.expect_download() as download:
                    page.locator('#downloadRag').click()
                file = download.value
                assert file.suggested_filename.endswith('.rag.txt')
                text = Path(file.path()).read_text()
                assert 'OPN 0060, 0.380, complete source note.' in text
                assert 'cannot automatically fetch omitted sources' in text
                assert 'fixture-project-secret' not in text

                page.evaluate("ragFixture.mode('partial')")
                await_downloads = []
                page.on('download', lambda download: await_downloads.append(download))
                page.locator('#downloadRag').click()
                expect(page.locator('#ragExportStatus')).to_contain_text('Click Download RAG again')
                assert not await_downloads
                with page.expect_download() as download:
                    page.locator('#downloadRag').click()
                assert 'Coverage: incomplete' in Path(download.value.path()).read_text()

                page.evaluate("ragFixture.mode('pending')")
                page.locator('#downloadRag').click()
                expect(page.locator('#downloadRag')).to_be_disabled()
                page.locator('#cancelExportChoice').click()
                expect(dialog).to_be_hidden()
                page.evaluate('document.getElementById("exportBtn").click()')
                expect(page.locator('#downloadRag')).to_be_enabled()
                page.evaluate("ragFixture.mode('old')")
                page.locator('#downloadRag').click()
                expect(page.locator('#ragExportStatus')).to_contain_text('Update FUPCJ or choose ZIP')

                page.locator('#chooseZipExport').click()
                expect(dialog).to_be_hidden()
                expect(page.locator('#exportDialog')).to_be_visible()
                with page.expect_download() as download:
                    page.locator('#startExport').click()
                assert download.value.suggested_filename.endswith('.zip')
                with zipfile.ZipFile(download.value.path()) as archive:
                    assert 'MAIN_PROMPT.txt' in archive.namelist()
                    assert 'Preserve 0.380 exactly.' in archive.read('MAIN_PROMPT.txt').decode()
                    assert archive.read('001-Reference/evidence.txt').startswith(b'OPN 0060 | 0.380')
                assert not errors, errors
                context.close()
        finally:
            browser.close()
    print('PASS: mobile/desktop chooser, smaller recommended label, RAG text, partial coverage, cancellation, old-server fallback, and original ZIP contents.')


if __name__ == '__main__':
    main()
