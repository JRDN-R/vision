// Project identity is portable in editable projects; it is never added to AI exports.
const PROJECT_STORE='vision-projects-v1',PROJECT_RECENT='vision-project-recents-v1',PROJECT_ACTIVE='vision-project-active-v1';
const PROJECT_MAX_BYTES=150*1024*1024;
let projectDBPromise=null,projectBackupTimer=null,projectSyncTimer=null,projectSyncPromise=null,projectSyncEpoch=-1,projectEpoch=0,projectGeneration=0,projectPending=false,projectLoading=false,projectConflict=false,projectLocalOK=false,projectMessage='Projects save automatically after your first change.',projectHealthCache=null,projectFirstPendingAt=0,projectFirstBackupAt=0,projectRecentMemory=[];
function validProjectIdentity(raw){
 if(!raw||typeof raw!=='object'||!/^[-\w]{16,120}$/.test(raw.id||'')||!/^[-\w]{32,256}$/.test(raw.key||''))return null;
 let backendUrl='';if(raw.backendUrl){const config=validCloudConfig({kind:'private-pc',backendUrl:raw.backendUrl});if(!config)return null;backendUrl=config.backendUrl;}
 return{id:raw.id,key:raw.key,backendUrl,revision:Number.isSafeInteger(raw.revision)&&raw.revision>=0?raw.revision:0};
}
function projectRandom(bytes){const a=new Uint8Array(bytes);crypto.getRandomValues(a);return Array.from(a,b=>b.toString(16).padStart(2,'0')).join('');}
function ensureProjectIdentity(){
 let meta=validProjectIdentity(state.projectCloud);
 if(!meta)meta={id:'project-'+projectRandom(16),key:projectRandom(32),backendUrl:cloudConfig?.kind==='private-pc'?cloudConfig.backendUrl:'',revision:0};
 if(!meta.backendUrl&&cloudConfig?.kind==='private-pc')meta.backendUrl=cloudConfig.backendUrl;
 state.projectCloud=meta;return meta;
}
function projectSnapshot(){return{format:'Vision',version:4,savedAt:new Date().toISOString(),...withoutVideoPayloads(snapshot()),projectCloud:{...ensureProjectIdentity()}};}
function projectLocalGet(key){try{return localStorage.getItem(key);}catch{return null;}}
function projectLocalSet(key,value){try{localStorage.setItem(key,value);return true;}catch{return false;}}
function projectRecentList(){try{const text=projectLocalGet(PROJECT_RECENT);return (text?JSON.parse(text):projectRecentMemory).filter(r=>validProjectIdentity(r)&&typeof r.title==='string').slice(0,30);}catch{return projectRecentMemory;}}
function projectRemember(meta,title,updatedAt=new Date().toISOString()){
 const entries=projectRecentList().filter(r=>r.id!==meta.id);entries.unshift({...meta,title:String(title||'Untitled project').slice(0,100),updatedAt});projectRecentMemory=entries.slice(0,30);projectLocalSet(PROJECT_RECENT,JSON.stringify(entries.slice(0,30)));projectLocalSet(PROJECT_ACTIVE,meta.id);
}
function projectDatabase(){
 if(!projectDBPromise)projectDBPromise=new Promise((resolve,reject)=>{if(!globalThis.indexedDB){reject(new Error('Browser storage is unavailable.'));return;}const request=indexedDB.open(PROJECT_STORE,1);request.onupgradeneeded=()=>request.result.createObjectStore('projects',{keyPath:'id'});request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error||new Error('Browser storage is unavailable.'));request.onblocked=()=>reject(new Error('Browser storage is blocked by another Vision tab.'));});
 return projectDBPromise;
}
async function projectStorePut(record,{activate=true}={}){
 try{const db=await projectDatabase();await new Promise((resolve,reject)=>{const tx=db.transaction('projects','readwrite');const store=tx.objectStore('projects');store.put(record);if(record.project&&activate)store.put({id:'__active__',activeId:record.id,recents:projectRecentList()});tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error||new Error('Browser backup was canceled.'));});return true;}
 catch(error){const text=JSON.stringify(record);if(text.length<1500000&&projectLocalSet(PROJECT_STORE+':'+record.id,text))return true;throw new Error('Browser backup is unavailable or full. Download a project copy; PC saving can still work.');}
}
async function projectStoreGet(id){
 try{const db=await projectDatabase();const record=await new Promise((resolve,reject)=>{const request=db.transaction('projects').objectStore('projects').get(id);request.onsuccess=()=>resolve(request.result||null);request.onerror=()=>reject(request.error);});if(record)return record;}catch{}
 try{return JSON.parse(projectLocalGet(PROJECT_STORE+':'+id)||'null');}catch{return null;}
}
function projectStatus(message,kind='local'){
 projectMessage=message;const el=$('saveState');if(el){el.textContent=message;el.dataset.projectStatus=kind;el.title='Open projects and save status';}
 const status=$('projectSyncStatus');if(status){status.textContent=message;status.dataset.projectStatus=kind;}
 if($('projectConflictActions'))$('projectConflictActions').hidden=!projectConflict;
 if($('projectUpdateHelp'))$('projectUpdateHelp').hidden=kind!=='update';
}
async function projectBackup(){
 projectFirstBackupAt=0;if(projectLoading||!validProjectIdentity(state.projectCloud))return;
 const epoch=projectEpoch,generation=projectGeneration,data=projectSnapshot(),record={id:data.projectCloud.id,project:data,pending:projectPending,updatedAt:data.savedAt,generation,epoch};
 projectRemember(data.projectCloud,data.title,data.savedAt);
 try{await projectStorePut(record);if(epoch===projectEpoch&&generation===projectGeneration){projectLocalOK=true;if(projectPending&&!projectConflict)projectStatus('Saved on this device · waiting for PC', 'local');}}
 catch(error){if(epoch===projectEpoch){projectLocalOK=false;projectStatus(error.message,'error');}}
}
function projectQueueSave(){
 if(projectLoading)return;ensureProjectIdentity();if(!projectPending||!projectFirstPendingAt)projectFirstPendingAt=Date.now();if(!projectFirstBackupAt)projectFirstBackupAt=Date.now();projectPending=true;projectGeneration++;projectRemember(state.projectCloud,state.title);clearTimeout(projectBackupTimer);clearTimeout(projectSyncTimer);
 projectStatus(projectConflict?'PC copy changed · choose which copy to keep':'Saving project…',projectConflict?'conflict':'saving');
 projectBackupTimer=setTimeout(()=>void projectBackup(),Math.max(0,Math.min(200,2000-(Date.now()-projectFirstBackupAt))));
 if(!projectConflict)projectSyncTimer=setTimeout(()=>void flushProjectSave().catch(()=>{}),Math.max(0,Math.min(1400,8000-(Date.now()-projectFirstPendingAt))));
}
const projectOriginalMarkDirty=markDirty;
markDirty=function(){projectOriginalMarkDirty();projectQueueSave();};
const projectOriginalValidate=validateProject;
validateProject=async function(raw){const loaded=await projectOriginalValidate(raw),meta=validProjectIdentity(raw?.projectCloud);if(meta)loaded.projectCloud=meta;return loaded;};
async function projectRequest(path,options={}){
 const meta=ensureProjectIdentity();if(!meta.backendUrl||meta.backendUrl!==cloudConfig?.backendUrl||cloudConfig?.kind!=='private-pc')throw new Error('This project belongs to a different PC. Connect its original processor, or save as a new project.');
 if(!path.startsWith('/projects/'+meta.id)||!['','/','?'].includes(path.slice(('/projects/'+meta.id).length,('/projects/'+meta.id).length+1)))throw new Error('The active project changed. Reopen its conversation to continue.');
 const headers=new Headers(options.headers||{});headers.set('X-Vision-Project-Key',meta.key);headers.set('Accept','application/json');
 return cloudFetch(path,{...options,headers});
}
async function projectResponse(response){let data;try{data=await response.json();}catch{throw new Error('The PC did not return a valid project response.');}if(!response.ok){const error=new Error(data.error||'The PC could not save this project.');error.status=response.status;error.data=data;throw error;}return data;}
async function projectCapabilities(force=false){
 if(cloudConfig?.kind!=='private-pc'||!cloudAuth?.accessToken)throw new Error('PC autosave is not connected. Your project can still be saved on this device or downloaded.');
 if(!force&&projectHealthCache?.url===cloudConfig.backendUrl&&Date.now()-projectHealthCache.time<30000)return projectHealthCache.data;
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000);let data;
 try{data=await projectResponse(await cloudFetch('/health',{signal:controller.signal}));}finally{clearTimeout(timer);}
 const caps=data.capabilities,has=name=>Array.isArray(caps)?caps.includes(name):caps?.[name]===true;
 if(!has('persistentProjects')||!has('projectRevision')){const error=new Error('Update PC processor to enable project autosave and background chats.');error.code='VISION_PC_UPDATE_REQUIRED';throw error;}
 projectHealthCache={url:cloudConfig.backendUrl,time:Date.now(),data};return data;
}
function projectReportFailure(error){
 if(error.status===409){projectConflict=true;projectStatus('PC copy changed · choose which copy to keep','conflict');}
 else if(error.code==='VISION_PC_UPDATE_REQUIRED')projectStatus(projectLocalOK?'Update PC processor · project kept on this device':'Update PC processor · download a project copy','update');
 else if(error.status===413)projectStatus('Project exceeds the PC limit of 150 MiB. Download a copy and reduce attached files.','error');
 else if(error.name==='AbortError'||error.code==='VISION_SERVER_UNAVAILABLE'||navigator.onLine===false)projectStatus(projectLocalOK?'Saved on this device · PC offline, will retry':'PC offline · download a project copy','offline');
 else projectStatus(error.message,'error');
}
async function projectPerformSave(){
 if(projectLoading)return ensureProjectIdentity();if(projectConflict){const e=new Error('The PC has a newer copy. Use Projects to load it or save this board as a new project.');e.status=409;throw e;}
 const meta=ensureProjectIdentity(),epoch=projectEpoch;if(meta.revision===0)projectPending=true;projectFirstPendingAt=Date.now();await projectBackup();await projectCapabilities();if(epoch!==projectEpoch)throw new Error('The active project changed.');
 if(meta.backendUrl!==cloudConfig.backendUrl)throw new Error('Connect this project’s original PC or save it as a new project.');
 const generation=projectGeneration,data=projectSnapshot(),payload=JSON.stringify({project:data,revision:meta.revision});
 if(new Blob([payload]).size>PROJECT_MAX_BYTES){const e=new Error('Project exceeds 150 MiB.');e.status=413;throw e;}
 projectStatus('Saving to PC…','saving');const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),120000);let result;
 try{result=await projectResponse(await projectRequest('/projects/'+meta.id,{method:'PUT',headers:{'Content-Type':'application/json'},body:payload,signal:controller.signal}));}finally{clearTimeout(timer);}
 if(!Number.isSafeInteger(result.revision)||result.revision<=meta.revision)throw new Error('The PC returned an invalid save revision.');
 if(epoch!==projectEpoch||state.projectCloud?.id!==meta.id){const prior=await projectStoreGet(meta.id);if(prior?.epoch===epoch&&prior.generation===generation&&prior.project?.projectCloud?.revision===meta.revision){prior.project.projectCloud={...meta,revision:result.revision};prior.pending=false;await projectStorePut(prior,{activate:false}).catch(()=>{});}return{...meta,revision:result.revision};}
 state.projectCloud={...meta,revision:result.revision};projectPending=generation!==projectGeneration;
 await projectBackup();if(!projectPending){dirty=false;updateRefreshNotice();projectStatus('Saved to PC · '+new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}),'saved');}
 else{clearTimeout(projectSyncTimer);projectSyncTimer=setTimeout(()=>void flushProjectSave().catch(()=>{}),1000);}
 return state.projectCloud;
}
async function flushProjectSave(){
 clearTimeout(projectSyncTimer);const callerEpoch=projectEpoch;if(projectSyncPromise){const previousEpoch=projectSyncEpoch;try{await projectSyncPromise;}catch(error){if(previousEpoch===projectEpoch)throw error;}if(callerEpoch!==projectEpoch)throw new Error('The active project changed.');if(projectPending&&!projectConflict||!state.projectCloud?.revision)return flushProjectSave();return ensureProjectIdentity();}
 if(!projectPending&&state.projectCloud?.revision>0)return ensureProjectIdentity();const epoch=projectEpoch;
 projectSyncEpoch=epoch;projectSyncPromise=projectPerformSave().catch(error=>{if(epoch===projectEpoch)projectReportFailure(error);throw error;}).finally(()=>{projectSyncPromise=null;});return projectSyncPromise;
}
async function ensureRemoteProject(){const meta=ensureProjectIdentity(),epoch=projectEpoch;await projectCapabilities();if(epoch!==projectEpoch||state.projectCloud?.id!==meta.id)throw new Error('The active project changed.');const saved=await flushProjectSave();if(epoch!==projectEpoch||state.projectCloud?.id!==meta.id)throw new Error('The active project changed.');return saved;}
async function projectReadRemote(meta){
 if(meta.id!==state.projectCloud?.id)throw new Error('The active project changed.');const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),120000);
 try{return await projectResponse(await projectRequest('/projects/'+meta.id,{signal:controller.signal}));}finally{clearTimeout(timer);}
}
async function projectApply(raw,{pendingSave=false,keepHistory=false}={}){
 const startEpoch=projectEpoch,startGeneration=projectGeneration,loaded=await validateProject(raw);
 if(startEpoch!==projectEpoch||startGeneration!==projectGeneration)throw new Error('The project changed while loading. Your current work was kept.');
 projectLoading=true;clearTimeout(projectBackupTimer);clearTimeout(projectSyncTimer);projectEpoch++;projectGeneration=0;
 try{cancelTranscriptionQueue();state=loaded;selected=null;selectedMark=null;selectedEdge=null;pending=null;action=null;if(!keepHistory){history=[];future=[];}projectPending=pendingSave;projectConflict=false;projectLocalOK=false;dirty=pendingSave;consoleRefreshProject();resumeYouTubeImports();scheduleTranscriptionQueue();R.clearImageCache();renderAll();updateRefreshNotice();ensureProjectIdentity();projectRemember(state.projectCloud,state.title);}
 finally{projectLoading=false;}
 await projectBackup();projectStatus(pendingSave?'Restored this device’s changes · waiting for PC':'Project restored','local');
}
async function projectReconcile({force=false}={}){
 const meta=validProjectIdentity(state.projectCloud);if(!meta||!meta.backendUrl)return;const epoch=projectEpoch,generation=projectGeneration;
 try{await projectCapabilities();const remote=await projectReadRemote(meta);if(epoch!==projectEpoch||generation!==projectGeneration)return;
  if(!Number.isSafeInteger(remote.revision)||remote.revision<1||!remote.project)throw new Error('The saved PC project is incomplete.');
  if(remote.revision!==meta.revision){if(projectPending&&!force){projectConflict=true;projectStatus('PC copy changed · choose which copy to keep','conflict');return;}
   const authoritative={...remote.project,projectCloud:{...meta,revision:remote.revision}};await projectApply(authoritative);projectStatus('Loaded latest PC copy','saved');
  }else if(projectPending){await flushProjectSave();}else{projectStatus('Saved to PC','saved');consoleRefreshProject();}
 }catch(error){if(epoch!==projectEpoch)return;if(error.status===404&&meta.revision===0){if(projectPending)await flushProjectSave().catch(()=>{});return;}projectReportFailure(error);}
}
async function projectOpenRecent(meta){
 if(ioBusy||busy)return;if(dirty&&!confirm('Open another project? Your current copy stays in Projects on this device.'))return;
 await projectBackup();const epoch=projectEpoch,generation=projectGeneration;projectStatus('Opening project…','saving');
 try{const record=await projectStoreGet(meta.id);if(epoch!==projectEpoch||generation!==projectGeneration)return;
  if(record?.project){await projectApply({...record.project,projectCloud:{...validProjectIdentity(record.project.projectCloud)}},{pendingSave:record.pending===true});}
  else{if(meta.backendUrl!==cloudConfig?.backendUrl)throw new Error('Connect this project’s original PC to open it.');await projectCapabilities();const headers={'X-Vision-Project-Key':meta.key,'Accept':'application/json'},remote=await projectResponse(await cloudFetch('/projects/'+meta.id,{headers}));if(epoch!==projectEpoch||generation!==projectGeneration)return;await projectApply({...remote.project,projectCloud:{...meta,revision:remote.revision}});}
  $('projectsDialog').close();await projectReconcile();
 }catch(error){projectReportFailure(error);}
}
async function projectFork(){
 if(busy||ioBusy)return;clearTimeout(projectSyncTimer);clearTimeout(projectBackupTimer);await projectBackup();projectEpoch++;delete state.projectCloud;ensureProjectIdentity();projectConflict=false;projectPending=true;projectGeneration++;history=history.map(item=>({...item,projectCloud:{...state.projectCloud}}));future=future.map(item=>({...item,projectCloud:{...state.projectCloud}}));
 // A new board copy has a new conversation; previous chats stay with the original project.
 state.consoleSession=null;consoleRefreshProject();projectQueueSave();renderProjectMenu();await flushProjectSave().catch(()=>{});
}
function renderProjectMenu(){
 $('projectSyncStatus').textContent=projectMessage;$('projectConflictActions').hidden=!projectConflict;const list=$('projectRecentList');list.replaceChildren();
 for(const entry of projectRecentList()){const button=document.createElement('button');button.className='project-recent';const name=document.createElement('strong'),detail=document.createElement('small');name.textContent=entry.title;detail.textContent=(entry.id===state.projectCloud?.id?'Current · ':'')+new Date(entry.updatedAt).toLocaleString();button.append(name,detail);button.onclick=()=>void projectOpenRecent(entry);list.appendChild(button);}
 if(!list.children.length){const empty=document.createElement('p');empty.className='mini-note';empty.textContent='Your recent projects will appear here after you start editing. Each editable project file also carries its connection to the PC copy.';list.appendChild(empty);}
}
function openProjectsMenu(){renderProjectMenu();if(!$('projectsDialog').open)$('projectsDialog').showModal();}
const projectDialog=document.createElement('dialog');projectDialog.id='projectsDialog';projectDialog.innerHTML='<div class="row spread"><h2>Projects</h2><button id="closeProjects" class="dialog-close" aria-label="Close projects">×</button></div><p id="projectSyncStatus" role="status" class="project-sync-status"></p><div id="projectConflictActions" hidden><p>The PC copy changed elsewhere. Your local changes are still here. Load the PC copy, or keep this board as a separate project.</p><button id="projectLoadPC">Load PC copy</button><button id="projectKeepLocal">Save as new project</button></div><details id="projectUpdateHelp" hidden><summary>Update PC processor</summary><p>On the Windows PC, open PowerShell as Administrator and run:</p><pre><code>$VisionSetup = Join-Path $env:TEMP \'Setup-Vision-PC.ps1\'\nInvoke-WebRequest -UseBasicParsing \'https://raw.githubusercontent.com/JRDN-R/vision/main/vision-pc/Setup-Vision-PC.ps1\' -OutFile $VisionSetup\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File $VisionSetup -Action Update</code></pre><p>Then use Retry PC save below.</p></details><div class="project-actions"><button id="projectOpenFile">Open project file</button><button id="projectDownload">Download project</button><button id="projectSyncNow">Retry PC save</button><button id="projectSaveNew">Save as new project</button></div><h3>Recent projects on this device</h3><div id="projectRecentList"></div><p class="mini-note">The PC keeps your editable project and background conversations. This browser keeps a recovery copy when storage is available. Download a project to open it on another device. Keep the PC awake and online for processing.</p>';
document.body.appendChild(projectDialog);
$('closeProjects').onclick=()=>projectDialog.close();$('projectOpenFile').onclick=()=>{projectDialog.close();$('projectInput').click();};$('projectDownload').onclick=()=>saveProject();$('projectSyncNow').onclick=async()=>{projectHealthCache=null;try{await ensureRemoteProject();}catch{}renderProjectMenu();};$('projectSaveNew').onclick=$('projectKeepLocal').onclick=()=>void projectFork();$('projectLoadPC').onclick=async()=>{if(!confirm('Load the latest PC copy and replace this board? Download this local copy first if you want to keep both.'))return;await projectReconcile({force:true});renderProjectMenu();};
$('openBtn').textContent='Projects';$('openBtn').onclick=openProjectsMenu;$('saveState').setAttribute('role','button');$('saveState').setAttribute('tabindex','0');$('saveState').onclick=openProjectsMenu;$('saveState').onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();openProjectsMenu();}};
const projectOriginalSave=saveProject;
saveProject=function(){if(!busy&&!ioBusy)ensureProjectIdentity();projectOriginalSave();if(state.projectCloud){void projectBackup();if(projectPending)void flushProjectSave().catch(()=>{});}};
$('saveBtn').onclick=saveProject;
const projectOriginalOpen=openProject;
openProject=async function(file){const oldState=state;await projectBackup();await projectOriginalOpen(file);if(state!==oldState){projectEpoch++;projectGeneration=0;projectPending=true;projectConflict=false;projectLocalOK=false;ensureProjectIdentity();await projectBackup();await projectReconcile();}};
const projectOriginalNew=$('newBtn').onclick;
$('newBtn').onclick=async function(event){await projectBackup();const oldState=state;projectOriginalNew(event);if(state!==oldState){clearTimeout(projectBackupTimer);clearTimeout(projectSyncTimer);projectEpoch++;projectGeneration=0;projectPending=false;projectConflict=false;projectLocalOK=false;projectLocalSet(PROJECT_ACTIVE,'');void projectStorePut({id:'__active__',activeId:'',recents:projectRecentList()}).catch(()=>{});projectStatus('New project · autosaves after your first change');}};
async function projectRecoverStartup(){
 const epoch=projectEpoch,generation=projectGeneration;
 try{const savedMeta=await projectStoreGet('__active__');if(savedMeta?.recents)projectRecentMemory=savedMeta.recents.filter(r=>validProjectIdentity(r));const id=projectLocalGet(PROJECT_ACTIVE)??savedMeta?.activeId;if(!id)return;const record=await projectStoreGet(id);if(!record?.project||epoch!==projectEpoch||generation!==projectGeneration||state.nodes.length||state.projectCloud)return;await projectApply(record.project,{pendingSave:record.pending===true});await projectReconcile();}catch(error){projectReportFailure(error);}
}
window.addEventListener('online',()=>{if(state.projectCloud){projectHealthCache=null;if(projectPending)void flushProjectSave().catch(()=>{});else void projectReconcile();}});
window.addEventListener('pagehide',()=>{clearTimeout(projectBackupTimer);void projectBackup();});
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden'){clearTimeout(projectBackupTimer);void projectBackup();}else if(state.projectCloud){if(projectPending)void flushProjectSave().catch(()=>{});else void projectReconcile();}});
setInterval(()=>{if(projectPending&&!projectConflict&&navigator.onLine!==false&&!projectSyncPromise)void flushProjectSave().catch(()=>{});},30000);
// Defer until the complete app has installed its renderers and handlers.
Promise.resolve().then(()=>projectRecoverStartup());
