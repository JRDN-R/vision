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
 let auth,sdk,expired=false;
 const expiry=setTimeout(()=>{expired=true;button.disabled=true;status.textContent='This connection expired. Return to Vision and start sign-in again.';},300000);
 async function prepare(){
  sdk=window.VisionFirebaseSDK.auth;
  const app=window.VisionFirebaseSDK.app.initializeApp(config,'vision-ios-broker');
  auth=sdk.initializeAuth(app,{persistence:sdk.inMemoryPersistence});
  await Promise.all([auth.authStateReady(),accountGoogleLogin.prepare()]);
  if(!expired){button.disabled=false;status.textContent='Ready to connect your Vision account.';}
 }
 button.onclick=async()=>{
  if(expired)return;button.disabled=true;status.textContent='Complete Google sign-in in the secure browser window.';
  try{
   const accessToken=await accountGoogleLogin.request();
   if(expired)return;
   const result=await sdk.signInWithCredential(auth,sdk.GoogleAuthProvider.credential(null,accessToken));
   const token=await result.user.getIdToken();
   const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),25000);
   let response;
   try{response=await fetch(server+'/api/ios/auth/approve',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({id,state,googleAccessToken:accessToken}),signal:controller.signal,credentials:'omit',cache:'no-store',referrerPolicy:'no-referrer'});}finally{clearTimeout(timer);}
   const body=await response.json().catch(()=>({}));
   if(!response.ok)throw new Error(typeof body.error==='string'?body.error:'FUPCJ Server could not approve this connection.');
   if(expired||!/^[-_A-Za-z0-9]{43}$/.test(body.code||''))throw new Error('This connection expired. Return to Vision and try again.');
   const callback='visioncompanion://auth?'+new URLSearchParams({id,state,code:body.code});
   clearTimeout(expiry);expired=true;
   returnLink.href=callback;returnLink.hidden=false;button.hidden=true;
   status.textContent='Connected. Return to Vision to finish signing in.';
   await sdk.signOut(auth);
   location.href=callback;
  }catch(error){
   status.textContent=error?.name==='AbortError'?'FUPCJ Server took too long to respond. Return to Vision and try again.':error.message||'Sign-in could not finish.';
   button.disabled=expired;
  }
 };
 prepare().catch(()=>{status.textContent='Google sign-in could not load. Close this window and try again.';});
})();
