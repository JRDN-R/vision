// Board controls are separate from project/job identity and the run conversation.
function syncBoardAppearance(){
 const mode=['drift','stars','static'].includes(state.settings.boardBackground)?state.settings.boardBackground:'drift';
 const palette=['sage','rose','redshift'].includes(state.settings.boardPalette)?state.settings.boardPalette:'sage';
 board.dataset.background=mode;board.dataset.palette=palette;const picker=$('boardBackground'),colors=$('boardPalette');if(picker)picker.value=mode;if(colors)colors.value=palette;
}
// The left board toolbar owns Undo/Redo; keep a single pair of controls.
function setConnectionConditional(from,to,enabled){
 const edge=state.edges.find(e=>e.from===from&&e.to===to);if(!edge||busy||ioBusy||edgeIsConditional(edge)===enabled)return;
 checkpoint();edge.conditionEnabled=enabled;markDirty();updateSequence();syncModuleInspector();drawCables();
}
const conditionToggle=document.createElement('label');conditionToggle.className='board-condition-toggle';conditionToggle.innerHTML='<input id="branchConditional" type="checkbox"> <span>Use an IF condition</span>';
$('branchCondition').previousElementSibling.before(conditionToggle);
$('branchConditional').onchange=e=>{const edge=state.edges.find(v=>v.from+'|'+v.to===selectedEdge);if(edge)setConnectionConditional(edge.from,edge.to,e.target.checked);};
$('branchCondition').previousElementSibling.textContent='When this is true, follow this path';
$('branchCondition').nextElementSibling.textContent='Without IF, this path always runs alongside other paths. Tap an IF label on the board to turn its condition off.';
function focusBoardNode(id){
 const n=nodeById(id);if(!n)return;pending=null;action=null;selectNode(id);
 const el=$('node-'+id),r=board.getBoundingClientRect(),panel=$('inspectorPanel').getBoundingClientRect();
 const height=mobileQuery.matches&&sidebarOpen?Math.max(120,Math.min(r.height,panel.top-r.top)):r.height;
 const naturalHeight=el?.offsetHeight||300,scale=clamp(Math.min(1,(r.width-90)/cardWidth(n),(height-80)/naturalHeight),.18,1);
 state.view.scale=scale;state.view.x=45+(r.width-90-cardWidth(n)*scale)/2-n.x*scale;state.view.y=(height-naturalHeight*scale)/2-n.y*scale;updateView();
 for(const node of world.querySelectorAll('.node'))node.tabIndex=node===el?0:-1;
 el?.focus({preventScroll:true});
}
function navigateBoardModule(backwards=false){
 const active=document.activeElement;if(active!==board&&!active?.matches('.node'))return false;
 const order=orderedNodes();if(!order.length)return false;const index=order.findIndex(n=>n.id===selected);
 focusBoardNode(order[index<0?(backwards?order.length-1:0):(index+(backwards?-1:1)+order.length)%order.length].id);return true;
}
// Cable handles and IF chips work with both pointer and keyboard input.
$('cables').addEventListener('keydown',e=>{
 if(!['Enter',' '].includes(e.key)||busy||ioBusy)return;const label=e.target.closest('.wire-condition'),end=e.target.closest('.wire-end-hit');if(!label&&!end)return;
 e.preventDefault();e.stopPropagation();if(label){setConnectionConditional(label.dataset.from,label.dataset.to,false);board.focus({preventScroll:true});return;}
 const edge=state.edges.find(v=>v.from===end.dataset.from&&v.to===end.dataset.to);if(!edge)return;const side=end.dataset.side,p=portPoint(nodeById(side==='out'?edge.from:edge.to),side==='out'),r=board.getBoundingClientRect();
 startConnection({nodeId:side==='out'?edge.from:edge.to,side,reconnect:edge},{clientX:r.left+p.x,clientY:r.top+p.y});
});

// Keyboard connector interaction mirrors tapping two ports.
world.addEventListener('keydown',e=>{
 const port=e.target.closest('[data-port]');if(!port||!['Enter',' '].includes(e.key)||busy||ioBusy)return;
 e.preventDefault();e.stopPropagation();const n=nodeById(port.closest('.node').dataset.id);if(!n||port.hidden)return;
 const hit={nodeId:n.id,side:port.dataset.port},p=portPoint(n,hit.side==='out'),r=board.getBoundingClientRect();
 if(pending&&pending.side!==hit.side)finishConnection(hit);else startConnection(hit,{clientX:r.left+p.x,clientY:r.top+p.y});
});

