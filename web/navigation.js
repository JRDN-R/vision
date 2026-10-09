/* Shared navigation, light dismissal, and account-scoped release receipts.
 * Uses the original action buttons so project/auth/export handlers stay intact.
 * No billing, provider credentials, or project content enters release receipts.
 */
const NAVIGATION_RELEASE = /* VISION_RELEASE_NOTES */ {};
const navigation = {menu:false, uid:'', newsTimer:null, receipts:new Set(), dialogPress:null};
const navigationSpark = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4Z"/><path d="M20 2v4m-2-2h4"/></svg>';
function navigationBrand(name){return '<span class="workspace-brand-head" aria-hidden="true">'+ventureBrandIcon()+'</span><span class="workspace-brand-name">'+name+'</span>';}
function navigationVersion(){return document.querySelector('meta[name="vision-version"]')?.content||'';}
function navigationReceipt(){return 'vision-release-seen-v1:'+encodeURIComponent(ventureScope())+':'+navigationVersion();}
function navigationSeen(){const key=navigationReceipt();if(navigation.receipts.has(key))return true;try{return localStorage.getItem(key)==='seen';}catch{return false;}}
function navigationMarkSeen(){const key=navigationReceipt();navigation.receipts.add(key);try{localStorage.setItem(key,'seen');}catch{ /* Private/full storage: retain a receipt for this session. */ }}
function navigationNews(){
 const dialog=$('workspaceNews');if(!dialog||!accountSignedIn())return;
 if(!dialog.open)dialog.showModal();
 navigationMarkSeen();
}
function navigationCheckNews(){
 clearTimeout(navigation.newsTimer);
 if(!accountSignedIn()||!navigationVersion()||navigationSeen())return;
 navigation.newsTimer=setTimeout(()=>{
  if(accountSignedIn()&&!navigationSeen()&&!document.hidden&&!document.querySelector('dialog[open]')&&!navigationMenuOpen())navigationNews();
 },300);
}
function navigationNewsButton(id,compact=false){
 const button=document.createElement('button');button.type='button';button.id=id;
 button.className=compact?'workspace-news-icon':'workspace-news-link';
 button.setAttribute('aria-label','What’s new in Vision '+navigationVersion());button.title='What’s new';
 button.innerHTML=navigationSpark+(compact?'':'<span>What’s new</span>');
 button.onclick=event=>{event.stopPropagation();navigationNews();};return button;
}
function navigationMenuOpen(){return venture.open?$('visionVenture').classList.contains('history-open'):navigation.menu;}
function navigationFooterActions(newsId,signOutId){
 const row=document.createElement('div');row.className='workspace-footer-actions';
 const button=document.createElement('button');button.id=signOutId;button.type='button';button.className='workspace-sign-out';button.title='Sign out';button.setAttribute('aria-label','Sign out');button.setAttribute('aria-haspopup','dialog');button.setAttribute('aria-controls','workspaceSignOutDialog');
 button.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 4H4v16h6M9 12h12m-4-4 4 4-4 4"/></svg>';
 button.onclick=()=>{if(!accountSignedIn()||accountBusy)return;$('workspaceSignOutDialog').showModal();$('workspaceSignOutNo').focus();};
 row.append(navigationNewsButton(newsId),button);return row;
}
function navigationSync(){
 const button=$('workspaceMenuButton');if(!button)return;
 const allowed=accountCanUseApp(),open=navigationMenuOpen();
 button.hidden=!allowed;button.inert=!allowed;
 button.setAttribute('aria-expanded',String(open));button.setAttribute('aria-controls',venture.open?'ventureSidebar':'workspaceMenu');
 button.setAttribute('aria-label',open?'Close menu':venture.open?'Open Venture menu':'Open Vision menu');
 button.tabIndex=open?-1:0;navigation.motion?.setOpen(open);
 document.body.classList.toggle('workspace-menu-open',open);
 if(!venture.open){
  const app=document.querySelector('main.app');if(app)app.inert=navigation.menu||!allowed;
  appHeader.inert=navigation.menu||!allowed||(mobileQuery.matches&&!document.documentElement.classList.contains('header-open'));
  $('sidebarToggle').inert=navigation.menu||!allowed;headerReveal.inert=navigation.menu||!allowed;
 }
 const uid=accountSignedIn()?ventureScope():'';
 for(const id of ['workspaceMenuSignOut','ventureMenuSignOut']){const control=$(id);if(control){control.hidden=!uid;control.disabled=accountBusy;}}
 if(!uid)$('workspaceSignOutDialog')?.close();
 if(uid!==navigation.uid){navigation.uid=uid;if(!uid){navigationVisionMenu(false,false);$('workspaceNews').close();}else navigationCheckNews();}
}
function navigationVisionMenu(show,restore=true){
 navigation.menu=!!show;const menu=$('workspaceMenu');menu.hidden=!show;menu.inert=!show;$('workspaceMenuScrim').hidden=!show;
 if(show){workspaceNavIcons();menu.focus({preventScroll:true});}
 else if(restore&&!venture.open)$('workspaceMenuButton').focus({preventScroll:true});
}
function navigationCloseMenu(restore=true){
 if(venture.open){ventureSidebar(false);if(restore)$('workspaceMenuButton').focus({preventScroll:true});}
 else navigationVisionMenu(false,restore);
 navigationSync();navigationCheckNews();
}
function navigationToggleMenu(){
 if(venture.open){ventureClosePopover('settings',false);ventureClosePopover('account',false);ventureSidebar(!navigationMenuOpen());}
 else navigationVisionMenu(!navigation.menu);
 navigationSync();
}
function navigationDetailsIcon(){
 const button=$('sidebarToggle');if(!button)return;
 button.setAttribute('aria-label',sidebarOpen?'Hide details':'Show details');
 button.textContent=sidebarOpen?'Details ↓':'Details ↑';
}
function navigationVortexURL(){
 return !['http:','https:'].includes(location.protocol)?'https://jrdn-r.github.io/vision/vortex/':new URL('vortex/',location.href).href;
}
async function navigationOpenVortex(event){
 // A normal link still supports opening Vortex in a separate tab. For a
 // same-tab transition, finish saving the board before leaving its document.
 if(event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
 event.preventDefault();
 if(!accountSignedIn()){openAccountDialog('Sign in to use Vortex.');return;}
 if(busy||ioBusy){toast('Finish the current browser operation before opening Vortex.',true);return;}
 const control=event.currentTarget,uid=ventureScope();
 if(control.getAttribute('aria-busy')==='true')return;
 control.setAttribute('aria-busy','true');
 try{
  await projectBackup();
  if(projectPending||dirty)await flushProjectSave();
  if(!accountSignedIn()||uid!==ventureScope())return;
  location.assign(navigationVortexURL());
 }catch(error){toast(error.message||'Save your current project before opening Vortex.',true);}
 finally{control.removeAttribute('aria-busy');}
}
function navigationVortexLink(id){
 const link=document.createElement('a');link.id=id;link.className='workspace-vortex-link';link.href=navigationVortexURL();
 link.innerHTML='<img class="workspace-vortex-icon" src="vortex_menu_icon.png" alt="" aria-hidden="true" width="20" height="20"><span>Vortex</span>';link.onclick=navigationOpenVortex;return link;
}
function navigationInstall(){
 document.documentElement.classList.add('compact-navigation');
 const button=document.createElement('button');button.id='workspaceMenuButton';button.type='button';button.className='workspace-menu-button';button.hidden=true;button.innerHTML='<span id="workspaceMenuGlyph" aria-hidden="true"></span>';button.onclick=navigationToggleMenu;
 const menu=document.createElement('aside');menu.id='workspaceMenu';menu.className='workspace-menu';menu.hidden=true;menu.inert=true;menu.setAttribute('aria-label','Vision menu');
 menu.tabIndex=-1;
 menu.innerHTML='<div class="workspace-menu-heading workspace-brand">'+navigationBrand('Vision')+'</div><nav class="workspace-menu-actions" aria-label="Project actions"></nav><div class="workspace-menu-footer"><span class="workspace-menu-version"></span></div>';
 menu.querySelector('nav').append(document.querySelector('#appHeader .top-actions'));
 const scrim=document.createElement('button');scrim.type='button';scrim.id='workspaceMenuScrim';scrim.className='workspace-menu-scrim';scrim.hidden=true;scrim.setAttribute('aria-label','Close Vision menu');scrim.tabIndex=-1;scrim.onclick=()=>navigationCloseMenu();
 document.body.append(scrim,menu,button);
 navigation.motion=window.VisionMenuMotion.create($('workspaceMenuGlyph'));
 const ventureHeading=$('ventureSidebar').querySelector('.venture-wordmark');ventureHeading.className='venture-wordmark workspace-brand';ventureHeading.innerHTML=navigationBrand('Venture');$('ventureSidebar').tabIndex=-1;
 const add=$('addNodeButton');add.innerHTML='<svg viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="13"/><path d="M16 9v14M9 16h14"/></svg>';add.setAttribute('aria-label','Add a node');
 // Retain original actions, including controls that only exist in portable builds.
 for(const [id,label,icon] of [['runProjectBtn','Venture','compass'],['downloadAppBtn','Download app','download'],['localUpdateButton','Local updates','retry']]){
  const control=$(id);if(!control)continue;control.innerHTML=ventureIcon(icon)+'<span class="workspace-nav-label">'+label+'</span>';
 }
 const extra=(label,icon,action)=>{const control=document.createElement('button');control.type='button';control.innerHTML=ventureIcon(icon)+'<span>'+label+'</span>';control.onclick=action;menu.querySelector('.top-actions').append(control);};
 extra('Board details','settings',()=>{navigationCloseMenu(false);setSidebar(true);});
 extra('Processing activity','retry',openActivity);
 extra('Help','file',()=>$('helpDialog').showModal());
 menu.querySelector('.top-actions').append(navigationVortexLink('workspaceVortexLink'));
 $('ventureSidebar').querySelector('.venture-sidebar-foot').before(navigationVortexLink('ventureVortexLink'));
 menu.querySelector('.workspace-menu-version').textContent='Vision v'+navigationVersion();
 menu.querySelector('.workspace-menu-footer').append(navigationFooterActions('workspaceMenuNews','workspaceMenuSignOut'));
 menu.addEventListener('click',event=>{if(!event.target.closest('.top-actions button'))return;queueMicrotask(()=>{if(!document.querySelector('dialog[open]'))navigationCloseMenu(false);});});
 const brand=document.querySelector('#appHeader .brand');
 // Processing activity remains in the menu and queue badge; the brand now
 // contains its own release-notes button.
 brand.removeAttribute('role');brand.removeAttribute('tabindex');brand.removeAttribute('aria-label');brand.removeAttribute('title');brand.onclick=null;brand.onkeydown=null;
 const version=brand.querySelector('.vision-version'),row=document.createElement('div');row.className='workspace-header-version';version.before(row);row.append(version,navigationNewsButton('workspaceHeaderNews',true));
 const footer=document.createElement('div');footer.className='workspace-menu-footer';const release=document.createElement('span');release.className='workspace-menu-version';release.textContent='Vision v'+navigationVersion();footer.append(release,navigationFooterActions('ventureMenuNews','ventureMenuSignOut'));$('ventureSidebar').append(footer);
 const signOut=document.createElement('dialog');signOut.id='workspaceSignOutDialog';signOut.className='workspace-sign-out-dialog';signOut.setAttribute('aria-labelledby','workspaceSignOutTitle');signOut.setAttribute('aria-describedby','workspaceSignOutQuestion');
 signOut.innerHTML='<h2 id="workspaceSignOutTitle">Sign out?</h2><p id="workspaceSignOutQuestion">Are you sure?</p><div class="workspace-sign-out-actions"><button id="workspaceSignOutNo" type="button" autofocus>No</button><button id="workspaceSignOutYes" type="button" class="primary">Yes</button></div>';
 document.body.append(signOut);$('workspaceSignOutNo').onclick=()=>signOut.close();
 $('workspaceSignOutYes').onclick=async()=>{if(!signOut.open||accountBusy||!accountSignedIn())return;signOut.close();navigationCloseMenu(false);await accountGoogleSignOut();if(accountSignedIn())openAccountDialog(accountError||'Unable to sign out. Please try again.');};
 const news=document.createElement('dialog');news.id='workspaceNews';news.className='workspace-news';news.setAttribute('aria-labelledby','workspaceNewsTitle');
 const eyebrow=document.createElement('p');eyebrow.className='workspace-news-eyebrow';eyebrow.textContent='WHAT’S NEW · V'+navigationVersion();
 const title=document.createElement('h2');title.id='workspaceNewsTitle';title.textContent=NAVIGATION_RELEASE.title||'What’s new';
 const list=document.createElement('ul');for(const item of NAVIGATION_RELEASE.items||[]){const li=document.createElement('li');li.textContent=item;list.append(li);}
 const done=document.createElement('button');done.type='button';done.className='primary';done.textContent='Got it';done.onclick=()=>news.close();news.append(eyebrow,title,list,done);document.body.append(news);
 news.addEventListener('close',()=>{if(!document.activeElement||document.activeElement===document.body)$('workspaceMenuButton').focus({preventScroll:true});});
 workspaceNavIcons();navigationDetailsIcon();navigationSync();
}
navigationInstall();
const navigationPriorSync=workspaceSyncSwitch;
workspaceSyncSwitch=function(){navigationPriorSync();navigationSync();};
const navigationPriorSidebar=ventureSidebar;
ventureSidebar=function(show){
 const wasOpen=$('visionVenture').classList.contains('history-open');
 navigationPriorSidebar(show);
 // Both sizes use the same overlay behavior and click-outside dismissal.
 $('ventureScrim').hidden=!show;$('visionVenture').querySelector('.venture-main').inert=!!show;
 navigationSync();
 if(show&&!wasOpen)$('ventureSidebar').focus({preventScroll:true});
};
const navigationPriorOpen=ventureOpen;
ventureOpen=function(){navigationVisionMenu(false,false);const result=navigationPriorOpen();ventureSidebar(false);navigationSync();return result;};
const navigationPriorDetails=setSidebar;
setSidebar=function(open){navigationPriorDetails(open);navigationDetailsIcon();};
mobileQuery.addEventListener('change',()=>{navigationDetailsIcon();navigationSync();});
const navigationPriorGate=accountUpdateGate;
accountUpdateGate=function(){navigationPriorGate();queueMicrotask(navigationSync);};
// A backdrop gesture must start AND end outside the same dialog. Dragging a
// slider or selecting text past a dialog edge must never dismiss the dialog.
function navigationOutside(dialog,event){const r=dialog.getBoundingClientRect();return event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom;}
document.addEventListener('pointerdown',event=>{
 const dialog=event.target.closest('dialog[open]');navigation.dialogPress=dialog&&event.target===dialog&&navigationOutside(dialog,event)?{dialog,id:event.pointerId}:null;
 for(const [panelId,buttonId,close] of [
  ['youtubeControlsPanel','youtubeControls',()=>setYouTubeControls(false)],
  ['consoleSettings','consoleSettingsToggle',()=>{ $('consoleSettings').hidden=true;$('consoleSettingsToggle').setAttribute('aria-expanded','false');if(typeof hideVisibleKeys==='function')hideVisibleKeys($('consoleSettings')); }]
 ]){const panel=$(panelId),button=$(buttonId);if(panel&&!panel.hidden&&!panel.contains(event.target)&&!button?.contains(event.target)&&(!dialog||dialog.contains(panel)))close();}
 if(!dialog&&!venture.open&&sidebarOpen&&mobileQuery.matches&&!$('inspectorPanel').contains(event.target)&&!$('sidebarToggle').contains(event.target))setSidebar(false);
},true);
document.addEventListener('pointerup',event=>{
 const press=navigation.dialogPress;navigation.dialogPress=null;
 if(!press||press.id!==event.pointerId||!press.dialog.open||event.target!==press.dialog||!navigationOutside(press.dialog,event))return;
 const dialog=press.dialog;
 // Existing cancel handlers protect active exports and clean up recording/imports.
 if(dialog.dispatchEvent(new Event('cancel',{cancelable:true}))&&dialog.open)dialog.close();
},true);
document.addEventListener('pointercancel',()=>navigation.dialogPress=null,true);
document.addEventListener('keydown',event=>{
 if(document.querySelector('dialog[open]')||!navigationMenuOpen())return;
 if(event.key==='Escape'){event.preventDefault();event.stopPropagation();navigationCloseMenu();return;}
 if(event.key!=='Tab')return;
 const menu=venture.open?$('ventureSidebar'):$('workspaceMenu');
 const focusable=[$('workspaceMenuButton'),...menu.querySelectorAll('button,input,a[href],select,[tabindex="0"]'),$('workspaceSwitchButton')].filter(el=>el&&el.tabIndex>=0&&!el.disabled&&!el.closest('[hidden]')&&el.getClientRects().length);
 const index=focusable.indexOf(document.activeElement),next=index<0?(event.shiftKey?focusable.length-1:0):(index+(event.shiftKey?-1:1)+focusable.length)%focusable.length;
 event.preventDefault();focusable[next]?.focus();
},true);
// Newly created dialogs inherit dismissal automatically; wait to show release
// notes if login has left another dialog open. No timer hides an open menu.
new MutationObserver(records=>{if(records.some(r=>r.target instanceof HTMLDialogElement&&!r.target.open))navigationCheckNews();}).observe(document.body,{subtree:true,attributes:true,attributeFilter:['open']});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)navigationCheckNews();});
if(typeof accountReady!=='undefined')void accountReady.then(()=>{navigationSync();navigationCheckNews();});
