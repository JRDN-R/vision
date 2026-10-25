// Exchange a Google Identity Services credential through the existing Firebase
// account. No Firebase hosted OAuth handler or cross-site sessionStorage is used.
const accountGoogleLogin=(()=>{
 const clientId='150865729216-1f7654bomu7se549rkofjku2fmjg98hu.apps.googleusercontent.com';
 let loading=null,pending=null;
 const error=(code,message)=>Object.assign(new Error(message),{code});
 const ready=()=>!!window.google?.accounts?.oauth2?.initTokenClient;
 function prepare(){
  if(ready())return Promise.resolve();
  if(loading)return loading;
  loading=new Promise((resolve,reject)=>{
   const script=document.createElement('script');let timer;
   const finish=err=>{clearTimeout(timer);script.onload=script.onerror=null;if(err){script.remove();reject(err);}else resolve();};
   script.src='https://accounts.google.com/gsi/client';script.async=true;
   script.onload=()=>finish(ready()?null:error('auth/network-request-failed','Google sign-in could not load.'));
   script.onerror=()=>finish(error('auth/network-request-failed','Google sign-in could not load.'));
   timer=setTimeout(()=>finish(error('auth/network-request-failed','Google sign-in could not load.')),12000);
   document.head.appendChild(script);
  }).catch(err=>{loading=null;throw err;});
  return loading;
 }
 function request({selectAccount=false}={}){
  if(pending)return Promise.reject(error('auth/cancelled-popup-request','A sign-in window is already open.'));
  if(!ready())return Promise.reject(error('auth/google-not-ready','Google sign-in is loading. Press Continue with Google again in a moment.'));
  return new Promise((resolve,reject)=>{
   let done=false,timer;
   const finish=(err,token)=>{if(done)return;done=true;clearTimeout(timer);pending=null;err?reject(err):resolve(token);};
   pending=()=>finish(error('auth/popup-closed-by-user','Google sign-in was cancelled.'));
   timer=setTimeout(()=>finish(error('auth/google-timeout','Google sign-in expired. Close the Google window and try again.')),180000);
   try{
    const client=window.google.accounts.oauth2.initTokenClient({
     client_id:clientId,scope:'openid email profile',include_granted_scopes:false,prompt:selectAccount?'select_account':'',
     callback:response=>{
      if(response?.error)return finish(error('auth/google-denied','Google sign-in was not completed. Please try again.'));
      if(typeof response?.access_token!=='string'||!response.access_token||response.access_token.length>20000)
       return finish(error('auth/google-invalid-credential','Google did not return a valid sign-in. Please try again.'));
      finish(null,response.access_token);
     },
     error_callback:response=>finish(error(response?.type==='popup_failed_to_open'?'auth/popup-blocked':response?.type==='popup_closed'?'auth/popup-closed-by-user':'auth/google-failed','Google sign-in could not finish. Please try again.'))
    });
    // Must run synchronously inside the user's click, before any await.
    client.requestAccessToken();
   }catch(err){finish(err);}
  });
 }
 return {prepare,ready,request,cancel:()=>pending?.()};
})();
