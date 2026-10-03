/* Real mobile browser; document API mocked, no external/provider requests. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..'),url='https://vision-documents.test/';
async function main(){
 const browser=await chromium.launch({headless:true,args:['--disable-gpu'],...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
 const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
 const errors=[];
 try{
  await context.route('**/*',route=>{
   if(route.request().url()!==url)return route.abort();
   let html=fs.readFileSync(path.join(root,'Vision.html'),'utf8');
   const hook=`
   let testAccount='alice',testComplete=false,testPosts=0,testReceipts=new Map(),testHold=null;
   const testMeta={id:'project_document_test_1',key:'k'.repeat(48),backendUrl:'https://processor.test.ts.net',ownerUid:'alice',revision:1};
   projectAccountUID=()=>testAccount;ensureProjectIdentity=()=>testMeta;
   cloudConfig={kind:'private-pc',backendUrl:testMeta.backendUrl};
   projectCapabilities=async()=>({documentProcessing:{ready:true}});
   ensureRemoteProject=async()=>testMeta;projectBackup=async()=>{};
   markDirty=()=>{dirty=true;};
   projectRequest=async(path,options={})=>{
    if(testHold&&path.endsWith('/result'))await testHold.promise;
    const response=data=>new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
    if(path.includes('/request/')){const receipt=testReceipts.get(path.split('/request/')[1]);return receipt?response(receipt):new Response(JSON.stringify({error:'Not yet accepted'}),{status:404});}
    if(options.method==='POST'){testPosts++;const id='a'.repeat(24),requestId=options.body.get('requestId');const row={id,requestId,status:'queued',sourceSha256:'b'.repeat(64)};testReceipts.set(requestId,row);return response(row);}
    if(path.endsWith('/result'))return response({schema:'vision-document-v1',sourceSha256:'b'.repeat(64),artifacts:[{name:'001-text.md',mime:'text/markdown',text:'Prepared evidence 42',location:'evidence.txt / document'}],warnings:[],tools:['Python'],limits:{},note:'Extracted evidence'});
    return response({id:'a'.repeat(24),status:testComplete?'complete':'queued',phase:'Waiting for the document worker'});
   };
   window.__docs={
    setup:async()=>{testAccount='alice';const src=await nodePoster();state.nodes=[{id:'N',kind:'node',title:'Evidence',prompt:'Summarize',caption:'',src,width:480,height:300,x:0,y:0,annotations:[],attachments:[{id:'S',name:'evidence.txt',mime:'text/plain',data:dataURLFromBytes(new TextEncoder().encode('Original evidence 42'),'text/plain'),size:20,generated:false}]}];state.edges=[];renderAll();collectDocumentSources();},
    state:()=>state,posts:()=>testPosts,complete:()=>{testComplete=true;state.nodes[0].attachments[0].documentJob.nextAttempt=0;scheduleDocuments(0);},
    saved:()=>snapshot(),load:async raw=>{state=await validateProject({format:'Vision',version:4,...raw});projectEpoch++;renderAll();},
    exports:async()=>{const f=await buildExportFiles(orderedNodes(),false);return f.map(v=>({name:v.name,text:typeof v.data==='string'?v.data:null}));},
    hold:()=>{let resolve;const promise=new Promise(r=>resolve=r);testHold={promise,resolve};testComplete=true;state.nodes[0].attachments[0].documentJob.nextAttempt=0;scheduleDocuments(0);},
    switch:()=>{testAccount='bob';state={...state,nodes:[],edges:[]};projectEpoch++;testHold?.resolve();renderAll();}
   };
   `;
   const end=html.lastIndexOf('renderAll();');html=html.slice(0,end)+hook+html.slice(end);
   return route.fulfill({contentType:'text/html',body:html});
  });
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  await page.goto(url,{waitUntil:'domcontentloaded'});await page.waitForFunction(()=>!!window.__docs);
  await page.evaluate(()=>window.__docs.setup());
  await page.waitForFunction(()=>window.__docs.state().nodes[0]?.attachments[0]?.documentJob?.id);
  assert.equal(await page.evaluate(()=>window.__docs.posts()),1);
  assert.ok(await page.locator('.document-status').count());
  const saved=await page.evaluate(()=>window.__docs.saved());
  saved.nodes[0].attachments[0].documentJob.id='';
  await page.evaluate(raw=>window.__docs.load(raw),saved);
  await page.evaluate(()=>window.__docs.complete());
  await page.waitForFunction(()=>window.__docs.state().nodes[0]?.attachments[0]?.documentJob?.status==='complete');
  assert.equal(await page.evaluate(()=>window.__docs.posts()),1,'accepted receipt recovered without reupload');
  const attachments=await page.evaluate(()=>window.__docs.state().nodes[0].attachments);
  assert.equal(attachments.length,3,'original, extracted text and source manifest');
  assert.equal(attachments.filter(a=>a.documentOf==='S').length,2);
  const completed=await page.evaluate(()=>window.__docs.saved());await page.evaluate(raw=>window.__docs.load(raw),completed);
  assert.equal(await page.evaluate(()=>window.__docs.state().nodes[0].attachments.filter(a=>a.documentOf==='S').length),2,'source linkage survives project reopen');
  const files=await page.evaluate(()=>window.__docs.exports());
  assert.ok(files.some(f=>f.name==='PREPARATION.json'));
  assert.match(files.find(f=>f.name==='MAIN_PROMPT.txt').text,/PREPARED DOCUMENTS/);
  assert.ok(files.some(f=>f.name.includes('extraction-notes.json')));
  assert.ok(files.some(f=>f.name.endsWith('/evidence.txt')),'original is preserved in the AI package');
  assert.ok(await page.locator('#documentStorageButton').count(),'project cache controls installed');
  await page.evaluate(()=>window.__docs.setup());await page.evaluate(()=>window.__docs.hold());
  await page.waitForTimeout(1200);await page.evaluate(()=>window.__docs.switch());await page.waitForTimeout(500);
  assert.equal(await page.evaluate(()=>window.__docs.state().nodes.length),0,'late results do not cross accounts/projects');
  assert.deepEqual(errors,[]);
  await page.screenshot({path:path.join(root,'../documents-mobile-test.png'),fullPage:false});
  console.log('PASS: mobile UI, upload/receipt recovery, saved extraction/source links, AI export, cache controls, stale account result guard.');
 }finally{await context.close();await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
