/* Full app authentication gate and account isolation. All network traffic is mocked. */
const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require('playwright');
const appModule='export function initializeApp(config,name){return {config,name}}';
const authModule=`const listeners=[];const auth={currentUser:null,authStateReady:async()=>{}};window.__fakeFirebase={auth,nextUID:'user-a'};export function getAuth(){return auth}export function useDeviceLanguage(){}export const browserLocalPersistence={};export async function setPersistence(){}export class GoogleAuthProvider{setCustomParameters(){}}export function onAuthStateChanged(a,fn){listeners.push(fn);fn(a.currentUser);return()=>{}}function user(uid,email=uid+'@example.com',providerId='google.com'){return{uid,email,providerData:[{providerId}],getIdToken:async()=>uid}}export async function signInWithPopup(){const uid=window.__fakeFirebase.nextUID;auth.currentUser=user(uid);for(const f of listeners)f(auth.currentUser);return{user:auth.currentUser}}export async function signInWithEmailAndPassword(a,email){const uid=window.__fakeFirebase.nextUID;auth.currentUser=user(uid,email,'password');for(const f of listeners)f(auth.currentUser);return{user:auth.currentUser}}export async function createUserWithEmailAndPassword(a,email){return signInWithEmailAndPassword(a,email)}export async function sendPasswordResetEmail(){}export async function signOut(){auth.currentUser=null;for(const f of listeners)f(null)}`;
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME||'/root/.cache/ms-playwright/chromium-1161/chrome-linux/chrome',args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true});const errors=[],keys=new Map(),projects=new Map();
  let html=fs.readFileSync('Vision.html','utf8');const end=html.lastIndexOf('})();');html=html.slice(0,end)+`window.__gateTest={state:()=>state,ready:()=>accountReady,signedIn:()=>accountSignedIn(),signOut:()=>accountGoogleSignOut(),clearGate:()=>accountUpdateGate()};\n`+html.slice(end);
  const json=(route,data,status=200)=>route.fulfill({status,contentType:'application/json',headers:{'Access-Control-Allow-Origin':'*'},body:JSON.stringify(data)});
  await context.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(request.url()==='https://vision.test/')return route.fulfill({contentType:'text/html',body:fs.readFileSync('index.html','utf8')});
   if(url.origin==='https://vision.test'&&url.pathname==='/Vision.html')return route.fulfill({contentType:'text/html',body:html});
   if(url.pathname.endsWith('/firebase-app.js'))return route.fulfill({contentType:'text/javascript',headers:{'Access-Control-Allow-Origin':'*'},body:appModule});
   if(url.pathname.endsWith('/firebase-auth.js'))return route.fulfill({contentType:'text/javascript',headers:{'Access-Control-Allow-Origin':'*'},body:authModule});
   if(url.pathname.startsWith('/api/')){
    const uid=(request.headers().authorization||'').replace('Bearer ','');
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers:{'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'authorization,content-type,x-vision-project-key','Access-Control-Allow-Methods':'GET,PUT,POST,DELETE'}});
    if(url.pathname==='/api/health')return json(route,{capabilities:{persistentProjects:true,projectRevision:true,accountProjects:true}});
    if(url.pathname==='/api/account/openai-key'){if(request.method()==='PUT')keys.set(uid,request.postDataJSON().apiKey);if(request.method()==='DELETE')keys.delete(uid);return json(route,{saved:keys.has(uid),apiKey:keys.get(uid)||''});}
    if(url.pathname==='/api/projects')return json(route,{projects:[]});
    const match=url.pathname.match(/^\/api\/projects\/([^/]+)$/);if(match){const old=projects.get(uid+match[1]);if(request.method()==='PUT'){const body=request.postDataJSON();projects.set(uid+match[1],{project:body.project,revision:(old?.revision||0)+1});}return json(route,projects.get(uid+match[1])||{error:'Missing'},projects.has(uid+match[1])?200:404);}
    return json(route,{items:[],runs:[]});
   }
   return route.abort();
  });
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());await page.goto('https://vision.test/',{waitUntil:'domcontentloaded'});await page.waitForFunction(()=>window.__fakeFirebase);await page.evaluate(()=>window.__gateTest.ready());
  assert.equal(await page.locator('#accountGate').isVisible(),true);assert.equal(await page.locator('#addNodeButton').isVisible(),false);assert.equal(await page.locator('#appHeader').evaluate(e=>e.inert),true);
  await page.evaluate(()=>{const data=new DataTransfer();data.setData('text/plain','Must not import');document.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));document.getElementById('board').dispatchEvent(new DragEvent('drop',{dataTransfer:data,bubbles:true,cancelable:true}));window.dispatchEvent(new KeyboardEvent('keydown',{key:'s',ctrlKey:true,bubbles:true,cancelable:true}));document.getElementById('addNodeButton').click();});
  assert.equal(await page.evaluate(()=>window.__gateTest.state().nodes.length),0,'signed-out events must not create a module');
  await page.locator('#accountGateSignIn').focus();await page.keyboard.press('Enter');await page.waitForFunction(()=>window.__gateTest.signedIn());await page.locator('#closeProjects').click();assert.equal(await page.locator('#accountGate').isVisible(),false);
  await page.locator('#addNodeButton').click();await page.waitForFunction(()=>window.__gateTest.state().nodes.length===1);assert.equal(await page.locator('#world .node').count(),1);
  await page.evaluate(()=>{const input=document.getElementById('consoleKey');input.value='user-a-secret';input.dispatchEvent(new Event('input',{bubbles:true}));});await page.waitForFunction(()=>document.getElementById('accountKeyStatus').textContent==='Saved to your account on FUPCJ Server');
  assert.equal(keys.get('user-a'),'user-a-secret');await page.evaluate(()=>window.__gateTest.signOut());assert.equal(await page.locator('#accountGate').isVisible(),true);assert.equal(await page.locator('#consoleKey').inputValue(),'');
  await page.evaluate(()=>window.__fakeFirebase.nextUID='user-b');await page.locator('#accountGateEmail').fill('user-b@example.com');await page.locator('#accountGatePassword').fill('password123');await page.locator('#accountGateEmailSignIn').click();await page.waitForFunction(()=>window.__gateTest.signedIn());await page.locator('#closeProjects').click();assert.equal(await page.evaluate(()=>window.__gateTest.state().nodes.length),0);assert.equal(await page.locator('#consoleKey').inputValue(),'');
  await page.evaluate(()=>window.__gateTest.signOut());await page.evaluate(()=>window.__fakeFirebase.nextUID='user-a');await page.locator('#accountGateSignIn').click();await page.waitForFunction(()=>document.getElementById('consoleKey').value==='user-a-secret');assert.equal(await page.evaluate(()=>window.__gateTest.state().nodes.length),1);
  assert.deepEqual(errors,[]);await context.close();console.log('Full-app mobile gate, signed-out paste/drop/shortcut block, Google and email/password login, signout relock, two-account board/key isolation and restore passed.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
