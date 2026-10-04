/* Exercise the actual owner UI render/auth logic without Google login or paid calls. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../activity.html'),'utf8');
const script=html.split('<script type="module">')[1].split('</script>')[0];
class Element{
 constructor(tag='div'){this.tagName=tag.toUpperCase();this.childNodes=[];this.dataset={};this.attributes={};this.listeners={};this._text='';this.hidden=false;this.disabled=false;this.value='';this.scrollLeft=0;}
 set textContent(value){this._text=String(value);this.childNodes=[];} get textContent(){return this._text+this.childNodes.map(n=>n.textContent).join('');}
 append(...items){this.childNodes.push(...items);}replaceChildren(...items){this._text='';this.childNodes=[...items];}
 setAttribute(key,value){this.attributes[key]=String(value);}addEventListener(event,callback){this.listeners[event]=callback;}
 focus(){}scrollIntoView(){}closest(){return null;}
}
const ids=new Map([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
const document={getElementById:id=>ids.get(id)||null,createElement:tag=>new Element(tag),createTextNode:text=>{const n=new Element('#text');n.textContent=text;return n;},querySelectorAll:()=>[],addEventListener:()=>{},hidden:false,activeElement:null};
const request={id:'sound-1',userId:'b'.repeat(64),name:'Andrea <img onerror="alert(1)">',email:'andrea@example.test',jobId:'job-1',projectId:'project-1',kind:'sound',eventStart:14.2,eventEnd:18.6,clipDuration:7.4,model:'test-sound-model',status:'succeeded',startedAt:1791044000,finishedAt:1791044002,inputTokens:214,outputTokens:38,thoughtTokens:0,totalTokens:252,billableTokens:252,httpStatus:200,rawUsage:{promptTokenCount:214,candidatesTokenCount:38,totalTokenCount:252},estimate:{cost:.00042,pricingVersion:'rates-v2'},historicalEstimate:{cost:.00036,pricingVersion:'rates-v1'}};
const aggregate={requests:1,inputTokens:214,outputTokens:38,thoughtTokens:0,totalTokens:252,estimatedCost:.00042,unpricedRequests:0};
let state={accessRequests:[{userId:request.userId,name:request.name,email:request.email,status:'pending',requestedAt:1791043900,waitingJobs:2}],pendingCount:1,usage:{requests:[request],totals:aggregate,byJob:[{...aggregate,jobId:request.jobId,projectId:request.projectId}],byUser:[{...aggregate,name:request.name,email:request.email,userId:request.userId}],byDay:[{...aggregate,day:'2026-10-04'}],pricingVersion:'rates-v2',hasMore:false}};
const activity={users:[{id:request.userId,name:request.name,email:request.email,eventCount:0,signIns:1}],events:[],totalEvents:0,hasMore:false,version:'activity-v1'};
const calls=[];let rejectDecision=false,denyRead=false,tokenWait=null,decisionWait=null;
const owner={uid:'owner',getIdToken:async()=>tokenWait?await tokenWait:'TEST-TOKEN'};
const context={document,location:{search:'',protocol:'https:'},window:{addEventListener:()=>{}},navigator:{clipboard:{}},console,Intl,URL,URLSearchParams,AbortController,DOMException,Response,Date,Math,Number,String,Object,JSON,Set,Map,Promise,matchMedia:()=>({matches:false}),setTimeout:()=>1,clearTimeout:()=>{},fixture:state,activity,owner,
 fetch:async(url,options)=>{calls.push({url,options});if(denyRead)return new Response(JSON.stringify({error:'Denied'}),{status:403});if(url.endsWith('/access')){if(decisionWait)await decisionWait;if(rejectDecision)return new Response(JSON.stringify({error:'Decision rejected'}),{status:409});const body=JSON.parse(options.body);assert.equal(body.userId,request.userId);state.accessRequests[0].status=body.decision;state.pendingCount=0;return new Response(JSON.stringify({status:body.decision,releasedJobs:body.decision==='approved'?2:0}));}return new Response(JSON.stringify(url.includes('/api/admin/gemini?')?state:activity));}};
vm.createContext(context);vm.runInContext(script.slice(0,script.indexOf("if(DEMO){$('demoBanner')")),context);
const run=code=>vm.runInContext(code,context),text=id=>ids.get(id).textContent,descendants=n=>[n,...n.childNodes.flatMap(descendants)];
(async()=>{
 run('user=owner;snapshot=activity;geminiSnapshot=fixture;paintGemini()');
 assert.equal(ids.get('geminiAlert').hidden,false);assert.equal(text('geminiAlertCount'),'1');
 assert.match(text('geminiAccessList'),/andrea@example.test/);assert.match(text('geminiAccessList'),/2 waiting jobs/);
 assert.equal(descendants(ids.get('geminiAccessList')).some(n=>n.tagName==='IMG'),false,'name is literal text');
 assert.equal(text('usageCostTotal'),'$0.00042');
 const fields=text('geminiUsageList');for(const expected of ['7.4 s','214','38','252','rates-v2','rates-v1','$0.00036','Raw API usage','project-1','job-1'])assert.ok(fields.includes(expected),expected);
 run("usageGroup='user';paintAggregates()");assert.match(text('usageAggregates'),/andrea@example.test/);
 run("usageGroup='day';paintAggregates()");assert.match(text('usageAggregates'),/2026-10-04/);
 assert.equal(run('aggregateCost({requests:2,unpricedRequests:2,estimatedCost:null})'),'Unavailable');
 assert.equal(run('aggregateCost({requests:2,unpricedRequests:1,estimatedCost:.00042})'),'$0.00042 + unpriced');
 assert.equal(run('tokens(null)'),'Not reported');assert.equal(run('money(null)'),'Unavailable');
 run("usagePeriod='7';selected=fixture.accessRequests[0].userId");const query=new URL('https://test'+run('geminiPath()'));
 assert.equal(query.searchParams.get('user'),request.userId);assert.ok(Number(query.searchParams.get('since'))>0);
 console.log('PASS account notification, text-safe identities, all request metrics, aggregates, missing prices and query scope');
 rejectDecision=true;await run("decideAccess(fixture.accessRequests[0],'approved')");assert.match(text('geminiNotice'),/Decision rejected/);assert.equal(run('mutationBusy'),false);assert.equal(state.accessRequests[0].status,'pending');
 rejectDecision=false;await run("decideAccess(fixture.accessRequests[0],'approved')");assert.equal(state.accessRequests[0].status,'approved');assert.match(text('geminiNotice'),/2 waiting jobs released/);assert.equal(ids.get('geminiAlert').hidden,true);
 const approval=calls.find(c=>c.url.endsWith('/access')&&JSON.parse(c.options.body).decision==='approved');assert.equal(approval.options.headers.Authorization,'Bearer TEST-TOKEN');assert.equal(approval.options.credentials,'omit');assert.equal(approval.options.method,'POST');
 run("accessFilter='approved';paintAccess()");assert.match(text('geminiAccessList'),/Revoke/);
 await run("decideAccess(fixture.accessRequests[0],'revoked')");assert.equal(state.accessRequests[0].status,'revoked');
 await run("decideAccess(fixture.accessRequests[0],'denied')");assert.equal(state.accessRequests[0].status,'denied');
 console.log('PASS failure recovery and authenticated account approve / deny / revoke');
 // Changing account during token acquisition must prevent any outgoing admin request.
 let resolveToken;tokenWait=new Promise(resolve=>{resolveToken=resolve;});const before=calls.length,pending=run("adminApi('/api/admin/gemini')");run('epoch++;user=null;clearPrivate()');resolveToken('TEST-TOKEN');await assert.rejects(pending,{name:'AbortError'});assert.equal(calls.length,before);tokenWait=null;
 assert.equal(text('geminiUsageList'),'');assert.equal(text('geminiAccessList'),'');assert.equal(ids.get('geminiAlert').hidden,true);
 // A late access-decision response cannot restore signed-out personal records.
 run('user=owner;snapshot=activity;geminiSnapshot=fixture;paintGemini()');let releaseDecision;decisionWait=new Promise(resolve=>{releaseDecision=resolve;});const mutating=run("decideAccess(fixture.accessRequests[0],'approved')");await new Promise(resolve=>setImmediate(resolve));run('epoch++;user=null;clearPrivate()');releaseDecision();await mutating;decisionWait=null;assert.equal(text('geminiAccessList'),'');assert.equal(text('geminiUsageList'),'');assert.equal(ids.get('dashboard').hidden,true);
 // Loss of owner authorization from either admin endpoint clears both existing activity and Gemini data.
 run('user=owner;snapshot=activity;geminiSnapshot=fixture;paintGemini()');denyRead=true;await run('refresh(true)');assert.equal(ids.get('dashboard').hidden,true);assert.equal(ids.get('geminiAlert').hidden,true);assert.equal(text('geminiUsageList'),'');assert.equal(text('geminiAccessList'),'');
 console.log('PASS token/account race, late decision response isolation, owner authorization loss and private-data clearing');
})().catch(error=>{console.error(error);process.exitCode=1;});
