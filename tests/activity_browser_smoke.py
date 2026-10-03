"""Browser regression checks using Chromium and a test-only Firebase/API stub.
Install Playwright separately. This tests the real HTML UI, not live Google sign-in.
Run from the repository: python tests/activity_browser_smoke.py
"""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'activity.html').read_text()
now=1791044000
fixture={'version':'v1','checkedAt':now,'users':[
 {'id':'a'*64,'name':'Jordan Owner','email':'one@example.test','firstSeen':now-1000,'lastSeen':now,'signIns':2,'eventCount':2,'lastActivity':now},
 {'id':'b'*64,'name':'Jordan Other','email':'two@example.test','firstSeen':now-1000,'lastSeen':now-5,'signIns':1,'eventCount':1,'lastActivity':now-5}],
 'events':[{'id':3,'userId':'a'*64,'at':now,'kind':'project_saved','details':{'httpStatus':200,'outcome':'accepted'}},
 {'id':2,'userId':'b'*64,'at':now-5,'kind':'video_uploaded','details':{'httpStatus':413,'outcome':'rejected'}},
 {'id':1,'userId':'a'*64,'at':now-10,'kind':'google_sign_in','details':{}}],
 'totalEvents':3,'hasMore':False}
MOCK=r'''<script>
window.__data=FIXTURE;window.__mode='ok';window.__reads=[];window.__tokenCalls=[];window.__held=[];
window.__owner={uid:'owner',email:'one@example.test',getIdToken:async force=>{window.__tokenCalls.push(!!force);return 'FAKE-TEST-TOKEN';}};
window.__auth={currentUser:window.__owner};
window.__appSDK={initializeApp:()=>({})};
window.__authSDK={getAuth:()=>window.__auth,useDeviceLanguage:()=>{},setPersistence:async()=>{},browserLocalPersistence:'local',GoogleAuthProvider:class{setCustomParameters(){}},onAuthStateChanged:(auth,cb)=>{window.__callback=cb;queueMicrotask(()=>cb(auth.currentUser));},signOut:async()=>{window.__auth.currentUser=null;window.__callback(null);},signInWithPopup:async()=>{}};
window.fetch=async(url,options)=>{
 window.__reads.push({url:String(url),headers:options.headers,credentials:options.credentials});
 if(window.__mode==='hold')return new Promise(resolve=>window.__held.push(()=>resolve(new Response(JSON.stringify(window.__data),{status:200}))));
 if(window.__mode==='offline')throw new TypeError('offline');
 if(window.__mode==='denied')return new Response(JSON.stringify({error:'Denied',code:'ACTIVITY_FORBIDDEN'}),{status:403});
 if(window.__mode==='expired'){window.__mode='ok';return new Response(JSON.stringify({error:'Expired'}),{status:401});}
 if(window.__mode==='missing')return new Response(JSON.stringify({error:'Not found'}),{status:404});
 const parsed=new URL(url),person=parsed.searchParams.get('user');let data=structuredClone(window.__data);
 if(person){data.events=data.events.filter(e=>e.userId===person);data.totalEvents=data.events.length;}
 data.version += ':'+(person||'all');
 if(parsed.searchParams.get('version')===data.version)return new Response(JSON.stringify({unchanged:true,version:data.version,checkedAt:Date.now()/1000}),{status:200});
 return new Response(JSON.stringify(data),{status:200});
};
</script>'''.replace('FIXTURE',json.dumps(fixture))
SDK="Promise.all([import('https://www.gstatic.com/firebasejs/12.19.0/firebase-app.js'),import('https://www.gstatic.com/firebasejs/12.19.0/firebase-auth.js')])"
TEST_HTML=HTML.replace(SDK,'Promise.resolve([window.__appSDK,window.__authSDK])').replace('<script type="module">',MOCK+'<script type="module">')
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path=__import__('os').environ.get('CHROMIUM_PATH',__import__('shutil').which('chromium') or __import__('shutil').which('chromium-browser')),headless=True,args=['--no-sandbox'])
 page=browser.new_page(viewport={'width':390,'height':844},timezone_id='America/New_York')
 errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.set_content(TEST_HTML);page.wait_for_selector('#dashboard:not([hidden])')
 assert page.locator('.event').count()==3
 assert page.locator('.person').count()==3
 assert page.locator('.person-copy strong').all_text_contents()==['Everyone','Jordan','Jordan']
 assert 'a'*64 not in page.locator('body').inner_text()
 print('PASS names, duplicate names, hidden identifiers')
 page.locator('.event details summary').first.click()
 assert page.locator('.event details[open]').count()==1
 page.evaluate("window.__data.version='v2';window.__data.users.push({id:'c'.repeat(64),name:'Andrea <img src=x onerror=alert(1)>',email:'three@example.test',firstSeen:1791044000,lastSeen:1791044000,signIns:1,eventCount:1});window.__data.events.unshift({id:4,userId:'c'.repeat(64),at:1791044001,kind:'google_sign_in',details:{}});window.__data.totalEvents=4;")
 page.wait_for_timeout(3500)
 assert page.locator('.event').count()==4
 assert page.locator('.person').count()==4
 assert page.locator('img').count()==0
 assert page.locator('.event details[open]').count()==1
 print('PASS automatic new user/event refresh, open details retained, safe text')
 page.locator('.filter[data-filter="issues"]').click();assert page.locator('.event').count()==1
 page.locator('.filter[data-filter="all"]').click()
 page.locator('.person[title="Jordan Other · two@example.test"]').click()
 page.wait_for_timeout(200);assert page.locator('.event').count()==1
 assert page.locator('#feedTitle').inner_text()=='Jordan’s activity'
 assert 'two@example.test' in page.locator('#feedSubtitle').inner_text()
 print('PASS event filters and selecting the correct same-name account')
 page.evaluate("window.__mode='offline'");page.locator('#refresh').click()
 page.wait_for_selector('#notice:not([hidden])')
 assert 'not live' in page.locator('#notice').inner_text()
 assert page.locator('#statusText').inner_text()=='Not connected'
 print('PASS offline indicator and stale-data warning')
 page.evaluate("window.__mode='expired'");page.locator('#refresh').click()
 page.wait_for_timeout(200);assert page.locator('#statusText').inner_text()=='Live · 3s'
 assert True in page.evaluate('window.__tokenCalls')
 print('PASS expired token refreshed once')
 page.evaluate("window.__mode='denied'");page.locator('#refresh').click()
 page.wait_for_selector('#gate:not([hidden])')
 assert page.locator('.event').count()==0 and page.locator('.person').count()==0
 assert page.locator('#dashboard').is_hidden()
 assert 'two@example.test' not in page.locator('body').inner_text()
 print('PASS authorization loss clears private records')
 page.evaluate("window.__mode='ok'");page.locator('#retry').click();page.wait_for_selector('#dashboard:not([hidden])')
 page.evaluate("window.__mode='hold'");page.locator('#refresh').click();page.wait_for_timeout(100)
 page.locator('#signOut').click();page.evaluate('window.__held.forEach(resolve=>resolve())');page.wait_for_timeout(100)
 assert page.locator('#dashboard').is_hidden() and page.locator('.event').count()==0
 print('PASS in-flight response cannot restore data after sign-out')
 for width in (320,390,768,1440):
  preview=browser.new_page(viewport={'width':width,'height':900},timezone_id='America/New_York')
  preview.set_content(HTML.replace("const DEMO=new URLSearchParams(location.search).get('demo')==='1';",'const DEMO=true;'))
  preview.wait_for_selector('#dashboard:not([hidden])')
  assert not preview.evaluate('document.documentElement.scrollWidth>innerWidth'),width
  assert preview.locator('#demoBanner').is_visible()
  # Screenshots are optional; no user data or authentication is used in this test.
  preview.close()
 print('PASS 320/390/768/1440 responsive layouts and explicit demo labeling')
 assert not errors,errors
 print('PASS zero application JavaScript errors')
 browser.close()
