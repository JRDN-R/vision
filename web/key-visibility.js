// Eye controls change display only. Credentials keep their existing storage behavior.
function setKeyVisibility(input,visible){
 const button=input?.parentElement?.querySelector('.vision-key-toggle');if(!input||!button)return;
 const start=input.selectionStart,end=input.selectionEnd;input.type=visible?'text':'password';
 try{if(start!==null&&end!==null)input.setSelectionRange(start,end);}catch{}
 const label=input.dataset.keyLabel||'API key';
 button.setAttribute('aria-pressed',String(visible));button.setAttribute('aria-label',(visible?'Hide ':'Show ')+label);button.title=(visible?'Hide ':'Show ')+label;
}
function installKeyVisibility(input){
 if(!input||!['password','text'].includes(input.type)||input.parentElement?.classList.contains('vision-key-control'))return;
 const label=input.closest('label');
 if(label){
  // Keep the toggle outside the label so it cannot activate or relabel the input.
  const field=document.createElement('div');field.className='vision-key-field';label.before(field);field.appendChild(label);label.htmlFor=input.id;field.appendChild(input);
 }
 const control=document.createElement('span');control.className='vision-key-control';input.before(control);control.appendChild(input);
 const button=document.createElement('button');button.type='button';button.className='vision-key-toggle';button.setAttribute('aria-controls',input.id);
 button.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.7-7 10-7 10 7 10 7-3.7 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/><path class="vision-key-slash" d="m3 3 18 18"/></svg>';
 control.appendChild(button);setKeyVisibility(input,false);button.addEventListener('click',()=>setKeyVisibility(input,input.type==='password'));
}
function hideVisibleKeys(root=document){for(const input of root.querySelectorAll('.vision-key-control input[type="text"]'))setKeyVisibility(input,false);}
installKeyVisibility(document.getElementById('consoleKey'));
for(const input of document.querySelectorAll('input[type="password"][data-key-label]'))installKeyVisibility(input);
document.addEventListener('visibilitychange',()=>{if(document.hidden)hideVisibleKeys();});
window.addEventListener('pagehide',()=>hideVisibleKeys());
