// Project identity is portable in editable projects; it is never added to AI exports.
const PROJECT_STORE='vision-projects-v1',PROJECT_RECENT='vision-project-recents-v1',PROJECT_ACTIVE='vision-project-active-v1';
const PROJECT_MAX_BYTES=150*1024*1024;
let projectStorageScope='',projectAccountSwitching=false,projectAccountList=[],projectAccountListMessage='',projectAccountListEpoch=0;
function projectIsTemporary(scope=projectStorageScope){return scope.startsWith('trial:')||cloudAuth?.kind==='trial';}
function projectAccountUID(){return cloudAuth?.kind==='firebase-google'?cloudAuth.uid:'';}
function projectScopedKey(key,scope=projectStorageScope){return scope?key+':account:'+scope:key;}
function projectAccountCanAccess(meta){return !meta?.ownerUid||meta.ownerUid===projectAccountUID();}
function projectNeedsAccountClaim(){const meta=validProjectIdentity(state.projectCloud);return !!(projectAccountUID()&&meta&&!meta.ownerUid);}
let projectDBPromise=null,projectBackupTimer=null,projectSyncTimer=null,projectSyncPromise=null,projectSyncEpoch=-1,projectEpoch=0,projectGeneration=0,projectPending=false,projectLoading=false,projectConflict=false,projectLocalOK=false,projectMessage='Projects save automatically after your first change.',projectHealthCache=null,projectFirstPendingAt=0,projectFirstBackupAt=0,projectRecentMemory=[];
function validProjectIdentity(raw){
 if(!raw||typeof raw!=='object'||!/^[-\w]{16,120}$/.test(raw.id||'')||!/^[-\w]{32,256}$/.test(raw.key||''))return null;
 let backendUrl='';if(raw.backendUrl){const config=validCloudConfig({kind:'private-pc',backendUrl:raw.backendUrl});if(!config)return null;backendUrl=config.backendUrl;}
 const ownerUid=typeof raw.ownerUid==='string'&&raw.ownerUid.length<=128?raw.ownerUid:'';
 return{id:raw.id,key:raw.key,backendUrl,revision:Number.isSafeInteger(raw.revision)&&raw.revision>=0?raw.revision:0,...(ownerUid?{ownerUid}:{})};
}
function projectRandom(bytes){const a=new Uint8Array(bytes);crypto.getRandomValues(a);return Array.from(a,b=>b.toString(16).padStart(2,'0')).join('');}
function ensureProjectIdentity(){
 if(projectIsTemporary()&&typeof trialSession!=='undefined'&&trialSession){state.projectCloud={...trialSession.project};return state.projectCloud;}
 let meta=validProjectIdentity(state.projectCloud);
 if(!meta)meta={id:'project-'+projectRandom(16),key:projectRandom(32),backendUrl:cloudConfig?.kind==='private-pc'?cloudConfig.backendUrl:'',revision:0,...(projectAccountUID()?{ownerUid:projectAccountUID()}:{})};
 if(!meta.backendUrl&&cloudConfig?.kind==='private-pc')meta.backendUrl=cloudConfig.backendUrl;
 state.projectCloud=meta;return meta;
}
function projectSnapshot(){return{format:'Vision',version:4,savedAt:new Date().toISOString(),...withoutVideoPayloads(snapshot()),projectCloud:{...ensureProjectIdentity()}};}
function projectLocalGet(key,scope=projectStorageScope){if(projectIsTemporary(scope))return null;try{return localStorage.getItem(projectScopedKey(key,scope));}catch{return null;}}
function projectLocalSet(key,value,scope=projectStorageScope){if(projectIsTemporary(scope))return false;try{localStorage.setItem(projectScopedKey(key,scope),value);return true;}catch{return false;}}
// Last import choices belong to this browser account, not the currently open
// project. Reading a saved project must not replace them or alter queued jobs.
const IMPORT_PREFERENCES_KEY='vision-import-preferences-v1';
let importPreferencesScope=null,importPreferencesValues={};
function validImportPreference(name,value){return name==='transcriptionProvider'?['local','gemini'].includes(value):['includeSoundEvents','youtubeNewModule'].includes(name)&&typeof value==='boolean';}
function importPreference(name,fallback){
 // The portable app calls its base controls before project storage initializes.
 if(!importPreference.ready)return fallback;
 if(importPreferencesScope!==projectStorageScope){
  importPreferencesScope=projectStorageScope;importPreferencesValues={};
  try{const saved=JSON.parse(projectLocalGet(IMPORT_PREFERENCES_KEY)||'{}');for(const name of ['transcriptionProvider','includeSoundEvents','youtubeNewModule'])if(validImportPreference(name,saved?.[name]))importPreferencesValues[name]=saved[name];}catch{}
 }
 return validImportPreference(name,importPreferencesValues[name])?importPreferencesValues[name]:fallback;
}
function rememberImportPreference(name,value){
 if(!importPreference.ready||projectAccountSwitching||!validImportPreference(name,value))return;
 importPreference(name,undefined);importPreferencesValues[name]=value;
 projectLocalSet(IMPORT_PREFERENCES_KEY,JSON.stringify(importPreferencesValues));
}
function rememberedYouTubeNewModule(){return importPreference('youtubeNewModule',false);}
function setYouTubeNewModulePreference(value){rememberImportPreference('youtubeNewModule',value===true);}
importPreference.ready=true;
function projectRecentList(){try{const text=projectLocalGet(PROJECT_RECENT);return (text?JSON.parse(text):projectRecentMemory).filter(r=>validProjectIdentity(r)&&typeof r.title==='string').slice(0,30);}catch{return projectRecentMemory;}}
function projectDateText(value){
 // FUPCJ Server sends Unix seconds; device recovery uses ISO strings or milliseconds.
 if(typeof value==='string')value=value.trim();
 if(value==null||value===''||!['string','number'].includes(typeof value))return 'Date unavailable';
 const numeric=typeof value==='number'||/^[+-]?\d+(?:\.\d+)?$/.test(value);
 const number=numeric?Number(value):null;
 if(numeric&&(!Number.isFinite(number)||number<=0))return 'Date unavailable';
 const date=new Date(numeric?(number<1e11?number*1000:number):value);
 return Number.isFinite(date.getTime())?date.toLocaleString():'Date unavailable';
}
function projectRemember(meta,title,updatedAt=new Date().toISOString()){
 if(projectIsTemporary())return;
 const entries=projectRecentList().filter(r=>r.id!==meta.id);entries.unshift({...meta,title:String(title||'Untitled project').slice(0,100),updatedAt});projectRecentMemory=entries.slice(0,30);projectLocalSet(PROJECT_RECENT,JSON.stringify(entries.slice(0,30)));projectLocalSet(PROJECT_ACTIVE,meta.id);
}
function projectDatabase(){
 if(!projectDBPromise)projectDBPromise=new Promise((resolve,reject)=>{if(!globalThis.indexedDB){reject(new Error('Browser storage is unavailable.'));return;}const request=indexedDB.open(PROJECT_STORE,1);request.onupgradeneeded=()=>request.result.createObjectStore('projects',{keyPath:'id'});request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error||new Error('Browser storage is unavailable.'));request.onblocked=()=>reject(new Error('Browser storage is blocked by another Vision tab.'));});
 return projectDBPromise;
}
async function projectStorePut(record,{activate=true,scope=projectStorageScope}={}){
 if(projectIsTemporary(scope))return false;
 const recents=projectRecentList();
 try{const db=await projectDatabase();await new Promise((resolve,reject)=>{const tx=db.transaction('projects','readwrite');const store=tx.objectStore('projects');store.put({...record,id:projectScopedKey(record.id,scope)});if(record.project&&activate)store.put({id:projectScopedKey('__active__',scope),activeId:record.id,recents});tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error||new Error('Browser backup was canceled.'));});return true;}
 catch(error){const text=JSON.stringify(record);if(text.length<1500000&&projectLocalSet(PROJECT_STORE+':'+record.id,text,scope))return true;throw new Error('Browser backup is unavailable or full. Download a project copy; FUPCJ Server saving can still work.');}
}
async function projectStoreGet(id,scope=projectStorageScope){
 if(projectIsTemporary(scope))return null;
 try{const db=await projectDatabase();const record=await new Promise((resolve,reject)=>{const request=db.transaction('projects').objectStore('projects').get(projectScopedKey(id,scope));request.onsuccess=()=>resolve(request.result||null);request.onerror=()=>reject(request.error);});if(record)return{...record,id};}catch{}
 try{return JSON.parse(projectLocalGet(PROJECT_STORE+':'+id,scope)||'null');}catch{return null;}
}
function projectStatus(message,kind='local'){
 projectMessage=message;const el=$('saveState');if(el){el.textContent=message;el.dataset.projectStatus=kind;el.title='Open projects and save status';}
 const status=$('projectSyncStatus');if(status){status.textContent=message;status.dataset.projectStatus=kind;}
 if($('projectConflictActions'))$('projectConflictActions').hidden=!projectConflict;
 if($('projectUpdateHelp'))$('projectUpdateHelp').hidden=kind!=='update';
 const updateCommand=$('projectUpdateCommand');if(updateCommand&&kind==='update')updateCommand.textContent=updateCommand.textContent.replace(/-Action (?:Update|EnableGoogleSignIn)/g,'-Action '+(projectAccountUID()?'EnableGoogleSignIn':'Update'));
}
async function projectBackup(){
 if(projectIsTemporary())return;
 projectFirstBackupAt=0;if(projectLoading||!validProjectIdentity(state.projectCloud))return;
 const epoch=projectEpoch,generation=projectGeneration,data=projectSnapshot(),record={id:data.projectCloud.id,project:data,pending:projectPending,updatedAt:data.savedAt,generation,epoch};
 projectRemember(data.projectCloud,data.title,data.savedAt);
 try{await projectStorePut(record);if(epoch===projectEpoch&&generation===projectGeneration){projectLocalOK=true;if(projectPending&&!projectConflict)projectStatus('Saved on this device · waiting for FUPCJ Server', 'local');}}
 catch(error){if(epoch===projectEpoch){projectLocalOK=false;projectStatus(error.message,'error');}}
}
function projectQueueSave(){
 if(projectIsTemporary()){projectGeneration++;projectPending=false;projectStatus('Temporary trial · projects are not saved');return;}
 if(projectLoading||projectAccountSwitching)return;ensureProjectIdentity();if(!projectPending||!projectFirstPendingAt)projectFirstPendingAt=Date.now();if(!projectFirstBackupAt)projectFirstBackupAt=Date.now();projectPending=true;projectGeneration++;projectRemember(state.projectCloud,state.title);clearTimeout(projectBackupTimer);clearTimeout(projectSyncTimer);
 projectStatus(projectConflict?'FUPCJ Server copy changed · choose which copy to keep':'Saving project…',projectConflict?'conflict':'saving');
 projectBackupTimer=setTimeout(()=>void projectBackup(),Math.max(0,Math.min(200,2000-(Date.now()-projectFirstBackupAt))));
 if(!projectConflict)projectSyncTimer=setTimeout(()=>void flushProjectSave().catch(()=>{}),Math.max(0,Math.min(1400,8000-(Date.now()-projectFirstPendingAt))));
}
const projectOriginalMarkDirty=markDirty;
markDirty=function(){projectOriginalMarkDirty();projectQueueSave();};
const projectOriginalValidate=validateProject;
validateProject=async function(raw){const validationUID=projectAccountUID(),validationEpoch=projectEpoch;const meta=validProjectIdentity(raw?.projectCloud);if(!projectAccountCanAccess(meta))throw new Error('This project belongs to another account. Sign in to that account to open it.');const loaded=await projectOriginalValidate(raw);if(validationUID!==projectAccountUID()||validationEpoch!==projectEpoch||!projectAccountCanAccess(meta))throw new Error('The account or project changed while opening this file. Please open it again.');if(meta)loaded.projectCloud=meta;return loaded;};
async function projectRequest(path,options={}){
 const meta=ensureProjectIdentity();if(!meta.backendUrl||meta.backendUrl!==cloudConfig?.backendUrl||cloudConfig?.kind!=='private-pc')throw new Error('This project belongs to a different FUPCJ Server. Connect its original processor, or save as a new project.');
 if(projectAccountSwitching||!projectAccountCanAccess(meta))throw new Error('This project belongs to another account. Open one of your saved projects.');
 if(projectNeedsAccountClaim())throw new Error('Use Projects → Add this project to my account before saving this device project.');
 if(!path.startsWith('/projects/'+meta.id)||!['','/','?'].includes(path.slice(('/projects/'+meta.id).length,('/projects/'+meta.id).length+1)))throw new Error('The active project changed. Reopen its conversation to continue.');
 const headers=new Headers(options.headers||{});headers.set('X-Vision-Project-Key',meta.key);headers.set('Accept','application/json');
 return cloudFetch(path,{...options,headers});
}
async function projectResponse(response){let data;try{data=await response.json();}catch{throw new Error('FUPCJ Server did not return a valid project response.');}if(!response.ok){const error=new Error(data.error||'FUPCJ Server could not save this project.');error.status=response.status;error.data=data;if(response.status===401&&projectAccountUID()){error.message='Enable Firebase account sign-in on FUPCJ Server, then retry. Your browser is signed in, but the processor has not accepted the account yet.';error.code='VISION_PC_UPDATE_REQUIRED';}throw error;}return data;}
async function projectCapabilities(force=false){
 if(typeof accountReady!=='undefined')await accountReady;
 if(cloudConfig?.kind!=='private-pc'||(!cloudAuth?.accessToken&&!projectAccountUID()))throw new Error('FUPCJ Server autosave is not connected. Sign in, or use your existing FUPCJ Server connection.');
 if(!force&&projectHealthCache?.url===cloudConfig.backendUrl&&Date.now()-projectHealthCache.time<30000)return projectHealthCache.data;
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000);let data;
 try{data=await projectResponse(await cloudFetch('/health',{signal:controller.signal}));}finally{clearTimeout(timer);}
 const caps=data.capabilities,has=name=>Array.isArray(caps)?caps.includes(name):caps?.[name]===true;
 if(!(projectIsTemporary()&&has('temporarySessions'))&&(!has('persistentProjects')||!has('projectRevision'))){const error=new Error('Update FUPCJ Server to enable project autosave and background chats.');error.code='VISION_PC_UPDATE_REQUIRED';throw error;}
 projectHealthCache={url:cloudConfig.backendUrl,time:Date.now(),data};return data;
}
function projectReportFailure(error){
 if(error.status===409){projectConflict=true;projectStatus('FUPCJ Server copy changed · choose which copy to keep','conflict');}
 else if(error.code==='VISION_PC_UPDATE_REQUIRED')projectStatus(projectLocalOK?'Update FUPCJ Server · project kept on this device':'Update FUPCJ Server · download a project copy','update');
 else if(error.status===413)projectStatus('Project exceeds FUPCJ Server limit of 150 MiB. Download a copy and reduce attached files.','error');
 else if(error.name==='AbortError'||error.code==='VISION_SERVER_UNAVAILABLE'||navigator.onLine===false)projectStatus(projectLocalOK?'Saved on this device · FUPCJ Server offline, will retry':'FUPCJ Server offline · download a project copy','offline');
 else projectStatus(error.message,'error');
}
async function projectPerformSave(){
 if(projectIsTemporary())return ensureProjectIdentity();
 if(projectAccountSwitching)throw new Error('Wait for the account change to finish.');
 if(projectLoading)return ensureProjectIdentity();if(projectConflict){const e=new Error('FUPCJ Server has a newer copy. Use Projects to load it or save this board as a new project.');e.status=409;throw e;}
 const meta=ensureProjectIdentity(),epoch=projectEpoch,scope=projectStorageScope;if(meta.revision===0)projectPending=true;projectFirstPendingAt=Date.now();await projectBackup();await projectCapabilities();if(epoch!==projectEpoch)throw new Error('The active project changed.');
 if(meta.backendUrl!==cloudConfig.backendUrl)throw new Error('Connect this project’s original FUPCJ Server or save it as a new project.');
 const generation=projectGeneration,data=projectSnapshot(),payload=JSON.stringify({project:data,revision:meta.revision});
 if(new Blob([payload]).size>PROJECT_MAX_BYTES){const e=new Error('Project exceeds 150 MiB.');e.status=413;throw e;}
 projectStatus('Saving to FUPCJ Server…','saving');const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),120000);let result;
 try{result=await projectResponse(await projectRequest('/projects/'+meta.id,{method:'PUT',headers:{'Content-Type':'application/json'},body:payload,signal:controller.signal}));}finally{clearTimeout(timer);}
 if(!Number.isSafeInteger(result.revision)||result.revision<=meta.revision)throw new Error('FUPCJ Server returned an invalid save revision.');
 if(epoch!==projectEpoch||state.projectCloud?.id!==meta.id){const prior=await projectStoreGet(meta.id,scope);if(prior?.epoch===epoch&&prior.generation===generation&&prior.project?.projectCloud?.revision===meta.revision){prior.project.projectCloud={...meta,revision:result.revision};prior.pending=false;await projectStorePut(prior,{activate:false,scope}).catch(()=>{});}return{...meta,revision:result.revision};}
 state.projectCloud={...meta,revision:result.revision};projectPending=generation!==projectGeneration;
 await projectBackup();if(!projectPending){dirty=false;updateRefreshNotice();projectStatus('Saved to FUPCJ Server · '+new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}),'saved');}
 else{clearTimeout(projectSyncTimer);projectSyncTimer=setTimeout(()=>void flushProjectSave().catch(()=>{}),1000);}
 return state.projectCloud;
}
async function flushProjectSave(){
 if(projectIsTemporary())return ensureProjectIdentity();
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
 if(!projectAccountCanAccess(loaded.projectCloud))throw new Error('This project belongs to another account. Sign in to that account to open it.');
 projectLoading=true;clearTimeout(projectBackupTimer);clearTimeout(projectSyncTimer);projectEpoch++;projectGeneration=0;
 try{cancelTranscriptionQueue();if(typeof cancelBoardImports==='function')cancelBoardImports();state=loaded;selected=null;selectedMark=null;selectedEdge=null;pending=null;action=null;if(!keepHistory){history=[];future=[];}projectPending=pendingSave;projectConflict=false;projectLocalOK=false;dirty=pendingSave;consoleRefreshProject();resumeYouTubeImports();scheduleTranscriptionQueue();R.clearImageCache();renderAll();updateRefreshNotice();ensureProjectIdentity();projectRemember(state.projectCloud,state.title);}
 finally{projectLoading=false;}
 await projectBackup();projectStatus(pendingSave?'Restored this device’s changes · waiting for FUPCJ Server':'Project restored','local');
}
async function projectReconcile({force=false}={}){
 if(projectIsTemporary())return;
 const meta=validProjectIdentity(state.projectCloud);if(!meta||!meta.backendUrl)return;const epoch=projectEpoch,generation=projectGeneration;
 try{await projectCapabilities();const remote=await projectReadRemote(meta);if(epoch!==projectEpoch||generation!==projectGeneration)return;
  if(!Number.isSafeInteger(remote.revision)||remote.revision<1||!remote.project)throw new Error('The saved FUPCJ Server project is incomplete.');
  if(remote.revision!==meta.revision){if(projectPending&&!force){projectConflict=true;projectStatus('FUPCJ Server copy changed · choose which copy to keep','conflict');return;}
   const authoritative={...remote.project,projectCloud:{...meta,revision:remote.revision}};await projectApply(authoritative);projectStatus('Loaded latest FUPCJ Server copy','saved');
  }else if(projectPending){await flushProjectSave();}else{projectStatus('Saved to FUPCJ Server','saved');consoleRefreshProject();}
 }catch(error){if(epoch!==projectEpoch)return;if(error.status===404&&meta.revision===0){if(projectPending)await flushProjectSave().catch(()=>{});return;}projectReportFailure(error);}
}
async function projectOpenRecent(meta){
 if(ioBusy||busy)return;if(dirty&&!confirm('Open another project? Your current copy stays in Projects on this device.'))return;
 await projectBackup();const epoch=projectEpoch,generation=projectGeneration;projectStatus('Opening project…','saving');
 try{const record=await projectStoreGet(meta.id);if(epoch!==projectEpoch||generation!==projectGeneration)return;
  if(record?.project){await projectApply({...record.project,projectCloud:{...validProjectIdentity(record.project.projectCloud)}},{pendingSave:record.pending===true});}
  else{if(meta.backendUrl!==cloudConfig?.backendUrl)throw new Error('Connect this project’s original FUPCJ Server to open it.');await projectCapabilities();const headers={'X-Vision-Project-Key':meta.key,'Accept':'application/json'},remote=await projectResponse(await cloudFetch('/projects/'+meta.id,{headers}));if(epoch!==projectEpoch||generation!==projectGeneration)return;await projectApply({...remote.project,projectCloud:{...meta,revision:remote.revision}});}
  $('projectsDialog').close();await projectReconcile();
 }catch(error){projectReportFailure(error);}
}
async function projectFork(){
 if(busy||ioBusy)return;clearTimeout(projectSyncTimer);clearTimeout(projectBackupTimer);await projectBackup();projectEpoch++;delete state.projectCloud;ensureProjectIdentity();projectConflict=false;projectPending=true;projectGeneration++;history=history.map(item=>({...item,projectCloud:{...state.projectCloud}}));future=future.map(item=>({...item,projectCloud:{...state.projectCloud}}));
 // A new board copy has a new conversation; previous chats stay with the original project.
 state.consoleSession=null;consoleRefreshProject();projectQueueSave();renderProjectMenu();await flushProjectSave().catch(()=>{});
}
function renderProjectMenu(){
 renderAccountProjectList();
 $('projectSyncStatus').textContent=projectMessage;$('projectConflictActions').hidden=!projectConflict;const list=$('projectRecentList');list.replaceChildren();
 for(const entry of projectRecentList()){const button=document.createElement('button');button.className='project-recent';const name=document.createElement('strong'),detail=document.createElement('small');name.textContent=entry.title;detail.textContent=(entry.id===state.projectCloud?.id?'Current · ':'')+projectDateText(entry.updatedAt);button.append(name,detail);button.onclick=()=>void projectOpenRecent(entry);list.appendChild(button);}
 if(!list.children.length){const empty=document.createElement('p');empty.className='mini-note';empty.textContent='Your recent projects will appear here after you start editing. Each editable project file also carries its connection to FUPCJ Server copy.';list.appendChild(empty);}
}
function openProjectsMenu(){if(projectIsTemporary()){openAccountDialog('Sign in to save projects. Temporary trial work will be cleared when you sign in.');return;}renderProjectMenu();if(!$('projectsDialog').open)$('projectsDialog').showModal();void projectRefreshAccountList();}
async function projectSwitchAccountScope(scope){
 if(scope===projectStorageScope)return;
 projectAccountSwitching=true;projectAccountList=[];projectAccountListMessage='';projectAccountListEpoch++;
 clearTimeout(projectSyncTimer);clearTimeout(projectBackupTimer);
 try{
  if(typeof cancelBoardCapture==='function')cancelBoardCapture();
  if(typeof cancelBoardImports==='function')cancelBoardImports();
  if(typeof documentWorkers!=='undefined'){for(const r of documentWorkers.values())r.controller.abort();documentWorkers.clear();}
  await projectBackup();projectEpoch++;projectGeneration=0;projectHealthCache=null;
  cancelTranscriptionQueue();projectStorageScope=scope;projectRecentMemory=[];
  state={title:'Untitled timeline',mainPrompt:DEFAULT_PROMPT,nodes:[],edges:[],settings:defaults(),view:{x:120,y:90,scale:1}};
  selected=null;selectedMark=null;selectedEdge=null;pending=null;action=null;history=[];future=[];dirty=false;ioBusy=false;
  projectPending=false;projectConflict=false;projectLocalOK=false;
  consoleRefreshProject();resumeYouTubeImports();R.clearImageCache();renderAll();updateRefreshNotice();
  const epoch=projectEpoch,active=await projectStoreGet('__active__');
  if(epoch!==projectEpoch)return;
  if(active?.recents)projectRecentMemory=active.recents.filter(r=>validProjectIdentity(r)&&projectAccountCanAccess(r));
  const id=projectLocalGet(PROJECT_ACTIVE)??active?.activeId;
  const record=id?await projectStoreGet(id):null;
  if(record?.project&&epoch===projectEpoch&&projectAccountCanAccess(record.project.projectCloud))await projectApply(record.project,{pendingSave:record.pending===true});
  else projectStatus(projectAccountUID()?'Signed in · open a saved project or create a new one':'Local workspace · sign in to open account projects');
 }finally{projectAccountSwitching=false;renderProjectMenu();}
}
function renderAccountProjectList(){
 const list=$('accountProjectList'),status=$('accountProjectStatus');if(!list||!status)return;
 const signedIn=!!projectAccountUID();list.replaceChildren();
 status.textContent=projectAccountListMessage||(signedIn?'Your projects are saved on FUPCJ Server.':'Sign in to see the same projects on every device.');
 $('accountProjectRefresh').hidden=!signedIn;$('projectAccountClaim').hidden=!projectNeedsAccountClaim();$('projectDeviceImport').hidden=!signedIn;
 for(const entry of signedIn?projectAccountList:[]){const button=document.createElement('button');button.className='project-recent';const name=document.createElement('strong'),detail=document.createElement('small');name.textContent=entry.title||'Untitled project';detail.textContent=(entry.id===state.projectCloud?.id?'Current · ':'')+projectDateText(entry.updatedAt);button.append(name,detail);button.onclick=()=>void projectOpenAccount(entry);list.appendChild(button);}
 const deviceList=$('projectDeviceList');deviceList.replaceChildren();
 if(signedIn){let entries=[];try{entries=JSON.parse(projectLocalGet(PROJECT_RECENT,'')||'[]');}catch{}
  for(const entry of entries.filter(r=>validProjectIdentity(r)&&!r.ownerUid)){const button=document.createElement('button');button.className='project-recent';button.textContent=entry.title||'Untitled project';button.onclick=()=>void projectOpenDeviceImport(entry);deviceList.appendChild(button);}
  if(!deviceList.children.length){const note=document.createElement('p');note.className='mini-note';note.textContent='No earlier device projects found. Use Open project file to import a downloaded project.';deviceList.appendChild(note);}
 }
}
async function projectRefreshAccountList(){
 const uid=projectAccountUID(),epoch=++projectAccountListEpoch;if(!uid){projectAccountList=[];projectAccountListMessage='';renderAccountProjectList();return;}
 projectAccountListMessage='Loading saved projects…';renderAccountProjectList();
 try{const health=await projectCapabilities();if(!health.capabilities?.accountProjects){const error=new Error('Update FUPCJ Server to enable Google account projects.');error.code='VISION_PC_UPDATE_REQUIRED';throw error;}
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);let data;
  try{data=await projectResponse(await cloudFetch('/projects',{signal:controller.signal}));}finally{clearTimeout(timer);}
  if(epoch!==projectAccountListEpoch||uid!==projectAccountUID())return;
  projectAccountList=(Array.isArray(data.projects)?data.projects:[]).filter(r=>r&&/^[-\w]{16,120}$/.test(r.id||'')).slice(0,500);
  projectAccountListMessage=projectAccountList.length?'Saved on FUPCJ Server · '+projectAccountList.length+' project'+(projectAccountList.length===1?'':'s'):'No projects saved to this account yet. Create a board or import a previous project.';
 }catch(error){if(epoch!==projectAccountListEpoch||uid!==projectAccountUID())return;projectAccountListMessage=error.name==='AbortError'?'FUPCJ Server is not responding. Keep it online, then refresh.':error.message;if(error.code==='VISION_PC_UPDATE_REQUIRED')projectReportFailure(error);}
 renderAccountProjectList();
}
async function projectOpenAccount(entry){
 if(busy||ioBusy||projectAccountSwitching)return;const uid=projectAccountUID();if(!uid)return;
 if(dirty&&!confirm('Open this saved project? Your current changes stay in this account’s device recovery copy.'))return;
 await projectBackup();const epoch=projectEpoch,generation=projectGeneration;
 try{const cached=await projectStoreGet(entry.id);if(epoch!==projectEpoch||generation!==projectGeneration||uid!==projectAccountUID())return;
  if(cached?.pending&&cached.project?.projectCloud?.ownerUid===uid){await projectApply(cached.project,{pendingSave:true});await projectReconcile();}
  else{const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),120000);let remote;
   try{remote=await projectResponse(await cloudFetch('/projects/'+entry.id,{signal:controller.signal}));}finally{clearTimeout(timer);}
   if(epoch!==projectEpoch||generation!==projectGeneration||uid!==projectAccountUID())return;
   const existing=validProjectIdentity(remote.project?.projectCloud);
   const meta={id:entry.id,key:existing?.key||projectRandom(32),backendUrl:cloudConfig.backendUrl,revision:remote.revision,ownerUid:uid};
   if(!validProjectIdentity(meta)||!remote.project)throw new Error('FUPCJ Server returned an incomplete project.');
   await projectApply({...remote.project,projectCloud:meta});projectStatus('Loaded your saved FUPCJ Server project','saved');
  }
  $('projectsDialog').close();
 }catch(error){if(uid===projectAccountUID())projectReportFailure(error);}
}
async function projectOpenDeviceImport(entry){
 if(busy||ioBusy||!projectAccountUID())return;
 if(dirty&&!confirm('Open this device project? Your current account project stays in its recovery copy.'))return;
 await projectBackup();const epoch=projectEpoch,uid=projectAccountUID();
 try{const record=await projectStoreGet(entry.id,'');if(epoch!==projectEpoch||uid!==projectAccountUID())return;if(!record?.project)throw new Error('Open the downloaded project file to import this project.');
  await projectApply(record.project,{pendingSave:record.pending===true});projectStatus('Device project opened · choose Add this project to my account');renderProjectMenu();
 }catch(error){projectReportFailure(error);}
}
async function projectClaimAccount(){
 if(busy||ioBusy||!projectNeedsAccountClaim())return;const meta={...state.projectCloud},uid=projectAccountUID(),epoch=projectEpoch;
 if((state.youtubeImports||[]).some(job=>job.status!=='failed')){projectStatus('Finish any active YouTube imports before adding this project to your account.','error');return;}
 if(!confirm('Add “'+state.title+'” to this Google account? Its FUPCJ Server copy and conversations will belong to this account.'))return;
 try{await projectCapabilities();if(epoch!==projectEpoch||uid!==projectAccountUID())return;
  if(meta.backendUrl&&meta.backendUrl!==cloudConfig.backendUrl)throw new Error('Connect this project’s original FUPCJ Server before adding it to your account.');
  let claimedRevision=meta.revision;
  if(meta.revision>0){const claimed=await projectResponse(await cloudFetch('/projects/'+meta.id+'/claim',{method:'POST',headers:{'X-Vision-Project-Key':meta.key}}));if(epoch!==projectEpoch||uid!==projectAccountUID())return;claimedRevision=claimed.revision;}
  state.projectCloud={...meta,backendUrl:cloudConfig.backendUrl,ownerUid:uid};projectConflict=false;projectGeneration++;
  // Claiming ownership must not authorize overwriting a newer remote revision.
  if(claimedRevision!==meta.revision){await projectBackup();await projectReconcile();}
  else{projectPending=true;await projectBackup();await flushProjectSave();}
  await projectRefreshAccountList();renderProjectMenu();
 }catch(error){projectReportFailure(error);}
}
const projectDialog=document.createElement('dialog');projectDialog.id='projectsDialog';projectDialog.innerHTML='<div class="row spread"><h2>Projects</h2><button id="closeProjects" class="dialog-close" aria-label="Close projects">×</button></div><p id="projectSyncStatus" role="status" class="project-sync-status"></p><div id="projectConflictActions" hidden><p>FUPCJ Server copy changed elsewhere. Your local changes are still here. Load FUPCJ Server copy, or keep this board as a separate project.</p><button id="projectLoadPC">Load FUPCJ Server copy</button><button id="projectKeepLocal">Save as new project</button></div><details id="projectUpdateHelp" hidden><summary>Update FUPCJ Server</summary><p>On FUPCJ Server, open PowerShell as Administrator and run:</p><pre><code id="projectUpdateCommand">$VisionSetup = Join-Path $env:TEMP \'Setup-Vision-PC.ps1\'\nInvoke-WebRequest -UseBasicParsing \'https://raw.githubusercontent.com/JRDN-R/vision/main/vision-pc/Setup-Vision-PC.ps1\' -OutFile $VisionSetup\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File $VisionSetup -Action Update</code></pre><p>Then use Retry FUPCJ Server save below.</p></details><div class="project-actions"><button id="projectOpenFile">Open project file</button><button id="projectDownload">Download project</button><button id="projectSyncNow">Retry FUPCJ Server save</button><button id="projectSaveNew">Save as new project</button></div><section id="accountProjectSection"><div class="row spread"><h3>My saved projects</h3><button id="accountProjectRefresh" class="ghost" type="button">Refresh</button></div><p id="accountProjectStatus" class="mini-note" role="status"></p><div id="accountProjectList"></div><button id="projectAccountClaim" class="full" type="button" hidden>Add this project to my account</button><details id="projectDeviceImport"><summary>Import a previous project from this device</summary><div id="projectDeviceList"></div></details></section><h3>Recent projects on this device</h3><div id="projectRecentList"></div><p class="mini-note">FUPCJ Server keeps your editable project and background conversations. This browser keeps a recovery copy when storage is available. Sign in with the same Google account on another device to open your saved projects. Keep FUPCJ Server awake and online for processing.</p>';
document.body.appendChild(projectDialog);
$('accountProjectRefresh').onclick=()=>void projectRefreshAccountList();$('projectAccountClaim').onclick=()=>void projectClaimAccount();
$('closeProjects').onclick=()=>projectDialog.close();$('projectOpenFile').onclick=()=>{projectDialog.close();$('projectInput').click();};$('projectDownload').onclick=()=>saveProject();$('projectSyncNow').onclick=async()=>{projectHealthCache=null;try{await ensureRemoteProject();}catch{}renderProjectMenu();};$('projectSaveNew').onclick=$('projectKeepLocal').onclick=()=>void projectFork();$('projectLoadPC').onclick=async()=>{if(!confirm('Load the latest FUPCJ Server copy and replace this board? Download this local copy first if you want to keep both.'))return;await projectReconcile({force:true});renderProjectMenu();};
$('openBtn').textContent='Projects';$('openBtn').onclick=openProjectsMenu;$('saveState').setAttribute('role','button');$('saveState').setAttribute('tabindex','0');$('saveState').onclick=openProjectsMenu;$('saveState').onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();openProjectsMenu();}};
const projectOriginalSave=saveProject;
saveProject=function(){if(projectIsTemporary()){openProjectsMenu();return;}if(!busy&&!ioBusy)ensureProjectIdentity();projectOriginalSave();if(state.projectCloud){void projectBackup();if(projectPending)void flushProjectSave().catch(()=>{});}};
$('saveBtn').onclick=saveProject;
const projectOriginalOpen=openProject;
openProject=async function(file){if(projectIsTemporary()){openAccountDialog('Sign in to open saved projects. You can still add files and videos to the trial board.');return;}const oldState=state;await projectBackup();await projectOriginalOpen(file);if(state!==oldState){if(typeof cancelBoardImports==='function')cancelBoardImports();projectEpoch++;projectGeneration=0;projectPending=true;projectConflict=false;projectLocalOK=false;ensureProjectIdentity();await projectBackup();await projectReconcile();}};
const projectOriginalNew=$('newBtn').onclick;
$('newBtn').onclick=async function(event){await projectBackup();const oldState=state;projectOriginalNew(event);if(state!==oldState){if(typeof cancelBoardImports==='function')cancelBoardImports();clearTimeout(projectBackupTimer);clearTimeout(projectSyncTimer);projectEpoch++;projectGeneration=0;projectPending=false;projectConflict=false;projectLocalOK=false;projectLocalSet(PROJECT_ACTIVE,'');void projectStorePut({id:'__active__',activeId:'',recents:projectRecentList()}).catch(()=>{});projectStatus('New project · autosaves after your first change');}};
async function projectRecoverStartup(){
 if(typeof accountReady!=='undefined')await accountReady;
 const epoch=projectEpoch,generation=projectGeneration;
 try{const savedMeta=await projectStoreGet('__active__');if(savedMeta?.recents)projectRecentMemory=savedMeta.recents.filter(r=>validProjectIdentity(r));const id=projectLocalGet(PROJECT_ACTIVE)??savedMeta?.activeId;if(!id)return;const record=await projectStoreGet(id);if(!record?.project||epoch!==projectEpoch||generation!==projectGeneration||state.nodes.length||state.projectCloud)return;await projectApply(record.project,{pendingSave:record.pending===true});await projectReconcile();}catch(error){projectReportFailure(error);}
}
window.addEventListener('online',()=>{if(state.projectCloud){projectHealthCache=null;if(projectPending)void flushProjectSave().catch(()=>{});else void projectReconcile();}});
window.addEventListener('pagehide',()=>{clearTimeout(projectBackupTimer);void projectBackup();});
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden'){clearTimeout(projectBackupTimer);void projectBackup();}else if(state.projectCloud){if(projectPending)void flushProjectSave().catch(()=>{});else void projectReconcile();}});
setInterval(()=>{if(!projectAccountSwitching&&projectPending&&!projectConflict&&navigator.onLine!==false&&!projectSyncPromise)void flushProjectSave().catch(()=>{});},30000);
// Defer until the complete app has installed its renderers and handlers.
Promise.resolve().then(async()=>{await projectRecoverStartup();if(state.projectCloud)void projectReconcile();});
