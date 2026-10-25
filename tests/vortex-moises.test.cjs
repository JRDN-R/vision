// Actual Vortex modules with deterministic DOM/auth fixtures. Native sharing
// and shortcut launching are mocked; installed Moises/iOS still needs a device.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {randomUUID} = require('node:crypto');
const ROOT = path.resolve(__dirname, '..');
const BACKEND = 'https://desktop-vjt2br2.tail385c9d.ts.net';
const LIMIT = 32 * 1024 * 1024;
const tick = () => new Promise(resolve => setImmediate(resolve));

function element() {
  const attributes = new Map(), classes = new Set();
  return {hidden:false, disabled:false, textContent:'', value:'', dataset:{}, children:[], style:{},
    classList:{toggle(name,on) { if (on) classes.add(name); else classes.delete(name); }, add:name=>classes.add(name), remove:name=>classes.delete(name)},
    setAttribute:(k,v)=>attributes.set(k,v), getAttribute:k=>attributes.get(k) ?? null, removeAttribute:k=>attributes.delete(k),
    addEventListener() {}, remove() {}, click() {}, showModal() { this.open = true; }, close() { this.open = false; },
    replaceChildren(...children) { this.children = children; }, append(...children) { this.children.push(...children); }};
}
function harness({ios=false, configured=false, storage=new Map()}={}) {
  let identity = {uid:'alice'}, epoch = 1, serial = 0, handler = async () => new Blob(['media'], {type:'application/octet-stream'});
  let ticketHandler = async route => ({url:BACKEND+'/api'+route.replace('/ticket','/file')+'?ticket=fixture',expiresAt:Date.now()/1000+300});
  const elements = new Map(), calls = [], shared = [], saved = [], launched = [];
  if (configured) storage.set('vortex:moises-shortcut:v1','ready');
  const node = id => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); };
  const document = {hidden:false, getElementById:node, querySelector:()=>null, querySelectorAll:()=>[], addEventListener() {}, body:element(), createElement:element};
  const sandbox = {URL, Blob, File, AbortController, crypto:{randomUUID}, document,
    navigator:{userAgent:ios?'iPhone':'',canShare:()=>true, share:async data=>shared.push(data)}, matchMedia:()=>({matches:true,addEventListener(){}}),
    setTimeout:()=>1, clearTimeout() {}, request:async (route, options={}) => {
      calls.push({route, options});
      if (options.binary) return handler(route, options);
      if (route === '/vortex/capabilities') return {videoFormats:['mp4','mov'], audioFormats:['m4a','mp3','wav']};
      if (route === '/vortex/jobs' && options.method === 'POST') return {id:'queued-'+(++serial), status:'queued', ...options.body};
      if (route.endsWith('/ticket')) return ticketHandler(route, options);
      throw new Error('Unexpected request: '+route);
    }, user:()=>identity, accountEpoch:()=>epoch, initAuth:()=>new Promise(()=>{}), authError:error=>error.message,
    signOut:async()=>{}, BACKEND, fetch:async()=>({ok:false}), sessionStorage:{getItem:()=>null,setItem(){}},
    localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},console};
  sandbox.window = {addEventListener() {},location:{assign:url=>launched.push(url)}};
  const context = vm.createContext(sandbox);
  // Auth is supplied by this fixture; load the real side-effect dependency
  // before executing the module body in Node's script-only VM.
  vm.runInContext(fs.readFileSync(path.join(ROOT,'web/mobile-menu-swipe.js'),'utf8'), context);
  const source = fs.readFileSync(path.join(ROOT,'web/vortex/app.js'),'utf8').replace(/^import .*;\r?\n/gm, '');
  vm.runInContext(source, context);
  vm.runInContext('triggerBrowserSave = (url, filename) => fixtureSaved.push({url, filename});', Object.assign(context,{fixtureSaved:saved}));
  const evaluate = source => vm.runInContext(source, context);
  function ready(ext='mp4', fields={}) {
    const media = {mediaType:['mp4','mov'].includes(ext)?'video':'audio',url:'https://example.test/media',title:'Fixture'};
    const job = {id:'ready-1',kind:'download',status:'complete',resultReady:true,size:5,filename:'fixture.'+ext,expiresAt:Date.now()/1000+86400,...fields};
    sandbox.fixtureMedia = media; sandbox.fixtureJob = job;
    evaluate("state.selected=fixtureMedia; state.jobs=[fixtureJob]; state.downloadMode=fixtureMedia.mediaType; state.videoFormat='mp4'; state.audioFormat='m4a'; state.readyDownload={key:currentDownloadKey(),url:BACKEND+'/api/vortex/jobs/'+fixtureJob.id+'/file?ticket=fixture',filename:fixtureJob.filename,expiresAt:Date.now()/1000+300}; renderDownloadStatus();");
    return job;
  }
  return {node,calls,shared,saved,launched,storage,sandbox,evaluate,ready,
    click:()=>evaluate('openInMoises()'),
    setBinary:fn=>{handler=fn;}, setTicket:fn=>{ticketHandler=fn;},
    switchAccount:uid=>{identity=uid?{uid}:null;epoch++;evaluate('renderDownloadStatus()');},
    binaryCalls:()=>calls.filter(call=>call.options.binary)};
}

