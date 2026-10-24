const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');

const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
function bridgeFixture(kind='vision'){
 const messages=[],observers=[];let installs=0,unsubscribes=0;
 const auth={currentUser:null,authStateReady:async()=>{}};
 const sdk={onIdTokenChanged:(_auth,next)=>{installs++;const item={next,closed:false};observers.push(item);return ()=>{item.closed=true;unsubscribes++;};},
  GoogleAuthProvider:{credential:(_id,accessToken)=>({accessToken})},signInWithCredential:async()=>({user:auth.currentUser}),signOut:async()=>{},
  indexedDBLocalPersistence:{},browserLocalPersistence:{},initializeAuth:()=>auth,useDeviceLanguage(){},onAuthStateChanged(){}};
 const context={window:{VisionNative:{isNative:true},webkit:{messageHandlers:{vision:{postMessage:body=>{messages.push(body);return Promise.resolve(true);}}}}},
  Date,Promise,Error,Map,Set,URL,AbortController,setTimeout,clearTimeout,workspaceSwitch(){}};
 if(kind==='vision'){
  vm.createContext(context);vm.runInContext(fs.readFileSync('web/ios-native.js','utf8'),context);
 }else{
  context.window.VisionFirebaseSDK={app:{getApps:()=>[],initializeApp:()=>({})},auth:sdk};
  vm.createContext(context);vm.runInContext(fs.readFileSync('web/vortex/auth.js','utf8').replace(/\bexport /g,''),context);
 }
 return {context,auth,sdk,messages,observers,get installs(){return installs;},get unsubscribes(){return unsubscribes;},
  install:()=>kind==='vision'?context.accountInstallNativeBridge(auth,sdk):context.initAuth(()=>{}),
  emit(user){auth.currentUser=user;return Promise.all(observers.filter(x=>!x.closed).map(x=>x.next(user)));}};
}
function user(uid,promise){return {uid,email:uid+'@example.test',refreshToken:'refresh-'+uid,getIdTokenResult:()=>promise};}
const token=uid=>({token:'id-'+uid,expirationTime:new Date(Date.now()+3600000).toISOString()});

for(const kind of ['vision','vortex']){
 test(kind+': old account token cannot repopulate native Keychain after switch or sign-out',async()=>{
  const f=bridgeFixture(kind);await f.install();
  const old=deferred(),next=deferred(),first=f.emit(user('alice',old.promise));
  const second=f.emit(user('bob',next.promise));next.resolve(token('bob'));await second;
  old.resolve(token('alice'));await first;
  const signed=f.messages.filter(x=>x.type==='session'&&x.signedIn);
  assert.equal(signed.length,1);assert.equal(signed[0].uid,'bob');assert.equal(signed[0].refreshToken,'refresh-bob');
  const stale=deferred(),late=f.emit(user('bob',stale.promise));await f.emit(null);
  stale.resolve(token('bob'));await late;
  assert.equal(f.messages.at(-1).signedIn,false);
  assert.equal(f.messages.filter(x=>x.type==='session'&&x.signedIn).length,1);
 });
 test(kind+': a stale rejected refresh cannot show a session error for the next account',async()=>{
  const f=bridgeFixture(kind);await f.install();
  const stale=deferred(),first=f.emit(user('alice',stale.promise));
  await f.emit(user('bob',Promise.resolve(token('bob'))));
  stale.reject(new Error('old credential request failed'));await first;
  assert.equal(f.messages.filter(x=>x.type==='sessionError').length,0);
  assert.equal(f.messages.filter(x=>x.type==='session').at(-1).uid,'bob');
 });
 test(kind+': repeated bridge installation does not duplicate credential observers',async()=>{
  const f=bridgeFixture(kind);await f.install();await f.install();
  assert.equal(f.installs,1);assert.equal(f.messages.filter(x=>x.type==='ready').length,1);
  await f.emit(user('alice',Promise.resolve(token('alice'))));
  assert.equal(f.messages.filter(x=>x.type==='session').length,1);
 });
}

test('replacing a Vision auth instance cancels its outstanding credential messages',async()=>{
 const f=bridgeFixture();await f.install();const late=deferred();const pending=f.emit(user('alice',late.promise));
 const next={currentUser:null};f.context.accountInstallNativeBridge(next,f.sdk);
 late.resolve(token('alice'));await pending;
 assert.equal(f.unsubscribes,1);assert.equal(f.messages.filter(x=>x.type==='session').length,0);
});

test('ordinary browsers never install or emit the native credential bridge',()=>{
 const f=bridgeFixture();delete f.context.window.VisionNative;f.install();assert.equal(f.installs,0);assert.equal(f.messages.length,0);
});

test('the shipped Firebase bundle exposes every native bridge API',()=>{
 const context={console,Promise,Map,Set,WeakMap,WeakSet,URL,URLSearchParams,Headers,Request,Response,fetch,setTimeout,clearTimeout,setInterval,clearInterval,
  navigator:{userAgent:'Vision test'},location:{protocol:'https:',href:'https://jrdn-r.github.io/vision/'}};
 context.window=context;context.self=context;context.globalThis=context;
 vm.createContext(context);vm.runInContext(fs.readFileSync('web/vendor/firebase.js','utf8'),context,{timeout:10000});
 for(const name of ['onIdTokenChanged','signInWithCredential','initializeAuth','signOut','GoogleAuthProvider'])assert.equal(typeof context.VisionFirebaseSDK.auth[name],'function',name);
 assert.ok(context.VisionFirebaseSDK.auth.inMemoryPersistence);
});

test('browser download keeps its expiring-ticket refresh guard after preparing a ticket',async()=>{
 const source=fs.readFileSync('web/vortex/app.js','utf8');
 const prepare=source.slice(source.indexOf('function actionCurrent('),source.indexOf('async function openActions('));
 const handleStart=source.indexOf('function handleSaveFile(');
 const handle=source.slice(handleStart,source.indexOf("$('saveFile').onclick = handleSaveFile;",handleStart));
 const elements={saveFile:{},actionStatus:{},actionDialog:{open:true}},job={id:'a'.repeat(24),filename:'media.mp4'};let requests=0;
 const context={window:{},state:{actionId:job.id,actionEpoch:1,ticket:null},$:(id)=>elements[id],user:()=>({uid:'alice'}),getJob:()=>job,
  request:async()=>{requests++;return {url:'https://server.example/media',expiresAt:Date.now()/1000+300};},
  jobPath:id=>'/vortex/jobs/'+id,validateTicketURL:(_job,ticket)=>new URL(ticket.url),expiryLabel:()=>'',seconds:value=>value*1000,Date,URL,Promise};
 vm.createContext(context);vm.runInContext(prepare+'\n'+handle,context);
 await context.prepareTicket(job,1);
 assert.equal(elements.saveFile.onclick,context.handleSaveFile);
 context.state.ticket.expiresAt=Date.now()/1000-1;
 let prevented=false;elements.saveFile.onclick({preventDefault(){prevented=true;}});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(prevented,true);assert.equal(requests,2);
 assert.equal(elements.saveFile.onclick,context.handleSaveFile);
 assert.match(elements.actionStatus.textContent,/Download refreshed/);
});