const inspector=$('inspectorPanel');
const backgroundControls=document.createElement('details');
backgroundControls.className='board-background-controls';backgroundControls.innerHTML='<summary>Board background</summary><label for="boardBackground">Appearance</label><select id="boardBackground" class="full"><option value="drift">Slow drifting dots</option><option value="stars">Sparse twinkling stars</option><option value="static">Still dots</option></select><label for="boardPalette">Palette</label><select id="boardPalette" class="full"><option value="sage">Sage</option><option value="rose">Dusty rose</option><option value="redshift">Redshift mix</option></select><p class="mini-note">Saved with this project.</p>';
inspector.appendChild(backgroundControls);
for(const id of ['boardBackground','boardPalette'])$(id).onchange=()=>{checkpoint();state.settings[id]=$(id).value;syncBoardAppearance();markDirty();};syncBoardAppearance();

// Only mobile moves these existing controls into swipable pages. Restoring the
// marker positions leaves the original desktop inspector layout intact.
const inspectorOriginal=[...inspector.children].map(el=>{const marker=document.createComment('inspector position');inspector.insertBefore(marker,el);return{el,marker};});
const sheetHandle=document.createElement('div');sheetHandle.className='board-sheet-handle';sheetHandle.innerHTML='<button type="button" id="boardSheetSize" aria-label="Expand details panel" title="Drag to resize or tap to expand"><span></span></button><button type="button" class="ghost" id="boardSheetClose" aria-label="Hide details panel">×</button>';
const sheetTabs=document.createElement('div');sheetTabs.className='board-sheet-tabs';sheetTabs.setAttribute('role','tablist');sheetTabs.setAttribute('aria-label','Module details sections');
const sheetTrack=document.createElement('div');sheetTrack.className='board-sheet-track';
const sheetGroups=[['details','Details'],['files','Files'],['style','Style'],['paths','Paths'],['prompt','Prompt']].map(([key,label],index)=>{
 const page=document.createElement('section');page.className='board-sheet-page';page.id='boardSheet-'+key;page.setAttribute('role','tabpanel');page.setAttribute('aria-labelledby','boardTab-'+key);
 const tab=document.createElement('button');tab.type='button';tab.id='boardTab-'+key;tab.textContent=label;tab.setAttribute('role','tab');tab.setAttribute('aria-controls',page.id);tab.onclick=()=>showBoardSection(key);sheetTabs.appendChild(tab);sheetTrack.appendChild(page);return{key,label,index,page,tab};
});
let sheetMounted=false,sheetResize=null,sheetIndex=0;
function setBoardTabSelection(i){
 sheetIndex=clamp(i,0,sheetGroups.length-1);for(const g of sheetGroups){g.tab.setAttribute('aria-selected',String(g.index===sheetIndex));g.tab.tabIndex=g.index===sheetIndex?0:-1;}
}
function updateBoardTabs(){setBoardTabSelection(sheetTrack.clientWidth?Math.round(sheetTrack.scrollLeft/sheetTrack.clientWidth):sheetIndex);}
function showBoardSection(key,instant=false){
 if(!sheetMounted)return;const group=sheetGroups.find(g=>g.key===key);if(!group)return;setBoardTabSelection(group.index);
 sheetTrack.scrollTo({left:group.index*sheetTrack.clientWidth,behavior:instant||window.matchMedia('(prefers-reduced-motion:reduce)').matches?'auto':'smooth'});
}
function mountBoardSheet(){
 if(mobileQuery.matches===sheetMounted)return;
 sheetMounted=mobileQuery.matches;
 if(!sheetMounted){for(const {el,marker}of inspectorOriginal){marker.after(el);if(el.classList.contains('sidebar-top-actions'))el.style.display='';}sheetHandle.remove();sheetTabs.remove();sheetTrack.remove();$('sidebarClose').style.display='';inspector.classList.remove('board-sheet');return;}
 inspector.classList.add('board-sheet');let phase='details';
 const put=(key,el)=>sheetGroups.find(g=>g.key===key).page.appendChild(el);
 for(const {el}of inspectorOriginal){
  if(el.classList.contains('sidebar-top-actions')){el.style.display='none';continue;}
  if(el.classList.contains('queue-panel')){put('files',el);continue;}
  if(el.id==='branchEditor'){put('paths',el);continue;}
  if(el.id==='attachmentSection'){put('files',el);phase='style';continue;}
  if(el.tagName==='DETAILS'){
   if(el.contains($('sequenceCount'))){put('paths',el);continue;}
   if(el.contains($('mainPrompt'))){put('prompt',el);continue;}
   if(el.contains($('fontSize'))){put('style',el);continue;}
   put('prompt',el);continue;
  }
  put(phase,el);
 }
 inspector.append(sheetHandle,sheetTabs,sheetTrack);updateBoardTabs();requestAnimationFrame(()=>showBoardSection(sheetGroups[sheetIndex].key,true));
}
sheetTrack.addEventListener('scroll',updateBoardTabs,{passive:true});
sheetTabs.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const i=e.key==='Home'?0:e.key==='End'?sheetGroups.length-1:(sheetIndex+(e.key==='ArrowRight'?1:-1)+sheetGroups.length)%sheetGroups.length;showBoardSection(sheetGroups[i].key);sheetGroups[i].tab.focus();});
$('sidebarClose').setAttribute('aria-label','Hide details panel');
inspector.append(sheetHandle);const sheetSize=$('boardSheetSize'),sheetClose=$('boardSheetClose');sheetHandle.remove();
sheetClose.onclick=()=>setSidebar(false);
function setBoardSheetHeight(height){const viewport=window.innerHeight;document.documentElement.style.setProperty('--board-sheet-height',clamp(height,Math.min(250,viewport*.35),viewport*.6)+'px');}
sheetSize.onclick=()=>{if(sheetSize.dataset.dragged==='true'){sheetSize.dataset.dragged='false';return;}const expanded=inspector.getBoundingClientRect().height>window.innerHeight*.47;setBoardSheetHeight(window.innerHeight*(expanded?.38:.56));sheetSize.setAttribute('aria-label',expanded?'Expand details panel':'Reduce details panel');};
sheetSize.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();sheetResize={id:e.pointerId,y:e.clientY,height:inspector.getBoundingClientRect().height};sheetSize.dataset.dragged='false';sheetSize.setPointerCapture(e.pointerId);});
sheetSize.addEventListener('pointermove',e=>{if(!sheetResize||sheetResize.id!==e.pointerId)return;const diff=sheetResize.y-e.clientY;if(Math.abs(diff)>4)sheetSize.dataset.dragged='true';setBoardSheetHeight(sheetResize.height+diff);});
function endSheetResize(e){if(sheetResize?.id!==e.pointerId)return;sheetResize=null;try{sheetSize.releasePointerCapture(e.pointerId);}catch{}}
sheetSize.addEventListener('pointerup',endSheetResize);sheetSize.addEventListener('pointercancel',endSheetResize);
const boardSetSidebar=setSidebar;setSidebar=function(open){boardSetSidebar(open);if(open&&sheetMounted)requestAnimationFrame(()=>showBoardSection(sheetGroups[sheetIndex].key,true));};
const boardSelectConnection=selectConnection;selectConnection=function(from,to){boardSelectConnection(from,to);showBoardSection('paths');};
const boardChooseAttachments=chooseAttachments;chooseAttachments=function(id){showBoardSection('files');return boardChooseAttachments(id);};
const boardSelectNode=selectNode;selectNode=function(id){boardSelectNode(id);if(id&&sheetMounted&&sheetIndex===3)showBoardSection('details');};
// Focusing a control from a board button reveals its horizontal page first.
inspector.addEventListener('focusin',e=>{if(!sheetMounted)return;const group=sheetGroups.find(g=>g.page.contains(e.target));if(group&&group.index!==sheetIndex)showBoardSection(group.key,true);});
mobileQuery.addEventListener('change',()=>{mountBoardSheet();setSidebar(sidebarOpen);});
window.addEventListener('resize',()=>{if(sheetMounted&&sidebarOpen)requestAnimationFrame(()=>showBoardSection(sheetGroups[sheetIndex].key,true));});
mountBoardSheet();