test('each Vortex audio/video format hands off a File with an explicit MIME type on a fresh tap', async () => {
  for (const [ext,type] of Object.entries({mp4:'video/mp4',mov:'video/quicktime',m4a:'audio/mp4',mp3:'audio/mpeg',wav:'audio/wav'})) {
    const h = harness(); h.ready(ext);
    await h.click();
    assert.equal(h.shared.length, 0);
    assert.equal(h.node('moisesOpenButton').disabled, false);
    assert.match(h.node('moisesStatus').textContent,/Ready/);
    await h.click();
    assert.equal(h.shared.length, 1);
    const data = h.shared[0];
    assert.deepEqual(Object.keys(data), ['files']);
    assert.equal(data.files[0].name, 'fixture.'+ext);
    assert.equal(data.files[0].type, type);
    assert.equal(data.files[0].size, 5);
    assert.equal(h.binaryCalls().length, 1);
    assert.equal(h.binaryCalls()[0].route, '/vortex/jobs/ready-1/file');
    assert.equal(h.binaryCalls()[0].options.timeout, 90000);
    assert.equal(h.evaluate('state.moisesPrepared'), null);
  }
});

test('iOS setup is required once, persists only the preference, and never starts an export or app itself', async () => {
  const h = harness({ios:true}); h.ready('mp3'); await h.click();
  assert.equal(h.node('moisesSetupDialog').open,true);
  assert.equal(h.node('moisesSetupButton').hidden,false);
  assert.equal(h.calls.length,0); assert.equal(h.launched.length,0);
  h.node('moisesSetupDone').onclick();
  assert.equal(h.node('moisesSetupDialog').open,false);
  assert.deepEqual([...h.storage],[['vortex:moises-shortcut:v1','ready']]);
  assert.equal(h.launched.length,0);
  const next = harness({ios:true,storage:h.storage}); next.ready('mp3'); await next.click();
  assert.equal(next.launched.length,1);
  assert.equal(next.node('moisesSetupDialog').open,undefined);
});

