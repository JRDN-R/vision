"""Build the desktop HTML and the matching server-side trust manifest.

All application assets come from the normal portable builder. The small HTTPS
launch page reads this file through a file chooser without a cross-window
handoff. Its SHA-256 allowlist prevents arbitrary HTML running at the server origin.
"""
import argparse
import base64
import hashlib
from html import escape
import json
from pathlib import Path
import re

import build as portable

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'
PARTS = WEB / 'standalone'
SERVER = 'https://desktop-vjt2br2.tail385c9d.ts.net'
POLICY = ("default-src 'none'; img-src 'self' data: blob: https://www.gstatic.com; "
          "style-src 'unsafe-inline'; script-src 'unsafe-inline' 'wasm-unsafe-eval' https://apis.google.com https://www.gstatic.com https://visionboard-api.firebaseapp.com; "
          "worker-src blob:; media-src blob: data:; connect-src 'self' blob: https://identitytoolkit.googleapis.com https://securetoken.googleapis.com https://visionboard-api.firebaseapp.com https://www.googleapis.com/identitytoolkit/ https://www.gstatic.com; "
          "frame-src blob: https://visionboard-api.firebaseapp.com https://accounts.google.com https://apis.google.com; "
          "font-src data:; manifest-src 'none'; object-src 'none'; base-uri 'none'; form-action 'self'")


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('Standalone source marker changed: ' + old[:90])
    return source.replace(old, new, 1)


def render(source):
    shell = (PARTS / 'launcher.html').read_text(encoding='utf-8')
    shell = replace_once(shell, '__VISION_LAUNCHER_SCRIPT__', (PARTS / 'launcher.js').read_text(encoding='utf-8'))
    source = replace_once(source, (WEB / 'workspace-route.js').read_text(encoding='utf-8'), (PARTS / 'route.js').read_text(encoding='utf-8'))
    source = re.sub(r'<link\b[^>]*\brel="manifest"[^>]*>', '', source)
    source = re.sub(r'<meta http-equiv="Content-Security-Policy" content="[^"]+">',
                    lambda _: '<meta http-equiv="Content-Security-Policy" content="' + escape(POLICY, quote=True) + '">', source, count=1)
    # No update checks, hosted login bridge, or Google Identity script prefetch.
    source = replace_once(source, (WEB / 'local-app.js').read_text(encoding='utf-8'), 'let localBridgeCancel=null;\n')
    source = replace_once(source, (WEB / 'google-login.js').read_text(encoding='utf-8'),
                          'const accountGoogleLogin={prepare:async()=>{},ready:()=>true,cancel:()=>{}};\n')
    start = source.index('async function accountGoogleSignIn(){')
    end = source.index('function accountEmailFields(', start)
    source = source[:start] + '''async function accountGoogleSignIn(){
 if(accountBusy||trialStarting)return;
 if(!accountFirebase||accountRestoring||accountRestoreFailed){void accountLoad().catch(()=>{});return;}
 accountBusy=true;accountError='';accountPaint();
 try{
  // The bundled Firebase SDK opens the popup synchronously from this click.
  const result=accountFirebase.currentUser?{user:accountFirebase.currentUser}:await accountSDK.signInWithPopup(accountFirebase,new accountSDK.GoogleAuthProvider(),accountSDK.browserPopupRedirectResolver);
  await accountTransition;await accountSetUser(result.user);
  $('accountDialog').close();openProjectsMenu();void accountRestoreRunKey();
 }catch(error){accountError=accountErrorText(error);}finally{accountBusy=false;accountPaint();}
}
''' + source[end:]
    source = replace_once(source,
        "const [appSDK,authSDK]=bundled?[bundled.app,bundled.auth]:await accountWait(Promise.all([import('https://www.gstatic.com/firebasejs/12.8.0/firebase-app.js'),import('https://www.gstatic.com/firebasejs/12.8.0/firebase-auth.js')]),'Sign-in could not load. Check your connection and retry.');",
        "if(!bundled)throw new Error('The embedded Firebase runtime is missing. Download a fresh standalone HTML.');\n  const [appSDK,authSDK]=[bundled.app,bundled.auth];")
    source = source.replace('Add jrdn-r.github.io to Firebase Authentication → Settings → Authorized domains.',
                            'Add desktop-vjt2br2.tail385c9d.ts.net to Firebase Authentication → Settings → Authorized domains, then retry Google sign-in. Email/password sign-in is also available.')
    # Provider profile photos are not an application dependency. Keep account
    # initials or the existing FUPCJ-owned, authenticated blob avatar instead.
    source = source.replace('const photo=signed&&(ventureAvatarObjectURL||user?.photoURL);', 'const photo=signed&&ventureAvatarObjectURL;')
    source = source.replace('const photo=ventureAvatarObjectURL||user?.photoURL;', 'const photo=ventureAvatarObjectURL;')
    source, count = re.subn(r"const accountContinueURL=\(\(\)=>\{.*?\}\)\(\);", "const accountContinueURL='';", source, count=1)
    if count != 1:
        raise ValueError('Missing standalone sign-in continuation marker')
    runtime = (PARTS / 'runtime.js').read_text(encoding='utf-8')
    runtime = replace_once(runtime, '__VISION_LAUNCHER_TEMPLATE__', json.dumps(shell).replace('</', '<\\/'))
    source = replace_once(source, '\nrenderAll();\n})();\n', '\n' + runtime + '\nrenderAll();\n})();\n')
    source = source.replace('<title>Vision — advance AI prompt generator</title>', '<title>Vision — Standalone</title>')
    source = source.replace('</head>', '<meta name="vision-edition" content="standalone-https">\n</head>', 1)
    if 'localCheckUpdates' in source or 'https://raw.githubusercontent.com/JRDN-R/vision/main/\'' in source:
        raise ValueError('Standalone app retained an automatic update client')
    return source, shell


def build(rebuild=True, output=None):
    if rebuild:
        portable.build()
    source, shell = render((ROOT / 'Vision.html').read_text(encoding='utf-8'))
    data = source.encode('utf-8')
    digest = hashlib.sha256(data).hexdigest()
    version = re.search(r'<meta name="vision-version" content="([^"]+)"', source)[1]
    manifest = dict(version=version, sha256=digest, size=len(data), server=SERVER, policy=POLICY)
    (ROOT / 'vision-pc/standalone-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    target = Path(output) if output else ROOT / 'Vision-Standalone.html'
    target.write_text(replace_once(shell, '__VISION_EMBEDDED_APP__', base64.b64encode(data).decode('ascii')), encoding='utf-8')
    print(f'Built {target.name}: {target.stat().st_size:,} bytes; app SHA256 {digest}')
    return target, manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-rebuild', action='store_true')
    parser.add_argument('--output')
    args = parser.parse_args()
    build(not args.no_rebuild, args.output)
