/* Google import flows with fake OAuth, Picker, and API responses. No credentials or network required. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('web/google-sources.js','utf8'),cloud=fs.readFileSync('web/cloud.js','utf8');
const normalize=cloud.slice(cloud.indexOf('function normalizeYouTubeURL('),cloud.indexOf('function normalizeYouTubeImports('));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve};};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(){
 const elements=new Map(),requests=[],imports=[],attachments=[],consents=[],pickers=[];
 class Element{
  constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.attrs={};this.listeners={};this.value='';this.textContent='';this.disabled=false;this.hidden=false;this.open=false;}
  set innerHTML(value){this.html=value;for(const m of value.matchAll(/id="([^"]+)"/g))elements.set(m[1],new Element());if(this.tagName==='TEXTAREA')this.value=value.replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');}
  get innerHTML(){return this.html||'';}
  setAttribute(key,value){this.attrs[key]=value;}getAttribute(key){return this.attrs[key];}
  append(...items){this.children.push(...items);}appendChild(item){this.children.push(item);if(item.id)elements.set(item.id,item);return item;}
  replaceChildren(...items){this.children=items;}after(item){if(item.id)elements.set(item.id,item);}remove(){}
  querySelectorAll(selector){return this.children.flatMap(child=>[...(selector==='[aria-pressed]'&&child.attrs['aria-pressed']!==undefined?[child]:[]),...child.querySelectorAll(selector)]);}
  addEventListener(event,handler){(this.listeners[event]||=[]).push(handler);}dispatch(event){for(const handler of this.listeners[event]||[])handler({preventDefault(){}});}
  showModal(){this.open=true;}close(){this.open=false;this.dispatch('close');}focus(){}
 }
 const el=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 const control={signedIn:true,docs:[],response:()=>{throw new Error('Unexpected network request');},reauth:async user=>({user,credential:{accessToken:'drive-test-token'}})};
 class Provider{addScope(scope){this.scope=scope;}setCustomParameters(params){this.params=params;}static credentialFromResult(result){return result.credential;}}
 class DocsView{setIncludeFolders(){return this;}setSelectFolderEnabled(){return this;}}
 class PickerBuilder{
  constructor(){this.settings={};}
  setTitle(v){this.settings.title=v;return this;}setAppId(v){this.settings.appId=v;return this;}setDeveloperKey(v){this.settings.key=v;return this;}
  setOAuthToken(v){this.settings.token=v;return this;}setOrigin(v){this.settings.origin=v;return this;}addView(){return this;}enableFeature(){return this;}setMaxItems(v){this.settings.max=v;return this;}
  setCallback(callback){this.callback=callback;return this;}build(){const picker={...this.settings,dispose(){this.disposed=true;},setVisible:()=>queueMicrotask(()=>this.callback({action:'picked',docs:control.docs}))};pickers.push(picker);return picker;}
 }
 const google={picker:{DocsView,PickerBuilder,ViewId:{DOCS:'docs'},Feature:{MULTISELECT_ENABLED:'multiselect'},Action:{PICKED:'picked',CANCEL:'cancel'}}};
 const c={console,URL,URLSearchParams,Headers,Response,Blob,File,Uint8Array,ReadableStream,TextEncoder,AbortController,DOMException,setTimeout,clearTimeout,Date,Map,Set,Intl,
  window:{google},google,location:{origin:'https://vision.test'},document:{createElement:tag=>new Element(tag),body:new Element(),head:new Element()},$:el,
  VISION_FIREBASE:{apiKey:'public-test-key',appId:'1:123:web:abc'},accountFirebase:{currentUser:{uid:'user-a',email:'a@example.com',providerData:[{providerId:'google.com'}]}},accountAuthEpoch:1,accountSignedIn:()=>control.signedIn,
  accountSDK:{GoogleAuthProvider:Provider,reauthenticateWithPopup:async(user,provider)=>{consents.push({user,provider});return control.reauth(user);}},
  state:{nodes:[]},selected:null,nodeById:id=>c.state.nodes.find(node=>node.id===id),busy:false,ioBusy:false,boardImportRunning:false,youtubeImportRunning:false,
  boardAsyncContext:()=>({project:c.state,epoch:c.accountAuthEpoch}),boardAsyncCurrent:context=>context.project===c.state&&context.epoch===c.accountAuthEpoch,
  boardImportContext:()=>c.boardAsyncContext(),boardImportCurrent:context=>c.boardAsyncCurrent(context),
  boardAddDialog:new Element('dialog'),transcriptionProvider:()=> 'local',soundEventsSelected:()=>true,
  importBoardFiles:async(files,location,options)=>{imports.push({files,location,options});return files.map(file=>({title:file.name}));},
  attachFiles:async(id,files)=>attachments.push({id,files}),
  toast(){},openAccountDialog(){},accountErrorText:error=>error.message,
  fetch:async(url,options)=>{const request={url:new URL(url),options};requests.push(request);return control.response(request);}
 };
 vm.createContext(c);vm.runInContext(normalize+'\n'+source,c);el('youtubeDialog').open=true;
 return{c,el,control,requests,imports,attachments,consents,pickers,run:code=>vm.runInContext(code,c)};
}
const json=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'content-type':'application/json'}});
const videoId='jNQXAC9IVRw';
(async()=>{
 // Choose only files from the existing Firebase account; native documents export
 // before going through the same board importer as files from the device.
 {
  const f=fixture();f.control.docs=[{id:'doc-1',name:'Notes'},{id:'photo-2',name:'Photo.png',resourceKey:'resource-key'}];
  f.control.response=({url,options})=>{
   assert.equal(options.headers.Authorization,'Bearer drive-test-token');assert.equal(options.credentials,'omit');
   if(url.pathname.endsWith('/about'))return json({user:{displayName:'A'}});
   if(url.pathname.endsWith('/doc-1/export')){assert.equal(url.searchParams.get('mimeType'),'application/vnd.openxmlformats-officedocument.wordprocessingml.document');return new Response('word bytes');}
   if(url.pathname.endsWith('/doc-1'))return json({name:'Notes',mimeType:'application/vnd.google-apps.document',capabilities:{canDownload:true}});
   assert.equal(options.headers['X-Goog-Drive-Resource-Keys'],'photo-2/resource-key');
   return url.searchParams.get('alt')==='media'?new Response('image bytes'):json({name:'Photo.png',mimeType:'image/png',size:'11',capabilities:{canDownload:true}});
  };
  await f.run('importDriveFiles()');assert.equal(f.consents.length,1);assert.equal(f.consents[0].user,f.c.accountFirebase.currentUser);
  assert.equal(f.consents[0].provider.scope,'https://www.googleapis.com/auth/drive.file');assert.equal(f.consents[0].provider.params.login_hint,'a@example.com');
  assert.equal(f.pickers[0].appId,'123');assert.equal(f.pickers[0].max,20);assert.equal(f.pickers[0].disposed,true);
  assert.deepEqual(Array.from(f.imports[0].files,file=>file.name),['Notes.docx','Photo.png']);assert.equal(await f.imports[0].files[0].text(),'word bytes');
  assert.equal(f.imports[0].options.provider,'local');assert.equal(f.imports[0].options.includeSoundEvents,true);assert.equal(f.el('driveDialog').open,false);
  f.control.docs=[];await f.run('importDriveFiles()');assert.equal(f.consents.length,1,'reuse only the in-memory credential for this account');
  f.run('cancelGoogleSources()');assert.equal(f.run('driveCredential'),null);
 }
 // The Files-tab shortcut captures its module before opening Drive, even when
 // selection later changes, and never silently creates a separate module.
 {
  const f=fixture();f.c.state.nodes=[{id:'module-a',title:'Evidence'},{id:'module-b',title:'Other'}];f.c.selected='module-a';
  f.el('addAttachmentsDrive').onclick();assert.match(f.el('driveStatus').textContent,/Evidence/);f.c.selected='module-b';
  f.control.docs=[{id:'photo-1'}];f.control.response=({url})=>url.pathname.endsWith('/about')?json({}):url.searchParams.get('alt')==='media'?new Response('photo'):json({name:'Photo.png',mimeType:'image/png',size:5});
  await f.run('importDriveFiles()');assert.equal(f.imports.length,0);assert.equal(f.attachments.length,1);assert.equal(f.attachments[0].id,'module-a');assert.equal(f.attachments[0].files[0].name,'Photo.png');
  f.el('boardAddDrive').onclick();await f.run('importDriveFiles()');assert.equal(f.imports.length,1,'the board shortcut still creates modules');
 }
 // Removed module targets cannot fall back to another selected module/board.
 {
  const f=fixture();f.c.state.nodes=[{id:'module-a',title:'Evidence'}];f.c.selected='module-a';f.el('addAttachmentsDrive').onclick();f.c.state.nodes=[];
  await f.run('importDriveFiles()');assert.equal(f.requests.length,0);assert.equal(f.attachments.length,0);assert.equal(f.imports.length,0);assert.equal(f.el('driveDialog').open,false);
 }
 // Password-only Vision accounts are valid for the app but must use Google sign-in for Drive.
 {
  const f=fixture();f.c.accountFirebase.currentUser.providerData=[{providerId:'password'}];f.el('boardAddDrive').onclick();assert.equal(f.el('driveDialog').open,false);assert.equal(f.consents.length,0);
 }
 // Reauthentication cannot silently replace the signed-in Vision account.
 {
  const f=fixture();f.control.reauth=async()=>({user:{uid:'user-b'},credential:{accessToken:'wrong-account'}});
  await f.run('importDriveFiles()');assert.equal(f.requests.length,0);assert.equal(f.imports.length,0);assert.match(f.el('driveStatus').textContent,/same Google account/);assert.equal(f.run('driveCredential'),null);
 }
 // A consent response belonging to the previous account must not start downloads.
 {
  const f=fixture(),pending=deferred();f.control.reauth=()=>pending.promise;const importing=f.run('importDriveFiles()');
  f.c.accountAuthEpoch++;f.run('cancelGoogleSources()');pending.resolve({user:{uid:'user-a'},credential:{accessToken:'stale-token'}});await importing;
  assert.equal(f.requests.length,0);assert.equal(f.imports.length,0);assert.equal(f.run('driveCredential'),null);assert.equal(f.el('driveDialog').open,false);
 }
 // The account/project boundary is checked again after an in-flight download.
 {
  const f=fixture(),pending=deferred();f.control.docs=[{id:'private-file'}];
  f.control.response=({url})=>url.pathname.endsWith('/about')?json({}):url.searchParams.get('alt')==='media'?pending.promise:json({name:'Private.txt',mimeType:'text/plain',size:7});
  const importing=f.run('importDriveFiles()');await tick();assert.equal(f.requests.length,3);f.c.state={nodes:[]};f.run('cancelGoogleSourceImports()');pending.resolve(new Response('private'));await importing;
  assert.equal(f.requests[2].options.signal.aborted,true);assert.equal(f.imports.length,0);assert.equal(f.el('driveDialog').open,false);
 }
 // Download restrictions and streamed byte limits are enforced before attaching.
 {
  const f=fixture();f.control.docs=[{id:'locked-file'}];f.control.response=({url})=>url.pathname.endsWith('/about')?json({}):json({name:'Locked',capabilities:{canDownload:false}});
  await f.run('importDriveFiles()');assert.equal(f.requests.length,2);assert.equal(f.imports.length,0);assert.match(f.el('driveStatus').textContent,/disabled downloads/);
  f.control.response=()=>new Response(new ReadableStream({start(controller){controller.enqueue(new Uint8Array(6));controller.enqueue(new Uint8Array(6));controller.close();}}));
  await assert.rejects(f.run("googleSourceResponse('https://www.googleapis.com/test',{limit:10,json:false})"),/size limit/);
  f.control.response=()=>json({error:{errors:[{reason:'exportSizeLimitExceeded'}]}},403);
  await assert.rejects(f.run("googleSourceResponse('https://www.googleapis.com/test',{service:'Drive'})"),/10 MB/);
 }
 // Search happens on request, renders safe cards, and fills a canonical URL.
 {
  const f=fixture();f.control.response=({url,options})=>{
   assert.equal(options.headers.Authorization,undefined,'YouTube search never receives the Drive token');
   if(url.pathname.endsWith('/search')){assert.equal(url.searchParams.get('q'),'cat sounds');assert.equal(url.searchParams.get('type'),'video');return json({items:[{id:{videoId},snippet:{title:'Cat &amp; dog',channelTitle:'Animals',description:'<img src=x onerror=bad()>',publishedAt:'2025-01-02T00:00:00Z'}},{id:{videoId:'invalid/path'},snippet:{title:'Discard'}}],nextPageToken:'next-page'});}
   return json({items:[{id:videoId,contentDetails:{duration:'PT1H2M3S'},statistics:{viewCount:'2500'}}]});
  };
  f.el('youtubeURL').value='cat sounds';f.el('youtubeURL').dispatch('input');assert.equal(f.requests.length,0);assert.equal(f.el('youtubeImport').disabled,true);
  await f.run('searchYouTubeVideos()');assert.equal(f.requests.length,2);const row=f.el('youtubeResults').children[0];assert.equal(f.el('youtubeResults').children.length,1);
  const select=row.children[0],thumb=select.children[0],copy=select.children[1];assert.equal(copy.children[0].textContent,'Cat & dog');assert.equal(copy.children[3].textContent,'<img src=x onerror=bad()>');assert.equal(copy.children[3].innerHTML,'');
  assert.equal(thumb.children[1].textContent,'1:02:03');assert.match(copy.children[1].textContent,/2.5K views/);assert.equal(thumb.children[0].src,'https://i.ytimg.com/vi/'+videoId+'/mqdefault.jpg');
  select.onclick();assert.equal(f.el('youtubeURL').value,'https://www.youtube.com/watch?v='+videoId);assert.equal(f.el('youtubeImport').disabled,false);assert.equal(f.imports.length,0,'selecting a card must not start an import');assert.equal(select.getAttribute('aria-pressed'),'true');assert.equal(f.el('youtubeRunSummary').textContent,'Cat & dog');assert.match(f.el('youtubeNotice').textContent,/Press Run/);
  await f.run('searchYouTubeVideos(true)');assert.equal(f.requests[2].url.searchParams.get('pageToken'),'next-page');assert.equal(f.el('youtubeResults').children.length,1,'pagination deduplicates videos');
  f.el('youtubeURL').value='cat sounds';await f.run('searchYouTubeVideos()');assert.equal(f.requests.length,4,'identical searches use the short-lived cache');
  f.el('youtubeURL').value='different search';f.el('youtubeURL').dispatch('input');assert.equal(f.el('youtubeMore').hidden,true);assert.equal(f.el('youtubeImport').disabled,true);assert.equal(f.el('youtubeRunSummary').textContent,'Choose a video');
 }
 // Controls open above the results without changing the selected video.
 {
  const f=fixture();f.el('youtubeURL').value='https://youtu.be/'+videoId;f.run('setYouTubeControls(false)');f.el('youtubeBody').scrollTop=1600;f.el('youtubeControls').onclick();
  assert.equal(f.el('youtubeControlsPanel').hidden,false);assert.equal(f.el('youtubeControls').getAttribute('aria-expanded'),'true');assert.equal(f.el('youtubeBody').scrollTop,0);assert.equal(f.el('youtubeURL').value,'https://youtu.be/'+videoId);
  f.el('youtubeControls').onclick();assert.equal(f.el('youtubeControlsPanel').hidden,true);assert.equal(f.requests.length,0);
 }
 // API setup errors keep direct-link import available; signed-out requests do nothing.
 {
  const f=fixture();f.control.response=()=>json({error:{errors:[{reason:'accessNotConfigured'}]}},403);f.el('youtubeURL').value='music';await f.run('searchYouTubeVideos()');
  assert.match(f.el('youtubeNotice').textContent,/not enabled/);f.el('youtubeURL').value='https://youtu.be/'+videoId;f.el('youtubeURL').dispatch('input');assert.equal(f.el('youtubeImport').disabled,false);
  f.control.signedIn=false;f.el('youtubeURL').value='anything';await f.run('searchYouTubeVideos()');assert.equal(f.requests.length,1);
 }
 // Closing/switching accounts cancels the search and discards delayed results.
 {
  const f=fixture(),pending=deferred();f.control.response=()=>pending.promise;f.el('youtubeURL').value='private query';const searching=f.run('searchYouTubeVideos()');
  f.c.accountAuthEpoch++;f.run('cancelGoogleSources()');pending.resolve(json({items:[{id:{videoId},snippet:{title:'Stale'}}]}));await searching;
  assert.equal(f.requests[0].options.signal.aborted,true);assert.equal(f.el('youtubeResults').children.length,0);assert.equal(f.el('youtubeURL').value,'');assert.equal(f.run('youtubeSearchCache.size'),0);
 }
 console.log('PASS: same-account Drive consent/import/export, cancellation and limits; explicit YouTube search, safe cards, pagination, selection, setup fallback, and account isolation.');
})().catch(error=>{console.error(error);process.exitCode=1;});
