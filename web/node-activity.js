// Activity belongs to its source node. Project-wide work stays in the top-left
// workspace control; it never covers the bottom-right add-node button.
function nodeActivityItems(activity){
 const items=[],owner=sourceId=>state.nodes.find(n=>(n.attachments||[]).some(a=>a.id===sourceId))?.id;
 const add=(nodeId,status,title,detail='')=>items.push({nodeId,status,title,detail});
 for(const {n,job} of activity.entries)add(n?.id||owner(job.sourceId),job.status,job.sourceName,queueStatusText(job));
 for(const task of activity.active){const job=(state.youtubeImports||[]).find(j=>j.sourceId===task.sourceId);add(task.nodeId||job?.targetId||owner(task.sourceId),job?.status||task.status||'working',task.title,task.detail);}
 for(const n of activity.videos)add(n.id,n.pcVideo.status,n.pcVideo.sourceName||n.title,n.pcVideo.phase||n.pcVideo.error);
 for(const {n,source,job} of activity.documents)add(n.id,job.status,source.name,job.phase||job.error);
 for(const {n,source} of activity.visual)add(n.id,source.visualAnalysis.status,'Visual descriptions',source.visualAnalysis.error||`${source.visualAnalysis.completed||0} / ${source.visualAnalysis.count||0} frames`);
 for(const run of activity.runs)add(null,run.status,run.ventureActivity?'Venture':'Vision session',run.phase);
 const naming=state.settings?.autoNaming;
 if(state.settings?.autoNameProject&&naming&&!['complete','cancelled'].includes(naming.status))add(naming.nodeId,naming.status,'Naming project',naming.error||'Reading project overview');
 return items;
}
function activityItemState(item){
 if(['error','failed','paused'].includes(item.status))return 'error';
 if(['queued','waiting','approval_waiting','collecting','disconnected','auth','awaiting'].includes(item.status)||/^(waiting|queued|connect to resume)/i.test(item.detail||''))return 'awaiting';
 return 'working';
}
function renderNodeActivities(activity){
 const items=nodeActivityItems(activity);
 for(const n of state.nodes){
  const el=$('node-'+n.id);if(!el)continue;
  const tasks=items.filter(item=>item.nodeId===n.id);let badge=el.querySelector('.node-activity');
  if(!tasks.length){badge?.remove();continue;}
  if(!badge){badge=document.createElement('button');badge.type='button';badge.className='queue-indicator node-activity';badge.innerHTML='<span></span>';badge.onclick=event=>{event.stopPropagation();openActivity(n.id);};for(const type of ['pointerdown','touchstart','wheel'])badge.addEventListener(type,event=>event.stopPropagation());el.appendChild(badge);}
  const states=tasks.map(activityItemState),status=states.includes('working')?'working':states.includes('error')?'error':'awaiting';
  const label=status==='working'?`${tasks.length} ${tasks.length===1?'Activity':'Activities'}`:status==='error'?'Needs attention':`${tasks.length} Awaiting`;
  badge.dataset.state=status;badge.querySelector('span').textContent=label;
  badge.title=tasks.map(item=>item.title+': '+(item.detail||item.status)).join('\n');
  badge.setAttribute('aria-label',n.title+': '+label+'. Open activity details');
 }
 // Board tasks have their own indicators. Unowned board work retains the
 // global entry point; Venture has already rendered only its own work.
 const indicator=$('queueIndicator');
 if(indicator&&document.documentElement.dataset.workspacePage!=='venture'){
  const unowned=items.filter(item=>!item.nodeId||!state.nodes.some(n=>n.id===item.nodeId));
  indicator.hidden=!unowned.length;
  if(unowned.length)indicator.querySelector('span').textContent=String(unowned.length);
 }
 return items;
}
