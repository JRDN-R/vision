/* One account surface for both workspaces. The existing controls, server
 * endpoints, encrypted key storage and account epoch remain authoritative. */
const sharedAccount={uid:'',origin:null,child:null,openingChild:false,profileRequested:'',balancePending:0};
function sharedAccountButtons(){return ['workspaceProfileButton','ventureProfileButton','ventureAvatar'].map($).filter(Boolean);}
function sharedAccountPaint(){
 const signed=accountSignedIn(),user=signed?accountFirebase?.currentUser:null;
 const name=user?.displayName||user?.email?.split('@')[0]||'Your account';
 const initials=name.split(/\s+/).filter(Boolean).map(part=>part[0]).join('').slice(0,2).toUpperCase()||'V';
 for(const id of ['workspaceProfileButton','ventureProfileButton']){
  const button=$(id);if(!button)continue;button.hidden=!signed;
  button.querySelector('.workspace-profile-name').textContent=name;
  button.setAttribute('aria-label','Open profile and API settings for '+name);
  const avatar=button.querySelector('.workspace-profile-avatar');avatar.replaceChildren();avatar.textContent=initials;
  const photo=signed&&(ventureAvatarObjectURL||user?.photoURL);
  if(photo){try{const url=new URL(photo);if(url.protocol==='https:'||(ventureAvatarObjectURL&&url.protocol==='blob:')){
   const image=document.createElement('img');image.alt='';image.referrerPolicy='no-referrer';image.src=url.href;image.onerror=()=>image.remove();avatar.append(image);
  }}catch{}}
 }
 const jump=$('sharedAccountWorkspace');if(jump)jump.textContent=venture.open?'Return to Vision board':'Open Venture';
}
function sharedAccountClear(){
 $('ventureKey').value='';if(typeof setKeyVisibility==='function')setKeyVisibility($('ventureKey'),false);
 $('ventureKey').type='password';$('ventureAvatarInput').value='';
 for(const button of sharedAccountButtons())button.setAttribute('aria-expanded','false');
 $('ventureAccount').hidden=true;
}
function sharedAccountClose(restore=true){
 const dialog=$('sharedAccountDialog');if(!dialog)return;
 const origin=sharedAccount.origin;sharedAccount.origin=null;sharedAccount.child=null;
 for(const id of ['ventureBalanceDialog','cloudDialog'])if($(id)?.open&&dialog.open)$(id).close();
 if(dialog.open)dialog.close();sharedAccountClear();
 if(restore&&accountSignedIn()&&origin?.isConnected&&!origin.closest('[hidden]')&&!origin.closest('[inert]'))origin.focus({preventScroll:true});
}
function sharedAccountOpen(origin=document.activeElement){
 if(!accountSignedIn()){openAccountDialog('Sign in to manage your profile and API connection.');return;}
 ventureIdentity();
 const dialog=$('sharedAccountDialog');if(dialog.open)return;
 sharedAccountClear();
 sharedAccount.origin=origin instanceof HTMLElement?origin:null;
 ventureClosePopover('settings',false);venturePaintAccount();sharedAccountPaint();
 $('ventureAccount').hidden=false;dialog.showModal();
 for(const button of sharedAccountButtons())button.setAttribute('aria-expanded','true');
 $('ventureAccount').focus({preventScroll:true});
 void ventureLoadFunding();void ventureLoadProfile();
}
function sharedAccountSync(){
 const uid=accountSignedIn()?ventureScope():'';
 if(uid!==sharedAccount.uid){
  sharedAccount.uid=uid;sharedAccount.profileRequested='';sharedAccountClose(false);
  $('ventureKeyStatus').textContent='Saved keys stay encrypted on FUPCJ Server. Never enter an Admin key here.';
  $('ventureChooseAvatar').disabled=false;$('ventureSaveKey').disabled=false;
  $('ventureRemoveAvatar').hidden=true;
 }
 sharedAccountPaint();
 // Fetch the saved photo when a menu is first opened, without entering Venture
 // or starting any model request. Explicit profile opening still refreshes it.
 if(uid&&navigationMenuOpen()&&sharedAccount.profileRequested!==uid){sharedAccount.profileRequested=uid;void ventureLoadProfile();}
}
function sharedAccountInstall(){
 const dialog=document.createElement('dialog');dialog.id='sharedAccountDialog';dialog.className='workspace-account-dialog';dialog.setAttribute('aria-labelledby','ventureAccountName');
 const panel=$('ventureAccount');panel.removeAttribute('role');panel.removeAttribute('aria-label');dialog.append(panel);document.body.append(dialog);
 for(const [host,id] of [[$('workspaceMenu'),'workspaceProfileButton'],[$('ventureSidebar'),'ventureProfileButton']]){
  const button=document.createElement('button');button.id=id;button.type='button';button.className='workspace-profile-button';button.hidden=true;
  button.setAttribute('aria-haspopup','dialog');button.setAttribute('aria-controls','sharedAccountDialog');button.setAttribute('aria-expanded','false');
  button.innerHTML='<span class="workspace-profile-avatar" aria-hidden="true"></span><span class="workspace-profile-text"><strong class="workspace-profile-name"></strong><small>Profile &amp; API settings</small></span>'+ventureIcon('dots');
  button.onclick=()=>sharedAccountOpen(button);host.append(button);
 }
 $('ventureAvatar').setAttribute('aria-controls','sharedAccountDialog');
 $('ventureAvatar').onclick=()=>sharedAccountOpen($('ventureAvatar'));
 const accountButton=$('accountButton'),previousAccountClick=accountButton.onclick;
 accountButton.onclick=event=>accountSignedIn()?sharedAccountOpen(accountButton):previousAccountClick?.call(accountButton,event);
 // Preserve the existing workspace preference and switch action, making its
 // destination match the workspace from which the shared profile was opened.
 const jump=[...panel.children].find(el=>el.tagName==='BUTTON'&&el.textContent==='Return to Vision board');
 if(jump){jump.id='sharedAccountWorkspace';jump.onclick=()=>{const next=venture.open?'vision':'venture';sharedAccountClose(false);navigationCloseMenu(false);workspaceSwitch(next);};}
 $('ventureConnection').onclick=()=>{sharedAccount.child=$('cloudDialog');openCloudSettings();};
 for(const id of ['ventureBalanceDialog','cloudDialog'])$(id).addEventListener('close',()=>{
  if(sharedAccount.child!==$(id))return;sharedAccount.child=null;
  if(dialog.open&&accountSignedIn())$(id==='ventureBalanceDialog'?'ventureCalibrate':'ventureConnection').focus({preventScroll:true});
 });
 dialog.addEventListener('cancel',sharedAccountClear);
 dialog.addEventListener('close',()=>{
  // close events are queued: an immediate re-open must not be cleared by the
  // previous close event (for example after a fast account-menu toggle).
  if(dialog.open)return;const origin=sharedAccount.origin;sharedAccount.origin=null;sharedAccount.child=null;sharedAccountClear();
  if(accountSignedIn()&&origin?.isConnected&&!origin.closest('[hidden]')&&!origin.closest('[inert]'))origin.focus({preventScroll:true});
 });
 sharedAccountSync();
}
const sharedAccountPriorToggle=ventureTogglePopover;
ventureTogglePopover=function(kind){
 if(kind!=='account')return sharedAccountPriorToggle(kind);
 // Saving a nested balance form already returns to this open account dialog.
 if(sharedAccount.child||sharedAccount.balancePending)return;
 if($('sharedAccountDialog').open)sharedAccountClose();else sharedAccountOpen();
};
const sharedAccountPriorClose=ventureClosePopover;
ventureClosePopover=function(kind,restore=true){
 if(kind!=='account')return sharedAccountPriorClose(kind,restore);
 if(sharedAccount.openingChild)return;
 sharedAccountClose(restore);
};
const sharedAccountPriorBalance=ventureOpenBalance;
ventureOpenBalance=function(kind='set'){
 const parent=$('sharedAccountDialog').open;
 if(parent){sharedAccount.child=$('ventureBalanceDialog');sharedAccount.openingChild=true;}
 try{return sharedAccountPriorBalance(kind);}finally{sharedAccount.openingChild=false;}
};
const sharedAccountPriorSaveBalance=ventureSaveBalance;
ventureSaveBalance=async function(){
 const fromProfile=$('sharedAccountDialog').open;if(fromProfile)sharedAccount.balancePending++;
 try{return await sharedAccountPriorSaveBalance();}
 finally{if(fromProfile)sharedAccount.balancePending--;}
};
const sharedAccountPriorPaint=venturePaintAccount;
venturePaintAccount=function(){sharedAccountPriorPaint();sharedAccountPaint();};
const sharedAccountPriorNavigation=navigationSync;
navigationSync=function(){sharedAccountPriorNavigation();sharedAccountSync();};
const sharedAccountPriorGate=accountUpdateGate;
accountUpdateGate=function(){sharedAccountPriorGate();sharedAccountSync();};
sharedAccountInstall();