test('iPhone and desktop-mode iPad dispatch the scoped URL synchronously without sharing, fetching bytes, or a page size cap', async () => {
  for (const ipad of [false,true]) {
    const h = harness({ios:!ipad,configured:true});
    if (ipad) Object.assign(h.sandbox.navigator,{userAgent:'Macintosh',platform:'MacIntel',maxTouchPoints:5});
    h.ready('mp3',{size:LIMIT+1});
    h.sandbox.navigator.share = undefined;
    const pending = h.click();
    assert.equal(h.launched.length,1,'launch must happen before awaiting a network response');
    await pending;
    const url = new URL(h.launched[0]);
    assert.equal(url.protocol,'shortcuts:'); assert.equal(url.host,'run-shortcut');
    assert.deepEqual([...url.searchParams],[['name','Vortex to Moises'],['input','text'],['text',BACKEND+'/api/vortex/jobs/ready-1/file?ticket=fixture']]);
    assert.equal(h.calls.length,0); assert.equal(h.shared.length,0); assert.equal(h.saved.length,0);
  }
});

test('iOS reuses the export queue but never launches Shortcuts from background completion', async () => {
  const h = harness({ios:true,configured:true}); h.ready('mp3'); h.evaluate('state.readyDownload=null');
  await h.click(); await h.click(); await tick();
  assert.equal(h.calls.filter(call=>call.route==='/vortex/jobs').length,1);
  h.evaluate("Object.assign(state.jobs[0],{status:'complete',resultReady:true,filename:'fixture.mp3',size:5,expiresAt:Date.now()/1000+86400}); checkAutoSaves();");
  await tick();
  assert.equal(h.saved.length,0); assert.equal(h.launched.length,0); assert.equal(h.binaryCalls().length,0);
  assert.match(h.node('moisesStatus').textContent,/run your shortcut/);
  await h.click(); assert.equal(h.launched.length,1);
});

test('iOS refreshes expiring tickets once and waits for another tap before launching', async () => {
  const h = harness({ios:true,configured:true}); h.ready('mp3'); h.evaluate('state.readyDownload.expiresAt=Date.now()/1000+30');
  let release;
  h.setTicket(()=>new Promise(resolve=>release=resolve));
  const pending = h.click(); await h.click();
  assert.equal(h.calls.length,1); assert.equal(h.calls[0].route,'/vortex/jobs/ready-1/ticket');
  assert.equal(h.calls[0].options.method,'POST'); assert.equal(h.node('moisesOpenButton').disabled,true);
  release({url:BACKEND+'/api/vortex/jobs/ready-1/file?ticket=renewed_token.sig',expiresAt:Date.now()/1000+300});
  await pending;
  assert.equal(h.launched.length,0); assert.equal(h.node('moisesOpenButton').disabled,false);
  assert.match(h.node('moisesStatus').textContent,/Ready\. Tap/);
  await h.click(); assert.equal(h.launched.length,1);
  assert.equal(new URL(h.launched[0]).searchParams.get('text'),BACKEND+'/api/vortex/jobs/ready-1/file?ticket=renewed_token.sig');
  assert.equal(h.calls.length,1);
});

test('iOS rejects unsafe URLs, unavailable results, and failed refreshes without launching or stranding the button', async () => {
  const file = BACKEND+'/api/vortex/jobs/ready-1/file';
  for (const url of [file,file+'?ticket=',file+'?ticket=a&ticket=b',file+'?ticket=a&token=secret',file+'?ticket=a#fragment',
    file+'?ticket='+encodeURIComponent('a&b'),file.replace('https:','http:')+'?ticket=a',
    file.replace('https://','https://user:password@')+'?ticket=a',
    'https://example.test/api/vortex/jobs/ready-1/file?ticket=a',BACKEND+'/api/vortex/jobs/missing/file?ticket=a']) {
    const h = harness({ios:true,configured:true}); h.ready('mp3'); h.sandbox.fixtureURL=url;
    h.evaluate('state.readyDownload.url=fixtureURL'); await h.click();
    assert.equal(h.launched.length,0); assert.equal(h.calls.length,0);
    assert.equal(h.node('moisesOpenButton').disabled,false); assert.ok(h.node('moisesStatus').textContent);
  }
  for (const fields of [{size:0},{size:Infinity},{status:'expired'},{resultReady:false},{kind:'inspect'},{expiresAt:1},{filename:'file.zip'}]) {
    const h = harness({ios:true,configured:true}); h.ready('mp3',fields); await h.click();
    assert.equal(h.launched.length,0); assert.equal(h.calls.length,0);
  }
  for (const response of [new Error('offline'),{url:file+'?ticket=a',expiresAt:Date.now()/1000+30},
    {url:'https://example.test/file?ticket=a',expiresAt:Date.now()/1000+300}]) {
    const h = harness({ios:true,configured:true}); h.ready('mp3'); h.evaluate('state.readyDownload.expiresAt=1');
    h.setTicket(async()=>{if(response instanceof Error)throw response;return response;});
    await h.click(); assert.equal(h.launched.length,0); assert.equal(h.node('moisesOpenButton').disabled,false);
    assert.equal(h.node('moisesOpenButton').getAttribute('aria-busy'),null);
    assert.equal(h.evaluate('state.readyDownload.expiresAt'),1);
  }
});

