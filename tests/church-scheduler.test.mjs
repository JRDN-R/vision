import test from 'node:test';
import assert from 'node:assert/strict';
import {validate,localDate,overlap,notification,all,pcoClient,preview,schedule,handleMcp,tools,runTool} from '../vision-pc/church_scheduler.mjs';
const a={service_type_id:'1',plan_id:'2',person_id:'3',team_position_id:'4',expected_date:'2027-01-10'};
const rel=id=>({data:{id}});
function fixture({existing=false,declined=false,occupied=false,block=false,mismatch=false,failPost=false,failVerify=false}={}){
 const writes=[];let created=false;let lock=false;let stored;
 const assignment={id:'99',type:'PlanPerson',attributes:{status:declined?'D':'U',team_position_name:'Streamers',prepare_notification:true},relationships:{person:rel('3'),plan:rel('2'),team:rel('5')}};
 const time={id:'7',type:'PlanTime',attributes:{time_type:'service',starts_at:'2027-01-10T15:00:00Z',ends_at:'2027-01-10T17:00:00Z'}};
 const api=async(path,method='GET',body)=>{
  const p=new URL(path,'https://example.test').pathname;
  if(method==='POST'){writes.push({path,body});if(failPost)throw Error('network');created=true;return {data:assignment};}
  if(method==='PATCH'){writes.push({path,body});return {data:assignment};}
  if(p.endsWith('/plans/2'))return {data:{id:'2',attributes:{}},included:[time]};
  if(p.endsWith('/people/3'))return {data:{id:'3',attributes:{first_name:'Grace'}}};
  if(p.endsWith('/team_positions/4'))return {data:{id:'4',attributes:{name:'Streamers'},relationships:{team:rel('5')}},included:[{id:'5',type:'Team',attributes:{schedule_to:'plan',rehearsal_team:false}}]};
  if(p.endsWith('/team_members'))return {data:[...(existing||created?[assignment]:[]),...(occupied?[{...assignment,id:'100',relationships:{...assignment.relationships,person:rel('9')}}]:[])]};
  if(p.endsWith('/blockouts'))return {data:block?[{id:'8'}]:[]};
  if(p.endsWith('/blockout_dates'))return {data:[{id:'8',attributes:{starts_at_utc:time.attributes.starts_at,ends_at_utc:time.attributes.ends_at}}]};
  if(p.endsWith('/schedules'))return {data:[],included:[]};
  if(p.endsWith('/plan_people/99')){if(failVerify)throw Error('timeout');return {data:mismatch?{...assignment,relationships:{...assignment.relationships,person:rel('88')}}:assignment};}
  throw Error('Unexpected fixture path '+p);
 };
 const db={prepare(sql){return {bind(...values){return {async run(){if(sql.startsWith('INSERT')){if(lock)return {meta:{changes:0}};lock=true;return {meta:{changes:1}};}if(sql.includes('external_id'))stored=values[0];return {meta:{changes:1}};}};}};}};
 return {api,db,writes,get stored(){return stored;},assignment};
}
test('tool names unique and include write annotations',()=>{assert.equal(new Set(tools.map(t=>t.name)).size,14);assert.equal(tools.find(t=>t.name==='schedule_person').annotations.readOnlyHint,false)});
test('valid IDs and expected date accepted',()=>assert.deepEqual(validate('preview_assignment',a),a));
test('path injection rejected',()=>assert.throws(()=>validate('preview_assignment',{...a,person_id:'../3'})));
test('unknown arguments rejected',()=>assert.throws(()=>validate('preview_assignment',{...a,url:'https://evil'})));
test('missing fields rejected',()=>assert.throws(()=>validate('schedule_person',{})));
test('invalid booleans rejected',()=>assert.throws(()=>validate('schedule_person',{...a,override_conflicts:'true'})));
test('pagination bounds rejected',()=>assert.throws(()=>validate('list_plans',{service_type_id:'1',offset:-1})));
test('winter date conversion uses Eastern timezone',()=>assert.equal(localDate('2027-01-10T04:30:00Z'),'2027-01-09'));
test('summer date conversion uses Eastern timezone',()=>assert.equal(localDate('2027-07-10T04:30:00Z'),'2027-07-10'));
test('overlap prefers UTC blockout fields',()=>assert.ok(overlap({starts_at_utc:'2027-01-10T15:00Z',ends_at_utc:'2027-01-10T16:00Z'},{starts_at:'2027-01-10T15:30Z',ends_at:'2027-01-10T17:00Z'})));
test('adjacent times do not overlap',()=>assert.equal(overlap({starts_at:'2027-01-10T15:00Z',ends_at:'2027-01-10T16:00Z'},{starts_at:'2027-01-10T16:00Z',ends_at:'2027-01-10T17:00Z'}),false));
test('prepared notification never reports dispatched',()=>{const n=notification(fixture().assignment);assert.equal(n.dispatch_supported,false);assert.equal(n.sent_at,null)});
test('pagination follows all pages',async()=>{let calls=0;const r=await all(async()=>({data:[{id:String(++calls)}],links:calls<2?{next:'next'}:{}}),'/services/v2/service_types');assert.equal(r.data.length,2)});
test('credentials absent fails explicitly',()=>assert.throws(()=>pcoClient({})));
test('API foreign destinations blocked',async()=>{const api=pcoClient({PCO_CLIENT_ID:'test',PCO_SECRET:'secret'},()=>{throw Error('must not call')});await assert.rejects(()=>api('https://evil.test/services/v2/'))});
test('API redirects rejected without forwarding credentials',async()=>{let calls=0;const api=pcoClient({PCO_CLIENT_ID:'test',PCO_SECRET:'secret'},async(url,options)=>{calls++;assert.equal(url.origin,'https://api.planningcenteronline.com');assert.equal(options.redirect,'manual');return new Response(null,{status:302,headers:{Location:'https://different.test/'}})});await assert.rejects(()=>api('/services/v2/'),/unexpected redirect/);assert.equal(calls,1)});
test('write timeout is not retried',async()=>{let n=0;const api=pcoClient({PCO_CLIENT_ID:'test',PCO_SECRET:'secret'},async()=>{n++;throw Error('secret');});await assert.rejects(()=>api('/services/v2/','POST',{}),/uncertain/);assert.equal(n,1)});
test('authorization errors hide provider response and secrets',async()=>{const api=pcoClient({PCO_CLIENT_ID:'test',PCO_SECRET:'secret'},async()=>new Response('secret',{status:401}));await assert.rejects(()=>api('/services/v2/'),/rejected the credentials/)});
test('preview validates correct assignment without writing',async()=>{const f=fixture();const p=await preview(f.api,a);assert.equal(p.team_id,'5');assert.equal(f.writes.length,0);assert.equal(p.has_conflicts,false)});
test('expected date mismatch blocked',async()=>await assert.rejects(()=>preview(fixture().api,{...a,expected_date:'2027-01-11'}),/date differs/));
test('blockout conflict reported',async()=>assert.equal((await preview(fixture({block:true}).api,a)).has_conflicts,true));
test('occupied position blocked without explicit override',async()=>{const f=fixture({occupied:true});const r=await schedule(f.api,f.db,a,'u');assert.equal(r.outcome,'conflict_requires_user_decision');assert.equal(f.writes.length,0)});
test('new assignment unconfirmed and IDs stored',async()=>{const f=fixture();const r=await schedule(f.api,f.db,{...a,prepare_notification:true},'u');assert.equal(r.outcome,'scheduled');assert.equal(f.stored,'99');assert.equal(f.writes[0].body.data.attributes.status,'U')});
test('repeated scheduling reuses exact assignment',async()=>{const f=fixture();await schedule(f.api,f.db,a,'u');const r=await schedule(f.api,f.db,a,'u');assert.equal(r.outcome,'already_scheduled');assert.equal(f.writes.length,1)});
test('existing declined assignment not overridden',async()=>{const f=fixture({existing:true,declined:true});await assert.rejects(()=>schedule(f.api,f.db,a,'u'),/declined/);assert.equal(f.writes.length,0)});
test('created ID survives verification failure',async()=>{const f=fixture({failVerify:true});await assert.rejects(()=>schedule(f.api,f.db,a,'u'));assert.equal(f.stored,'99')});
test('readback identity mismatch fails',async()=>{const f=fixture({mismatch:true});await assert.rejects(()=>schedule(f.api,f.db,a,'u'),/verification differed/)});
test('uncertain POST blocks repeat creation',async()=>{const f=fixture({failPost:true});await assert.rejects(()=>schedule(f.api,f.db,a,'u'));await assert.rejects(()=>schedule(f.api,f.db,a,'u'),/previous attempt/);assert.equal(f.writes.length,1)});
test('missing journal prevents writes',async()=>await assert.rejects(()=>runTool('schedule_person',a,{api:fixture().api,actor:'u'}),/journal unavailable/));
const req=(method,params={},auth=false)=>new Request('https://site.test/mcp',{method:'POST',headers:auth?{'oai-authenticated-user-id':'u'}:{},body:JSON.stringify({jsonrpc:'2.0',id:1,method,params})});
test('MCP initialize includes renamed server and version',async()=>{const r=await handleMcp(req('initialize',{protocolVersion:'2025-03-26'}),{});const {result}=await r.json();assert.equal(result.protocolVersion,'2025-03-26');assert.deepEqual(result.serverInfo,{name:'FirstUPCJax Admins',version:'1.1.0'})});
test('MCP discovery works without credentials',async()=>{const r=await handleMcp(req('tools/list'),{});assert.equal((await r.json()).result.tools.length,14)});
test('MCP data calls reject anonymous requests',async()=>{const r=await handleMcp(req('tools/call',{name:'connection_status'}),{api:()=>{throw Error('must not call')}});assert.equal(r.status,401)});
test('MCP unknown tools rejected',async()=>{const r=await handleMcp(req('tools/call',{name:'execute',arguments:{}},true),{});assert.equal((await r.json()).result.isError,true)});
test('MCP internal errors cannot leak secrets',async()=>{const r=await handleMcp(req('tools/call',{name:'connection_status'},true),{api:()=>{throw Error('sensitive-token')}});assert.equal((await r.text()).includes('sensitive-token'),false)});
test('MCP malformed JSON rejected',async()=>{const r=await handleMcp(new Request('https://site.test/mcp',{method:'POST',body:'['}),{});assert.equal(r.status,400)});
test('MCP authenticated read returns structured result',async()=>{const r=await handleMcp(req('tools/call',{name:'connection_status'},true),{api:async()=>({data:{attributes:{name:'Test Church'}}})});const v=await r.json();assert.equal(v.result.structuredContent.connected,true);assert.equal(v.result.structuredContent.send_notifications,false)});

