/* System-browser login broker. Credentials only travel in HTTPS POST bodies. */
(() => {
 'use strict';
 const server='https://desktop-vjt2br2.tail385c9d.ts.net';
 const config={apiKey:'AIzaSyDh1AHhi41cAXcSFnvFkfeZWmxc8gI0zSg',authDomain:'visionboard-api.firebaseapp.com',projectId:'visionboard-api',appId:'1:150865729216:web:554423d0c7602d3a47bf25'};
 const params=new URLSearchParams(location.hash.slice(1)),id=params.get('id'),state=params.get('state');
 const button=document.getElementById('connect'),status=document.getElementById('status'),returnLink=document.getElementById('return');
 history.replaceState(null,'',location.pathname);
 if(location.origin!=='https://jrdn-r.github.io'||!/^[-_A-Za-z0-9]{43}$/.test(id||'')||!/^[-_A-Za-z0-9]{32,128}$/.test(state||'')){
  status.textContent='Open this page using Continue with Google inside the Vision iPhone app.';return;
 }
 let auth,sdk,expired=false,busy=false;
 const expiry=setTimeout(()=>{expired=true;button.disabled=true;status.textContent='This connection expired. Return to Vision and start sign-in again.';accountGoogleLogin.cancel();},300000);
 async function prepare(){
  sdk=window.VisionFirebaseSDK.auth;
  const app=window.VisionFirebaseSDK.app.initializeApp(config,'vision-ios-broker');
  auth=sdk.initializeAuth(app,{persistence:sdk.inMemoryPersistence});
  await Promise.all([auth.authStateReady(),accountGoogleLogin.prepare()]);
  if(!expired){button.disabled=false;status.textContent='Ready to connect your Vision account.';}
 }
 async function approve(user,accessToken){
  // Retry only a rejected Firebase token, once, without opening Google again.
  for(let attempt=0;attempt<2;attempt++){
   const token=await user.getIdToken(attempt===1);
   if(expired)return null;
   const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),25000);
   let response;
   try{response=await fetch(server+'/api/ios/auth/approve',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({id,state,googleAccessToken:accessToken}),signal:controller.signal,credentials:'omit',cache:'no-store',referrerPolicy:'no-referrer'});}finally{clearTimeout(timer);}
   const body=await response.json().catch(()=>({}));
   if(expired)return null;
   if(response.status===401&&attempt===0&&(!body.code||['sign-in-expired','sign-in-invalid'].includes(body.code)))continue;
   if(!response.ok)throw new Error('Google signed in, but FUPCJ Server could not connect your iPhone. '+(typeof body.error==='string'?body.error:'Please try again.'));
   return body;
  }
 }
 async function clearSession(){
  if(auth?.currentUser)await sdk.signOut(auth).catch(()=>{});
 }
 button.onclick=async()=>{
  if(expired||busy)return;busy=true;button.disabled=true;status.textContent='Choose a Google account in the secure browser window.';
  try{
   const accessToken=await accountGoogleLogin.request({selectAccount:true});
   if(expired)return;
   const result=await sdk.signInWithCredential(auth,sdk.GoogleAuthProvider.credential(null,accessToken));
   if(expired)return;
   status.textContent='Google account selected. Connecting to FUPCJ Server…';
   const body=await approve(result.user,accessToken);
   if(expired)return;
   if(!/^[-_A-Za-z0-9]{43}$/.test(body?.code||''))throw new Error('This connection expired. Return to Vision and try again.');
   const callback='visioncompanion://auth?'+new URLSearchParams({id,state,code:body.code});
   clearTimeout(expiry);expired=true;
   returnLink.href=callback;returnLink.hidden=false;button.hidden=true;
   status.textContent='Connected. Return to Vision to finish signing in.';
   await clearSession();
   location.href=callback;
  }catch(error){
   if(!expired)status.textContent=error?.name==='AbortError'?'FUPCJ Server took too long to respond. Return to Vision and try again.':error.message||'Sign-in could not finish.';
  }finally{
   await clearSession();
   busy=false;button.disabled=expired;
  }
 };
 prepare().catch(()=>{status.textContent='Google sign-in could not load. Close this window and try again.';});
})();
