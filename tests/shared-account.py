"""Shared account UI with the real handlers and mocked, nonbillable server I/O."""
import base64
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / 'tests/venture-screenshots'
SHOTS.mkdir(parents=True, exist_ok=True)
SHELL = (ROOT / 'Vision.html').read_text()
if 'const sharedAccount=' in SHELL:
    start = SHELL.index('const sharedAccount=')
    end = SHELL.index('\nsharedAccountInstall();', start) + len('\nsharedAccountInstall();')
    SHELL = SHELL[:start] + (ROOT / 'web/account-profile.js').read_text() + SHELL[end:]
else:
    point = SHELL.rfind('\n})();')
    SHELL = SHELL[:point] + '\n' + (ROOT / 'web/account-profile.js').read_text() + SHELL[point:]
    SHELL = SHELL.replace('</head>', '<style>' + (ROOT / 'web/account-profile.css').read_text() + '</style></head>', 1)
PROBE = r'''
let profileTestUser='',profileTestAvatar=false,profileTestVersion=0;
window.profileRequests=[];window.profileDelayed=[];
accountSignedIn=()=>!!profileTestUser;accountCanUseApp=()=>!!profileTestUser;
cloudFetch=async(path,options={})=>{
 const entry={path,method:options.method||'GET',uid:profileTestUser};profileRequests.push(entry);
 if(window.profileHold===path&&(!window.profileHoldMethod||window.profileHoldMethod===entry.method)){await new Promise(resolve=>profileDelayed.push(resolve));}
 let value={};
 if(path==='/health')value={capabilities:{ventureV2:true}};
 else if(path==='/venture/profile'){
  if(entry.method==='PUT'){profileTestAvatar=true;profileTestVersion++;}
  if(entry.method==='DELETE')profileTestAvatar=false;
  value={hasAvatar:profileTestAvatar,avatarVersion:profileTestVersion};
 }else if(path==='/venture/profile/avatar')return new Response(window.profilePixelBlob);
 else if(path==='/venture/funding')value={status:'available',fraction:.7,revision:1,updatedAt:1791320000};
 else if(path==='/venture/preferences')value={settings:VENTURE_DEFAULTS};
 else if(path==='/venture/conversations')value={conversations:[]};
 else if(path==='/venture/models')value={status:'available',models:[{id:'gpt-6-astra'}]};
 else if(path==='/account/openai-key')value={saved:entry.method!=='DELETE'};
 return new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}});
};
window.profileSetUser=uid=>{
 profileTestUser=uid;profileTestAvatar=false;profileTestVersion=0;
 accountFirebase={currentUser:uid?{uid,displayName:uid==='alice'?'Alice Example':'Bob Example',email:uid+'@example.test'}:null};
 cloudAuth=uid?{kind:'firebase-google',uid,email:uid+'@example.test'}:null;
 workspace.uid=uid;
 if(uid)navigation.receipts.add('vision-release-seen-v1:'+uid+':'+navigationVersion());
 document.querySelectorAll('[data-account-inert]').forEach(el=>{el.inert=false;el.removeAttribute('data-account-inert');});
 accountUpdateGate();navigationSync();
};
accountGoogleSignOut=async()=>profileSetUser('');
window.profileState=()=>({open:venture.open,uid:venture.uid,custom:!!ventureAvatarObjectURL,board:state.title});
window.profileResolve=()=>{window.profileHold=null;window.profileHoldMethod=null;for(const resolve of profileDelayed.splice(0))resolve();};
'''
point = SHELL.rfind('\n})();')
SHELL = SHELL[:point] + PROBE + SHELL[point:]
URL = 'http://127.0.0.1:8899/'
PIXEL = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j4z8AAAAASUVORK5CYII=')


def outside(page, selector):
    box = page.locator(selector).bounding_box()
    page.mouse.click(2, max(2, box['y'] - 4))