test('iOS ticket refresh aborts across account, selection and result changes and ignores late responses', async () => {
  for (const change of [h=>h.switchAccount('bob'),h=>h.switchAccount('alice'),h=>h.switchAccount(null),
    h=>h.evaluate("discardQueuedDownload(); state.selected.url='https://example.test/other'; renderDownloadStatus(); state.selected.url='https://example.test/media'; renderDownloadStatus()"),
    h=>h.evaluate("state.jobs[0].resultReady=false; renderDownloadStatus()")]) {
    const h = harness({ios:true,configured:true}); h.ready('mp3'); h.evaluate('state.readyDownload.expiresAt=1');
    let release, signal;
    h.setTicket((_,options)=>{signal=options.signal;return new Promise(resolve=>release=resolve);});
    const pending=h.click(); change(h); assert.equal(signal.aborted,true);
    release({url:BACKEND+'/api/vortex/jobs/ready-1/file?ticket=stale',expiresAt:Date.now()/1000+300});
    await pending;
    assert.equal(h.launched.length,0); assert.equal(h.evaluate('state.moisesShortcutRequest'),null);
    assert.equal(h.node('moisesStatus').textContent,''); assert.equal(h.node('moisesOpenButton').disabled,false);
    assert.ok(!h.evaluate('state.readyDownload?.url.includes("stale")'));
  }
});

test('a blocked shortcut launch leaves setup help and manual saving available', async () => {
  const h = harness({ios:true,configured:true}); h.ready('mp3');
  h.sandbox.window.location.assign=()=>{throw new Error('blocked');};
  await h.click(); assert.match(h.node('moisesStatus').textContent,/Could not open Shortcuts/);
  assert.equal(h.node('moisesOpenButton').disabled,false);
  assert.equal(h.node('moisesSetupButton').hidden,false); assert.equal(h.node('downloadFallback').hidden,false);
});

test('Moises uses the existing queue once, skips automatic saves, and allows an explicit Download upgrade', async () => {
  for (const upgrade of [false,true]) {
    const h = harness(); h.ready(); h.evaluate('state.readyDownload=null');
    await h.click(); await h.click(); await tick();
    assert.equal(h.calls.filter(call=>call.route==='/vortex/jobs').length,1);
    assert.equal(h.binaryCalls().length,0);
    if (upgrade) h.evaluate('downloadSelected()');
    h.evaluate("Object.assign(state.jobs[0],{status:'complete',resultReady:true,filename:'fixture.mp4',size:5,expiresAt:Date.now()/1000+86400}); checkAutoSaves();");
    await tick();
    assert.equal(h.saved.length,upgrade?1:0);
    assert.equal(h.shared.length,0);
    assert.equal(h.calls.filter(call=>call.route.endsWith('/ticket')).length,1);
    h.evaluate('checkAutoSaves()'); await tick();
    assert.equal(h.saved.length,upgrade?1:0);
    await h.click(); await h.click();
    assert.equal(h.shared.length,1);
    h.evaluate('downloadSelected()');
    assert.equal(h.saved.length,upgrade?2:1);
  }
});

