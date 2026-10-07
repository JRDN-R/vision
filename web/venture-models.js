/* Friendly model identities. API IDs are retained only in request state.
 * The live catalog is fetched with this account's saved key on the PC.
 */
const VENTURE_MODEL_STARTERS = ['gpt-6-astra','gpt-6.1-sol','gpt-6-sol','gpt-5.6-terra','gpt-6-luna','gpt-5.6-sol','gpt-5.6-luna','gpt-5.5','gpt-5.4','gpt-5.4-mini','gpt-5.4-nano','gpt-5.2','gpt-5.1','gpt-5','gpt-5-mini','gpt-5-nano','gpt-4.1','gpt-4.1-mini','gpt-4.1-nano','gpt-4o','gpt-4o-mini','o3','o3-mini','o4-mini'];
const VENTURE_MODEL_BRANDS = {
 'gpt-6-astra': {name:'Astra',family:'astra',icon:'orbit'},
 'gpt-6.1-sol': {name:'6.1 Sol',family:'sol',icon:'sunny'},
 'gpt-6-sol': {name:'Sol',family:'sol',icon:'sunny'},
 'gpt-5.6-terra': {name:'Terra',family:'terra',icon:'globe'},
 'gpt-6-luna': {name:'Luna',family:'luna',icon:'mode_night'}
};
// Material Symbols Outlined by Google, Apache-2.0. Paths bundled at build time.
const VENTURE_MODEL_SYMBOLS = {"orbit":"<svg xmlns=\"http://www.w3.org/2000/svg\" height=\"24\" viewBox=\"0 -960 960 960\" width=\"24\"><path d=\"M240-100q-58 0-99-41t-41-99q0-58 41-99t99-41q58 0 99 41t41 99q0 22-6.5 42.5T354-159v-27q30 13 62 19.5t64 6.5q134 0 227-93t93-227h80q0 83-31.5 156T763-197q-54 54-127 85.5T480-80q-45 0-88-9.5T309-118q-16 9-33.5 13.5T240-100Zm0-80q25 0 42.5-17.5T300-240q0-25-17.5-42.5T240-300q-25 0-42.5 17.5T180-240q0 25 17.5 42.5T240-180Zm240-160q-58 0-99-41t-41-99q0-58 41-99t99-41q58 0 99 41t41 99q0 58-41 99t-99 41ZM80-480q0-83 31.5-156T197-763q54-54 127-85.5T480-880q45 0 88 9.5t83 28.5q16-9 33.5-13.5T720-860q58 0 99 41t41 99q0 58-41 99t-99 41q-58 0-99-41t-41-99q0-22 6.5-42.5T606-801v27q-30-13-62-19.5t-64-6.5q-134 0-227 93t-93 227H80Zm640-180q25 0 42.5-17.5T780-720q0-25-17.5-42.5T720-780q-25 0-42.5 17.5T660-720q0 25 17.5 42.5T720-660ZM240-240Zm480-480Z\"/></svg>","sunny":"<svg xmlns=\"http://www.w3.org/2000/svg\" height=\"24\" viewBox=\"0 -960 960 960\" width=\"24\"><path d=\"M440-760v-160h80v160h-80Zm266 110-55-55 112-115 56 57-113 113Zm54 210v-80h160v80H760ZM440-40v-160h80v160h-80ZM254-652 140-763l57-56 113 113-56 54Zm508 512L651-255l54-54 114 110-57 59ZM40-440v-80h160v80H40Zm157 300-56-57 112-112 29 27 29 28-114 114Zm283-100q-100 0-170-70t-70-170q0-100 70-170t170-70q100 0 170 70t70 170q0 100-70 170t-170 70Zm0-80q66 0 113-47t47-113q0-66-47-113t-113-47q-66 0-113 47t-47 113q0 66 47 113t113 47Zm0-160Z\"/></svg>","globe":"<svg xmlns=\"http://www.w3.org/2000/svg\" height=\"24\" viewBox=\"0 -960 960 960\" width=\"24\"><path d=\"M480-80q-83 0-156-31.5T197-197q-54-54-85.5-127T80-480q0-83 31.5-156T197-763q54-54 127-85.5T480-880q83 0 156 31.5T763-763q54 54 85.5 127T880-480q0 83-31.5 156T763-197q-54 54-127 85.5T480-80Zm0-80q134 0 227-93t93-227q0-7-.5-14.5T799-507q-5 29-27 48t-52 19h-80q-33 0-56.5-23.5T560-520v-40H400v-80q0-33 23.5-56.5T480-720h40q0-23 12.5-40.5T563-789q-20-5-40.5-8t-42.5-3q-134 0-227 93t-93 227h200q66 0 113 47t47 113v40H400v110q20 5 39.5 7.5T480-160Z\"/></svg>","mode_night":"<svg xmlns=\"http://www.w3.org/2000/svg\" height=\"24\" viewBox=\"0 -960 960 960\" width=\"24\"><path d=\"M380-160q133 0 226.5-93.5T700-480q0-133-93.5-226.5T380-800h-21q-10 0-19 2 57 66 88.5 147.5T460-480q0 89-31.5 170.5T340-162q9 2 19 2h21Zm0 80q-53 0-103.5-13.5T180-134q93-54 146.5-146T380-480q0-108-53.5-200T180-826q46-27 96.5-40.5T380-880q83 0 156 31.5T663-763q54 54 85.5 127T780-480q0 83-31.5 156T663-197q-54 54-127 85.5T380-80Zm80-400Z\"/></svg>"};
function ventureModelIdentity(model, mode='auto') {
 const id=String(model||''),base=id.replace(/-\d{4}-\d{2}-\d{2}$/, ''),brand=VENTURE_MODEL_BRANDS[base];
 let name=brand?.name||id.replace(/^gpt-/,'GPT ').replace(/-/g,' ').replace(/\b(sol|terra|luna|mini|nano|pro|codex)\b/gi,w=>w[0].toUpperCase()+w.slice(1));
 if(mode==='pro'&&!/\bpro\b/i.test(name))name+=' Pro';
 if(brand&&id!==base)name+=' · '+id.slice(-10);
 return {...brand,name,family:brand?.family||'',base};
}
function ventureModelBadge(model,mode='auto') {
 const item=ventureModelIdentity(model,mode),symbol=VENTURE_MODEL_SYMBOLS[item.icon];
 return '<span class="venture-model-identity'+(item.family?' model-'+item.family:'')+'">'+(symbol?'<span class="venture-model-symbol" aria-hidden="true" style="--model-symbol:url(\'data:image/svg+xml,'+encodeURIComponent(symbol).replace(/'/g,'%27')+'\')"></span>':'')+'<span class="venture-model-name">'+escapeHTML(item.name)+'</span></span>';
}
function ventureModelIsAstra(model=venture.settings.model){return ventureModelIdentity(model).family==='astra';}
function ventureModelUsable(id){return /^(?:gpt-[456](?:[.-]|$)|o[134](?:[.-]|$)|chat-latest$|chatgpt-4o-latest$|ft:)/.test(id)&&!/(?:audio|realtime|transcribe|tts|image|search-preview)/.test(id);}
function ventureModelChoices() {
 const ids=venture.models||VENTURE_MODEL_STARTERS;
 const result=[];
 for(const id of ids){result.push({id,mode:'standard'});if(ventureModelIsAstra(id))result.push({id,mode:'pro'});}
 return result;
}
function venturePaintModelPicker() {
 const s=venture.settings,label=ventureModelBadge(s.model,s.runOptions.mode);
 $('ventureModelLabel').innerHTML=label;$('ventureModel').innerHTML=label+'<span aria-hidden="true">⌄</span>';
 $('ventureModel').setAttribute('aria-label','Choose model: '+ventureModelIdentity(s.model,s.runOptions.mode).name);
 $('ventureProRow').hidden=ventureModelIsAstra();$('venturePro').checked=s.runOptions.mode==='pro';
 const list=$('ventureModels');list.replaceChildren();
 const choices=ventureModelChoices(),featured=choices.filter(c=>VENTURE_MODEL_BRANDS[c.id]);
 const addGroup=(title,items)=>{
  if(!items.length)return;
  const group=document.createElement('div');group.className='venture-model-group';group.setAttribute('role','group');group.setAttribute('aria-label',title);
  const heading=document.createElement('div');heading.className='venture-model-group-title';heading.textContent=title;group.appendChild(heading);
  for(const c of items){const option=document.createElement('button');option.type='button';option.className='venture-model-option';option.setAttribute('role','option');option.dataset.model=c.id;option.dataset.mode=c.mode;
   const selected=c.id===s.model&&(!ventureModelIsAstra(c.id)||c.mode===(s.runOptions.mode==='pro'?'pro':'standard'));
   option.setAttribute('aria-selected',String(selected));option.disabled=!ventureModelUsable(c.id);
   option.innerHTML=ventureModelBadge(c.id,ventureModelIsAstra(c.id)?c.mode:'auto')+(option.disabled?'<small>Separate API</small>':'<span class="venture-model-check" aria-hidden="true">'+(selected?'✓':'')+'</span>');group.appendChild(option);
  }list.appendChild(group);
 };
 addGroup('Featured models',featured);
 addGroup(venture.models?'All available models':'Model catalog',choices.filter(c=>!VENTURE_MODEL_BRANDS[c.id]));
 $('ventureModelsStatus').textContent=venture.modelNotice||(venture.models?venture.models.length+' models available. Scroll to browse the full list.':'Connect your API key to load all available models.');
}
async function ventureLoadModels(force=false) {
 if(venture.modelsLoading&&!force)return;const epoch=venture.epoch,loadId=(venture.modelLoadId||0)+1;venture.modelLoadId=loadId;venture.modelsLoading=true;
 try{const data=await ventureJSON('/venture/models'+(force?'?refresh=1':''));if(epoch!==venture.epoch||loadId!==venture.modelLoadId)return;
  venture.models=Array.isArray(data.models)?data.models.map(m=>m.id).filter(id=>typeof id==='string'&&/^[A-Za-z0-9_.:-]{1,100}$/.test(id)):null;
  venture.modelNotice=data.status==='no-key'?'Save your API key to load all available models.':'';
  if(data.status==='no-key')venture.models=null;
 }catch(error){if(epoch!==venture.epoch||loadId!==venture.modelLoadId)return;venture.modelNotice=error.status===404?'Update FUPCJ Server to load your available models.':'Could not refresh models. '+ventureError(error);}
 finally{if(epoch===venture.epoch&&loadId===venture.modelLoadId){venture.modelsLoading=false;venturePaintModelPicker();}}
}
function ventureModelPickerOpen(open) {
 $('ventureModelsPanel').hidden=!open;$('ventureModel').setAttribute('aria-expanded',String(open));ventureSettingsIdle();
 if(open){const selected=$('ventureModels').querySelector('[aria-selected="true"]')||$('ventureModels').querySelector('button:not(:disabled)');selected?.focus({preventScroll:true});selected?.scrollIntoView({block:'nearest'});}
}
function ventureInstallModels() {
 $('ventureModel').onclick=()=>ventureModelPickerOpen($('ventureModelsPanel').hidden);
 $('ventureModels').onclick=event=>{const option=event.target.closest('[data-model]');if(!option||option.disabled)return;
  const wasAstra=ventureModelIsAstra(),isAstra=ventureModelIsAstra(option.dataset.model);
  venture.settings.model=option.dataset.model;
  venture.settings.runOptions.mode=isAstra?option.dataset.mode:wasAstra?'standard':($('venturePro').checked?'pro':'standard');
  venturePaintSettings();ventureModelPickerOpen(false);$('ventureModel').focus();ventureChangedSettings({target:{id:'ventureModel'}});
 };
 $('ventureModels').onkeydown=event=>{const options=Array.from($('ventureModels').querySelectorAll('button:not(:disabled)'));const i=options.indexOf(document.activeElement);let next;
  if(event.key==='ArrowDown')next=(i+1)%options.length;else if(event.key==='ArrowUp')next=(i-1+options.length)%options.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=options.length-1;
  else if(event.key==='Escape'){event.preventDefault();event.stopPropagation();ventureModelPickerOpen(false);$('ventureModel').focus();return;}else return;
  event.preventDefault();options[next]?.focus();
 };
 $('ventureRefreshModels').onclick=()=>void ventureLoadModels(true);
}
