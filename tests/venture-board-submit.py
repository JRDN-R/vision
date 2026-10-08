"""Submit real mixed-content board ZIPs in the bundled UI, without paid requests."""
import base64
import io
import os
from pathlib import Path
import zipfile

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
shell = (ROOT / 'Vision.html').read_text()
probe = r'''
let boardFixtureCounter=0;
if(!crypto.randomUUID)crypto.randomUUID=()=> 'board-fixture-'+(++boardFixtureCounter);
window.boardSubmissionSetup=()=>{
 accountSignedIn=()=>true;ventureScope=()=> 'board-fixture';
 ventureSchedule=()=>{};ventureRemember=()=>{};ventureLoadHistory=async()=>{};
 document.querySelectorAll('[data-account-inert]').forEach(el=>{el.inert=false;delete el.dataset.accountInert;});
 document.documentElement.classList.remove('account-locked');$('accountGate').hidden=true;
 venture.uid='board-fixture';venture.open=true;venture.ready=true;
 $('visionVenture').hidden=false;ventureSidebar(false);
 const canvas=document.createElement('canvas');canvas.width=40;canvas.height=40;
 canvas.getContext('2d').fillRect(0,0,40,40);
 state={title:'Mixed board',mainPrompt:'Describe this scene with a funny caption.',
  nodes:[{id:'reference',kind:'image',title:'Reference',prompt:'Use the transcript too.',
   src:canvas.toDataURL(),width:40,height:40,x:0,y:0,caption:'A reference image',annotations:[],
   attachments:[
    {id:'binary',name:'source.bin',mime:'application/octet-stream',data:'data:application/octet-stream;base64,AAECA3+A/v8='},
    {id:'transcript',name:'transcript.txt',mime:'text/plain',data:'data:text/plain;base64,'+btoa('A funny transcript.\nSecond line.')}
   ]}],edges:[],settings:{...defaults(),outputWidth:160},view:{x:0,y:0,scale:1}};
 window.boardBefore=JSON.stringify(state);window.boardPosts=[];
 venture.files=[new File(['Extra message attachment.'],'extra.txt',{type:'text/plain'})];
 venturePaintAttachments();venturePaintStatus();
 cloudFetch=async(path,options={})=>{
  if(path==='/venture/conversations'&&options.method==='POST')return Response.json({conversation:{
   id:'board-conversation',title:'Board test',settings:venture.settings,revision:1}});
  if(path==='/projects/board-conversation/runs'&&options.method==='POST'){
   const form=options.body,request=JSON.parse(form.get('options'));
   window.boardPosts.push({form,request});
   return Response.json({runId:'board-run',status:'queued',phase:'Queued',
    clientRequestId:request.clientRequestId,message:request.message,projectPrompt:request.projectPrompt,
    model:request.model,runOptions:request.runOptions,attachments:[],artifacts:[],
    createdAt:'2026-10-08T00:00:00Z',updatedAt:'2026-10-08T00:00:00Z'},{status:202});
  }
  throw new Error('Unexpected fixture request: '+path);
 };
};
window.boardSubmissionResult=async()=>{
 const post=window.boardPosts[0];
 if(!post)return {count:0,notice:venture.notice};
 const archive=post.form.get('file'),bytes=new Uint8Array(await archive.arrayBuffer());
 return {count:window.boardPosts.length,name:archive.name,mime:archive.type,
  archive:btoa(Array.from(bytes,b=>String.fromCharCode(b)).join('')),options:post.request,
  extra:await post.form.get('attachments').text(),unchanged:window.boardBefore===JSON.stringify(state),
  pending:!!venture.pending,notice:venture.notice};
};
window.boardSubmissionFail=()=>{
 venture.runs=[];venture.rendered.clear();$('ventureTurns').replaceChildren();
 venture.files=[new File(['Keep this attachment.'],'keep.txt')];venturePaintAttachments();
 consoleBoardFiles=async()=>{throw new Error('Unable to prepare the board fixture.');};
 venturePaintStatus();
};
window.boardFailureResult=()=>({posts:window.boardPosts.length,files:venture.files.map(f=>f.name),
 pending:!!venture.pending,unchanged:window.boardBefore===JSON.stringify(state)});
'''
where = shell.rfind('\n})();')
assert where >= 0
shell = shell[:where] + probe + shell[where:]

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, **(
        {'executable_path': os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}))
    try:
        for width in (390, 1280):
            page = browser.new_page(viewport={'width': width, 'height': 844})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/*', lambda route: route.abort())
            page.set_content(shell, wait_until='load')
            page.evaluate('boardSubmissionSetup()')
            assert page.evaluate('typeof window.fflate') == 'undefined'
            page.locator('#ventureIncludeBoard').check()
            page.locator('#ventureMessage').fill("Make it good (it's supposed to be funny)")
            page.locator('#ventureSend').click()
            page.locator('#ventureNotice:not([hidden]), #ventureStop:not([hidden])').first.wait_for()
            result = page.evaluate('boardSubmissionResult()')
            assert result['count'] == 1, result
            assert result['name'] == 'vision-board.zip'
            assert result['mime'] == 'application/zip'
            with zipfile.ZipFile(io.BytesIO(base64.b64decode(result['archive']))) as archive:
                assert archive.testzip() is None
                assert archive.read('001-Reference/source.bin') == bytes([0, 1, 2, 3, 127, 128, 254, 255])
                assert archive.read('001-Reference/transcript.txt') == b'A funny transcript.\nSecond line.'
                assert archive.read('001-Reference/image.png').startswith(b'\x89PNG\r\n\x1a\n')
                prompt = archive.read('MAIN_PROMPT.txt').decode()
                assert 'Describe this scene with a funny caption.' in prompt
                assert 'Use the transcript too.' in prompt
                assert result['options']['projectPrompt'] == prompt
            assert result['extra'] == 'Extra message attachment.'
            assert result['unchanged'] and not result['pending'] and not result['notice'], result
            assert page.locator('#ventureMessage').input_value() == ''
            assert not errors, errors

            # A local preparation failure keeps the user's draft, board, and files.
            page.evaluate('boardSubmissionFail()')
            page.locator('#ventureMessage').fill('Keep this draft.')
            page.locator('#ventureSend').click()
            expect(page.locator('#ventureSend')).to_be_enabled()
            assert page.locator('#ventureNotice').inner_text() == 'Unable to prepare the board fixture.'
            assert page.locator('#ventureActivity').inner_text() == 'Review the message above.'
            assert page.locator('#ventureCheck').inner_text() == 'Check status'
            assert page.locator('#ventureMessage').input_value() == 'Keep this draft.'
            failure = page.evaluate('boardFailureResult()')
            assert failure == {'posts': 1, 'files': ['keep.txt'], 'pending': False, 'unchanged': True}, failure
            assert not errors, errors
            page.close()
            print(f'{width}px: mixed board ZIP submitted once; prompt and bytes intact; failed preparation preserves the draft.')
    finally:
        browser.close()
