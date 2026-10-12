"""Recover a restored uncertain submission, then attach real image bytes.

Only the HTTP responses are fixtures: the composer, file input, FormData,
session storage, reload and request reconciliation use the application code.
No model quality or FUPCJ acceptance is implied.
"""
import json
import os
from pathlib import Path
import runpy

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = runpy.run_path(str(ROOT/'tests/venture-smoke.py'), run_name='pending_fixture')['fixture']
photo = ROOT/'tests/fixtures/local-visual/hopper.jpg'
shots = ROOT/'tests/venture-screenshots'
shots.mkdir(exist_ok=True)
probe = r'''
window.uploadPosts=[];
const pendingTestFetch=cloudFetch;
cloudFetch=async(path,options={})=>{
 if(path.endsWith('/runs')&&options.method==='POST'){
  const form=options.body,request=JSON.parse(form.get('options'));
  const file=form.get('attachments');
  window.uploadPosts.push({path,request,name:file.name,size:file.size,
   bytes:Array.from(new Uint8Array(await file.arrayBuffer()))});
  return Response.json({runId:'new-image-run',clientRequestId:request.clientRequestId,
   status:'queued',phase:'Queued',model:request.model,message:request.message,
   attachments:[],artifacts:[],text:'',createdAt:'2026-10-12T00:00:00Z',sequence:1},{status:202});
 }
 return pendingTestFetch(path,options);
};
'''
fixture = fixture.replace('</body>', '<script>'+probe+'</script></body>')

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, **(
        {'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}))
    for width in (390, 1280):
        settings = dict(model='qwen3.8-local',maxOutputTokens=2048,memoryEnabled=False,
                        runOptions=dict(webSearch=False,codeInterpreter=False))
        local = dict(id='qwen3.8-local',enabled=True,contextTokens=4096,maxOutputTokens=2048,
                     visual=dict(status='awaiting-integrated-check'),capabilities=dict(vision=False))
        old = dict(id='old-conversation',title='New venture',settings=settings,revision=1)
        conversations = {old['id']:old}
        status_checks = []

        def api(url, method, raw):
            path = url.split('?')[0]
            body = json.loads(raw or '{}')
            prefs = dict(settings=settings,preferenceRevision=0,disclosureAcknowledged=True)
            if path == '/health':data = dict(capabilities=dict(ventureV2=True))
            elif path == '/venture/models':data = dict(status='local-only',models=[local])
            elif path == '/venture/preferences':data = prefs
            elif path == '/venture/profile':data = dict(hasAvatar=False)
            elif path == '/venture/funding':data = dict(status='local',noApiTokenCharge=True)
            elif path == '/venture/conversations':
                if method == 'POST':
                    item = dict(id=body['id'],title='New venture',settings=body['settings'],revision=1)
                    conversations[item['id']] = item;data = dict(conversation=item)
                else:data = dict(conversations=list(conversations.values()),nextCursor=None,localModel=local)
            elif path.startswith('/venture/conversations/'):
                item = conversations[path.split('/')[-1]]
                if method == 'PATCH':item.update(body);item['revision'] += 1
                data = dict(conversation=item,runs=[],cursor=1,nextCursor=None,localModel=local)
            elif path == '/projects/old-conversation/runs':
                assert method == 'GET'
                status_checks.append(path);data = dict(runs=[])
            else:raise AssertionError('Unexpected fixture request: '+method+' '+path)
            return dict(status=200,type='application/json',body=json.dumps(data))

        context = browser.new_context(viewport=dict(width=width,height=844))
        context.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html',body=fixture)
                      if route.request.url == 'http://venture.test/' else route.abort())
        page = context.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.expose_function('ventureTestAPI',api)
        page.goto('http://venture.test/')
        page.locator('#runProjectBtn').click()
        page.wait_for_function('venture.ready')
        page.evaluate('''()=>{
          ventureRemember('selection','old-conversation');
          ventureRemember('pending-old-conversation',{cid:'old-conversation',clientRequestId:'original-request'});
        }''')
        page.reload()
        page.locator('#runProjectBtn').click()
        page.wait_for_function('venture.ready && !!venture.pending')
        expect(page.locator('#ventureModelLabel')).to_contain_text('image check pending')
        page.locator('#ventureFileInput').set_input_files(str(photo))
        expect(page.locator('#ventureNotice')).to_contain_text('Start new venture')
        assert page.evaluate('uploadPosts.length') == 0
        assert page.evaluate('venture.files.length') == 0
        page.locator('#ventureCheck').click()
        expect(page.locator('#ventureNotice')).to_contain_text('no longer has its upload')
        assert status_checks
        expect(page.locator('#venturePendingNew')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(shots/f'venture-pending-{width}.png'))
        page.locator('#venturePendingNew').click()
        page.wait_for_function('!venture.pending && !venture.current')
        assert page.evaluate("ventureRecall('pending-old-conversation').clientRequestId") == 'original-request'
        page.locator('#ventureFileInput').set_input_files(str(photo))
        expect(page.locator('#ventureAttachments')).to_contain_text(photo.name)
        page.locator('#ventureMessage').fill('Describe this image.')
        page.locator('#ventureSend').click()
        page.wait_for_function('uploadPosts.length===1 && !venture.pending && !venture.sending')
        upload = page.evaluate('uploadPosts[0]')
        assert bytes(upload['bytes']) == photo.read_bytes()
        assert upload['request']['model'] == 'qwen3.8-local'
        assert upload['request']['clientRequestId'] != 'original-request'
        assert upload['path'] != '/projects/old-conversation/runs'
        assert upload['request']['runOptions']['codeInterpreter'] is False
        assert page.evaluate('venture.localCapability.capabilities.vision') is False
        expect(page.locator('#ventureStop')).to_be_visible()
        assert not errors,errors
        context.close()
    browser.close()
print('Reload recovery and real image attachment submission passed at 390px and 1280px.')
