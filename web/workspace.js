/* Workspace navigation and Sources. No credentials or remote HTML enter the UI. */
const WORKSPACE_VIEWS = new Set(['vision', 'venture']);
const workspace = {uid:'', epoch:0, navigation:0, preferenceSequence:0, preferenceQueue:Promise.resolve(), prefs:{launchView:'vision',swipeNoticeVersion:0}, sources:false,
 sourceCID:null, sourceEpoch:0, files:new Map(), cursor:null, timer:null, loading:false};
const WORKSPACE_NAV_PATHS = {
 accountButton:'M234-276q51-39 114-61.5T480-360q69 0 132 22.5T726-276q35-41 54.5-93T800-480q0-133-93.5-226.5T480-800q-133 0-226.5 93.5T160-480q0 59 19.5 111t54.5 93Zm246-164q-59 0-99.5-40.5T340-580q0-59 40.5-99.5T480-720q59 0 99.5 40.5T620-580q0 59-40.5 99.5T480-440Zm0 360q-83 0-156-31.5T197-197q-54-54-85.5-127T80-480q0-83 31.5-156T197-763q54-54 127-85.5T480-880q83 0 156 31.5T763-763q54 54 85.5 127T880-480q0 83-31.5 156T763-197q-54 54-127 85.5T480-80Zm0-80q53 0 100-15.5t86-44.5q-39-29-86-44.5T480-280q-53 0-100 15.5T294-220q39 29 86 44.5T480-160Zm0-360q26 0 43-17t17-43q0-26-17-43t-43-17q-26 0-43 17t-17 43q0 26 17 43t43 17Zm0-60Zm0 360Z',
 newBtn:'M440-280h80v-160h160v-80H520v-160h-80v160H280v80h160v160ZM200-120q-33 0-56.5-23.5T120-200v-560q0-33 23.5-56.5T200-840h560q33 0 56.5 23.5T840-760v560q0 33-23.5 56.5T760-120H200Zm0-80h560v-560H200v560Zm0-560v560-560Z',
 openBtn:'M280-280h80v-400h-80v400Zm320-80h80v-320h-80v320ZM440-480h80v-200h-80v200ZM200-120q-33 0-56.5-23.5T120-200v-560q0-33 23.5-56.5T200-840h560q33 0 56.5 23.5T840-760v560q0 33-23.5 56.5T760-120H200Zm0-80h560v-560H200v560Zm0-560v560-560Z',
 saveBtn:'M840-680v480q0 33-23.5 56.5T760-120H200q-33 0-56.5-23.5T120-200v-560q0-33 23.5-56.5T200-840h480l160 160Zm-80 34L646-760H200v560h560v-446ZM480-240q50 0 85-35t35-85q0-50-35-85t-85-35q-50 0-85 35t-35 85q0 50 35 85t85 35ZM240-560h360v-160H240v160Zm-40-86v446-560 114Z',
 exportBtn:'M200-120q-33 0-56.5-23.5T120-200v-160h80v160h560v-560H200v160h-80v-160q0-33 23.5-56.5T200-840h560q33 0 56.5 23.5T840-760v560q0 33-23.5 56.5T760-120H200Zm220-160-56-58 102-102H120v-80h346L364-622l56-58 200 200-200 200Z'
}; // Google Material Symbols Outlined, Apache-2.0, 24px / weight 400 / fill 0.
function workspaceNavIcons() {
 const names={accountButton:accountSignedIn()?'Account':'Sign in',newBtn:'New',openBtn:'Projects',saveBtn:'Save project',exportBtn:'Export'};
 for(const [id,path] of Object.entries(WORKSPACE_NAV_PATHS)) {
  const button=$(id); if(!button)continue;
  button.classList.add('workspace-nav-icon'); button.title=names[id]; button.setAttribute('aria-label',names[id]);
  // Real SVG children prevent iOS Safari's text autosizing and CSS-mask rendering
  // from exposing the original button label instead of showing the icon.
  let svg=button.querySelector(':scope > svg.workspace-nav-svg');
  if(!svg||button.childNodes.length!==1){
   svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
   svg.setAttribute('class','workspace-nav-svg');
   svg.setAttribute('viewBox','0 -960 960 960');
   svg.setAttribute('aria-hidden','true');
   svg.setAttribute('focusable','false');
   const shape=document.createElementNS('http://www.w3.org/2000/svg','path');
   shape.setAttribute('d',path);svg.append(shape);
   button.replaceChildren(svg);
  }
 }
}
function workspaceLocal(value) {
 const key='vision-workspace-v1-'+encodeURIComponent(workspace.uid);
 try {if(value)localStorage.setItem(key,JSON.stringify(value));return JSON.parse(localStorage.getItem(key)||'null');}catch{return null;}
}
function workspaceChosenURL() {
 const value=new URLSearchParams(location.search).get('view');return WORKSPACE_VIEWS.has(value)?value:null;
}
async function workspaceSave(change) {
 const epoch=workspace.epoch,sequence=++workspace.preferenceSequence;
 workspace.prefs={...workspace.prefs,...change};workspaceLocal(workspace.prefs);workspacePaintPreferences();
 const operation=workspace.preferenceQueue.catch(()=>{}).then(()=>{if(epoch!==workspace.epoch)return null;return ventureJSON('/venture/workspace-preferences','PATCH',change);});
 workspace.preferenceQueue=operation.catch(()=>{});
 try {const data=await operation;if(epoch!==workspace.epoch||sequence!==workspace.preferenceSequence||!data)return;
  workspace.prefs=data;workspaceLocal(data);workspacePaintPreferences('Saved for your account.');
 }catch(error){if(epoch===workspace.epoch&&sequence===workspace.preferenceSequence)workspacePaintPreferences('Saved on this device only. Update or reconnect FUPCJ Server to sync.');}
}
function workspacePaintPreferences(message='') {
 for(const id of ['workspaceAccountView','workspaceVentureView'])if($(id))$(id).value=workspace.prefs.launchView;
 for(const id of ['workspaceAccountStatus','workspaceVentureStatus'])if($(id))$(id).textContent=message;
}
function workspaceSwitch(view) {
 if(!WORKSPACE_VIEWS.has(view))return;
 workspace.navigation++;
 if(!accountSignedIn()){openAccountDialog();return;}
 if(view==='venture'){
  for(const id of ['accountDialog','projectsDialog'])$(id)?.close();
  if(!venture.open)void ventureOpen();
 }else ventureClose();
}
async function workspaceIdentity() {
 workspaceNavIcons();workspaceSyncSwitch();
 const uid=ventureScope();if(uid===workspace.uid)return;
 workspace.uid=uid;workspace.epoch++;workspace.sources=false;workspace.prefs={launchView:'vision',swipeNoticeVersion:0};
 document.body.classList.remove('workspace-sources-ready');
 workspaceClearSources();
 if(!uid)return;
 ventureIdentity();const epoch=workspace.epoch,navigation=workspace.navigation;
 const saved=workspaceLocal();if(saved&&WORKSPACE_VIEWS.has(saved.launchView))workspace.prefs=saved;
 let pending=null;try{pending=sessionStorage.getItem('vision-launch-pending');}catch{}
 try {const data=await ventureJSON('/venture/workspace-preferences');if(epoch!==workspace.epoch)return;
  workspace.sources=data.sourcesV1===true;workspace.prefs={...data,swipeNoticeVersion:Math.max(data.swipeNoticeVersion||0,saved?.swipeNoticeVersion||0)};
  if(saved?.swipeNoticeVersion===1&&data.swipeNoticeVersion!==1)void workspaceSave({swipeNoticeVersion:1});
 }catch{ /* Older/offline PC: keep inline file controls and the device preference. */ }
 if(epoch!==workspace.epoch)return;
 document.body.classList.toggle('workspace-sources-ready',workspace.sources);
 if(WORKSPACE_VIEWS.has(pending)){workspace.prefs.launchView=pending;void workspaceSave({launchView:pending});try{sessionStorage.removeItem('vision-launch-pending');}catch{}}
 workspaceLocal(workspace.prefs);workspacePaintPreferences();
 const launch=workspaceChosenURL()||workspace.prefs.launchView;
 if(launch==='venture'&&navigation===workspace.navigation)workspaceSwitch('venture');
}
function workspaceClearSources() {
 workspace.sourceEpoch++;workspace.sourceCID=null;workspace.files.clear();workspace.cursor=null;workspace.loading=false;
 clearTimeout(workspace.timer);$('workspaceSources')?.close();$('workspaceSourcesList')?.replaceChildren();
}
function workspaceSourcesCurrent(epoch,cid) {return epoch===workspace.sourceEpoch&&workspace.sourceCID===cid&&venture.current?.id===cid&&venture.open&&$('workspaceSources').open;}
async function workspaceLoadSources(more=false) {
 if(workspace.loading||!workspace.sourceCID)return;
 const cid=workspace.sourceCID,epoch=workspace.sourceEpoch;workspace.loading=true;
 $('workspaceSourcesStatus').textContent='Checking conversation files…';
 try {
  const data=await ventureJSON(venturePath(cid)+'/sources'+(more&&workspace.cursor?'?before='+encodeURIComponent(workspace.cursor):''));
  if(!workspaceSourcesCurrent(epoch,cid)||data.conversationId!==cid)return;
  const keepCursor=!more&&workspace.files.size>100;
  for(const file of data.files||[])workspace.files.set(file.id,file);
  if(!keepCursor)workspace.cursor=data.nextCursor;
  workspacePaintSources();
 }catch(error){if(workspaceSourcesCurrent(epoch,cid))$('workspaceSourcesStatus').textContent=ventureError(error);}
 finally{if(epoch===workspace.sourceEpoch){workspace.loading=false;clearTimeout(workspace.timer);if(workspaceSourcesCurrent(epoch,cid))workspace.timer=setTimeout(()=>void workspaceLoadSources(),5000);}}
}
function workspacePaintSources() {
 const host=$('workspaceSourcesList'),files=[...workspace.files.values()].sort((a,b)=>b.createdAt-a.createdAt||b.id.localeCompare(a.id));
 host.replaceChildren();
 for(const file of files) {
  const row=document.createElement('div');row.className='workspace-source-row';
  const label=document.createElement('div'),name=document.createElement('strong'),meta=document.createElement('small');name.textContent=file.name;
  meta.textContent=(file.kind==='artifact'?'Generated':'Uploaded')+' · '+new Date(file.createdAt*1000).toLocaleString()+' · '+(file.ready?'Saved':file.error||'Not available yet');label.append(name,meta);row.append(label);
  const action=(text,preview)=>{const button=document.createElement('button');button.type='button';button.textContent=text;button.disabled=!file.ready;
   button.onclick=()=>void workspaceSourceAction(file,preview,button);row.append(button);};
  if(file.kind==='artifact')action('Preview',true);action('Download',false);host.append(row);
 }
 $('workspaceSourcesMore').hidden=!workspace.cursor;
 $('workspaceSourcesStatus').textContent=files.length?files.length+' files shown'+(workspace.cursor?' · More files are available below.':'.')+' Different response versions are retained.':'No saved files in this conversation yet.';
}
async function workspaceSourceAction(file,preview,button) {
 const cid=workspace.sourceCID,epoch=workspace.sourceEpoch;if(!cid)return;button.disabled=true;
 try {
  const run={runId:file.runId,ventureId:cid,artifacts:[{id:file.artifactId,name:file.name,mime:file.mime,ready:file.ready}]};
  if(preview)await consolePreviewArtifact(run,0);
  else {const blob=file.kind==='artifact'?await consoleArtifactBlob(run.artifacts[0],run):await(await ventureFetch(venturePath(cid)+'/uploads/'+encodeURIComponent(file.runId)+'/'+file.inputIndex)).blob();
   if(workspaceSourcesCurrent(epoch,cid))download(blob,R.safeFilename(file.name));}
 }catch(error){if(workspaceSourcesCurrent(epoch,cid))$('workspaceSourcesStatus').textContent=ventureError(error);}
 finally{button.disabled=!file.ready;}
}
function workspaceOpenSources() {
 workspaceClearSources();workspace.sourceCID=venture.current?.id||null;
 $('workspaceSources').showModal();$('workspaceSourcesHeading').textContent='Sources · '+(venture.current?.title||'New venture');
 if(!workspace.sourceCID){$('workspaceSourcesStatus').textContent='Send a message with files to save this conversation’s Sources.';return;}
 if(!workspace.sources){$('workspaceSourcesStatus').textContent='Update FUPCJ Server to enable conversation-wide Sources. Existing file buttons are still available in the conversation.';return;}
 void workspaceLoadSources();
}
// One shared mobile button keeps its screen position and keyboard focus in both views.
let workspaceChevronView='', workspaceChevronVisible=false;
const workspaceChevrons={};
function workspaceInstallSwitch() {
 const button=document.createElement('button');button.id='workspaceSwitchButton';button.type='button';button.className='workspace-switch';button.hidden=true;
 for(const direction of ['right','left']) {
  const host=document.createElement('span');host.className='workspace-chevron';host.dataset.direction=direction;host.setAttribute('aria-hidden','true');host.hidden=true;button.append(host);
  workspaceChevrons[direction]=window.VisionChevron.create(host,direction);
 }
 button.onclick=()=>workspaceSwitch(venture.open?'vision':'venture');document.body.append(button);
 matchMedia('(max-width:760px)').addEventListener('change',()=>workspaceSyncSwitch());
 workspaceSyncSwitch();
}
function workspaceSyncSwitch() {
 const button=$('workspaceSwitchButton');if(!button)return;
 const direction=venture.open?'left':'right', label=venture.open?'Return to Vision':'Open Venture';
 const visible=accountSignedIn()&&matchMedia('(max-width:760px)').matches&&!(venture.open&&$('visionVenture').classList.contains('history-open'));
 button.hidden=!visible;button.setAttribute('aria-label',label);button.title=label;button.setAttribute('aria-controls',venture.open?'board':'visionVenture');
 document.body.classList.toggle('workspace-in-venture',venture.open);
 // The board is an isolated stacking context. Put its mobile Details control
 // beside the fixed top bar so the bar cannot cover it.
 const panel=$('sidebarToggle'),board=$('board');
 if(panel&&board){const parent=matchMedia('(max-width:760px)').matches?document.body:board;if(panel.parentElement!==parent)parent.append(panel);panel.inert=venture.open;}
 const app=document.querySelector('main.app');if(app)app.inert=venture.open;
 const header=$('appHeader');if(header)header.inert=venture.open||(matchMedia('(max-width:760px)').matches&&!document.documentElement.classList.contains('header-open'));
 if($('headerReveal'))$('headerReveal').inert=venture.open;
 for(const host of button.children)host.hidden=host.dataset.direction!==direction;
 if(!visible||direction!==workspaceChevronView||!workspaceChevronVisible) {
  for(const icon of Object.values(workspaceChevrons))icon.pause();
  if(visible)workspaceChevrons[direction].start();
 }
 workspaceChevronView=direction;workspaceChevronVisible=visible;
}
function workspaceAnimateView(view) {
 if(matchMedia('(prefers-reduced-motion:reduce)').matches)return;
 const host=view==='venture'?$('visionVenture'):document.querySelector('main.app');
 if(!host?.animate)return;
 host.getAnimations().forEach(animation=>animation.cancel());
 host.animate([{opacity:.65,transform:'translateX('+(view==='venture'?'18':'-18')+'px)'},{opacity:1,transform:'translateX(0)'}],{duration:220,easing:'cubic-bezier(.2,.75,.25,1)'});
}
function workspaceInstall() {
 const createDialog=(id,html)=>{const d=document.createElement('dialog');d.id=id;d.className='workspace-dialog';d.innerHTML=html;document.body.append(d);return d;};
 createDialog('workspaceSources','<div class="row spread"><h2 id="workspaceSourcesHeading">Sources</h2><button id="workspaceSourcesClose" type="button" aria-label="Close Sources">×</button></div><p id="workspaceSourcesStatus" role="status"></p><button id="workspaceSourcesRefresh" type="button">Refresh files</button><div id="workspaceSourcesList"></div><button id="workspaceSourcesMore" type="button" hidden>Load earlier files</button>');
 $('workspaceSourcesClose').onclick=()=>workspaceClearSources();$('workspaceSources').addEventListener('close',()=>clearTimeout(workspace.timer));
 $('workspaceSourcesRefresh').onclick=()=>void workspaceLoadSources();$('workspaceSourcesMore').onclick=()=>void workspaceLoadSources(true);
 const sources=document.createElement('button');sources.id='workspaceSourcesButton';sources.type='button';sources.className='v-icon';sources.title='Conversation Sources';sources.setAttribute('aria-label','Open conversation Sources');sources.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 5h7l2 2h9v13H3z"/></svg>';
 sources.onclick=workspaceOpenSources;$('ventureBack').before(sources);
 const slot=document.createElement('span');slot.className='workspace-switch-slot';slot.setAttribute('aria-hidden','true');$('ventureAvatar').before(slot);
 workspaceInstallSwitch();
 const preference=(host,id,status)=>{if(!host)return;const div=document.createElement('div');div.className='workspace-preference';
  div.innerHTML='<label for="'+id+'">Open by default</label><select id="'+id+'"><option value="vision">Vision board</option><option value="venture">Venture</option></select><p id="'+status+'" class="mini-note" role="status"></p>';
  host.append(div);$(id).onchange=()=>void workspaceSave({launchView:$(id).value});};
 preference($('accountDialog'),'workspaceAccountView','workspaceAccountStatus');preference($('ventureAccount'),'workspaceVentureView','workspaceVentureStatus');
 for(const [host,view,label] of [[$('accountDialog'),'venture','Open Venture'],[$('ventureAccount'),'vision','Return to Vision board']])if(host){const button=document.createElement('button');button.type='button';button.textContent=label;button.onclick=()=>workspaceSwitch(view);host.append(button);}
 const gate=$('accountGateSignIn');if(gate){const label=document.createElement('label');label.className='workspace-login-choice';label.textContent='After sign-in';const select=document.createElement('select');select.id='workspaceLoginView';
  select.innerHTML='<option value="">Use my account preference</option><option value="vision">Open Vision</option><option value="venture">Open Venture</option>';label.append(select);gate.before(label);
  select.onchange=()=>{try{if(select.value)sessionStorage.setItem('vision-launch-pending',select.value);else sessionStorage.removeItem('vision-launch-pending');}catch{}};}
 workspaceNavIcons();
}
workspaceInstall();
const workspacePriorGate=accountUpdateGate;
accountUpdateGate=function(){workspacePriorGate();queueMicrotask(()=>void workspaceIdentity());};
const workspacePriorClose=ventureClose;
ventureClose=function(){const changed=venture.open;workspace.navigation++;workspaceClearSources();workspacePriorClose();workspaceSyncSwitch();if(changed)workspaceAnimateView('vision');};
$('ventureBack').onclick=ventureClose;
const workspacePriorOpen=ventureOpen;
ventureOpen=function(){const changed=!venture.open;const pending=workspacePriorOpen();workspaceSyncSwitch();if(changed&&venture.open)workspaceAnimateView('venture');return pending;};
const workspacePriorSidebar=ventureSidebar;
ventureSidebar=function(show){workspacePriorSidebar(show);workspaceSyncSwitch();};
const workspacePriorPaint=venturePaint;
venturePaint=function(){workspacePriorPaint();if(workspace.sourceCID&&workspace.sourceCID!==venture.current?.id)workspaceClearSources();};
if(typeof accountReady!=='undefined')void accountReady.then(()=>workspaceIdentity());else void workspaceIdentity();