def run():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, **({'executable_path': os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}))
        for width in (320, 390, 1280):
            context = browser.new_context(viewport={'width': width, 'height': 844}, has_touch=width < 761)
            context.route('**/*', lambda route: route.fulfill(body=SHELL, content_type='text/html') if route.request.url == URL else route.abort())
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(URL)
            page.wait_for_timeout(450)
            page.evaluate("profileSetUser('alice')")
            page.locator('#workspaceMenuButton').click()
            page.locator('#workspaceProfileButton').wait_for(state='visible')
            assert page.locator('#workspaceProfileButton .workspace-profile-name').inner_text() == 'Alice Example'
            page.locator('#workspaceProfileButton').click()
            assert page.locator('#sharedAccountDialog').is_visible()
            assert page.locator('#workspaceMenu').is_visible()
            assert page.locator('#visionVenture').is_hidden(), 'Profile access switched away from Vision'
            assert page.locator('#sharedAccountWorkspace').inner_text() == 'Open Venture'
            before = page.evaluate('profileState().board')
            page.locator('.venture-key-details summary').click()
            if width == 390:
                page.screenshot(path=str(SHOTS / 'shared-profile.png'))
            page.locator('#ventureKey').fill('fixture-openai-key')
            page.locator('#ventureSaveKey').click()
            page.wait_for_function("()=>document.getElementById('ventureKeyStatus').textContent.startsWith('Saved encrypted')")
            assert page.evaluate("profileRequests.some(r=>r.path==='/account/openai-key'&&r.method==='PUT'&&r.uid==='alice')")
            page.locator('#ventureKey').fill('unsaved-fixture-key')
            page.locator('#ventureCalibrate').click()
            assert page.locator('#ventureBalanceDialog').is_visible()
            outside(page, '#ventureBalanceDialog')
            assert page.locator('#ventureBalanceDialog').is_hidden()
            assert page.locator('#sharedAccountDialog').is_visible()
            assert page.locator('#ventureKey').input_value() == 'unsaved-fixture-key'
            page.locator('#ventureCalibrate').click()
            page.locator('#ventureBalanceAmount').fill('10')
            page.locator('#ventureBalanceSubmit').click()
            page.locator('#ventureBalanceDialog').wait_for(state='hidden')
            assert page.locator('#sharedAccountDialog').is_visible(), 'Saving a balance closed the parent profile'
            page.locator('#ventureConnection').click()
            assert page.locator('#cloudDialog').is_visible()
            outside(page, '#cloudDialog')
            assert page.locator('#sharedAccountDialog').is_visible()
            # Avatar uploads use the original compression and server handlers.
            page.evaluate("async()=>window.profilePixelBlob=await new Promise(resolve=>{const c=document.createElement('canvas');c.width=c.height=2;c.toBlob(resolve,'image/png');})")
            page.locator('#ventureAvatarInput').set_input_files({'name': 'profile.png', 'mimeType': 'image/png', 'buffer': PIXEL})
            page.wait_for_function("()=>document.getElementById('ventureAvatarStatus').textContent.includes('saved for this account')")
            assert page.locator('#workspaceProfileButton img').count() == 1
            assert page.locator('#ventureProfileButton img').count() == 1
            page.locator('#ventureRemoveAvatar').click()
            page.wait_for_function("()=>document.getElementById('ventureAvatarStatus').textContent.includes('account picture again')")
            assert page.locator('#workspaceProfileButton img').count() == 0
            outside(page, '#sharedAccountDialog')
            assert page.locator('#sharedAccountDialog').is_hidden()
            assert page.locator('#workspaceMenu').is_visible()
            assert page.locator('#ventureKey').input_value() == ''
            assert page.evaluate('document.activeElement.id') == 'workspaceProfileButton'
            assert page.evaluate('profileState().board') == before
            page.locator('#workspaceProfileButton').click()
            page.locator('#ventureCalibrate').click()
            page.locator('#ventureBalanceAmount').fill('10')
            page.evaluate("window.profileHold='/venture/funding';window.profileHoldMethod='POST'")
            page.locator('#ventureBalanceSubmit').click()
            page.wait_for_function('()=>profileDelayed.length>0')
            outside(page, '#ventureBalanceDialog')
            outside(page, '#sharedAccountDialog')
            page.evaluate('profileResolve()')
            page.wait_for_timeout(120)
            assert page.locator('#sharedAccountDialog').is_hidden(), 'A delayed balance save reopened a dismissed profile'
            page.locator('#workspaceProfileButton').click()
            page.locator('#sharedAccountWorkspace').click()
            page.locator('#visionVenture').wait_for(state='visible')
            page.locator('#workspaceMenuButton').click()
            page.locator('#ventureProfileButton').click()
            assert page.locator('#sharedAccountWorkspace').inner_text() == 'Return to Vision board'
            outside(page, '#sharedAccountDialog')
            assert page.locator('#visionVenture').is_visible()
            assert page.locator('#ventureSidebar').is_visible()
            page.keyboard.press('Escape')
            page.locator('#ventureAvatar').click()
            assert page.locator('#sharedAccountDialog').is_visible()
            page.keyboard.press('Escape')
            assert page.locator('#sharedAccountDialog').is_hidden()
            assert page.evaluate('document.activeElement.id') == 'ventureAvatar'
            page.locator('#workspaceMenuButton').click()
            # Delayed account A replies cannot populate the new account's UI.
            page.evaluate("window.profileHold='/venture/profile';")
            page.locator('#ventureProfileButton').click()
            page.wait_for_function('()=>profileDelayed.length>0')
            page.evaluate("profileSetUser('bob');profileResolve();")
            page.wait_for_timeout(150)
            assert page.locator('#sharedAccountDialog').is_hidden()
            assert page.locator('#ventureKey').input_value() == ''
            assert page.locator('#workspaceProfileButton .workspace-profile-name').inner_text() == 'Bob Example'
            assert not page.evaluate('profileState().custom')
            page.locator('#workspaceMenuButton').click()
            page.locator('#workspaceProfileButton').click()
            page.locator('#ventureSignOut').click()
            assert page.locator('#sharedAccountDialog').is_hidden()
            assert page.locator('#workspaceProfileButton').is_hidden()
            assert not errors, errors
            print(f'{width}px: shared profile, upload/remove, existing key save, nested dismissal, workspace return, account isolation passed')
            context.close()
        browser.close()


if __name__ == '__main__':
    run()