test('unsupported sharing and image/gallery selections do not retrieve files', async () => {
  const h = harness(); h.ready(); h.sandbox.navigator.share = undefined;
  await h.click(); assert.equal(h.binaryCalls().length,0); assert.match(h.node('moisesStatus').textContent,/Save to Files/);
  for (const kind of ['image','gallery']) {
    h.evaluate(`state.selected.mediaType='${kind}'; renderDownloadStatus()`);
    assert.equal(h.node('moisesOpenButton').hidden,true);
    await h.click(); assert.equal(h.binaryCalls().length,0);
  }
});

test('invalid size metadata and unavailable results are rejected before fetching', async () => {
  for (const fields of [{size:0},{size:-1},{size:LIMIT+1},{size:NaN},{size:Infinity},{status:'expired'},{resultReady:false},{kind:'inspect'},{expiresAt:1}]) {
    const h = harness(); h.ready('mp4',fields); await h.click();
    assert.equal(h.binaryCalls().length,0); assert.equal(h.shared.length,0);
  }
});

test('received bytes, formats, and browser file capability are checked', async () => {
  for (const blob of [{size:LIMIT+1},new Blob([])]) {
    const h = harness(); h.ready(); h.setBinary(async()=>blob); await h.click();
    assert.equal(h.shared.length,0); assert.equal(h.evaluate('state.moisesPrepared'),null);
    assert.equal(h.node('moisesOpenButton').disabled,false); assert.match(h.node('moisesStatus').textContent,/32 MB/);
  }
  const atLimit = harness(); atLimit.ready('mp4',{size:LIMIT}); await atLimit.click(); assert.ok(atLimit.evaluate('state.moisesPrepared'));
  const unsupported = harness(); unsupported.ready(); unsupported.sandbox.navigator.canShare=()=>false; await unsupported.click();
  assert.equal(unsupported.evaluate('state.moisesPrepared'),null); assert.match(unsupported.node('moisesStatus').textContent,/cannot share/);
  const wrongFormat = harness(); wrongFormat.ready('zip'); await wrongFormat.click(); assert.equal(wrongFormat.evaluate('state.moisesPrepared'),null);
});

test('foreign, credential-bearing, mismatched, and malformed secure URLs cannot be fetched', async () => {
  for (const url of ['https://example.test/api/vortex/jobs/ready-1/file',BACKEND.replace('https://','https://user:password@')+'/api/vortex/jobs/ready-1/file',BACKEND+'/api/vortex/jobs/missing/file',BACKEND+'/api/vortex/jobs/ready-1/other','http://[bad']) {
    const h = harness(); h.ready(); h.sandbox.fixtureURL=url; h.evaluate('state.readyDownload.url=fixtureURL'); await h.click();
    assert.equal(h.binaryCalls().length,0); assert.equal(h.shared.length,0);
  }
});

test('an expired ticket still uses the authenticated completed job without re-exporting', async () => {
  const h = harness(); h.ready(); h.evaluate('state.readyDownload.expiresAt=1'); await h.click(); await h.click();
  assert.equal(h.binaryCalls().length,1); assert.equal(h.shared.length,1);
  assert.equal(h.calls.filter(call=>call.route==='/vortex/jobs').length,0);
});

test('preparation deduplicates taps and aborts when the selection changes, including A to B to A', async () => {
  const h = harness(); h.ready(); let release, signal;
  h.setBinary((_,options)=>{signal=options.signal;return new Promise(resolve=>release=resolve);});
  const pending=h.click(); await h.click(); assert.equal(h.binaryCalls().length,1); assert.equal(h.node('moisesOpenButton').disabled,true);
  h.evaluate("discardQueuedDownload(); state.selected.url='https://example.test/other'; renderDownloadStatus(); state.selected.url='https://example.test/media'; renderDownloadStatus()");
  assert.equal(signal.aborted,true); release(new Blob(['media'])); await pending;
  assert.equal(h.evaluate('state.moisesPrepared'),null); assert.equal(h.node('moisesOpenButton').disabled,false);
  assert.equal(h.node('moisesStatus').textContent,'');
});

