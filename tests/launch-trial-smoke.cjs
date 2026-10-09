/* Full launch/trial flow; mocked Firebase, server clock, jobs and billing. No real requests. */
const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require('playwright');
const appModule='export function initializeApp(config,name){return {config,name}}';
const authModule=`const listeners=[];const auth={currentUser:null,authStateReady:async()=>{}};export function getAuth(){return auth}export const initializeAuth=getAuth;export const browserPopupRedirectResolver={};export function useDeviceLanguage(){}export const browserLocalPersistence={};export async function setPersistence(){}export class GoogleAuthProvider{setCustomParameters(){}}export function onAuthStateChanged(a,fn){listeners.push(fn);fn(a.currentUser);return()=>{}}export async function signInWithPopup(){auth.currentUser={uid:'google-user',email:'user@example.com',getIdToken:async()=>'google-token'};for(const f of listeners)f(auth.currentUser);return{user:auth.currentUser}}export async function signOut(){auth.currentUser=null;for(const f of listeners)f(null)}`;
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROME?{executablePath:process.env.CHROME}:{}),args:['--no-sandbox']});
 try{
  let html=fs.readFileSync('Vision.html','utf8');const end=html.lastIndexOf('})();');
  html=html.slice(0,end)+`window.__trialTest={state:()=>state,ready:()=>accountReady,active:()=>trialActive(),signed:()=>accountSignedIn(),expire:()=>{trialSession.until=performance.now()-1;return trialFinish()},session:()=>trialSession,cloud:(p,o)=>cloudFetch(p,o),remote:()=>ensureRemoteProject(),dirty:()=>markDirty(),backup:()=>projectBackup(),signOut:()=>accountGoogleSignOut(),collectDocuments:()=>collectDocumentSources()};\n`+html.slice(end);
  const errors=[],requests=[],receipts=new Map();let serverOffset=0;
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true});
  const json=(route,data,status=200)=>route.fulfill({status,contentType:'application/json',headers:{'Access-Control-Allow-Origin':'*'},body:JSON.stringify(data)});
  await context.route('**/*',async route=>{
   const req=route.request(),u=new URL(req.url()),auth=req.headers().authorization||'';requests.push({path:u.pathname,method:req.method(),auth,body:req.postData()});
   if(u.origin==='https://vision.test'&&u.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync('index.html','utf8')});
   if(u.origin==='https://vision.test'&&u.pathname==='/Vision.html')return route.fulfill({contentType:'text/html',body:html});
   if(u.pathname.endsWith('/firebase-app.js'))return route.fulfill({contentType:'text/javascript',headers:{'Access-Control-Allow-Origin':'*'},body:appModule});
   if(u.pathname.endsWith('/firebase-auth.js'))return route.fulfill({contentType:'text/javascript',headers:{'Access-Control-Allow-Origin':'*'},body:authModule});
   if(req.method()==='OPTIONS')return route.fulfill({status:204,headers:{'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'authorization,content-type,x-vision-project-key','Access-Control-Allow-Methods':'GET,PUT,POST,DELETE'}});
   if(u.pathname==='/api/trial/start'){
    const id=req.postDataJSON().deviceId;let r=receipts.get(id);const now=Date.now()/1000+serverOffset;
    if(r&&(r.closed||r.expiresAt<=now))return json(route,{code:'trial-used',error:'Your trial has ended.'},403);
    if(!r){r={id:'a'.repeat(24),token:'trial_'+'a'.repeat(24)+'_'+'b'.repeat(64),expiresAt:now+300,project:{id:'trial-'+'a'.repeat(24),key:'c'.repeat(64),backendUrl:'https://desktop-vjt2br2.tail385c9d.ts.net',revision:0}};receipts.set(id,r);}
    return json(route,{...r,serverNow:now},201);
   }
   if(u.pathname==='/api/trial/end'){for(const r of receipts.values())if(auth==='Bearer '+r.token)r.closed=true;return json(route,{ended:true});}
   if(u.pathname==='/api/health')return json(route,{capabilities:{persistentProjects:true,projectRevision:true,accountProjects:true,temporarySessions:true,localTranscription:true,soundEvents:true,uploadedMedia:true,documentProcessing:true},documentProcessing:{ready:true}});
   if(u.pathname==='/api/account/openai-key')return json(route,{saved:false,apiKey:''});
   if(u.pathname==='/api/projects')return json(route,{projects:[]});
   if(u.pathname.includes('/documents/request/'))return json(route,{error:'No receipt yet'},404);
   if(u.pathname.endsWith('/documents')&&req.method()==='POST')return json(route,{id:'d'.repeat(24),status:'queued',sourceSha256:'e'.repeat(64)},202);
   if(u.pathname.includes('/documents/'))return json(route,{id:'d'.repeat(24),status:'processing',phase:'Processing document'});
   if(u.pathname.endsWith('/transcriptions'))return json(route,{id:'f'.repeat(24),status:'queued'},202);
   if(u.pathname.startsWith('/api/projects/'))return json(route,{runs:[],items:[],revision:1});
   return route.abort();
  });
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  await page.goto('https://vision.test/');await page.waitForFunction(()=>window.__trialTest);await page.evaluate(()=>window.__trialTest.ready());
  await page.waitForTimeout(100);
  assert.equal(requests.filter(r=>r.path==='/api/trial/start').length,0,'visiting and reading Why must not consume a trial');
  const logo=page.locator('.account-gate-logo');assert.equal(await logo.evaluate(e=>e.complete&&e.naturalWidth===2048),true);
  assert.ok((await logo.boundingBox()).width<=110);
  await page.locator('#whyVisionButton').click();assert.equal(await page.locator('#whyVisionDialog').isVisible(),true);
  await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.getElementById('whyVisionDialog').contains(document.activeElement)),true);
  fs.mkdirSync('test-results',{recursive:true});await page.screenshot({path:'test-results/why-vision-mobile.png'});
  await page.keyboard.press('Escape');assert.equal(await page.locator('#whyVisionDialog').isVisible(),false);
  await page.screenshot({path:'test-results/launch-mobile.png'});
  assert.equal(await page.locator('#addNodeButton').isVisible(),false);
  await page.locator('#accountGateTrial').click();await page.waitForFunction(()=>window.__trialTest.active());
  assert.equal(await page.locator('#accountGate').isVisible(),false);assert.equal(await page.locator('#trialBanner').isVisible(),true);
  await page.locator('#addNodeButton').click();await page.waitForFunction(()=>window.__trialTest.state().nodes.length===1);assert.equal(await page.evaluate(()=>window.__trialTest.state().nodes.length),1);
  await page.evaluate(()=>{window.__trialTest.state().title='GUEST CONTENT MUST NOT PERSIST';window.__trialTest.dirty();document.getElementById('consoleKey').value='guest-api-key';});
  await page.evaluate(()=>window.__trialTest.backup());
  const meta=await page.evaluate(()=>window.__trialTest.remote());assert.equal(meta.id,'trial-'+'a'.repeat(24));
  await page.evaluate(async()=>{const meta=await window.__trialTest.remote();const r=await window.__trialTest.cloud('/projects/'+meta.id+'/transcriptions',{method:'POST',headers:{'X-Vision-Project-Key':meta.key,'Content-Type':'application/json'},body:'{}'});if(r.status!==202)throw new Error('guest transcription unavailable');});
  await page.waitForTimeout(1700);
  assert.equal(requests.filter(r=>r.auth.startsWith('Bearer trial_')&&r.method==='PUT').length,0);
  const storage=await page.evaluate(()=>JSON.stringify({...localStorage})+JSON.stringify({...sessionStorage}));
  assert.ok(!storage.includes('GUEST CONTENT MUST NOT PERSIST'));assert.ok(!storage.includes('guest-api-key'));
  await page.evaluate(()=>{
   const n=window.__trialTest.state().nodes[0];n.attachments.push({id:'trial-doc-file',name:'trial.txt',mime:'text/plain',data:'data:text/plain;base64,VGVtcG9yYXJ5',size:9,generated:false});
   window.__trialTest.collectDocuments();
   if(!n.attachments.find(a=>a.id==='trial-doc-file').documentJob)throw new Error('Guest document was not scheduled');
  });
  await page.waitForFunction(()=>window.__trialTest.state().nodes[0].attachments.find(a=>a.id==='trial-doc-file').documentJob.id==='d'.repeat(24));
  assert.ok(requests.some(r=>r.path.endsWith('/documents')&&r.method==='POST'&&r.auth.startsWith('Bearer trial_')));
  const expiry=await page.evaluate(()=>window.__trialTest.session().expiresAt);
  serverOffset=45;await page.reload();await page.waitForFunction(()=>window.__trialTest?.active());
  assert.equal(await page.evaluate(()=>window.__trialTest.session().expiresAt),expiry);
  assert.equal(await page.evaluate(()=>window.__trialTest.state().nodes.length),0,'refresh cannot restore guest projects');
  assert.ok((await page.locator('#trialCountdown').textContent()).startsWith('4:'));
  await page.locator('#addNodeButton').click();await page.waitForFunction(()=>window.__trialTest.state().nodes.length===1);await page.evaluate(()=>window.__trialTest.expire());
  assert.equal(await page.locator('#accountGate').isVisible(),true);assert.equal(await page.evaluate(()=>window.__trialTest.state().nodes.length),0);
  assert.equal(await page.locator('#accountGateTrial').isDisabled(),true);
  await page.reload();await page.waitForFunction(()=>window.__trialTest);await page.evaluate(()=>window.__trialTest.ready());
  assert.equal(await page.locator('#accountGateTrial').isDisabled(),true);
  await page.locator('#accountGateSignIn').click();await page.waitForFunction(()=>window.__trialTest.signed());
  assert.equal(await page.locator('#accountGate').isVisible(),false);assert.equal(await page.locator('#trialBanner').isVisible(),false);
  assert.equal(await page.evaluate(()=>window.__trialTest.state().nodes.length),0);assert.equal(await page.locator('#consoleKey').inputValue(),'');
  // A Google sign-in during an active trial must discard temporary content too.
  await page.evaluate(async()=>{await window.__trialTest.signOut();localStorage.removeItem('vision-guest-receipt-v1');});
  await page.reload();await page.waitForFunction(()=>window.__trialTest);await page.evaluate(()=>window.__trialTest.ready());
  await page.locator('#accountGateTrial').click();await page.waitForFunction(()=>window.__trialTest.active());
  await page.locator('#addNodeButton').click();await page.waitForFunction(()=>window.__trialTest.state().nodes.length===1);
  await page.evaluate(()=>{document.getElementById('consoleKey').value='temporary-key';});
  await page.locator('#trialSignIn').click();await page.waitForFunction(()=>window.__trialTest.signed());
  assert.equal(await page.evaluate(()=>window.__trialTest.state().nodes.length),0);
  assert.equal(await page.locator('#consoleKey').inputValue(),'');
  assert.equal(await page.locator('#trialBanner').isVisible(),false);
  assert.deepEqual(errors,[]);await context.close();
  console.log('PASS: small supplied logo; accessible Why modal; no auto-consumption; trial processing and document extraction without saved project; no guest persistence; refresh retains deadline; expiry wipes and locks; Google sign-in unaffected.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
