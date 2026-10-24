/* Installed only by the signed iOS container. Native code checks the main frame
 * and exact Vision origin before accepting any message. */
let accountNativeBridge=null;
function accountInstallNativeBridge(auth,sdk){
 if(!window.VisionNative?.isNative||!window.webkit?.messageHandlers?.vision)return;
 if(accountNativeBridge?.auth===auth)return;
 accountNativeBridge?.stop();
 let sequence=0,active=true;
 const post=value=>{try{const reply=window.webkit.messageHandlers.vision.postMessage(value);reply?.catch?.(()=>{});}catch{}};
 window.VisionNativeReceiveGoogle=async accessToken=>{
  if(typeof accessToken!=='string'||!accessToken||accessToken.length>20000)throw new Error('Invalid native sign-in.');
  return sdk.signInWithCredential(auth,sdk.GoogleAuthProvider.credential(null,accessToken)).then(()=>true);
 };
 window.VisionNativeSignOut=()=>sdk.signOut(auth).then(()=>true);
 window.VisionNativeSelectWorkspace=view=>{if(['vision','venture'].includes(view))workspaceSwitch(view);};
 const unsubscribe=sdk.onIdTokenChanged(auth,async user=>{
  if(!active)return;
  const current=++sequence;
  if(!user){post({type:'session',signedIn:false});return;}
  try{
   const result=await user.getIdTokenResult();
   if(!active||current!==sequence||auth.currentUser?.uid!==user.uid)return;
   post({type:'session',signedIn:true,uid:user.uid,email:user.email||'',idToken:result.token,refreshToken:user.refreshToken,expiresAt:Date.parse(result.expirationTime)/1000});
  }catch{if(active&&current===sequence&&auth.currentUser?.uid===user.uid)post({type:'sessionError',message:'Your sign-in could not be shared with Vortex. Reconnect in Vision.'});}
 });
 accountNativeBridge={auth,stop(){active=false;sequence++;unsubscribe?.();}};
 post({type:'ready'});
}
