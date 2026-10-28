/* Paid features are authorized by the server; this file only presents that
 * policy and synchronizes account preferences across old and new projects. */
const upgradeAccount={uid:'',epoch:0,status:'unrequested',preferences:{},pending:{},busy:false,loadedAt:0,sequence:0,queue:Promise.resolve(),admin:false};
function upgradeUID(){return accountSignedIn()?accountFirebase?.currentUser?.uid||'':'';}
function upgradeDefaults(){return {transcriptionProvider:upgradeAccount.status==='approved'?'gemini':'local',frameDescriptionEngine:upgradeAccount.status==='approved'?'auto':'local',describeFrames:true,includeSoundEvents:false};}
function accountProcessingPreference(key,fallback){
 if(!accountProcessingPreference.ready||upgradeAccount.uid!==upgradeUID())return fallback;
 const value={...upgradeDefaults(),...upgradeAccount.preferences,...upgradeAccount.pending}[key];
 if(upgradeAccount.status!=='approved'&&['transcriptionProvider','frameDescriptionEngine'].includes(key))return 'local';
 return value===undefined?fallback:value;
}
function upgradeRemember(){try{localStorage.setItem('vision-processing-account-v1:'+upgradeAccount.uid,JSON.stringify({preferences:upgradeAccount.preferences,pending:upgradeAccount.pending}));}catch{}}
function upgradePaint(){
 const approved=upgradeAccount.status==='approved',signed=!!upgradeUID();
 for(const id of ['accountUpgradeButton','ventureUpgradeButton']){
  const button=$(id);if(!button)continue;button.hidden=!signed||approved;
  button.querySelector('span').textContent=upgradeAccount.status==='pending'?'Request awaiting approval':'Account Upgrade';
 }
 $('accountUpgradeStatus').textContent=({approved:'Your account is upgraded.',pending:'Your request is waiting for admin approval.',denied:'Your request was declined. Contact your admin.',revoked:'Your upgrade access was revoked. Contact your admin.'})[upgradeAccount.status]||'Request access to Gemini transcription, Luna screenshot descriptions, and Luna project titles.';
 $('requestAccountUpgrade').hidden=['approved','pending','denied','revoked'].includes(upgradeAccount.status);
 $('manageAccountUpgrades').hidden=!upgradeAccount.admin;
 $('manageVisionMemberships').hidden=!upgradeAccount.admin;
 if(typeof syncTranscriptionProviderUI==='function')syncTranscriptionProviderUI();
}
async function upgradeRefresh(force=false){
 const uid=upgradeUID();
 if(uid!==upgradeAccount.uid){
  Object.assign(upgradeAccount,{uid,status:'unrequested',preferences:{},pending:{},busy:false,loadedAt:0,admin:false});upgradeAccount.sequence++;upgradeAccount.epoch++;
  if(uid)try{const saved=JSON.parse(localStorage.getItem('vision-processing-account-v1:'+uid)||'{}');upgradeAccount.preferences=saved.preferences||{};upgradeAccount.pending=saved.pending||{};}catch{}
  $('accountUpgradeDialog')?.close();upgradePaint();
 }
 if(!uid||!cloudAuth||upgradeAccount.busy||(!force&&Date.now()-upgradeAccount.loadedAt<30000))return;
 const epoch=upgradeAccount.epoch,sequence=upgradeAccount.sequence;upgradeAccount.busy=true;
 try{
  // Account endpoints use Firebase auth directly, without a board identity or key.
  const data=await projectResponse(await cloudFetch('/account/upgrade'));
  if(uid!==upgradeUID()||epoch!==upgradeAccount.epoch)return;
  upgradeAccount.status=data.status;upgradeAccount.admin=data.canAdmin===true;if(sequence===upgradeAccount.sequence)upgradeAccount.preferences=data.preferences||{};upgradeAccount.loadedAt=Date.now();
  upgradeRemember();upgradePaint();
  if(Object.keys(upgradeAccount.pending).length)void saveAccountProcessing({...upgradeAccount.pending}).catch(()=>{});
 }catch{ /* Keep local choices; the server still guards every paid request. */ }
 finally{if(epoch===upgradeAccount.epoch)upgradeAccount.busy=false;}
}
async function saveAccountProcessing(patch){
 const uid=upgradeUID();if(!uid)return;
 if(upgradeAccount.status!=='approved'&&(patch.transcriptionProvider==='gemini'||['auto','luna'].includes(patch.frameDescriptionEngine))){openAccountUpgrade();throw new Error('Account Upgrade approval is required.');}
 Object.assign(upgradeAccount.pending,patch);upgradeRemember();upgradePaint();
 const changes={...upgradeAccount.pending};
 const sequence=++upgradeAccount.sequence;
 const operation=upgradeAccount.queue.catch(()=>{}).then(async()=>{
  if(uid!==upgradeUID())return null;
  return projectResponse(await cloudFetch('/account/processing-preferences',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(changes)}));
 });upgradeAccount.queue=operation.catch(()=>{});
 try{const data=await operation;if(!data||uid!==upgradeUID()||sequence!==upgradeAccount.sequence)return;
  upgradeAccount.preferences=data.preferences;upgradeAccount.pending={};upgradeRemember();upgradePaint();
 }catch(error){if(uid===upgradeUID()&&sequence===upgradeAccount.sequence){
  if(error.status===403){upgradeAccount.pending={};upgradeAccount.loadedAt=0;void upgradeRefresh(true);}
  upgradeRemember();throw error;
 }}
}
function openAccountUpgrade(){navigationCloseMenu(false);upgradePaint();$('accountUpgradeDialog').showModal();void upgradeRefresh(true);}
function installAccountUpgrade(){
 const dialog=document.createElement('dialog');dialog.id='accountUpgradeDialog';dialog.className='workspace-news';
 dialog.innerHTML='<form method="dialog"><div class="row spread"><h2>Account Upgrade</h2><button aria-label="Close account upgrade">×</button></div><p id="accountUpgradeStatus" role="status"></p><p class="mini-note">Once approved, Gemini and Luna are enabled by default across your projects. You can choose Whisper or local descriptions in Settings at any time. Your choices apply to all projects.</p><button type="button" id="requestAccountUpgrade" class="primary">Request upgrade</button></form>';
 document.body.append(dialog);
 const make=id=>{const b=document.createElement('button');b.type='button';b.id=id;b.className='account-upgrade';b.innerHTML=navigationSpark+'<span>Account Upgrade</span>';b.onclick=openAccountUpgrade;return b;};
 $('accountButton').after(make('accountUpgradeButton'));
 $('ventureSidebar').querySelector('.venture-sidebar-top').after(make('ventureUpgradeButton'));
 const admin=document.createElement('a');admin.id='manageAccountUpgrades';admin.className='workspace-news-link';admin.href='https://jrdn-r.github.io/vision-status/?module=gemini';admin.target='_blank';admin.rel='noopener';admin.textContent='Manage account upgrades';admin.hidden=true;$('ventureAccount').append(admin);
 const memberships=admin.cloneNode();memberships.id='manageVisionMemberships';memberships.href='https://jrdn-r.github.io/vision-status/?module=memberships';memberships.textContent='Approve new Vision accounts';$('ventureAccount').append(memberships);
 $('requestAccountUpgrade').onclick=async()=>{
  const uid=upgradeUID(),button=$('requestAccountUpgrade');button.disabled=true;
  try{const data=await projectResponse(await cloudFetch('/account/upgrade',{method:'POST'}));if(uid!==upgradeUID())return;upgradeAccount.status=data.status;upgradePaint();}
  catch(error){if(uid===upgradeUID())$('accountUpgradeStatus').textContent=error.message;}
  finally{button.disabled=false;}
 };
 for(const host of [$('workspaceMenu'),$('ventureSidebar')]){const apps=host.querySelector('.workspace-app-links');if(apps)host.append(apps);}
 const pages=document.createElement('div');pages.className='venture-history-pages';pages.innerHTML='<button type="button" id="ventureHistoryPrevious">Previous</button><span id="ventureHistoryPage"></span><button type="button" id="ventureHistoryNext">Next</button>';$('ventureHistory').after(pages);
 $('ventureHistoryPrevious').onclick=()=>{venture.historyPage=Math.max(0,(venture.historyPage||0)-1);venturePaintHistory();};
 $('ventureHistoryNext').onclick=()=>{venture.historyPage=(venture.historyPage||0)+1;venturePaintHistory();};
 new ResizeObserver(()=>venturePaintHistory()).observe($('ventureHistory'));
 accountProcessingPreference.ready=true;upgradePaint();
}
installAccountUpgrade();
const upgradePriorGate=accountUpdateGate;
accountUpdateGate=function(){upgradePriorGate();void upgradeRefresh();};
const upgradePriorSettings=openProcessingSettings;
openProcessingSettings=function(){upgradePriorSettings();void upgradeRefresh(true);};
setInterval(()=>{if(!document.hidden)void upgradeRefresh();},30000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)void upgradeRefresh(true);});
window.addEventListener('storage',event=>{if(event.key==='vision-processing-account-v1:'+upgradeUID())void upgradeRefresh(true);});
if(typeof accountReady!=='undefined')void accountReady.then(()=>upgradeRefresh(true));
