from pathlib import Path

def edit(name, replacements):
    p=Path(name); text=p.read_text()
    for old,new in replacements:
        assert old in text,(name,old[:120])
        text=text.replace(old,new,1)
    p.write_text(text)

edit('web/build.py', [("'projects.js', 'auth.js'", "'projects.js', 'launch.js', 'auth.js'")])
p=Path('web/document.html');text=p.read_text();a=text.index('<section id="accountGate"');b=text.index('</section>',a)+len('</section>')
text=text[:a]+'''<section id="accountGate" class="account-gate" aria-labelledby="accountGateTitle">
 <div class="account-gate-card">
  <img class="account-gate-logo" src="{{VISION_ASSET_77187dc76a61545b317d80ad11f628d36f69d191e16fd2ca9c55263aad5b7b66}}" alt="Vision" width="96" height="30">
  <button id="whyVisionButton" class="why-vision-button" type="button" aria-haspopup="dialog" aria-controls="whyVisionDialog">Why use Vision? <span aria-hidden="true">↗</span></button>
  <h1 id="accountGateTitle">Your workspace, connected.</h1>
  <p id="accountGateDetail">Your projects, files, and saved settings are stored on FUPCJ Server. Google is used for sign-in.</p>
  <button id="accountGateSignIn" type="button" class="primary">Sign in with Google</button>
  <button id="accountGateTrial" type="button" aria-describedby="accountTrialWarning">Or try for five minutes</button>
  <p id="accountTrialWarning" class="account-trial-warning">No projects are saved on temporary accounts. The one-time timer starts when you enter, including processing time. Trial work is cleared when time runs out or you sign in.</p>
  <a id="accountGateOnline" class="account-gate-online" href="https://jrdn-r.github.io/vision/" hidden>Open Vision online to sign in ↗</a>
  <p id="accountGateStatus" role="status" aria-live="polite">Checking sign-in…</p>
  <details class="trial-details"><summary>About the one-time trial</summary><p>A browser receipt and a protected network identifier help prevent repeat trials. Shared Wi-Fi can share one trial. Google sign-in is not affected. Processing requires FUPCJ Server to be online.</p></details>
 </div>
 <dialog id="whyVisionDialog" aria-labelledby="whyVisionTitle">
  <div class="why-vision-heading"><span class="why-vision-eyebrow">BETTER CONTEXT. CLEARER ANSWERS.</span><button id="whyVisionClose" class="dialog-close" type="button" aria-label="Close Why use Vision">×</button></div>
  <h2 id="whyVisionTitle">Why use Vision?</h2>
  <p class="why-vision-lead">Give AI the whole picture.<br>Not one overwhelming prompt.</p>
  <p>Map your files, videos, links, and notes, then show how they connect. Vision turns your project into organized, AI-ready context for ChatGPT, Claude, Gemini, or your model of choice.</p>
  <p>Process videos and YouTube links into transcripts and timestamped screenshots, with optional sound descriptions. Give your AI clearer evidence so it can focus on the actual task.</p>
  <p>Run AI inside your project with your own API key. Sign in with Google to save projects to FUPCJ Server for free, pick up on another computer, browse your Google Drive, and search YouTube without leaving Vision.</p>
  <p class="why-vision-note">Google handles sign-in. FUPCJ Server stores and processes your project data; connected AI providers receive what you submit to them. API usage may have provider charges.</p>
  <button id="whyVisionDone" type="button" class="primary">Explore Vision</button>
 </dialog>
</section>
<div id="trialBanner" class="trial-banner" hidden><div><strong>Temporary trial</strong> <span id="trialCountdown" role="timer" aria-live="off">5:00 left</span><small>Projects are not saved.</small></div><button id="trialSignIn" type="button">Sign in with Google</button></div>'''+text[b:];p.write_text(text)
edit('web/auth.js', [
 ('const locked=!accountSignedIn();','const locked=!accountCanUseApp();'),
 ("document.querySelectorAll('dialog[open]')","document.querySelectorAll('dialog[open]:not(#whyVisionDialog)')"),
 ("(accountBusy?'Connecting to Google…':'Sign in with your Google account to continue.')","(trialStarting?'Starting your five-minute trial…':accountBusy?'Connecting to Google…':'Sign in to save your work, or explore without an account.')"),
 ("$('accountGateSignIn').disabled=accountBusy;","$('accountGateSignIn').disabled=accountBusy||trialStarting;trialPaint();"),
 ("if(accountSignedIn()&&!$('accountDialog').open)","if(accountCanUseApp()&&!$('accountDialog').open)"),
 (' if(accountLastUID===uid)return;accountLastUID=uid;'," if(accountLastUID===uid)return;\n if(user&&(trialSession||cloudAuth?.kind==='trial'))await trialFinish({message:''});\n accountLastUID=uid;"),
 (" if(accountBusy)return;accountBusy=true;accountError='';accountPaint();"," if(accountBusy||trialStarting)return;accountBusy=true;accountError='';accountPaint();"),
 (' if(accountSignedIn())return;\n const inGate=',' if(accountCanUseApp())return;\n const inGate='),
 ('new MutationObserver(()=>{if(!accountSignedIn())accountUpdateGate();})','new MutationObserver(()=>{if(!accountCanUseApp())accountUpdateGate();})')
])
edit('web/cloud.js', [
 ("cloudAuth.kind!=='firebase-google'","!['firebase-google','trial'].includes(cloudAuth.kind)"),
 (" if(typeof accountSignedIn==='function'&&!accountSignedIn()){"," if(cloudAuth?.kind==='trial'){if(!trialActive())throw new Error('Your trial has ended. Sign in with Google.');return cloudAuth.accessToken;}\n if(typeof accountSignedIn==='function'&&!accountSignedIn()){"),
 ('async function cloudFetch(path,options={}){\n',"async function cloudFetch(path,options={}){\n if(cloudAuth?.kind==='trial'){if(!/^\\/[a-z]/i.test(path)||path.includes('..'))throw new Error('Invalid service request.');return trialFetch(path,options);}\n"),
 ("const privateConnection=cloudConfig?.kind==='private-pc'","const privateConnection=cloudAuth?.kind!=='trial'&&cloudConfig?.kind==='private-pc'"),
 ("function youtubeConnected(){if(typeof accountSignedIn==='function'&&!accountSignedIn())return false;","function youtubeConnected(){if(typeof accountCanUseApp==='function'&&!accountCanUseApp())return false;"),
 ("cloudAuth.kind==='private-pc'?cloudAuth.accessToken","['private-pc','trial'].includes(cloudAuth.kind)?cloudAuth.accessToken")
])
edit('web/projects.js', [
 ('function projectAccountUID(){',"function projectIsTemporary(scope=projectStorageScope){return scope.startsWith('trial:')||cloudAuth?.kind==='trial';}\nfunction projectAccountUID(){"),
 ('function ensureProjectIdentity(){\n',"function ensureProjectIdentity(){\n if(projectIsTemporary()&&typeof trialSession!=='undefined'&&trialSession){state.projectCloud={...trialSession.project};return state.projectCloud;}\n"),
 ('function projectLocalGet(key,scope=projectStorageScope){','function projectLocalGet(key,scope=projectStorageScope){if(projectIsTemporary(scope))return null;'),
 ('function projectLocalSet(key,value,scope=projectStorageScope){','function projectLocalSet(key,value,scope=projectStorageScope){if(projectIsTemporary(scope))return false;'),
 ('function projectRemember(meta,title,updatedAt=new Date().toISOString()){','function projectRemember(meta,title,updatedAt=new Date().toISOString()){\n if(projectIsTemporary())return;'),
 ('async function projectStorePut(record,{activate=true,scope=projectStorageScope}={}){','async function projectStorePut(record,{activate=true,scope=projectStorageScope}={}){\n if(projectIsTemporary(scope))return false;'),
 ('async function projectStoreGet(id,scope=projectStorageScope){','async function projectStoreGet(id,scope=projectStorageScope){\n if(projectIsTemporary(scope))return null;'),
 ('async function projectBackup(){','async function projectBackup(){\n if(projectIsTemporary())return;'),
 ('function projectQueueSave(){',"function projectQueueSave(){\n if(projectIsTemporary()){projectGeneration++;projectPending=false;projectStatus('Temporary trial · projects are not saved');return;}"),
 (" if(!has('persistentProjects')||!has('projectRevision'))"," if(!(projectIsTemporary()&&has('temporarySessions'))&&(!has('persistentProjects')||!has('projectRevision')))"),
 ('async function projectPerformSave(){','async function projectPerformSave(){\n if(projectIsTemporary())return ensureProjectIdentity();'),
 ('async function flushProjectSave(){','async function flushProjectSave(){\n if(projectIsTemporary())return ensureProjectIdentity();'),
 ('async function projectReconcile({force=false}={}){','async function projectReconcile({force=false}={}){\n if(projectIsTemporary())return;'),
 ('function openProjectsMenu(){',"function openProjectsMenu(){if(projectIsTemporary()){openAccountDialog('Sign in with Google to save projects. Temporary trial work will be cleared when you sign in.');return;}"),
 ('saveProject=function(){','saveProject=function(){if(projectIsTemporary()){openProjectsMenu();return;}'),
 ('openProject=async function(file){',"openProject=async function(file){if(projectIsTemporary()){openAccountDialog('Sign in with Google to open saved projects. You can still add files and videos to the trial board.');return;}")
])
edit('tests/account-auth.test.cjs', [("projectSource+'\\n'+authSource","projectSource+'\\n'+fs.readFileSync('web/launch.js','utf8').split(\"$('accountGateTrial').onclick\")[0]+'\\n'+authSource")])
p=Path('web/auth.css');p.write_text(p.read_text()+'''
/* Launch additions; the signed-in workspace and header logo remain unchanged. */
.account-gate-logo{display:block;width:96px;height:auto;max-height:32px;object-fit:contain;object-position:left center;margin:0 0 20px}
.account-gate-card .why-vision-button{width:auto;min-height:36px;margin:0 0 23px;padding:8px 12px;border:1px solid #b7a2ed70;background:#b7a2ed1c;color:#ddcfff;font-size:13px;display:flex;align-items:center;gap:22px;text-align:left}
.account-gate-card .why-vision-button:hover{background:#b7a2ed32}
.account-gate-card #accountGateTrial{margin-top:10px;background:transparent;border:1px solid #9daea164;color:#e2ebdf}
.account-gate-card #accountGateTrial:disabled{opacity:.6;cursor:default}
.account-gate-card .account-trial-warning{font-size:11px;line-height:1.55;color:#b0bdb0;margin:13px 0 0}
.account-gate-card #accountGateStatus{font-size:12px;min-height:1.5em;margin:15px 0 0}
.trial-details{font-size:11px;color:#adb9ae;margin-top:12px}.trial-details summary{cursor:pointer}.account-gate-card .trial-details p{font-size:11px;line-height:1.5}
#whyVisionDialog{width:min(520px,calc(100vw - 32px));max-height:calc(100dvh - 40px);overflow:auto;margin:auto;padding:28px;border:1px solid #9daea14d;border-radius:20px;background:#18221d;color:#eef4ed;box-shadow:0 24px 90px #0009}
#whyVisionDialog::backdrop{background:#070b09bd;backdrop-filter:blur(8px)}
.why-vision-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}.why-vision-eyebrow{color:#cdbbee;font-size:10px;letter-spacing:.12em;font-weight:600}
#whyVisionDialog h2{font-size:30px;letter-spacing:-.04em;line-height:1.2;margin:18px 0 14px}
#whyVisionDialog .why-vision-lead{font-size:20px;line-height:1.4;color:#eef4ed;font-weight:550;letter-spacing:-.02em}
#whyVisionDialog p{font-size:14px;line-height:1.65;color:#c1cec2;margin:16px 0}
#whyVisionDialog .why-vision-note{font-size:11px;color:#a4b2a5;border-top:1px solid #9daea12b;padding-top:16px}
#whyVisionDialog .dialog-close{background:transparent;color:#d4dfd2;border:0}#whyVisionDone{width:100%;min-height:43px;border-radius:10px;margin-top:8px}
.trial-banner{position:fixed;right:16px;bottom:16px;z-index:2147483000;display:flex;align-items:center;gap:18px;max-width:calc(100vw - 32px);padding:12px 16px;border:1px solid #b7a2ed70;border-radius:14px;background:#202029f5;color:#f1edf8;box-shadow:0 4px 24px #0004;font-size:12px}
.trial-banner[hidden]{display:none}.trial-banner strong{margin-right:8px}.trial-banner small{display:block;color:#c4bcce;margin-top:4px}
.trial-banner button{background:#cab6f1;border:0;color:#241936;border-radius:8px;min-height:36px;padding:8px 12px;font-size:12px}
@media(max-width:480px){#accountGate{padding:18px;align-items:safe center}.account-gate-card{padding:26px 24px}.account-gate-card h1{font-size:27px}.account-gate-card #accountGateSignIn{margin-top:22px}#whyVisionDialog{padding:22px}.trial-banner{right:10px;bottom:max(10px,env(safe-area-inset-bottom));max-width:calc(100vw - 20px);gap:10px;padding:11px 12px}}
@media(max-height:690px){#accountGate{align-items:flex-start}.account-gate-card{margin:auto 0}}
#accountGateTrial[hidden],#accountGateSignIn[hidden]{display:none}
#whyVisionButton:focus-visible,#accountGateTrial:focus-visible,#whyVisionDialog button:focus-visible{outline:3px solid #ddcfff;outline-offset:3px}
''')
