import {BACKEND, initAuth, user, signOut, accountEpoch} from './vortex/auth.js?v=1.0.4&rev=moises.1';
const $ = id => document.getElementById(id);
function notice(message, error=false){$('notice').textContent=message;$('notice').classList.toggle('error',error);}
async function api(path='',method='GET',body){
  const identity=user(),epoch=accountEpoch();if(!identity)throw new Error('Sign in through Vision.');
  const token=await identity.getIdToken();if(epoch!==accountEpoch())throw new Error('Account changed.');
  const response=await fetch(BACKEND+'/api/church-admin'+path,{method,headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(20000),credentials:'omit',cache:'no-store',referrerPolicy:'no-referrer'});
  const value=await response.json().catch(()=>({}));if(epoch!==accountEpoch())throw new Error('Account changed.');
  if(!response.ok)throw new Error(value.error||(response.status===404?'Install the FirstUPCJax Admins update on FUPCJ Server.':'FUPCJ Server could not complete the request.'));return value;
}
function el(tag,text,className){const item=document.createElement(tag);item.textContent=text;if(className)item.className=className;return item;}
async function task(fn,button){if(button)button.disabled=true;try{await fn();}catch(error){notice(error.name==='TimeoutError'?'FUPCJ Server did not respond. Refresh to check whether your request completed.':error.message,true);}finally{if(button)button.disabled=false;}}
async function people(){
  const data=await api('/people');$('people').replaceChildren();
  const rows=[...data.members,...data.invitations.filter(i=>!data.members.some(m=>m.email===i.email))];
  for(const person of rows){const row=el('div','','person');row.append(el('span',person.email),el('span',person.role+(person.active?'':' · revoked'),'pill'));
    if(person.role!=='owner'&&person.active){const button=el('button','Revoke','secondary');button.onclick=()=>task(async()=>{if(!confirm('Revoke '+person.email+'? Their next request will be blocked.'))return;await api('/people','DELETE',{email:person.email});await people();notice('Access revoked.');},button);row.append(button);} $('people').append(row);}
}
function jobs(items){$('jobs').replaceChildren();if(!items.length){$('jobs').append(el('p','No notification requests yet.'));return;}
  for(const job of items){const row=el('div','','job'),info=el('div','');info.append(el('strong','Assignment '+job.assignment_id),el('p',job.detail||'Waiting for the worker.'));
    const link=el('a','Open exact plan');link.href='https://services.planningcenteronline.com/plans/'+encodeURIComponent(job.plan_id);link.target='_blank';link.rel='noopener noreferrer';info.append(link);row.append(info,el('span',job.state.replaceAll('_',' '),'pill'));
    if(job.sent_at)row.append(el('p',new Date(job.sent_at).toLocaleString()));
    if(job.state==='needs_review'){const button=el('button','Resolve after checking','secondary');button.onclick=()=>task(async()=>{if(!confirm('Have you checked this exact assignment in Planning Center and confirmed no email was sent? This closes the uncertain attempt. It does not send another email.'))return;await api('/jobs/'+job.id+'/review','POST',{confirmedNoEmailSent:true});await refresh();},button);row.append(button);}
    $('jobs').append(row);}
}
async function refresh(){const data=await api();$('dashboard').hidden=false;$('identity').textContent=data.member.email;$('role').textContent=data.member.role[0].toUpperCase()+data.member.role.slice(1);$('connection').textContent=data.linkedConnections?'ChatGPT connection linked.':'ChatGPT has not been linked yet.';$('browserState').textContent=data.browserReady?'Browser session saved':'PC sign-in needed';$('browserHelp').textContent=data.browserReady?'The worker verifies the saved session when each request runs. If it expires, sign in again on FUPCJ Server.':'On FUPCJ Server, run FirstUPCJax-Admins.ps1 -Action Login once. A saved session alone does not prove an email was sent.';$('access').hidden=data.member.role!=='owner';jobs(data.jobs);if(data.member.role==='owner')await people();notice('Connected to FUPCJ Server.');}
$('link').onclick=()=>notice('Install the private FirstUPCJax Admins plugin package in ChatGPT, then choose Connect. Sign in through Vision and approve the connection here.');
$('unlink').onclick=event=>task(async()=>{if(!confirm('Unlink your ChatGPT connections?'))return;await api('/link','DELETE');await refresh();},event.currentTarget);
$('invite').onsubmit=event=>{event.preventDefault();task(async()=>{await api('/people','POST',{email:$('email').value.trim(),role:$('inviteRole').value});$('email').value='';await people();notice('Access added. Share this page with the person; no email has been sent.');},event.submitter);};
$('refresh').onclick=event=>task(refresh,event.currentTarget);
$('signOut').onclick=()=>task(signOut);
const connect=new URL(location.href).searchParams.get('connect');
const validConnect=/^[A-Za-z0-9_-]{43}$/.test(connect||'');
if(validConnect){const sign=new URL($('signinLink').href);sign.searchParams.set('connect',connect);$('signinLink').href=sign.href;}
async function consent(){if(!validConnect)return;const details=await api('/oauth/'+connect);$('oauthDetails').textContent=details.clientName+' is requesting: '+details.scopes.join(', ')+'. Your role: '+details.role+'.';$('oauthConsent').hidden=false;}
async function answerConsent(approve){const answer=await api('/oauth/'+connect,'POST',{approve});const destination=new URL(answer.redirectUrl);if(destination.origin!=='https://chatgpt.com')throw new Error('Unexpected connection destination.');location.assign(destination.href);}
$('approveOAuth').onclick=event=>task(()=>answerConsent(true),event.currentTarget);
$('denyOAuth').onclick=event=>task(()=>answerConsent(false),event.currentTarget);
initAuth(identity=>{$('dashboard').hidden=true;$('signedOut').hidden=!!identity;$('signOut').hidden=!identity;$('oauthConsent').hidden=true;if(identity)task(async()=>{await refresh();await consent();});else notice('Sign in to continue.');}).catch(error=>notice(error.message,true));
