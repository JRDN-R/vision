"""Browser regression checks using Chromium and a test-only Firebase/API stub.
Install Playwright separately. This tests the real HTML UI, not live Google sign-in.
Run from the repository: python tests/activity_browser_smoke.py
"""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/('activity.html' if (ROOT/'activity.html').exists() else 'index.html')).read_text()
now=1791044000
fixture={'version':'v1','checkedAt':now,'users':[
 {'id':'a'*64,'provider':'google','apps':[{'app':'vision','lastSeen':now},{'app':'vortex','lastSeen':now}],'name':'Jordan Owner','email':'one@example.test','firstSeen':now-1000,'lastSeen':now,'signIns':2,'eventCount':2,'lastActivity':now},
 {'id':'b'*64,'provider':'password','apps':[{'app':'vortex','lastSeen':now}],'name':'Jordan Other','email':'two@example.test','firstSeen':now-1000,'lastSeen':now-5,'signIns':1,'eventCount':1,'lastActivity':now-5}],
 'events':[{'id':3,'userId':'a'*64,'at':now,'kind':'project_saved','details':{'httpStatus':200,'outcome':'accepted'}},
 {'id':2,'userId':'b'*64,'at':now-5,'kind':'video_uploaded','details':{'httpStatus':413,'outcome':'rejected'}},
 {'id':1,'userId':'a'*64,'at':now-10,'kind':'google_sign_in','details':{}}],
 'totalEvents':3,'hasMore':False}
gemini={'accessRequests':[{'userId':'b'*64,'provider':'password','apps':[{'app':'vortex','lastSeen':now}],'name':'Jordan Other','email':'two@example.test','status':'pending','requestedAt':now-60,'waitingJobs':2}], 'pendingCount':1,
 'usage':{'requests':[{'id':'request-1','userId':'a'*64,'provider':'google','apps':[{'app':'vision','lastSeen':now},{'app':'vortex','lastSeen':now}],'name':'Jordan Owner','email':'one@example.test','projectId':'project-1','jobId':'job-1','kind':'sound','eventStart':14.2,'eventEnd':18.6,'clipDuration':7.4,'model':'gemini-sound-test','status':'succeeded','startedAt':now-30,'finishedAt':now-28,'httpStatus':200,'inputTokens':214,'outputTokens':38,'thoughtTokens':0,'totalTokens':252,'billableTokens':252,'rawUsage':{'promptTokenCount':214,'candidatesTokenCount':38,'totalTokenCount':252},'estimate':{'currency':'USD','cost':.00042,'available':True,'pricingVersion':'pricing-v2'},'historicalEstimate':{'currency':'USD','cost':.00036,'available':True,'pricingVersion':'pricing-v1'}}],
 'totals':{'requests':1,'inputTokens':214,'outputTokens':38,'thoughtTokens':0,'totalTokens':252,'estimatedCost':.00042,'unpricedRequests':0},
 'byJob':[{'jobId':'job-1','projectId':'project-1','requests':1,'inputTokens':214,'outputTokens':38,'thoughtTokens':0,'totalTokens':252,'estimatedCost':.00042,'unpricedRequests':0}],
 'byUser':[{'userId':'a'*64,'provider':'google','apps':[{'app':'vision','lastSeen':now},{'app':'vortex','lastSeen':now}],'name':'Jordan Owner','email':'one@example.test','requests':1,'inputTokens':214,'outputTokens':38,'thoughtTokens':0,'totalTokens':252,'estimatedCost':.00042,'unpricedRequests':0}],
 'byDay':[{'day':'2026-10-04','requests':1,'inputTokens':214,'outputTokens':38,'thoughtTokens':0,'totalTokens':252,'estimatedCost':.00042,'unpricedRequests':0}], 'hasMore':False,'pricingVersion':'pricing-v2'}}