test('late account responses and cached files cannot cross account epochs or selections', async () => {
  for (const uid of ['bob','alice',null]) {
    const h = harness(); h.ready(); let release, signal;
    h.setBinary((_,options)=>{signal=options.signal;return new Promise(resolve=>release=resolve);});
    const pending=h.click(); h.switchAccount(uid); assert.equal(signal.aborted,true); release(new Blob(['old file'])); await pending;
    assert.equal(h.evaluate('state.moisesPrepared'),null); assert.equal(h.shared.length,0); assert.equal(h.node('moisesStatus').textContent,'');
  }
  for (const change of ["state.audioFormat='mp3'", "state.videoFormat='mov'", "state.downloadMode='audio'", "state.quality='max'", "state.jobs=[]", "state.jobs[0].status='expired'", "state.jobs[0].resultReady=false"]) {
    const h = harness(); h.ready(); await h.click(); assert.ok(h.evaluate('state.moisesPrepared'));
    h.evaluate(change+'; renderDownloadStatus()'); assert.equal(h.evaluate('state.moisesPrepared'),null);
  }
});

test('share cancellation retries the prepared bytes and errors do not strand the control', async () => {
  for (const name of ['AbortError','NotAllowedError']) {
    const h = harness(); h.ready(); await h.click(); h.sandbox.navigator.share=async()=>{throw Object.assign(new Error('fixture'),{name});};
    await h.click(); assert.ok(h.evaluate('state.moisesPrepared')); assert.equal(h.node('moisesOpenButton').disabled,false);
    assert.equal(h.node('moisesOpenButton').getAttribute('aria-busy'),null);
    h.sandbox.navigator.share=async data=>h.shared.push(data); await h.click(); assert.equal(h.shared.length,1); assert.equal(h.binaryCalls().length,1);
  }
  const h = harness(); h.ready(); h.setBinary(async()=>{throw new Error('offline fixture');}); await h.click();
  assert.match(h.node('moisesStatus').textContent,/offline fixture/); assert.equal(h.node('moisesOpenButton').disabled,false);
});

test('the real auth request retains owner authorization and cancels its fetch with the handoff signal', async () => {
  const identity={uid:'alice',getIdToken:async()=> 'fixture-alice'}, auth={currentUser:identity,authStateReady:async()=>{}};
  const calls=[];
  const sandbox={URL,AbortController,setTimeout,clearTimeout,console,
    window:{VisionFirebaseSDK:{app:{getApps:()=>[],initializeApp:()=>({})},auth:{initializeAuth:()=>auth,useDeviceLanguage(){},onAuthStateChanged(){}}}},
    fetch:(_,options)=>{calls.push(options);return new Promise((_,reject)=>{if(options.signal.aborted)reject(Object.assign(new Error('aborted'),{name:'AbortError'}));else options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('aborted'),{name:'AbortError'})),{once:true});});}};
  const context=vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(ROOT,'web/vortex/auth.js'),'utf8').replace(/^export /gm,''),context);
  await vm.runInContext('initAuth(()=>{})',context);
  const controller=new AbortController(); sandbox.fixtureSignal=controller.signal;
  const pending=vm.runInContext("request('/vortex/jobs/ready-1/file',{binary:true,timeout:90000,signal:fixtureSignal})",context);
  await tick(); assert.equal(calls.length,1);
  assert.equal(calls[0].headers.Authorization,'Bearer fixture-alice'); assert.equal(calls[0].credentials,'omit'); assert.equal(calls[0].cache,'no-store');
  controller.abort(); await assert.rejects(pending,/did not reply in time/); assert.equal(calls[0].signal.aborted,true);
});