// The production SQLite adapter persists the journal across interpreter restarts.
import {openJournal} from '../vision-pc/church_journal.mjs';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
test('native SQLite restart preserves uncertain-write lock',async()=>{
 const dir=mkdtempSync(join(tmpdir(),'Sound Room church-'));const path=join(dir,'journal.sqlite3');let db=openJournal(path);
 try{const f=fixture({failPost:true});await assert.rejects(()=>schedule(f.api,db,a,'firebase:u'));db.close();db=openJournal(path);
 await assert.rejects(()=>schedule(f.api,db,a,'firebase:u'),/previous attempt/);assert.equal(f.writes.length,1);
 }finally{db.close();rmSync(dir,{recursive:true,force:true});}
});
test('native SQLite handles duplicate scheduling from independent connections',async()=>{
 const dir=mkdtempSync(join(tmpdir(),'Sound Room church-'));const path=join(dir,'journal.sqlite3');const db=openJournal(path),other=openJournal(path);
 try{const f=fixture();assert.equal((await schedule(f.api,db,a,'firebase:u')).outcome,'scheduled');
 assert.equal((await schedule(f.api,other,a,'firebase:u')).outcome,'already_scheduled');assert.equal(f.writes.length,1);
 }finally{db.close();other.close();rmSync(dir,{recursive:true,force:true});}
});
