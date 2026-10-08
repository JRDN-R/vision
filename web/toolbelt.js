/* Docked board controls. The rail is outside the board's coordinate system:
 * opening, collapsing, and docking never mutate nodes or a project's view. */
const toolbelt = {uid:null,side:'left',mode:'auto',open:true,armed:false,press:null,hold:null,drag:null,suppressClick:0,animation:0,timer:null,mem:new Map()};
const toolbeltMotion=matchMedia('(prefers-reduced-motion:reduce)');
const TOOLBELT_GEAR='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 3-.6 2.5-2.2 1.3-2.5-.7-2 3.5 1.9 1.8v2.6l-1.9 1.8 2 3.5 2.5-.7 2.2 1.3L9 22h4l.6-2.6 2.2-1.3 2.5.7 2-3.5-1.9-1.8v-2.6l1.9-1.8-2-3.5-2.5.7-2.2-1.3L13 3Z" transform="translate(1 -1) scale(.95)"/><circle cx="12" cy="12" r="3"/></svg>';
const TOOLBELT_GRIP='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h.01M12 5h.01M17 5h.01M7 9h.01M12 9h.01M17 9h.01" stroke-width="3"/><path d="m8 18 4-4 4 4"/></svg>';
const TOOLBELT_PIN='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3h8l-1 7 3 4v2H6v-2l3-4zM12 16v6"/></svg>';
function toolbeltScope(){return accountSignedIn()?ventureScope():'';}
function toolbeltSave(){
 if(!toolbelt.uid)return;const value={side:toolbelt.side,mode:toolbelt.mode};toolbelt.mem.set(toolbelt.uid,value);
 try{localStorage.setItem('vision-toolbelt-v1:'+encodeURIComponent(toolbelt.uid),JSON.stringify(value));}catch{}
}
function toolbeltIdentity(){
 const uid=toolbeltScope();if(uid===toolbelt.uid)return;toolbeltCancelDrag();toolbelt.uid=uid;let saved=toolbelt.mem.get(uid);
 if(uid&&!saved)try{saved=JSON.parse(localStorage.getItem('vision-toolbelt-v1:'+encodeURIComponent(uid))||'null');}catch{}
 toolbelt.mode=saved?.mode==='pinned'?'pinned':'auto';toolbeltDock(saved?.side==='right'?'right':'left',false);toolbeltSetOpen(true,false);toolbelt.armed=false;
}
function toolbeltMeasure(){
 const rail=$('toolbeltRail'),belt=$('toolbelt'),body=$('toolbeltBody');if(!rail||!belt||!body)return;
 const style=getComputedStyle(rail),available=rail.clientHeight-parseFloat(style.paddingTop)-parseFloat(style.paddingBottom);
 belt.style.height=(toolbelt.open?Math.max(50,Math.min(available,body.scrollHeight+56)):50)+'px';
}
function toolbeltStopAnimation(){
 toolbelt.animation++;clearTimeout(toolbelt.timer);
 for(const el of [$('toolbelt'),$('toolbeltBody'),$('toolbeltSlot'),...$('toolbeltSlot').children])el.getAnimations?.().forEach(a=>a.cancel());
}
function toolbeltPaint(){
 const belt=$('toolbelt'),toggle=$('toolbeltToggle'),pin=$('toolbeltPin'),side=$('toolbeltDock');
 belt.dataset.expanded=String(toolbelt.open);belt.dataset.mode=toolbelt.mode;
 toggle.setAttribute('aria-expanded',String(toolbelt.open));
 toggle.setAttribute('aria-label',toolbelt.open?'Board tools. '+(toolbelt.mode==='pinned'?'Pinned open.':'Collapse tools.')+' Hold and drag left or right to dock.':'Open board tools. Hold and drag left or right to dock.');
 toggle.title=toolbelt.open?'Collapse tools · hold and drag to dock':'Open tools · hold and drag to dock';
 pin.setAttribute('aria-pressed',String(toolbelt.mode==='pinned'));pin.setAttribute('aria-label',toolbelt.mode==='pinned'?'Unpin tools and use automatic collapse':'Pin tools open');pin.title=toolbelt.mode==='pinned'?'Pinned open · tap for Auto':'Auto · tap to pin open';
 pin.querySelector('small').textContent=toolbelt.mode==='pinned'?'PINNED':'AUTO';
 side.setAttribute('aria-label','Dock tools on the '+(toolbelt.side==='left'?'right':'left'));side.title=side.getAttribute('aria-label');
 side.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="'+(toolbelt.side==='left'?'M4 12h15m-5-5 5 5-5 5M21 5v14':'M20 12H5m5-5-5 5 5 5M3 5v14')+'"/></svg>';
}
function toolbeltSetOpen(open,animate=true){
 if(!open&&toolbelt.mode==='pinned')return;
 const changed=toolbelt.open!==!!open,body=$('toolbeltBody'),slot=$('toolbeltSlot');toolbeltStopAnimation();toolbelt.open=!!open;toolbelt.armed=false;toolbelt.press=null;
 body.inert=!toolbelt.open;body.setAttribute('aria-hidden',String(!toolbelt.open));toolbeltPaint();toolbeltMeasure();
 slot.innerHTML=toolbelt.open?TOOLBELT_GRIP:TOOLBELT_GEAR;
 if(!changed||!animate||toolbeltMotion.matches)return;
 body.animate(toolbelt.open?[{opacity:0,filter:'blur(5px)',transform:'translateY(-12px) scaleY(.94)'},{opacity:1,filter:'blur(0)',transform:'none'}]:[{opacity:1,filter:'blur(0)',transform:'none'},{opacity:0,filter:'blur(6px)',transform:'translateY(-20px) scaleY(.82)'}],{duration:320,easing:'cubic-bezier(.2,.8,.2,1)'});
 if(toolbelt.open)return;
 const sequence=toolbelt.animation,icons=[...body.querySelectorAll('[data-tool] svg,#addBtn svg,#boardCaptureButton svg,#helpBtn svg,#undoBtn svg,#redoBtn svg')].map(svg=>svg.cloneNode(true));
 // A vertical slot animation with a steady background, never blinking/flashing.
 let index=0;
 const next=()=>{
  if(sequence!==toolbelt.animation||toolbelt.open)return;
  const old=slot.firstElementChild,icon=index<icons.length?icons[index++]:null;
  if(!icon){slot.innerHTML=TOOLBELT_GEAR;return;}
  slot.append(icon);old?.animate([{transform:'translateY(0)',opacity:1},{transform:'translateY(-26px)',opacity:.35}],{duration:110,easing:'ease-in'}).finished.then(()=>old.remove()).catch(()=>{});
  icon.animate([{transform:'translateY(26px)',opacity:.35},{transform:'translateY(0)',opacity:1}],{duration:110,easing:'ease-out'});
  toolbelt.timer=setTimeout(next,120);
 };
 toolbelt.timer=setTimeout(next,100);
}
function toolbeltDock(side,animate=true){
 side=side==='right'?'right':'left';const rail=$('toolbeltRail'),changed=side!==toolbelt.side;
 toolbelt.side=side;rail.dataset.side=side;
 const focused=rail.contains(document.activeElement)?document.activeElement:null;
 if(side==='left'&&rail.nextElementSibling!==board)board.before(rail);
 else if(side==='right'&&rail.previousElementSibling!==board)board.after(rail);
 focused?.focus({preventScroll:true});
 toolbeltPaint();toolbeltSave();
 if(changed&&animate&&!toolbeltMotion.matches)$('toolbelt').animate([{opacity:0,filter:'blur(7px)',transform:'translateX('+(side==='right'?'12':'-12')+'px) scale(.95)'},{opacity:1,filter:'blur(0)',transform:'none'}],{duration:300,easing:'cubic-bezier(.2,.8,.2,1)'});
 requestAnimationFrame(()=>{toolbeltMeasure();updateView();});
}
function toolbeltCancelDrag(){
 clearTimeout(toolbelt.hold);toolbelt.drag=null;const belt=$('toolbelt');if(belt){belt.classList.remove('dock-ready');delete belt.dataset.dropSide;}
}
function toolbeltInstall(){
 const belt=document.querySelector('#board .tools');belt.id='toolbelt';belt.setAttribute('role','toolbar');belt.setAttribute('aria-label','Board tools');belt.setAttribute('aria-orientation','vertical');
 const rail=document.createElement('aside');rail.id='toolbeltRail';rail.setAttribute('aria-label','Docked board tools');
 const body=document.createElement('div');body.id='toolbeltBody';body.className='toolbelt-body';body.append(...belt.childNodes);
 const pin=document.createElement('button');pin.id='toolbeltPin';pin.type='button';pin.className='toolbelt-pin';pin.innerHTML=TOOLBELT_PIN+'<small>AUTO</small>';
 pin.onclick=()=>{toolbelt.mode=toolbelt.mode==='pinned'?'auto':'pinned';toolbelt.armed=false;toolbeltSetOpen(true);toolbeltPaint();toolbeltSave();};
 const dock=document.createElement('button');dock.id='toolbeltDock';dock.type='button';dock.onclick=()=>toolbeltDock(toolbelt.side==='left'?'right':'left');
 const preferences=document.createElement('div');preferences.className='toolbelt-preferences';preferences.append(pin,dock);body.prepend(preferences);
 const toggle=document.createElement('button');toggle.id='toolbeltToggle';toggle.type='button';toggle.setAttribute('aria-controls','toolbeltBody');toggle.setAttribute('aria-describedby','toolbeltHint');toggle.innerHTML='<span id="toolbeltSlot" class="toolbelt-slot"></span>';
 toggle.onclick=()=>{if(Date.now()<toolbelt.suppressClick)return;toolbeltSetOpen(!toolbelt.open);};
 toggle.onkeydown=event=>{if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();toolbeltDock(event.key==='ArrowLeft'?'left':'right');}else if(event.key==='Escape'&&toolbelt.open){event.preventDefault();toolbeltSetOpen(false);}};
 toggle.addEventListener('contextmenu',event=>event.preventDefault());
 toggle.addEventListener('pointerdown',event=>{
  if(event.button!==0)return;toolbelt.suppressClick=0;toolbeltCancelDrag();toolbelt.drag={id:event.pointerId,x:event.clientX,y:event.clientY,held:false,target:null};toggle.setPointerCapture(event.pointerId);
  toolbelt.hold=setTimeout(()=>{if(!toolbelt.drag)return;toolbelt.drag.held=true;belt.classList.add('dock-ready');},280);
 });
 toggle.addEventListener('pointermove',event=>{
  const drag=toolbelt.drag;if(!drag||drag.id!==event.pointerId)return;const dx=event.clientX-drag.x,dy=event.clientY-drag.y;
  if(!drag.held){if(Math.hypot(dx,dy)>10){toolbelt.suppressClick=Date.now()+450;toolbeltCancelDrag();}return;}
  drag.target=Math.abs(dx)>32&&Math.abs(dx)>Math.abs(dy)*.8?(dx>0?'right':'left'):null;
  if(drag.target)belt.dataset.dropSide=drag.target;else delete belt.dataset.dropSide;
 });
 toggle.addEventListener('pointerup',event=>{const drag=toolbelt.drag;if(!drag||drag.id!==event.pointerId)return;
  if(drag.held){toolbelt.suppressClick=Date.now()+450;if(drag.target)toolbeltDock(drag.target);}
  toolbeltCancelDrag();
 });
 toggle.addEventListener('pointercancel',toolbeltCancelDrag);toggle.addEventListener('lostpointercapture',toolbeltCancelDrag);
 const hint=document.createElement('span');hint.id='toolbeltHint';hint.className='toolbelt-sr-only';hint.textContent='Hold the tools button, then drag left or right to dock. Keyboard: left or right arrow. Auto collapses after you choose a tool and use the board. Pin keeps tools open.';
 belt.append(toggle,body,hint);board.before(rail);rail.append(belt);
 body.addEventListener('click',event=>{if(event.target.closest('[data-tool]')&&toolbelt.mode==='auto'&&toolbelt.open)toolbelt.armed=true;});
 board.addEventListener('pointerdown',event=>{
  if(!toolbelt.open||toolbelt.mode!=='auto'||!toolbelt.armed||event.button!==0||event.target.closest('button,input,textarea,select,a,.bottom-bar,#inspectorPanel,#empty,.prompt-editor,.prompt-resize'))return;
  toolbelt.press={id:event.pointerId};
 },true);
 window.addEventListener('pointerup',event=>{if(toolbelt.press?.id!==event.pointerId)return;toolbelt.press=null;queueMicrotask(()=>{if(toolbelt.mode==='auto'&&toolbelt.armed)toolbeltSetOpen(false);});});
 window.addEventListener('pointercancel',()=>{toolbelt.press=null;toolbeltCancelDrag();});
 window.addEventListener('blur',()=>{toolbelt.press=null;toolbeltCancelDrag();});
 new ResizeObserver(toolbeltMeasure).observe(rail);
 toolbeltMotion.addEventListener('change',()=>{toolbeltSetOpen(toolbelt.open,false);toolbeltMeasure();});
 toolbeltIdentity();
}
toolbeltInstall();
const toolbeltPriorNavigationSync=navigationSync;
navigationSync=function(){toolbeltPriorNavigationSync();toolbeltIdentity();};
