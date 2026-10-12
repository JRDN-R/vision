/* Account-authenticated, bounded image previews. Never put credentials in img URLs. */
const ventureImageState={cache:new Map(),queue:[],active:0,generation:0,observer:null,controllers:new Set()};
function ventureIsImage(file){return /^image\/(jpeg|png|webp|gif|avif|bmp|tiff|heic|heif)$/i.test(file?.mime||'')||/\.(jpe?g|png|webp|gif|avif|bmp|tiff?|heic|heif)$/i.test(file?.name||'');}
function ventureImagePath(run,index){return venturePath(run.ventureId||run.projectId)+'/uploads/'+encodeURIComponent(run.runId)+'/'+index;}
function ventureImageStrip(run){
 const images=(run.attachments||[]).flatMap((file,index)=>{if(!ventureIsImage(file))return [];const url=ventureImageState.cache.get(ventureImagePath(run,index))?.url;return ['<button class="venture-photo'+(url?' ready':'')+'" data-v-action="image" data-file="'+index+'" aria-label="Open '+escapeHTML(file.name)+'"><img data-venture-thumb="'+index+'"'+(url?' src="'+escapeHTML(url)+'"':'')+' alt="'+escapeHTML(file.name)+'" decoding="async"><span>'+escapeHTML(file.name)+'</span></button>'];});
 return images.length?'<div class="venture-image-row" aria-label="Attached images. Scroll sideways for more.">'+images.join('')+'</div>':'';
}
function ventureImagesReset(){
 const s=ventureImageState;s.generation++;s.observer?.disconnect();s.observer=null;s.queue=[];
 for(const controller of s.controllers)controller.abort();s.controllers.clear();
 for(const value of s.cache.values())if(value.url)URL.revokeObjectURL(value.url);s.cache.clear();
 document.querySelectorAll('.venture-image-viewer').forEach(dialog=>dialog.close());
}
function ventureHydrateImages(){
 const s=ventureImageState;
 if(!s.observer)s.observer=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){s.observer.unobserve(entry.target);s.queue.push(entry.target);}void ventureImagePump();},{root:$('ventureTranscript'),rootMargin:'250px'});
 s.observer.disconnect();
 document.querySelectorAll('#ventureTurns img[data-venture-thumb]:not([src])').forEach(img=>s.observer.observe(img));
}
async function ventureImagePump(){
 const s=ventureImageState;
 while(s.active<2&&s.queue.length){
  const img=s.queue.shift();if(!img.isConnected||img.hasAttribute('src'))continue;
  const article=img.closest('[data-venture-run]'),run=venture.runs.find(r=>r.runId===article?.dataset.ventureRun),index=Number(img.dataset.ventureThumb);
  if(!run||!Number.isInteger(index)||!ventureIsImage(run.attachments?.[index]))continue;
  const key=ventureImagePath(run,index),cached=s.cache.get(key);
  if(cached?.url){s.cache.delete(key);s.cache.set(key,cached);img.src=cached.url;img.closest('button').classList.add('ready');continue;}
  if(cached?.failed||cached?.loading)continue;
  s.cache.set(key,{loading:true});
  const epoch=venture.epoch,generation=s.generation,controller=new AbortController();s.controllers.add(controller);s.active++;
  try{
   const response=await ventureFetch(key+'/preview/thumb',{signal:controller.signal});const blob=await response.blob();
   if(epoch!==venture.epoch||generation!==s.generation)continue;
   const url=URL.createObjectURL(blob);const old=s.cache.get(key);if(old?.url)URL.revokeObjectURL(old.url);s.cache.set(key,{url});
   document.querySelectorAll('#ventureTurns img[data-venture-thumb]').forEach(target=>{if(target.closest('[data-venture-run]')?.dataset.ventureRun===run.runId&&Number(target.dataset.ventureThumb)===index){target.src=url;target.closest('button').classList.add('ready');}});
   while(s.cache.size>48){const first=s.cache.keys().next().value,value=s.cache.get(first);if(value.url)URL.revokeObjectURL(value.url);s.cache.delete(first);}
  }catch(error){if(epoch===venture.epoch&&generation===s.generation){s.cache.set(key,{failed:true});if(img.isConnected)img.closest('button').classList.add('unavailable');}}
  finally{s.controllers.delete(controller);s.active--;}
 }
}
async function ventureOpenImage(run,index){
 const file=run.attachments?.[index];if(!Number.isInteger(index)||!ventureIsImage(file))return;
 const epoch=venture.epoch,path=ventureImagePath(run,index),controller=new AbortController();
 const dialog=document.createElement('dialog');dialog.className='venture-image-viewer';dialog.setAttribute('aria-label','Image viewer');
 dialog.innerHTML='<div class="venture-image-glow"></div><div class="venture-image-toolbar"><button type="button" data-image-close aria-label="Close image" title="Close image">'+ventureIcon('close')+'</button></div><div class="venture-image-stage"><img alt="" draggable="false"></div><div class="venture-image-controls"><button type="button" data-image-out aria-label="Zoom out">−</button><button type="button" data-image-fit aria-label="Fit image">100%</button><button type="button" data-image-in aria-label="Zoom in">+</button><button type="button" data-image-download aria-label="Download original">'+ventureIcon('download')+'</button></div><p class="venture-image-caption" role="status"></p>';
 const image=dialog.querySelector('img'),stage=dialog.querySelector('.venture-image-stage'),caption=dialog.querySelector('.venture-image-caption'),fit=dialog.querySelector('[data-image-fit]');
 image.alt=file.name;caption.textContent='Opening '+file.name+'…';
 let url=null,zoom=1,x=0,y=0,dragged=false,pinch=null;const points=new Map();
 const render=()=>{image.style.transform='translate('+x+'px,'+y+'px) scale('+zoom+')';image.style.cursor=zoom>1?'grab':'zoom-in';fit.textContent=Math.round(zoom*100)+'%';};
 const scale=value=>{zoom=Math.max(1,Math.min(6,value));if(zoom===1){x=0;y=0;}render();};
 dialog.querySelector('[data-image-close]').onclick=()=>dialog.close();
 dialog.querySelector('[data-image-in]').onclick=()=>scale(zoom*1.4);dialog.querySelector('[data-image-out]').onclick=()=>scale(zoom/1.4);fit.onclick=()=>scale(1);
 stage.onclick=event=>{if(event.target===stage&&!dragged)dialog.close();dragged=false;};
 dialog.onclick=event=>{if(event.target===dialog)dialog.close();};
 image.ondblclick=()=>scale(zoom>1?1:2);
 stage.addEventListener('wheel',event=>{event.preventDefault();scale(zoom*Math.exp(-event.deltaY*.002));},{passive:false});
 image.onpointerdown=event=>{dragged=false;points.set(event.pointerId,{x:event.clientX,y:event.clientY});image.setPointerCapture(event.pointerId);};
 image.onpointermove=event=>{const old=points.get(event.pointerId);if(!old)return;points.set(event.pointerId,{x:event.clientX,y:event.clientY});if(points.size===2){const [a,b]=[...points.values()],distance=Math.hypot(a.x-b.x,a.y-b.y);if(pinch)scale(zoom*distance/pinch);pinch=distance;dragged=true;}else if(zoom>1){x+=event.clientX-old.x;y+=event.clientY-old.y;dragged=true;render();}};
 image.onpointerup=image.onpointercancel=event=>{points.delete(event.pointerId);pinch=null;};
 dialog.addEventListener('keydown',event=>{if(event.key==='+'||event.key==='='){event.preventDefault();scale(zoom*1.4);}else if(event.key==='-'){event.preventDefault();scale(zoom/1.4);}else if(event.key==='0'){event.preventDefault();scale(1);}});
 dialog.querySelector('[data-image-download]').onclick=async event=>{const button=event.currentTarget;button.disabled=true;try{const response=await ventureFetch(path,{signal:controller.signal});const blob=await response.blob();if(epoch===venture.epoch&&dialog.open)download(blob,R.safeFilename(file.name));}catch(error){if(dialog.open)caption.textContent=ventureError(error);}finally{button.disabled=false;}};
 dialog.addEventListener('close',()=>{controller.abort();if(url)URL.revokeObjectURL(url);dialog.remove();},{once:true});
 document.body.appendChild(dialog);dialog.showModal();
 try{const response=await ventureFetch(path+'/preview/full',{signal:controller.signal});const blob=await response.blob();if(epoch!==venture.epoch||!dialog.open)return;url=URL.createObjectURL(blob);image.src=url;dialog.querySelector('.venture-image-glow').style.backgroundImage='url("'+url+'")';caption.textContent=file.name;}
 catch(error){if(dialog.open)caption.textContent=ventureError(error);}
}
