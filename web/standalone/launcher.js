(() => {
 'use strict';
 const server='https://desktop-vjt2br2.tail385c9d.ts.net';
 const button=document.getElementById('open'),status=document.getElementById('status');
 let active=null;
 function finish(text){
  if(active){clearTimeout(active.timer);clearInterval(active.closed);active=null;}
  button.disabled=false;status.textContent=text;
 }
 function open(){
  if(active)return;
  const nonce=Array.from(crypto.getRandomValues(new Uint8Array(24)),b=>b.toString(16).padStart(2,'0')).join('');
  // The click opens a top-level HTTPS document, so microphone, account storage,
  // and API requests do not inherit the local file's opaque origin.
  const popup=window.open(server+'/standalone/#'+nonce,'_blank');
  if(!popup){finish('Allow the new tab, then press Open Vision again.');return;}
  button.disabled=true;status.textContent='Connecting to FUPCJ Server…';
  active={popup,nonce,sent:false,timer:setTimeout(()=>finish('FUPCJ Server did not finish opening. Update the server, check its connection, then retry.'),45000),closed:setInterval(()=>{if(popup.closed)finish('Vision was closed before it finished opening. You can try again.');},500)};
 }
 window.addEventListener('message',event=>{
  const job=active,data=event.data;
  if(!job||event.origin!==server||event.source!==job.popup||data?.nonce!==job.nonce)return;
  if(data.type==='vision-standalone-ready'&&!job.sent){
   job.sent=true;status.textContent='Opening the app embedded in this file…';
   try{
    const raw=atob(document.getElementById('vision-payload').textContent.trim());
    const bytes=Uint8Array.from(raw,c=>c.charCodeAt(0));
    job.popup.postMessage({type:'vision-standalone-load',nonce:job.nonce,html:new TextDecoder().decode(bytes)},server);
   }catch{finish('This file could not be read. Download a fresh standalone HTML.');}
  }else if(data.type==='vision-standalone-opened')finish('Vision is open in the new tab. You can close this launcher.');
  else if(data.type==='vision-standalone-error')finish('The server could not open this build. Update FUPCJ Server using the matching standalone release, then try again.');
 });
 button.addEventListener('click',open);
})();
