// Run preferences and the page's authenticated file workspace. No model output is trusted as HTML.
function consoleReadRunOptions(){
 return normalizeRunOptions({mode:$('consoleMode')?.value,effort:$('consoleEffort')?.value,verbosity:$('consoleVerbosity')?.value,webSearch:$('consoleWebSearch')?.checked,codeInterpreter:$('consoleCodeInterpreter')?.checked!==false});
}
function consoleRestoreRunOptions(){
 const options=normalizeRunOptions(state.consoleSession?.runOptions);
 for(const [id,key] of [['consoleMode','mode'],['consoleEffort','effort'],['consoleVerbosity','verbosity']])if($(id))$(id).value=options[key];
 for(const [id,key] of [['consoleWebSearch','webSearch'],['consoleCodeInterpreter','codeInterpreter']])if($(id))$(id).checked=options[key];
 consoleModelHelp();
}
function consoleModelHelp(){
 const model=$('consoleModel').value.trim(),note=$('consoleModelHelp');if(!note)return;
 note.textContent='Use the exact OpenAI API model ID. Model default omits that parameter. Availability, tools, and supported settings depend on the model and your API account. Pro and higher effort can increase cost and latency.';
 const effort=$('consoleEffort');
 for(const option of effort.options)option.disabled=(model.startsWith('gpt-6-astra')&&option.value==='none')||(model.startsWith('gpt-6.1-sol')&&['none','minimal'].includes(option.value));
 if(effort.selectedOptions[0]?.disabled)note.textContent='This model does not support the selected thinking effort. Select Model default or a higher effort before sending.';
}
function consoleInstallRunSettings(){
 const panel=$('consoleSettings'),fields=document.createElement('div');fields.className='console-run-options';
 fields.innerHTML=`<label>Reasoning mode<select id="consoleMode"><option value="auto">Model default</option><option value="standard">Standard</option><option value="pro">Pro</option></select></label>
 <label>Thinking effort<select id="consoleEffort"><option value="auto">Model default</option><option value="none">None</option><option value="minimal">Minimal</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="xhigh">Extra high</option><option value="max">Max</option></select></label>
 <label>Verbosity<select id="consoleVerbosity"><option value="auto">Model default</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label>
 <label class="console-check"><input id="consoleWebSearch" type="checkbox"> Web search</label><label class="console-check"><input id="consoleCodeInterpreter" type="checkbox" checked> Code &amp; files</label>
 <p id="consoleModelHelp" class="mini-note"></p><p class="mini-note">Web search allows the model to search when useful. The output limit includes reasoning tokens, so very low limits can end before a final answer. Code &amp; files enables the remote code interpreter and file generation; it is required for board ZIPs and attachments. Generated files return to this page, not to a separate ChatGPT sandbox.</p>`;
 panel.prepend(fields);const models=document.createElement('datalist');models.id='consoleModels';for(const id of ['gpt-6-astra','gpt-6.1-sol']){const option=document.createElement('option');option.value=id;models.appendChild(option);}panel.appendChild(models);$('consoleModel').setAttribute('list',models.id);$('consoleModel').setAttribute('maxlength','100');$('consoleModel').addEventListener('input',consoleModelHelp);
 fields.addEventListener('change',()=>{const session=consoleConversation();session.runOptions=consoleReadRunOptions();consoleChanged();consoleModelHelp();});
 const files=document.createElement('button');files.type='button';files.className='btn';files.id='consoleFileBagButton';files.textContent='File bag';files.onclick=consoleOpenFileBag;$('consoleSettingsToggle').before(files);
 consoleRestoreRunOptions();
}
function consoleArtifactIndex(url,run){
 let name;try{name=decodeURIComponent(String(url).replace(/&amp;/g,'&').replace(/^sandbox:/i,'')).replace(/\\/g,'/').split('/').pop();}catch{return -1;}
 if(!name)return -1;const matches=run.artifacts.map((a,i)=>({a,i})).filter(({a})=>a.name.replace(/\\/g,'/').split('/').pop()===name||a.id===name||a.fileId===name);
 return matches.length===1?matches[0].i:-1;
}
function consoleArtifactLink(label,url,run,index){
 const ai=consoleArtifactIndex(url,run),a=run.artifacts[ai];
 return a?'<button type="button" class="console-artifact-link" data-console-action="preview" data-run="'+index+'" data-artifact="'+ai+'" '+(!a.ready?'disabled':'')+'>'+label+'</button>':label+' <small>(file not available in this response)</small>';
}
function consoleSourceLinks(run){
 return run.citations?.length?'<nav class="console-sources" aria-label="Response sources"><strong>Sources</strong> '+run.citations.map(a=>'<a href="'+escapeHTML(a.url)+'" target="_blank" rel="noopener noreferrer">'+escapeHTML(a.title)+'</a>').join(' · ')+'</nav>':'';
}
let consolePreviewURL='',consolePreviewSerial=0,consolePreviewFile=null;
function consoleClearPreview(){
 consolePreviewSerial++;if(consolePreviewURL)URL.revokeObjectURL(consolePreviewURL);consolePreviewURL='';consolePreviewFile=null;$('consolePreviewBody')?.replaceChildren();
}
function consoleClosePreview(){consoleClearPreview();$('consolePreviewDialog')?.close();$('consoleFileBagDialog')?.close();}
function consoleOpenFileBag(){
 const target=$('consoleFileBagList');target.replaceChildren();
 for(const [ri,run] of (state.consoleSession?.runs||[]).entries())for(const [ai,a] of run.artifacts.entries()){
  const row=document.createElement('div');row.className='console-file';const button=document.createElement('button');button.type='button';button.className='console-file-name';button.textContent=a.name+' · response '+(ri+1);button.disabled=!a.ready;button.onclick=()=>{void consolePreviewArtifact(run,ai).catch(e=>toast(e.message,true));};row.appendChild(button);target.appendChild(row);
 }
 if(!target.childNodes.length)target.textContent='No generated files yet. Enable Code & files and ask the model to create a file.';
 $('consoleFileBagDialog').showModal();
}
async function consolePreviewArtifact(run,ai){
 const a=run.artifacts[ai];if(!a?.ready)throw new Error('This file is not ready yet.');
 consoleClearPreview();const serial=consolePreviewSerial,generation=visionConsoleGeneration,dialog=$('consolePreviewDialog'),body=$('consolePreviewBody');
 $('consolePreviewTitle').textContent=a.name;$('consolePreviewDownload').disabled=true;$('consolePreviewScripts').checked=false;$('consolePreviewScriptsLabel').hidden=true;body.textContent='Loading file from your project…';if(!dialog.open)dialog.showModal();
 const blob=await consoleArtifactBlob(a,run);
 if(serial!==consolePreviewSerial||generation!==visionConsoleGeneration||!dialog.open)return;
 consolePreviewFile={blob,name:a.name};$('consolePreviewDownload').disabled=false;body.replaceChildren();
 const name=a.name.toLowerCase(),mime=blob.type||a.mime;
 if(/\.(html?|svg)$/.test(name)||mime==='text/html'||mime==='image/svg+xml'){
  if(blob.size>5*1024*1024){body.textContent='This file is too large for an inline preview. Use Download.';return;}
  const text=await blob.text();if(serial!==consolePreviewSerial||generation!==visionConsoleGeneration)return;
  const frame=document.createElement('iframe');frame.title=a.name;frame.className='console-preview-frame';frame.setAttribute('sandbox','');
  const render=()=>{const scripts=$('consolePreviewScripts').checked;frame.setAttribute('sandbox',scripts?'allow-scripts':'');frame.srcdoc='<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data: blob:; media-src data: blob:; style-src \'unsafe-inline\'; font-src data:; script-src '+(scripts?"'unsafe-inline'":"'none'")+'; connect-src \'none\'; frame-src \'none\'; form-action \'none\'; base-uri \'none\'">'+text;};
  $('consolePreviewScriptsLabel').hidden=false;$('consolePreviewScripts').onchange=render;render();body.appendChild(frame);
 }else if(/^image\/(png|jpeg|gif|webp|bmp|avif)$/.test(mime)){
  consolePreviewURL=URL.createObjectURL(blob);const image=document.createElement('img');image.src=consolePreviewURL;image.alt=a.name;image.className='console-preview-image';body.appendChild(image);
 }else if(/^(text\/|application\/(json|xml))/.test(mime)||/\.(txt|csv|md|json|js|py|css|xml|log|ya?ml)$/.test(name)){
  const text=await blob.slice(0,2*1024*1024).text();if(serial!==consolePreviewSerial||generation!==visionConsoleGeneration)return;const pre=document.createElement('pre');pre.textContent=text+(blob.size>2*1024*1024?'\n\n[Preview limited to 2 MB. Download the complete file.]':'');body.appendChild(pre);
 }else body.textContent='This file type is available for download. Use Download to open it in its usual application.';
}
function consoleInstallFileWorkspace(){
 const dialog=document.createElement('dialog');dialog.id='consolePreviewDialog';dialog.className='console-file-dialog';dialog.innerHTML='<header><h2 id="consolePreviewTitle">File preview</h2><button type="button" class="btn" id="consolePreviewClose">Close</button></header><p class="mini-note">Preview stays in Vision. HTML runs in an isolated frame without network, account storage, or access to the board.</p><label id="consolePreviewScriptsLabel" hidden><input type="checkbox" id="consolePreviewScripts"> Run scripts inside isolated preview</label><div id="consolePreviewBody"></div><button type="button" class="btn primary" id="consolePreviewDownload" disabled>Download</button>';
 document.body.appendChild(dialog);dialog.addEventListener('close',consoleClearPreview);$('consolePreviewClose').onclick=()=>dialog.close();$('consolePreviewDownload').onclick=()=>{if(consolePreviewFile)download(consolePreviewFile.blob,R.safeFilename(consolePreviewFile.name));};
 const bag=document.createElement('dialog');bag.id='consoleFileBagDialog';bag.className='console-file-dialog';bag.innerHTML='<header><h2>Project file bag</h2><button type="button" class="btn" id="consoleFileBagClose">Close</button></header><div id="consoleFileBagList"></div>';document.body.appendChild(bag);$('consoleFileBagClose').onclick=()=>bag.close();
}
consoleInstallRunSettings();consoleInstallFileWorkspace();
