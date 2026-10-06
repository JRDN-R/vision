// Project conversations are durable on FUPCJ Server. API credentials stay outside project state.
const CONSOLE_KEY_KEY='vision.console.openai.tab.v1';
const CONSOLE_ACTIVE=new Set(['preparing','queued','in_progress','disconnected']);
const CONSOLE_TERMINAL=new Set(['completed','incomplete','failed','error','cancelled']);
const visionConsoleFilesExpanded=new WeakMap();
let visionConsoleController=null,visionConsoleRendering=0,visionConsolePollTimer=0,visionConsolePolling=false,visionConsoleGeneration=0,visionConsoleConnectionError='',visionConsoleAttachments=[],visionConsoleRecognition=null,visionConsoleRendered='',visionConsoleDraftTimer=0,visionConsoleLastProject='';
function consoleText(v,n){return typeof v==='string'?v.slice(0,n):'';}
function consoleId(v){return typeof v==='string'&&/^[\w-]{1,160}$/.test(v)?v:'';}
function normalizeConsoleArtifact(a){
 if(!a||typeof a!=='object'||(!consoleId(a.id)&&!(consoleId(a.fileId)&&consoleId(a.containerId))))return null;
 return{id:consoleId(a.id),fileId:consoleId(a.fileId),containerId:consoleId(a.containerId),name:consoleText(a.name,250)||'Generated file',mime:consoleText(a.mime,120)||'application/octet-stream',size:Math.max(0,Number(a.size)||0),ready:a.ready!==false,include:!!a.include,...(typeof a.data==='string'&&/^data:[^,]*;base64,[A-Za-z0-9+/]*={0,2}$/.test(a.data)?{data:a.data}:{})};
}
function normalizeRunCitations(raw){return (Array.isArray(raw)?raw:[]).slice(0,100).filter(a=>a&&typeof a.url==='string'&&/^https?:\/\//i.test(a.url)).map(a=>({url:consoleText(a.url,4000),title:consoleText(a.title,300)||consoleText(a.url,300)}));}
function normalizeRunOptions(raw={}){
 const pick=(key,values,fallback)=>values.includes(raw?.[key])?raw[key]:fallback;
 return {mode:pick('mode',['auto','standard','pro'],'auto'),effort:pick('effort',['auto','none','minimal','low','medium','high','xhigh','max'],'auto'),verbosity:pick('verbosity',['auto','low','medium','high'],'auto'),webSearch:raw?.webSearch===true,codeInterpreter:raw?.codeInterpreter!==false};
}
function normalizeConsoleRun(raw){
 if(!raw||typeof raw!=='object')return null;
 return{runOptions:normalizeRunOptions(raw.runOptions),citations:normalizeRunCitations(raw.citations),runId:consoleId(raw.runId),clientRequestId:consoleId(raw.clientRequestId),responseId:consoleId(raw.responseId),previousRunId:consoleId(raw.previousRunId),legacy:!!raw.legacy||(!raw.runId&&!!raw.responseId),sequence:Number.isSafeInteger(raw.sequence)?Math.max(-1,raw.sequence):-1,model:consoleText(raw.model,120)||'gpt-6-astra',message:consoleText(raw.message,100000),projectPrompt:consoleText(raw.projectPrompt,1000000),maxOutputTokens:Math.min(64000,Math.max(1024,Number(raw.maxOutputTokens)||8192)),text:consoleText(raw.text,3000000),status:CONSOLE_ACTIVE.has(raw.status)||CONSOLE_TERMINAL.has(raw.status)?raw.status:'disconnected',phase:consoleText(raw.phase,500)||consoleText(raw.activity?.[raw.activity.length-1],500),error:consoleText(raw.error,3000),createdAt:consoleText(raw.createdAt,50),updatedAt:consoleText(raw.updatedAt,50),notified:!!raw.notified,unread:!!raw.unread,submissionUnknown:!!raw.submissionUnknown,retryable:!!raw.retryable,attachments:(Array.isArray(raw.attachments)?raw.attachments:[]).slice(0,40).map(a=>({name:consoleText(typeof a==='string'?a:a?.name,250),size:Math.max(0,Number(a?.size)||0)})),artifacts:(Array.isArray(raw.artifacts)?raw.artifacts:[]).slice(0,100).map(normalizeConsoleArtifact).filter(Boolean)};
}
function normalizeConsoleSession(raw){
 if(!raw||typeof raw!=='object')return null;
 const runs=(Array.isArray(raw.runs)?raw.runs:[raw]).slice(-500).map(normalizeConsoleRun).filter(Boolean),latest=runs[runs.length-1];
 return{runOptions:normalizeRunOptions(raw.runOptions||latest?.runOptions),version:2,projectId:consoleId(raw.projectId),projectTitle:consoleText(raw.projectTitle,100),draft:consoleText(raw.draft,100000),runs,model:consoleText(raw.model,120)||latest?.model||'gpt-6-astra',maxOutputTokens:Math.min(64000,Math.max(1024,Number(raw.maxOutputTokens)||latest?.maxOutputTokens||8192)),responseId:latest?.responseId||'',status:latest?.status||'completed',text:latest?.text||'',message:latest?.message||'',artifacts:latest?.artifacts||[]};
}
function consoleLatest(session=state.consoleSession){return session?.runs?.[session.runs.length-1]||session||null;}
function consoleSessionActive(s=consoleLatest()){return !!s&&CONSOLE_ACTIVE.has(s.status);}
function consoleIsRunning(){return !!visionConsoleController||!!state.consoleSession?.runs?.some(consoleSessionActive);}
function consolePhase(s){return s?.phase||({preparing:'Preparing project…',queued:'Queued on FUPCJ Server',in_progress:'Working…',completed:'Response ready',incomplete:'Output limit reached',error:'Run failed',failed:'Run failed',cancelled:'Cancelled',disconnected:'Reconnecting…'}[s?.status]||'Ready');}
function consoleConversation(){
 if(!state.consoleSession||state.consoleSession.version!==2)state.consoleSession=normalizeConsoleSession(state.consoleSession)||normalizeConsoleSession({runs:[]});
 return state.consoleSession;
}
function consoleSyncAliases(){const session=state.consoleSession;if(!session)return;const last=consoleLatest(session);if(!last)return;for(const k of ['responseId','status','text','message','artifacts'])session[k]=last[k];}
function consoleChanged(persist=true){consoleSyncAliases();if(persist)markDirty();if(!visionConsoleRendering)visionConsoleRendering=requestAnimationFrame(()=>{visionConsoleRendering=0;renderConsole();});}
function consoleActivity(text,s=consoleLatest()){if(!s||!text)return;s.phase=String(text).slice(0,500);consoleChanged();}
function consoleInlineMarkdown(value,run=null,index=-1){
 let s=escapeHTML(value),codes=[];s=s.replace(/`([^`]+)`/g,(_,v)=>'\u0001'+(codes.push('<code>'+v+'</code>')-1)+'\u0001');
 s=s.replace(/\[([^\]]+)\]\((sandbox:[^\s)]+|\/mnt\/data\/[^\s)]+)\)/g,(_,label,url)=>{
  if(!run||typeof consoleArtifactLink!=='function')return label+' (see Files)';
  return consoleArtifactLink(label,url,run,index);
 });
 s=s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,(_,label,url)=>'<a href="'+url.replace(/"/g,'&quot;')+'" target="_blank" rel="noopener noreferrer">'+label+'</a>');
 s=s.replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/__([^_]+)__/g,'<strong>$1</strong>').replace(/\*([^*\n]+)\*/g,'<em>$1</em>');
 return s.replace(/\u0001(\d+)\u0001/g,(_,i)=>codes[Number(i)]||'');
}
function consoleMarkdown(text,run=null,index=-1){
 const lines=String(text||'').split('\n'),out=[];let code=null,buffer=[],list='';
 const closeList=()=>{if(list){out.push('</'+list+'>');list='';}};
 for(let i=0;i<lines.length;i++){
  const line=lines[i],fence=line.match(/^\s*```(.*)$/);
  if(fence){closeList();if(code!==null){out.push('<pre><code>'+escapeHTML(buffer.join('\n'))+'</code></pre>');code=null;buffer=[];}else code=fence[1];continue;}
  if(code!==null){buffer.push(line);continue;}
  if(/^\s*\|?.+\|.+$/.test(line)&&i+1<lines.length&&/^\s*\|?\s*:?-{3,}:?\s*\|/.test(lines[i+1])){
   closeList();const cells=s=>s.trim().replace(/^\||\|$/g,'').split('|').map(x=>x.trim());out.push('<table><thead><tr>'+cells(line).map(x=>'<th>'+consoleInlineMarkdown(x,run,index)+'</th>').join('')+'</tr></thead><tbody>');i++;
   while(i+1<lines.length&&lines[i+1].includes('|')){i++;out.push('<tr>'+cells(lines[i]).map(x=>'<td>'+consoleInlineMarkdown(x,run,index)+'</td>').join('')+'</tr>');}out.push('</tbody></table>');continue;
  }
  const bullet=line.match(/^\s*([-*]|\d+\.)\s+(.+)$/);if(bullet){const tag=/\d/.test(bullet[1])?'ol':'ul';if(list!==tag){closeList();list=tag;out.push('<'+tag+'>');}out.push('<li>'+consoleInlineMarkdown(bullet[2],run,index)+'</li>');continue;}closeList();
  const heading=line.match(/^(#{1,6})\s+(.+)$/);if(heading){const level=Math.min(3,heading[1].length);out.push('<h'+level+'>'+consoleInlineMarkdown(heading[2],run,index)+'</h'+level+'>');}
  else if(/^\s*([-*_])\1{2,}\s*$/.test(line))out.push('<hr>');else if(/^>\s?/.test(line))out.push('<blockquote>'+consoleInlineMarkdown(line.replace(/^>\s?/,''),run,index)+'</blockquote>');else if(line.trim())out.push('<p>'+consoleInlineMarkdown(line,run,index)+'</p>');
 }
 closeList();if(code!==null)out.push('<pre><code>'+escapeHTML(buffer.join('\n'))+'</code></pre>');return out.join('');
}
// Console UI. The conversation keeps the space; settings and activity stay compact.
function consoleInstallUI(){
 const root=$('visionConsole');root.classList.add('vision-chat');root.innerHTML=`
 <header class="console-header"><button type="button" class="btn" id="consoleBack" aria-label="Return to vision board">← Board</button><div class="console-heading"><strong>Run</strong><small id="consoleProjectTitle"></small></div><button type="button" class="btn console-settings-toggle" id="consoleSettingsToggle" aria-expanded="false" aria-controls="consoleSettings">Settings</button></header>
 <section id="consoleSettings" class="console-chat-settings" hidden aria-label="AI settings"><label>Model<input id="consoleModel" value="gpt-6-astra" autocomplete="off" spellcheck="false"></label><div class="vision-key-field"><label for="consoleKey">OpenAI API key</label><input type="password" id="consoleKey" placeholder="Your API key" autocomplete="off" spellcheck="false" data-key-label="OpenAI API key"></div><label>Output limit<input id="consoleMaxTokens" type="number" min="1024" max="64000" step="1024" value="8192"></label><label class="console-check"><input id="consoleRememberKey" type="checkbox"> Keep key for this tab</label><div id="consoleAccountKey" hidden><span id="accountKeyStatus" role="status"></span><button id="accountSaveKey" class="btn" type="button">Save key</button><button id="accountRemoveKey" class="btn" type="button">Remove saved key</button></div><p>OpenAI API usage is billed to your API account. When signed in, your key saves encrypted on FUPCJ Server and loads on your other devices. Your key is never saved in the project.</p><button class="btn" type="button" id="consoleNotifyYes">Enable reply notifications</button></section>
 <div id="consoleConnection" class="console-connection" hidden role="status"></div>
 <main id="consoleTranscript" class="console-transcript" aria-label="Conversation" tabindex="0"></main>
 <div class="console-current"><span id="consoleStatus" role="status" aria-live="polite">Ready</span><button type="button" class="btn" id="consoleResume" hidden>Reconnect</button><button type="button" class="btn" id="consoleCancel" hidden>Stop</button></div>
 <form class="console-compose" id="consoleCompose"><div id="consoleAttachmentList" class="console-attachment-list"></div><label for="consoleMessage" class="sr-only">Message</label><textarea id="consoleMessage" rows="2" placeholder="Ask about your project or continue the conversation…" maxlength="100000"></textarea><div id="consoleSpeechPreview" class="console-speech-preview" aria-live="polite" hidden></div><div class="console-actions"><input type="file" id="consoleAttachmentInput" multiple hidden><button type="button" class="btn" id="consoleAttach" aria-label="Attach files" title="Attach files">＋ <span>Files</span></button><button type="button" class="btn" id="consoleMic" aria-label="Start dictation" aria-pressed="false" title="Dictate a message">Mic</button><label class="console-board-check"><input type="checkbox" id="consoleIncludeBoard" checked> Include board</label><button type="button" class="btn" id="consoleCopyPrompt">Copy prompt</button><button type="submit" class="btn primary" id="consoleRun">Send</button></div><p id="consoleRunHint" class="console-run-hint">FUPCJ Server keeps accepted runs processing when you leave.</p></form>`;
 const badge=document.createElement('span');badge.id='consoleReplyBadge';badge.className='console-reply-badge';badge.hidden=true;badge.setAttribute('aria-label','Unread replies');$('runProjectBtn').appendChild(badge);
}
function consoleFilesState(){
 const session=state.consoleSession;if(!session)return new Set();
 if(!visionConsoleFilesExpanded.has(session))visionConsoleFilesExpanded.set(session,new Set());
 return visionConsoleFilesExpanded.get(session);
}
function consoleFilesIdentity(run,index){return run.clientRequestId||run.runId||run.responseId||'turn-'+index;}
function consoleTurnHTML(run,index){
 const attachmentText=run.attachments.map(a=>'<span>'+escapeHTML(a.name)+'</span>').join('');
 const files=run.artifacts.map((a,ai)=>'<div class="console-file"><button type="button" class="console-file-name" data-console-action="preview" data-run="'+index+'" data-artifact="'+ai+'" '+(!a.ready?'disabled':'')+'>'+escapeHTML(a.name)+'</button><button type="button" class="btn" data-console-action="download" data-run="'+index+'" data-artifact="'+ai+'" '+(!a.ready?'disabled':'')+'>'+(a.ready?'Download':'Saving…')+'</button><label><input type="checkbox" data-console-action="include" data-run="'+index+'" data-artifact="'+ai+'" '+(a.include?'checked':'')+' '+(!a.ready?'disabled':'')+'> Include in project / ZIP</label></div>').join('');
 const expanded=consoleFilesState().has(consoleFilesIdentity(run,index)),filesId='consoleTurnFiles-'+index;
 const fileControl=files?'<button type="button" class="console-files-toggle" data-console-action="files" data-run="'+index+'" aria-expanded="'+expanded+'" aria-controls="'+filesId+'" aria-label="'+(expanded?'Hide':'Show')+' '+run.artifacts.length+' '+(run.artifacts.length===1?'file':'files')+' from this response"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M7 3h7l4 4v14H7zM14 3v5h4"/></svg>Files <span class="console-file-count">'+run.artifacts.length+'</span></button>':'';
 return '<article class="console-turn" data-turn="'+index+'"><div class="console-user-message"><span class="console-speaker">You</span><div>'+escapeHTML(run.message||(run.projectPrompt?'Run this project.':'Project sent.'))+'</div>'+(attachmentText?'<div class="console-sent-attachments">'+attachmentText+'</div>':'')+'<button type="button" class="console-copy-link" data-console-action="prompt" data-run="'+index+'">Copy prompt</button></div><div class="console-assistant-message"><div class="console-response-heading"><span class="console-speaker">'+escapeHTML(run.model)+'</span>'+fileControl+'</div>'+(files?'<div class="console-turn-files" id="'+filesId+'" role="group" aria-label="Files from this response" '+(expanded?'':'hidden')+'>'+files+'</div>':'')+(run.text?consoleMarkdown(run.text,run,index):'<p class="console-placeholder">'+(consoleSessionActive(run)?'Working on your project…':run.status==='cancelled'?'Stopped.':'No response text.')+'</p>')+(typeof consoleSourceLinks==='function'?consoleSourceLinks(run):'')+(run.error?'<p class="console-error">'+escapeHTML(run.error)+'</p>':'')+(run.text?'<button type="button" class="console-copy-link" data-console-action="copy" data-run="'+index+'">Copy response</button>':'')+'</div></article>';
}
function renderConsole(){
 if(typeof renderActivity==='function')renderActivity();
 if(!$('consoleCompose'))return;
 const session=state.consoleSession,last=consoleLatest(),active=session?.runs?.find(consoleSessionActive),sending=!!visionConsoleController;
 $('consoleProjectTitle').textContent=state.title;$('consoleStatus').textContent=consolePhase(active||last);$('consoleStatus').classList.toggle('console-thinking',!!active||sending);
 $('consoleRun').disabled=sending||!!active||busy||ioBusy;$('consoleCancel').hidden=!active||(!active.runId&&!active.responseId);$('consoleResume').hidden=!visionConsoleConnectionError&&!(active?.legacy);$('consoleResume').disabled=visionConsolePolling||sending;
 $('consoleRunHint').textContent=sending?'Sending to FUPCJ Server. Keep this page open until accepted.':active?.legacy?'This older run uses its original connection. Enter its API key to reconnect.':active?'You can return to the board or leave. FUPCJ Server is processing this run.':'FUPCJ Server keeps accepted runs processing when you leave.';
 $('consoleConnection').hidden=!visionConsoleConnectionError;$('consoleConnection').textContent=visionConsoleConnectionError;
 const unread=(session?.runs||[]).filter(r=>r.unread).length;const badge=$('consoleReplyBadge');if(badge){badge.hidden=!unread;badge.textContent=String(unread);}
 const host=$('consoleTranscript');const content=session?.runs?.length?session.runs.map(consoleTurnHTML).join(''):'<div class="console-empty"><strong>Your board, as a conversation.</strong><p>Run the project, ask a question, or add files. Come back to this project to pick up the response.</p></div>';
 if(content!==visionConsoleRendered){const atBottom=host.scrollHeight-host.scrollTop-host.clientHeight<120;host.innerHTML=content;visionConsoleRendered=content;if(atBottom)host.scrollTop=host.scrollHeight;}
}
function consoleApiKey(){const key=$('consoleKey').value.trim();if(!key){$('consoleSettings').hidden=false;$('consoleSettingsToggle').setAttribute('aria-expanded','true');$('consoleKey').focus();throw new Error('Enter your OpenAI API key to send a message.');}return key;}
async function consoleCheckedResponse(response){if(!response.ok){let message;try{const body=await response.json();message=body.error?.message||body.error||body.detail;}catch{}const failure=new Error(typeof message==='string'?message:response.status===404?'Update FUPCJ Server to enable saved conversations.':'The service returned '+response.status+'.');failure.status=response.status;throw failure;}return response;}
async function consoleProjectFetch(path,options={}){
 const {signal,...rest}=options,controller=new AbortController(),abort=()=>controller.abort(),timeout=setTimeout(abort,rest.method==='POST'?120000:15000);
 if(signal){if(signal.aborted)controller.abort();else signal.addEventListener('abort',abort,{once:true});}
 try{return await consoleCheckedResponse(await projectRequest(path,{...rest,signal:controller.signal}));}finally{clearTimeout(timeout);signal?.removeEventListener('abort',abort);}
}
async function consoleLegacyFetch(path,options={}){return consoleCheckedResponse(await cloudFetch(path,{...options,headers:{...(options.headers||{}),'X-OpenAI-Key':consoleApiKey()}}));}
function consoleProjectPath(id=state.projectCloud?.id){return '/projects/'+encodeURIComponent(id);}
async function consoleCopyText(text){
 try{await navigator.clipboard.writeText(text);}catch{const el=document.createElement('textarea');el.value=text;el.style.cssText='position:fixed;left:-10000px;top:0';document.body.appendChild(el);el.select();const ok=document.execCommand('copy');el.remove();if(!ok)throw new Error('Copy is unavailable in this browser. Select the text and copy it.');}toast('Copied.');
}
function consolePromptText(run){return [run?.projectPrompt,run?.message,run?.attachments?.length?'Attached files: '+run.attachments.map(a=>a.name).join(', '):''].filter(Boolean).join('\n\n');}
async function consoleBoardFiles(){
 const nodes=orderedNodes(),files=await buildExportFiles(nodes,false);
 if(!files.some(f=>f.name==='MAIN_PROMPT.txt'))files.unshift({name:'MAIN_PROMPT.txt',data:exportResponseRules()+'\n\nMAIN TASK\n'+(state.mainPrompt||DEFAULT_PROMPT)+'\n\nRead numbered images in order.\n'+nodes.map((n,i)=>'MODULE '+(i+1)+': '+n.title+'\n'+(n.prompt||'')+'\n'+(n.caption||'')).join('\n\n')});
 return files;
}
async function consoleFileText(file){return typeof file?.data==='string'?file.data:file?.data?.text?file.data.text():'';}
async function consoleCopyCurrentPrompt(){try{let prompt='';if($('consoleIncludeBoard').checked)prompt=await consoleFileText((await consoleBoardFiles()).find(f=>f.name==='MAIN_PROMPT.txt'));await consoleCopyText(consolePromptText({projectPrompt:prompt,message:$('consoleMessage').value,attachments:visionConsoleAttachments}));}catch(e){toast(e.message,true);}}
function consoleMergeRemote(raw,existing){
 const next=normalizeConsoleRun(raw);if(!next)return existing;next.legacy=false;next.submissionUnknown=false;next.retryable=false;
 if(existing){if(!raw.runOptions)next.runOptions=existing.runOptions;if(!raw.citations)next.citations=existing.citations;next.clientRequestId=next.clientRequestId||existing.clientRequestId;next.projectPrompt=next.projectPrompt||existing.projectPrompt;next.attachments=next.attachments.length?next.attachments:existing.attachments;next.notified=existing.notified;next.unread=existing.unread;next.maxOutputTokens=existing.maxOutputTokens;next.artifacts=next.artifacts.map(a=>{const old=existing.artifacts.find(v=>v.id===a.id&&!!a.id||v.fileId===a.fileId&&!!a.fileId);return old?{...a,include:old.include,...(old.data?{data:old.data}:{} )}:a;});}
 return next;
}
function consoleNotifyRun(run){
 if(!CONSOLE_TERMINAL.has(run.status)||run.notified)return;run.notified=true;
 if(!$('visionConsole').hidden&&!document.hidden)return;
 run.unread=true;const message=run.status==='completed'?'Your Vision response is ready.':run.status==='incomplete'?'Your Vision response reached its output limit.':run.status==='cancelled'?'Vision run stopped.':'Vision run needs attention.';toast(message,run.status==='failed'||run.status==='error');
 if(typeof Notification!=='undefined'&&Notification.permission==='granted')try{const notice=new Notification('Vision',{body:message,tag:'vision-response-'+(run.runId||run.responseId)});notice.onclick=()=>{window.focus();openVisionConsole();notice.close();};}catch{}
}
function consoleSchedulePoll(delay=1200){clearTimeout(visionConsolePollTimer);if(state.projectCloud?.id&&state.projectCloud?.revision>0)visionConsolePollTimer=setTimeout(()=>void consolePollRuns(),delay);}
async function consolePollRuns(){
 if(visionConsolePolling)return;const meta=state.projectCloud;if(!meta?.id||!meta.key||!(meta.revision>0))return;
 const generation=visionConsoleGeneration,projectId=meta.id;visionConsolePolling=true;
 try{
  const response=await consoleProjectFetch(consoleProjectPath(projectId)+'/runs'),body=await response.json();if(generation!==visionConsoleGeneration||state.projectCloud?.id!==projectId)return;
  const session=consoleConversation();if(session.projectId&&session.projectId!==projectId)return;session.projectId=projectId;
  const before=JSON.stringify(session.runs),oldTerminal=session.runs.filter(r=>CONSOLE_TERMINAL.has(r.status)).length;for(const raw of body.runs||[]){const existing=session.runs.find(r=>r.runId===raw.runId||(raw.clientRequestId&&r.clientRequestId===raw.clientRequestId));const next=consoleMergeRemote(raw,existing);if(!next)continue;if(existing)session.runs[session.runs.indexOf(existing)]=next;else session.runs.push(next);consoleNotifyRun(next);}
  for(const run of session.runs){if(run.submissionUnknown&&!run.runId){run.status='failed';run.phase='Message not accepted';run.error='No accepted run was found on FUPCJ Server. You can send this message again.';run.submissionUnknown=false;run.retryable=true;}}
  session.runs.sort((a,b)=>(a.createdAt||'').localeCompare(b.createdAt||''));visionConsoleConnectionError='';if(before!==JSON.stringify(session.runs))consoleChanged(oldTerminal!==session.runs.filter(r=>CONSOLE_TERMINAL.has(r.status)).length);else renderConsole();
 }catch(e){if(generation===visionConsoleGeneration){visionConsoleConnectionError=e.message+' Your saved conversation will reconnect when FUPCJ Server is available.';renderConsole();}}
 finally{visionConsolePolling=false;if(generation===visionConsoleGeneration)consoleSchedulePoll(visionConsoleConnectionError?8000:consoleIsRunning()?1200:$('visionConsole').hidden?15000:4000);else consoleSchedulePoll(100);}
}
async function consoleRunProject(){
 if(consoleIsRunning()||busy||ioBusy)return;
 const message=$('consoleMessage').value.trim(),includeBoard=$('consoleIncludeBoard').checked&&state.nodes.length>0,attachments=visionConsoleAttachments.slice();if(!message&&!includeBoard&&!attachments.length){toast('Add a message, files, or a module to send.');return;}
 let controller,run,session,projectId,generation,submissionAttempted=false;
 try{
  const apiKey=consoleApiKey(),runOptions=consoleReadRunOptions();
  if(!runOptions.codeInterpreter&&(includeBoard||attachments.length))throw new Error('Enable Code & files for a board or attachments, or uncheck Include board to send text only.');
  controller=new AbortController();visionConsoleController=controller;generation=visionConsoleGeneration;renderConsole();
  const health=await projectCapabilities();if(!health.capabilities?.runParametersV1&&JSON.stringify(runOptions)!==JSON.stringify(normalizeRunOptions()))throw new Error('Update FUPCJ Server before using these Run settings. This message was not sent.');
  const meta=await ensureRemoteProject();projectId=meta.id;if(generation!==visionConsoleGeneration||state.projectCloud?.id!==projectId)throw new Error('The project changed before sending. Please send from the intended project.');
  session=consoleConversation();session.projectId=projectId;const previous=[...session.runs].reverse().find(r=>r.runId&&r.responseId&&['completed','incomplete'].includes(r.status));
  run=normalizeConsoleRun({runOptions,clientRequestId:'run-'+uid(),model:$('consoleModel').value.trim()||'gpt-6-astra',message,maxOutputTokens:$('consoleMaxTokens').value,previousRunId:previous?.runId||'',status:'preparing',phase:'Packaging the project…',createdAt:new Date().toISOString(),attachments});const retry=session.runs[session.runs.length-1];if(retry?.retryable&&!retry.runId&&retry.clientRequestId&&retry.message===run.message&&retry.model===run.model&&retry.maxOutputTokens===run.maxOutputTokens&&JSON.stringify(retry.runOptions)===JSON.stringify(run.runOptions)&&JSON.stringify(retry.attachments)===JSON.stringify(run.attachments)){run.clientRequestId=retry.clientRequestId;session.runs.pop();}session.runs.push(run);session.model=run.model;session.maxOutputTokens=run.maxOutputTokens;session.runOptions=run.runOptions;consoleChanged();
  const form=new FormData();let uploadBytes=attachments.reduce((sum,f)=>sum+f.size,0);
  if(includeBoard){const files=await consoleBoardFiles();run.projectPrompt=(await consoleFileText(files.find(f=>f.name==='MAIN_PROMPT.txt'))).slice(0,250000);const archive=await R.zip(files);uploadBytes+=archive.size;form.append('file',archive,R.safeFilename(state.title)+'.zip');}
  if(uploadBytes>25*1024*1024)throw new Error('This message exceeds the 25 MB upload limit. Use smaller files or contact sheets.');for(const file of attachments)form.append('attachments',file,file.name);
  form.append('options',JSON.stringify({runOptions:run.runOptions,clientRequestId:run.clientRequestId,model:run.model,message:message||(includeBoard?'Follow the instructions in the project.':'Use the attached files to answer.'),maxOutputTokens:run.maxOutputTokens,previousRunId:run.previousRunId||undefined,projectPrompt:run.projectPrompt}));
  run.phase='Sending to FUPCJ Server…';consoleChanged();await flushProjectSave();if(generation!==visionConsoleGeneration)throw new Error('The project changed before sending.');
  submissionAttempted=true;const response=await consoleProjectFetch(consoleProjectPath(projectId)+'/runs',{method:'POST',headers:{'X-OpenAI-Key':apiKey},body:form,signal:controller.signal});const accepted=await response.json();if(!accepted.runId)throw new Error('FUPCJ Server did not return a run ID. Reconnect before sending again.');
  if(generation!==visionConsoleGeneration)return;run.runId=accepted.runId;run.status=accepted.status||'queued';run.phase='Accepted by FUPCJ Server';if($('consoleMessage').value.trim()===message)$('consoleMessage').value='';session.draft=$('consoleMessage').value;visionConsoleAttachments=visionConsoleAttachments.filter(file=>!attachments.includes(file));consoleRenderAttachments();consoleChanged();await flushProjectSave();consoleSchedulePoll(0);
 }catch(e){if(run&&session===state.consoleSession){if(run.runId){visionConsoleConnectionError='FUPCJ Server accepted the run. Reconnecting to its saved progress…';consoleSchedulePoll(0);}else{run.submissionUnknown=submissionAttempted&&(!e.status||e.status===408||e.status>=500);run.status=run.submissionUnknown?'disconnected':'failed';run.phase=run.submissionUnknown?'Checking whether FUPCJ Server accepted the message…':'Message not sent';run.error=run.submissionUnknown?'Connection interrupted. Vision will check FUPCJ Server before allowing another send.':e.message;run.retryable=!run.submissionUnknown;visionConsoleConnectionError=run.submissionUnknown?'Reconnecting to check your last message…':'';consoleChanged();consoleSchedulePoll(0);}}else if(e.name!=='AbortError')toast(e.message,true);}
 finally{if(visionConsoleController===controller)visionConsoleController=null;renderConsole();updateRefreshNotice();}
}
async function consoleResumeRun(){
 const legacy=(state.consoleSession?.runs||[]).find(r=>r.legacy&&consoleSessionActive(r));if(legacy){await consoleResumeLegacy(legacy);return;}visionConsoleConnectionError='';await consolePollRuns();
}
async function consoleCancelRun(){
 const run=(state.consoleSession?.runs||[]).find(consoleSessionActive);if(!run)return;
 try{const response=run.runId?await consoleProjectFetch(consoleProjectPath()+'/runs/'+encodeURIComponent(run.runId)+'/cancel',{method:'POST'}):await consoleLegacyFetch('/openai/responses/'+encodeURIComponent(run.responseId)+'/cancel',{method:'POST'});const data=await response.json();if(run.runId)Object.assign(run,consoleMergeRemote(data.run||data,run));else consoleApplyLegacyResponse(data,run);consoleChanged();consoleSchedulePoll(0);}catch(e){toast('Could not stop: '+e.message,true);}
}
async function consoleArtifactBlob(a,run){
 const generation=visionConsoleGeneration;
 if(a.data)return new Blob([bytesFromDataURL(a.data)],{type:a.mime});
 const response=run.runId&&a.id?await consoleProjectFetch(consoleProjectPath()+'/runs/'+encodeURIComponent(run.runId)+'/artifacts/'+encodeURIComponent(a.id)):await consoleLegacyFetch('/openai/containers/'+encodeURIComponent(a.containerId)+'/files/'+encodeURIComponent(a.fileId)+'/content?response_id='+encodeURIComponent(run.responseId));const blob=await response.blob();if(generation!==visionConsoleGeneration)throw new Error('The project changed before this file finished loading.');return blob;
}
function consoleExportFiles(session=state.consoleSession){
 if(!session)return[];const used=new Set();return (session.runs||[session]).flatMap(run=>(run.artifacts||[]).filter(a=>a.include&&a.data).map(a=>{let name=R.safeFilename(a.name),i=2;while(used.has(name.toLowerCase()))name=(i++)+'-'+R.safeFilename(a.name);used.add(name.toLowerCase());return{name:'results/'+name,data:new Blob([bytesFromDataURL(a.data)],{type:a.mime})};}));
}
function consoleRefreshProject(){
 if(typeof consoleClosePreview==='function')consoleClosePreview();
 const nextProject=state.projectCloud?.id||'';const changed=nextProject!==visionConsoleLastProject;visionConsoleLastProject=nextProject;
 visionConsoleGeneration++;clearTimeout(visionConsoleDraftTimer);visionConsoleController?.abort();visionConsoleController=null;clearTimeout(visionConsolePollTimer);visionConsoleConnectionError='';state.consoleSession=normalizeConsoleSession(state.consoleSession);for(const run of state.consoleSession?.runs||[]){if(!run.runId&&!run.legacy&&run.clientRequestId&&consoleSessionActive(run)){run.submissionUnknown=true;run.status='disconnected';run.phase='Checking the saved run…';}}
 if(changed){visionConsoleAttachments=[];consoleStopDictation(false);consoleRenderAttachments();}
 if(state.consoleSession){$('consoleModel').value=state.consoleSession.model;$('consoleMaxTokens').value=state.consoleSession.maxOutputTokens;$('consoleMessage').value=state.consoleSession.draft||'';}else $('consoleMessage').value='';
 if(typeof consoleRestoreRunOptions==='function')consoleRestoreRunOptions();
 visionConsoleRendered='';renderConsole();consoleSchedulePoll(0);
}
function openVisionConsole(){
 $('visionConsole').hidden=false;for(const run of state.consoleSession?.runs||[])run.unread=false;renderConsole();consoleSchedulePoll(0);$('consoleMessage').focus({preventScroll:true});
}
function consoleRenderAttachments(){const list=$('consoleAttachmentList');if(!list)return;list.innerHTML=visionConsoleAttachments.map((f,i)=>'<span class="console-attachment-chip">'+escapeHTML(f.name)+'<button type="button" aria-label="Remove '+escapeHTML(f.name)+'" data-remove-attachment="'+i+'">×</button></span>').join('');}
function consoleAddAttachments(files){
 for(const file of Array.from(files||[])){if(visionConsoleAttachments.length>=20){toast('Attach up to 20 files per message.',true);break;}if(file.size>25*1024*1024){toast(file.name+' exceeds the 25 MB message limit.',true);continue;}if(!visionConsoleAttachments.some(f=>f.name===file.name&&f.size===file.size&&f.lastModified===file.lastModified))visionConsoleAttachments.push(file);}consoleRenderAttachments();
}
function consoleSaveDraft(){const session=consoleConversation();session.draft=$('consoleMessage').value;clearTimeout(visionConsoleDraftTimer);visionConsoleDraftTimer=setTimeout(()=>consoleChanged(),400);}
function consoleStopDictation(commit=true){if(visionConsoleRecognition){const recognition=visionConsoleRecognition;visionConsoleRecognition=null;if(commit&&recognition.visionInterim){const input=$('consoleMessage');input.value=(input.value.trimEnd()+(input.value.trim()?' ':'')+recognition.visionInterim).slice(0,100000);consoleSaveDraft();}try{recognition.stop();}catch{}}if($('consoleMic')){$('consoleMic').textContent='Mic';$('consoleMic').setAttribute('aria-pressed','false');$('consoleMic').setAttribute('aria-label','Start dictation');$('consoleSpeechPreview').hidden=true;}}
function consoleStartDictation(){
 if(visionConsoleRecognition){consoleStopDictation();return;}const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
 if(!Recognition){$('consoleMessage').focus();toast('Use the microphone on your device’s keyboard to dictate into this message.');return;}
 try{const recognition=new Recognition(),committed=new Set();visionConsoleRecognition=recognition;recognition.lang=navigator.language||'en-US';recognition.continuous=true;recognition.interimResults=true;
 recognition.onresult=event=>{if(visionConsoleRecognition!==recognition)return;let interim='';for(let i=event.resultIndex;i<event.results.length;i++){const result=event.results[i];if(result.isFinal&&!committed.has(i)){committed.add(i);const input=$('consoleMessage');input.value=(input.value.trimEnd()+(input.value.trim()?' ':'')+result[0].transcript).slice(0,100000);consoleSaveDraft();}else if(!result.isFinal)interim+=result[0].transcript;}recognition.visionInterim=interim;const preview=$('consoleSpeechPreview');preview.textContent=interim||'Listening… Review your message before sending.';preview.hidden=false;};
 recognition.onerror=event=>{if(event.error!=='aborted')toast(event.error==='not-allowed'?'Allow microphone access to dictate, or use your keyboard microphone.':'Dictation stopped. You can edit the message or use your keyboard microphone.',true);consoleStopDictation();};recognition.onend=()=>{if(visionConsoleRecognition===recognition)consoleStopDictation();};recognition.start();$('consoleMic').textContent='Stop mic';$('consoleMic').setAttribute('aria-pressed','true');$('consoleMic').setAttribute('aria-label','Stop dictation');$('consoleSpeechPreview').textContent='Listening with your browser’s speech service…';$('consoleSpeechPreview').hidden=false;
 }catch(e){consoleStopDictation();toast('Dictation is unavailable here. Use your device’s keyboard microphone.',true);}
}
// Read existing pre-project Responses sessions without discarding their results.
function consoleFindLegacyArtifacts(item,run){if(!item||typeof item!=='object')return;if(item.type==='container_file_citation'&&item.file_id&&item.container_id&&!run.artifacts.some(a=>a.fileId===item.file_id&&a.containerId===item.container_id))run.artifacts.push(normalizeConsoleArtifact({fileId:item.file_id,containerId:item.container_id,name:item.filename}));for(const value of Object.values(item)){if(Array.isArray(value))value.forEach(v=>consoleFindLegacyArtifacts(v,run));else if(value&&typeof value==='object')consoleFindLegacyArtifacts(value,run);}}
function consoleApplyLegacyResponse(response,run){if(!response)return;if(response.id)run.responseId=response.id;if(response.status)run.status=response.status;const parts=[];for(const item of response.output||[]){if(item.type==='message')for(const content of item.content||[])if(content.type==='output_text')parts.push(content.text||'');else if(content.type==='refusal')parts.push(content.refusal||'');consoleFindLegacyArtifacts(item,run);}if(parts.length)run.text=parts.join('\n\n');if(response.error)run.error=response.error.message||'The run failed.';run.phase='';}
async function consoleResumeLegacy(run){try{const response=await consoleLegacyFetch('/openai/responses/'+encodeURIComponent(run.responseId));consoleApplyLegacyResponse(await response.json(),run);consoleNotifyRun(run);consoleChanged();if(consoleSessionActive(run))toast('This older run is still processing. Reconnect again to check its response.');}catch(e){toast(e.message,true);}}
consoleInstallUI();
$('consoleBack').onclick=()=>{consoleStopDictation();if(typeof hideVisibleKeys==='function')hideVisibleKeys($('visionConsole'));$('visionConsole').hidden=true;};$('consoleCompose').onsubmit=e=>{e.preventDefault();consoleStopDictation();void consoleRunProject();};$('consoleResume').onclick=()=>void consoleResumeRun();$('consoleCancel').onclick=()=>void consoleCancelRun();$('runProjectBtn').onclick=openVisionConsole;
$('consoleSettingsToggle').onclick=()=>{const panel=$('consoleSettings');panel.hidden=!panel.hidden;if(panel.hidden&&typeof hideVisibleKeys==='function')hideVisibleKeys(panel);$('consoleSettingsToggle').setAttribute('aria-expanded',String(!panel.hidden));};
$('consoleMessage').oninput=consoleSaveDraft;$('consoleMessage').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&!(typeof matchMedia==='function'&&matchMedia('(pointer:coarse)').matches)){e.preventDefault();if(!$('consoleRun').disabled)$('consoleCompose').requestSubmit();}});
$('consoleCopyPrompt').onclick=()=>void consoleCopyCurrentPrompt();$('consoleAttach').onclick=()=>$('consoleAttachmentInput').click();$('consoleAttachmentInput').onchange=e=>{consoleAddAttachments(e.target.files);e.target.value='';};$('consoleAttachmentList').onclick=e=>{const button=e.target.closest('[data-remove-attachment]');if(button){visionConsoleAttachments.splice(Number(button.dataset.removeAttachment),1);consoleRenderAttachments();}};
$('consoleCompose').addEventListener('dragover',e=>{e.preventDefault();e.stopPropagation();$('consoleCompose').classList.add('console-drop-active');});$('consoleCompose').addEventListener('dragleave',e=>{if(!$('consoleCompose').contains(e.relatedTarget))$('consoleCompose').classList.remove('console-drop-active');});$('consoleCompose').addEventListener('drop',e=>{e.preventDefault();e.stopPropagation();$('consoleCompose').classList.remove('console-drop-active');consoleAddAttachments(e.dataTransfer.files);});
$('consoleTranscript').addEventListener('click',async e=>{const button=e.target.closest('button[data-console-action]');if(!button)return;const index=Number(button.dataset.run),run=state.consoleSession?.runs?.[index];if(!run)return;if(button.dataset.consoleAction==='files'){const expanded=consoleFilesState(),key=consoleFilesIdentity(run,index);if(expanded.has(key))expanded.delete(key);else expanded.add(key);renderConsole();$('consoleTranscript').querySelector('[data-console-action="files"][data-run="'+index+'"]').focus({preventScroll:true});return;}button.disabled=true;try{if(button.dataset.consoleAction==='copy')await consoleCopyText(run.text);else if(button.dataset.consoleAction==='prompt')await consoleCopyText(consolePromptText(run));else if(button.dataset.consoleAction==='preview'){await consolePreviewArtifact(run,Number(button.dataset.artifact));}else if(button.dataset.consoleAction==='download'){const a=run.artifacts[Number(button.dataset.artifact)];download(await consoleArtifactBlob(a,run),R.safeFilename(a.name));}}catch(error){toast(error.message,true);}finally{button.disabled=false;}});
$('consoleTranscript').addEventListener('change',async e=>{const input=e.target.closest('input[data-console-action="include"]');if(!input)return;const run=state.consoleSession?.runs?.[Number(input.dataset.run)],a=run?.artifacts[Number(input.dataset.artifact)];if(!a)return;input.disabled=true;try{if(input.checked&&!a.data){const blob=await consoleArtifactBlob(a,run);a.data=await readFile(blob);a.mime=blob.type||a.mime;}a.include=input.checked;consoleChanged();}catch(error){input.checked=false;toast(error.message,true);}finally{input.disabled=false;}});
function consoleRememberKey(){try{if(typeof accountUsesGoogle==='function'&&accountUsesGoogle()){sessionStorage.removeItem(CONSOLE_KEY_KEY);return;}if($('consoleRememberKey').checked)sessionStorage.setItem(CONSOLE_KEY_KEY,$('consoleKey').value);else sessionStorage.removeItem(CONSOLE_KEY_KEY);}catch{}}
$('consoleRememberKey').onchange=consoleRememberKey;$('consoleKey').oninput=consoleRememberKey;try{const key=sessionStorage.getItem(CONSOLE_KEY_KEY);if(key){$('consoleKey').value=key;$('consoleRememberKey').checked=true;}}catch{}
$('consoleModel').onchange=$('consoleMaxTokens').onchange=()=>{const session=consoleConversation();session.model=$('consoleModel').value.trim()||'gpt-6-astra';session.maxOutputTokens=Math.min(64000,Math.max(1024,Number($('consoleMaxTokens').value)||8192));consoleChanged();};
$('consoleMic').onclick=consoleStartDictation;if(!(window.SpeechRecognition||window.webkitSpeechRecognition)){$('consoleMic').title='Use your device’s keyboard microphone';$('consoleMic').setAttribute('aria-label','Dictation help');}
$('consoleNotifyYes').onclick=async()=>{if(typeof Notification==='undefined'){toast('Replies are marked on the Run button. Your browser does not support additional notifications.');return;}try{const permission=await Notification.requestPermission();toast(permission==='granted'?'Reply notifications enabled.':'Replies will still be marked on the Run button.');}catch{toast('Replies will be marked on the Run button.');}};
window.addEventListener('online',()=>consoleSchedulePoll(0));document.addEventListener('visibilitychange',()=>{if(!document.hidden){if(!$('visionConsole').hidden){for(const r of state.consoleSession?.runs||[])r.unread=false;renderConsole();}consoleSchedulePoll(0);}else consoleStopDictation();});
$('screenshotMode').onchange=()=>{checkpoint();state.settings.screenshotMode=$('screenshotMode').value;markDirty();refreshExport();};
renderAll();
