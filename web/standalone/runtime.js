// Inserted inside the existing application closure. Firebase remains the only
// browser-side identity service; API requests use the current server origin.
const standaloneShell=__VISION_LAUNCHER_TEMPLATE__;
function standaloneFileSource(source){
 const bytes=new TextEncoder().encode(source);let binary='';
 for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
 return standaloneShell.replace('__VISION_EMBEDDED_APP__',()=>btoa(binary));
}
downloadDesktopApp=function(){
 if(!window.VisionStandaloneSource){toast('Open the original Vision-Standalone.html file to download this edition.',true);return;}
 download(new Blob([standaloneFileSource(window.VisionStandaloneSource)],{type:'text/html;charset=utf-8'}),'Vision-Standalone.html');
 $('downloadAppDialog').close();toast('Standalone HTML downloaded.');
};
$('confirmDownloadApp').onclick=downloadDesktopApp;
$('downloadAppDialog').querySelector('.intro').textContent='One HTML contains the interface, fonts, artwork, Firebase SDK and media runtimes. Open it and press Open Vision. FUPCJ Server provides the secure launch connection; Firebase provides sign-in.';

// Direct Drive picking and YouTube search need additional Google services. The
// standalone edition accepts device files and pasted video links through FUPCJ.
openDriveDialog=function(){toast('Download the file from Drive, then add it from your device.',true);};
searchYouTubeVideos=async function(){toast('Paste a YouTube video link to process it on FUPCJ Server.',true);};
for(const id of ['boardAddDrive','addAttachmentsDrive','youtubeSearch','youtubeMore'])$(id)?.style.setProperty('display','none','important');
$('youtubeURL').placeholder='Paste a YouTube video link';
const standaloneYouTubePaint=syncYouTubeSearchUI;
syncYouTubeSearchUI=function(){standaloneYouTubePaint();$('youtubeSearch').disabled=true;$('youtubeMore').disabled=true;};

// Browser speech recognition may contact an unrelated vendor. Venture's own
// microphone uses the existing FUPCJ transcription API and remains available.
consoleStartDictation=function(){toast('Use the Venture microphone for FUPCJ transcription, or type your message.');};
$('consoleMic').onclick=consoleStartDictation;

// Separate hosted applications are not part of this one-file edition.
for(const link of document.querySelectorAll('a[href]')){
 try{const url=new URL(link.href,location.href);if(/\/(?:vortex|admins)\/$/.test(url.pathname)||/\/activity\.html$/.test(url.pathname))link.style.setProperty('display','none','important');}catch{}
}

// Send only a readiness receipt, never an identity token or project, to the
// local launcher. The nonce and source window are checked on both sides.
window.addEventListener('load',()=>{
 const launch=window.VisionStandaloneLaunch;if(!launch)return;
 try{launch.sender.postMessage({type:'vision-standalone-opened',nonce:launch.nonce},launch.origin==='null'?'*':launch.origin);}catch{}
 delete window.VisionStandaloneLaunch;
},{once:true});