MOCK=r'''<script>
window.__vortex={available:true,jobs:[{id:'vortex-1',userId:'a'.repeat(64),kind:'download',mode:'audio',format:'mp3',quality:'max',status:'complete',outputBytes:1024,createdAt:1791044000,updatedAt:1791044001,expiresAt:1791476001}],totals:{jobs:1,downloads:1,inspections:0,queued:0,processing:0,readyDownloads:1,retainedBytes:1024,error:0,expired:0},hasMore:false};window.__vortexMode='ok';window.__data=FIXTURE;window.__gemini=GEMINI_FIXTURE;window.__actionMode='ok';window.__mode='ok';window.__reads=[];window.__tokenCalls=[];window.__held=[];window.__removals=[];
window.__owner={uid:'owner',email:'one@example.test',getIdToken:async force=>{window.__tokenCalls.push(!!force);return 'FAKE-TEST-TOKEN';}};
window.__auth={currentUser:window.__owner};
window.__appSDK={initializeApp:()=>({})};
window.__authSDK={getAuth:()=>window.__auth,initializeAuth:()=>window.__auth,useDeviceLanguage:()=>{},setPersistence:async()=>{},browserLocalPersistence:'local',GoogleAuthProvider:class{setCustomParameters(){}},onAuthStateChanged:(auth,cb)=>{window.__callback=cb;queueMicrotask(()=>cb(auth.currentUser));},signOut:async()=>{window.__auth.currentUser=null;window.__callback(null);},signInWithPopup:async()=>{}};
window.fetch=async(url,options)=>{
 window.__reads.push({url:String(url),headers:options.headers,credentials:options.credentials,method:options.method||'GET',body:options.body});
 if(window.__mode==='hold')return new Promise(resolve=>window.__held.push(()=>resolve(new Response(JSON.stringify(window.__data),{status:200}))));
 if(window.__mode==='offline')throw new TypeError('offline');
 if(window.__mode==='denied')return new Response(JSON.stringify({error:'Denied',code:'ACTIVITY_FORBIDDEN'}),{status:403});
 if(window.__mode==='expired'){window.__mode='ok';return new Response(JSON.stringify({error:'Expired'}),{status:401});}
 if(window.__mode==='missing')return new Response(JSON.stringify({error:'Not found'}),{status:404});
 const parsed=new URL(url),person=parsed.searchParams.get('user');
 if(parsed.pathname.startsWith('/api/admin/accounts/')){
   const id=parsed.pathname.split('/')[4],record=window.__data.users.find(p=>p.id===id);
   if(!record)return new Response(JSON.stringify({error:'Account not found'}),{status:404});
   if(options.method==='POST'){
     const body=JSON.parse(options.body);
     window.__removals.push({id,body,authorization:options.headers.Authorization});
     return new Response(JSON.stringify({deleted:true,removedProjects:2,removedVortexJobs:1}),{status:200});
   }
   return new Response(JSON.stringify({id,email:record.email,name:record.name,counts:{projects:2,ventureConversations:1,vortexJobs:1,visionImports:0},
     hasActiveWork:false,adminConfigured:true,canDelete:true}),{status:200});
 }
 if(parsed.pathname==='/api/admin/gemini/access'){
   if(window.__actionMode==='fail')return new Response(JSON.stringify({error:'Test update rejected'}),{status:409});
   const body=JSON.parse(options.body),row=window.__gemini.accessRequests.find(item=>item.userId===body.userId);row.status=body.decision;row.decidedAt=Date.now()/1000;row.waitingJobs=0;window.__gemini.pendingCount=window.__gemini.accessRequests.filter(item=>item.status==='pending').length;
   return new Response(JSON.stringify({userId:body.userId,status:body.decision,releasedJobs:body.decision==='approved'?2:0,pendingCount:window.__gemini.pendingCount}),{status:200});
 }
 if(parsed.pathname==='/api/admin/vortex'){if(window.__vortexMode==='missing')return new Response('{}',{status:404});if(window.__vortexMode==='denied')return new Response('{}',{status:403});const data=structuredClone(window.__vortex);if(person&&person!=='a'.repeat(64)){data.jobs=[];data.totals={jobs:0,downloads:0,inspections:0,queued:0,processing:0,readyDownloads:0,retainedBytes:0,error:0,expired:0};}return new Response(JSON.stringify(data),{status:200});}
 if(parsed.pathname==='/api/admin/gemini')return new Response(JSON.stringify(window.__gemini),{status:200});
 let data=structuredClone(window.__data);
 if(person){data.events=data.events.filter(e=>e.userId===person);data.totalEvents=data.events.length;}
 const module=parsed.searchParams.get('module')||'all';data.module=module;data.moduleCounts={all:data.events.length,logins:data.events.filter(e=>e.kind==='google_sign_in').length,transcriptions:0};if(module==='logins')data.events=data.events.filter(e=>e.kind==='google_sign_in');if(module==='transcriptions')data.events=[];data.totalEvents=data.events.length;data.version += ':'+(person||'all')+':'+module;
 if(parsed.searchParams.get('version')===data.version)return new Response(JSON.stringify({unchanged:true,version:data.version,checkedAt:Date.now()/1000}),{status:200});
 return new Response(JSON.stringify(data),{status:200});
};
</script>'''.replace('GEMINI_FIXTURE',json.dumps(gemini)).replace('FIXTURE',json.dumps(fixture))
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
 assert page.locator('#overviewPanel').is_visible()
 assert page.locator('#geminiAdmin').is_hidden() and page.locator('#activityWorkspace').is_hidden()
 assert page.locator('[role=tab]').count()==6
 page.locator('#tab-logins').click();page.wait_for_timeout(150)
 assert 'Email / password' in page.locator('#accountDirectory').inner_text()
 assert 'Vortex' in page.locator('#accountDirectory').inner_text()
 assert 'First name' in page.locator('#accountDirectory').inner_text()
 assert 'Last name' in page.locator('#accountDirectory').inner_text()
 page.locator('#accountDirectory .remove-account').nth(1).click()
 page.locator('#removeAccountDialog[open]').wait_for()
 assert 'Venture conversations: 1' in page.locator('#removeAccountStats').inner_text()
 assert page.locator('#removeAccountConfirm').is_disabled()
 page.locator('#removeAccountEmail').fill('wrong@example.test')
 page.locator('#removeAccountPhrase').fill('DELETE')
 assert page.locator('#removeAccountConfirm').is_disabled()
 page.locator('#removeAccountEmail').fill('two@example.test')
 assert page.locator('#removeAccountConfirm').is_enabled()
 page.locator('#removeAccountConfirm').click()
 page.locator('#removeAccountDialog').wait_for(state='hidden')
 removal=page.evaluate('window.__removals')
 assert len(removal)==1 and removal[0]['body']=={'email':'two@example.test','confirmation':'DELETE'}
 assert removal[0]['authorization']=='Bearer FAKE-TEST-TOKEN'
 print('PASS first/last columns, account deletion preview, confirmation and owner token')
 assert page.locator('.event').count()==1
 page.locator('#tab-transcriptions').click();page.wait_for_timeout(150)
 assert page.locator('.event').count()==0
 page.locator('#tab-vortex').click();page.wait_for_timeout(150)
 assert page.locator('#vortexJobTotal').inner_text()=='1'
 assert 'Audio download · MP3' in page.locator('#vortexJobs').inner_text()
 page.locator('#vortexJobs summary').click()
 assert 'one@example.test' in page.locator('#vortexJobs').inner_text()
 page.locator('#personScope').select_option('b'*64);page.wait_for_timeout(150)
 assert page.locator('#vortexJobTotal').inner_text()=='0'
 assert 'one@example.test' not in page.locator('#vortexJobs').inner_text()
 page.locator('#personScope').select_option('');page.wait_for_timeout(150)
 page.evaluate("window.__vortexMode='missing'");page.locator('#refresh').click();page.wait_for_timeout(150)
 assert 'latest Vision PC update' in page.locator('#vortexNotice').inner_text()
 assert page.locator('#dashboard').is_visible()
 assert page.locator('#overviewVortex').inner_text()=='Unavailable'
 page.evaluate("window.__vortexMode='denied'");page.locator('#refresh').click();page.wait_for_timeout(150)
 assert page.locator('#dashboard').is_hidden() and page.locator('#vortexJobs').inner_text()==''
 page.evaluate("window.__vortexMode='ok'");page.locator('#retry').click();page.wait_for_selector('#dashboard:not([hidden])')
 page.locator('#tab-gemini').click()
 print('PASS Vortex-only unavailability and owner-authorization loss')
 print('PASS compact default, module switching, login providers, Vortex data and account scope')
 assert page.locator('#geminiAlert').is_visible()
 assert page.locator('#geminiAlertCount').inner_text()=='1'
 assert 'two@example.test' in page.locator('#geminiAccessList').inner_text()
 assert '2 waiting jobs' in page.locator('#geminiAccessList').inner_text()
 assert page.locator('#usageCostTotal').inner_text()=='$0.00042'
 page.locator('#geminiUsageList .usage-request summary').click()
 detail=page.locator('#geminiUsageList .usage-request').inner_text()
 for expected in ('7.4 s','214','38','252','pricing-v2','pricing-v1','$0.00036','project-1','job-1','Thought tokens','Raw API usage'):
  assert expected in detail,expected
 page.locator('#usageGroup').select_option('user')
 assert 'one@example.test' in page.locator('#usageAggregates').inner_text()
 page.locator('#usageGroup').select_option('day')
 assert '2026-10-04' in page.locator('#usageAggregates').inner_text()
 page.locator('#usageGroup').select_option('job')
 page.locator('#usagePeriod').select_option('7');page.wait_for_timeout(150)
 assert any('/api/admin/gemini?' in r['url'] and 'since=' in r['url'] for r in page.evaluate('window.__reads'))
 page.locator('#usagePeriod').select_option('all')
 print('PASS pending notification, named access request, event details and aggregates / period')
 page.evaluate("window.__actionMode='fail'")
 page.locator('.access-approve').click();page.wait_for_timeout(150)
 assert 'Test update rejected' in page.locator('#geminiNotice').inner_text()
 assert page.locator('.access-approve').is_enabled()
 page.evaluate("window.__actionMode='ok'")
 page.locator('.access-approve').click();page.wait_for_timeout(150)
 assert page.locator('#geminiAlert').is_hidden()
 assert '2 waiting jobs released' in page.locator('#geminiNotice').inner_text()
 approval=[r for r in page.evaluate('window.__reads') if r['method']=='POST'][-1]
 assert approval['headers']['Authorization']=='Bearer FAKE-TEST-TOKEN'
 assert json.loads(approval['body'])=={'userId':'b'*64,'decision':'approved'}
 page.locator('#accessFilter').select_option('approved')
 assert page.locator('.access-deny').inner_text()=='Revoke'
 page.locator('.access-deny').click();page.wait_for_timeout(150)
 page.locator('#accessFilter').select_option('denied')
 assert 'revoked' in page.locator('#geminiAccessList').inner_text().lower()
 page.evaluate("window.__gemini.accessRequests[0].status='pending';window.__gemini.pendingCount=1;")
 page.locator('#refresh').click();page.wait_for_timeout(150)
 page.locator('#geminiAlert').click()
 assert page.locator('#accessFilter').input_value()=='pending'
 page.locator('.access-deny').click();page.wait_for_timeout(150)
 assert page.locator('#geminiAlert').is_hidden()
 assert page.evaluate('window.__gemini.accessRequests[0].status')=='denied'
 print('PASS approve, deny, revoke, failed mutation retry, bearer-authenticated account-level decisions')

 page.locator('#tab-activity').click();page.wait_for_timeout(150)
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
 assert page.locator('#geminiAlert').is_hidden() and page.locator('.usage-request').count()==0
 assert page.locator('.access-row').count()==0
 assert page.locator('#vortexJobs').inner_text()=='' and page.locator('#accountDirectory').inner_text()==''
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
  assert preview.locator('#geminiAlert').is_visible()
  assert preview.locator('.access-approve').is_disabled()
  for module in ('vortex','logins','transcriptions','gemini','activity','overview'):
   preview.locator('#tab-'+module).click()
   assert not preview.evaluate('document.documentElement.scrollWidth>innerWidth'),(width,module)
   assert preview.locator('[role=tabpanel]:visible').count()==1,(width,module)
  preview.locator('#tab-overview').focus();preview.keyboard.press('ArrowRight')
  assert preview.locator('#tab-vortex').get_attribute('aria-selected')=='true'
  preview.keyboard.press('End');assert preview.locator('#tab-activity').get_attribute('aria-selected')=='true'
  preview.keyboard.press('Home');assert preview.locator('#tab-overview').get_attribute('aria-selected')=='true'
  if width in (390,1440): preview.screenshot(path='/tmp/vision-status-'+str(width)+'.png',full_page=True)
  # Screenshots are optional; no user data or authentication is used in this test.
  preview.close()
 print('PASS 320/390/768/1440 responsive layouts and explicit demo labeling')
 assert not errors,errors
 print('PASS zero application JavaScript errors')
 browser.close()
