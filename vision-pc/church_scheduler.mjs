import {BridgeError} from './church_bridge.mjs';
export const VERSION = '1.1.0';
const logoIcons = [{src:'https://firstupcjax-scheduling.rapp-exe.chatgpt.site/firstupcjax-logo.svg',mimeType:'image/svg+xml',sizes:['any']}];
const ORIGIN = 'https://api.planningcenteronline.com';
export class SafeError extends Error {}
const id = {type:'string',pattern:'^[0-9]+$',maxLength:24};
const text = {type:'string',minLength:1,maxLength:200};
const offset = {type:'integer',minimum:0,maximum:100000};
const planArgs = {service_type_id:id,plan_id:id};
const assignmentArgs = {...planArgs,person_id:id,team_position_id:id,expected_date:{type:'string',pattern:'^\\d{4}-\\d{2}-\\d{2}$'}};
function tool(name, description, properties, required=[], write=false) {
 return {name,description,icons:logoIcons,inputSchema:{type:'object',properties,required,additionalProperties:false},annotations:{readOnlyHint:!write,destructiveHint:false,idempotentHint:true,openWorldHint:true}};
}
export const tools = [
 tool('link_vision_account','Link this authenticated ChatGPT connection using the one-time code from Vision → FirstUPCJax Admins. The code expires after ten minutes. Never invent a code or user identity.',{code:{type:'string',pattern:'^[A-Za-z0-9_-]{32,100}$'}},['code'],true),
 tool('send_scheduling_email','Queue the official Planning Center Scheduling Email with Accept/Decline through the FUPCJ PC. Only when explicitly requested. Verify exact person/assignment/plan first. A queued or waiting result is NOT sent. Poll notification_job. Reuse the same request_id on uncertain retries. resend=true only for an explicitly requested resend.',{...planArgs,person_id:id,assignment_id:id,request_id:{type:'string',pattern:'^[A-Za-z0-9_-]{16,100}$'},resend:{type:'boolean'}},['service_type_id','plan_id','person_id','assignment_id','request_id'],true),
 tool('notification_job','Read a PC notification operation. Only state=sent proves this attempt was recorded as sent. already_sent means no new email; needs_review must never be resent automatically.',{job_id:{type:'string',pattern:'^[a-f0-9]{24}$'}},['job_id']),
 tool('connection_status','Verify Planning Center authentication. Reports scheduling and notification capabilities honestly.',{}),
 tool('find_people','Find people by exact first name and optional last name. Resolve all candidates; do not guess among names. Returns IDs for Services. Follow pagination when present.',{first_name:text,last_name:text,offset},['first_name']),
 tool('list_service_types','List Services service types, such as Sunday Morning and Sunday Evening.',{offset}),
 tool('list_plans','List upcoming plans chronologically with service times. Dates are America/New_York. Follow next_offset for additional results.',{service_type_id:id,offset},['service_type_id']),
 tool('get_plan','Read exact plan details, service/rehearsal times, roster, and ordered service items. This is authoritative; do not substitute a livestream Publishing time.',planArgs,Object.keys(planArgs)),
 tool('list_positions','Read positions and their team IDs in a service type. Return IDs; never invent role names.',{service_type_id:id,offset},['service_type_id']),
 tool('get_person_availability','Read expanded recurring blockouts and upcoming schedules with plan times. Inspect overlap before scheduling.',{person_id:id},['person_id']),
 tool('preview_assignment','Validate exact person, plan, position, expected local date, roster, blockouts, and conflicting schedules. No external changes. Resolve names before calling.',assignmentArgs,Object.keys(assignmentArgs)),
 tool('schedule_person','Schedule an explicitly requested, identity-verified person as Unconfirmed. Checks date, availability, role, occupied slots and duplicates again. prepare_notification only queues a notification, it DOES NOT send it. Never claim sent from queued status. May use override_conflicts only if user explicitly requested overriding reported conflicts. Returns verified assignment and notification status.',{...assignmentArgs,prepare_notification:{type:'boolean'},override_conflicts:{type:'boolean'}},Object.keys(assignmentArgs),true),
 tool('prepare_notification','Prepare a notification for one already scheduled person, and verify its status. DOES NOT dispatch email/push. Use only when user requested notification. Do not claim sent unless notification_sent_at is present; that timestamp may refer to an earlier send.',{person_id:id,assignment_id:id},['person_id','assignment_id'],true),
 tool('get_assignment','Read an assignment and notification timestamps; distinguish prepared from sent.',{person_id:id,assignment_id:id},['person_id','assignment_id'])
];
export function validate(name,args) {
 const t=tools.find(t=>t.name===name); if(!t) throw new SafeError('Unknown tool');
 if(!args || typeof args!=='object'||Array.isArray(args)) throw new SafeError('Arguments must be an object');
 for(const k of t.inputSchema.required) if(args[k]===undefined) throw new SafeError(`Missing ${k}`);
 for(const [k,v] of Object.entries(args)) {
  const s=t.inputSchema.properties[k]; if(!s) throw new SafeError(`Unexpected argument: ${k}`);
  if(s.type==='integer'? !Number.isInteger(v):typeof v!==s.type) throw new SafeError(`Invalid ${k}`);
  if(s.pattern&&!new RegExp(s.pattern).test(v)) throw new SafeError(`Invalid ${k}`);
  if(s.minLength&&v.length<s.minLength || s.maxLength&&v.length>s.maxLength || s.minimum!==undefined&&v<s.minimum || s.maximum!==undefined&&v>s.maximum) throw new SafeError(`Invalid ${k}`);
 }
 return args;
}
export function pcoClient(env,fetcher=fetch) {
 if(!env.PCO_CLIENT_ID||!env.PCO_SECRET) throw new SafeError('Planning Center credentials are not configured.');
 return async function request(path,method='GET',data) {
  const url=new URL(path,ORIGIN);
  if(url.origin!==ORIGIN || !/^\/(services|people)\/v2\//.test(url.pathname)) throw new SafeError('Unsupported API destination');
  for(let attempt=0;attempt<3;attempt++) {
   let r;
   // Workers supports manual/follow, but rejects redirect: 'error'. Never
   // follow a redirect carrying the Planning Center Authorization header.
   try { r=await fetcher(url,{method,redirect:'manual',headers:{Authorization:'Basic '+btoa(env.PCO_CLIENT_ID+':'+env.PCO_SECRET),'Content-Type':'application/json','X-PCO-API-Version':url.pathname.startsWith('/services/')?'2018-11-01':'2025-11-10'},body:data?JSON.stringify(data):undefined,signal:AbortSignal.timeout(20000)}); }
   catch {throw new SafeError(method==='GET'?'Planning Center could not be reached. Try again.':'Write result uncertain. Check the assignment before retrying; no automatic write retry was made.');}
   if(r.status>=300&&r.status<400)throw new SafeError('Planning Center returned an unexpected redirect. No redirect was followed; credentials remained on the configured API host.');
   if(r.ok) return r.status===204?{}:await r.json();
   if(method==='GET' && (r.status===429||r.status>=500) && attempt<2) {await new Promise(resolve=>setTimeout(resolve,Math.min(3000,1000*2**attempt)));continue;}
   throw new SafeError(r.status===401?'Planning Center rejected the credentials.':r.status===403?'Your Planning Center account does not permit this action.':r.status===404?'Planning Center record not found.':`Planning Center returned HTTP ${r.status}. ${method==='GET'?'Try again later.':'Verify the current assignment before retrying.'}`);
  }
 };
}
function query(path,params={}){const u=new URL(path,ORIGIN);for(const[k,v]of Object.entries(params))if(v!==undefined)u.searchParams.set(k,String(v));return u.pathname+u.search;}
function page(result,offset=0){return {...result,next_offset:result.links?.next?offset+(result.data?.length??0):null};}
export async function all(api,path,params={}) {
 const records=[],included=[]; let start=0;
 for(let n=0;n<30;n++) {const r=await api(query(path,{...params,per_page:100,offset:start})); records.push(...r.data);included.push(...(r.included??[])); if(!r.links?.next)return {data:records,included}; start+=r.data.length;if(!r.data.length)break;}
 throw new SafeError('Result exceeds the safe pagination limit; narrow the request in Planning Center.');
}
export const relation=(v,k)=>v?.relationships?.[k]?.data?.id;
export function localDate(timestamp){if(!timestamp||!Number.isFinite(Date.parse(timestamp)))throw new SafeError('Plan has no valid service date.');return new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(timestamp));}
export function overlap(a,b){const x=Date.parse(a.starts_at_utc??a.starts_at),y=Date.parse(a.ends_at_utc??a.ends_at),p=Date.parse(b.starts_at_utc??b.starts_at),q=Date.parse(b.ends_at_utc??b.ends_at);return Number.isFinite(x+y+p+q)&&x<q&&p<y;}
export function notification(record){return {prepared:!!record.attributes.prepare_notification,sent_at:record.attributes.notification_sent_at??null,prepared_at:record.attributes.notification_prepared_at??null,dispatch_supported:false,message:'Preparing is not sending. Send pending notifications in Planning Center Services. Existing sent_at may be from a previous notification.'};}
export async function availability(api,person) {
 const base=`/services/v2/people/${person}`;
 const [blocks,schedules]=await Promise.all([all(api,base+'/blockouts'),all(api,base+'/schedules',{include:'plan_times'})]);
 const dates=[];
 for(const b of blocks.data){const d=await all(api,`${base}/blockouts/${b.id}/blockout_dates`);dates.push(...d.data);}
 return {blockout_dates:dates,schedules:schedules.data,plan_times:schedules.included.filter(x=>x.type==='PlanTime'),coverage_note:'Expanded recurring blockouts are generated by Planning Center up to one year ahead.'};
}
export async function preview(api,a,now=Date.now()) {
 const base=`/services/v2/service_types/${a.service_type_id}`;
 const [plan,person,position,roster,available]=await Promise.all([
  api(`${base}/plans/${a.plan_id}?include=plan_times`),api(`/services/v2/people/${a.person_id}`),api(`${base}/team_positions/${a.team_position_id}?include=team`),all(api,`${base}/plans/${a.plan_id}/team_members`),availability(api,a.person_id)
 ]);
 const team=relation(position.data,'team');if(!team)throw new SafeError('Position has no team.');
 const teamInfo=position.included?.find(t=>t.type==='Team'&&t.id===team);
 if(teamInfo?.attributes?.schedule_to && teamInfo.attributes.schedule_to!=='plan')throw new SafeError('This team schedules to individual times. Use Planning Center for this assignment.');
 const times=(plan.included??[]).filter(t=>t.type==='PlanTime');const serviceTimes=times.filter(t=>t.attributes.time_type==='service');
 if(!serviceTimes.length)throw new SafeError('Plan has no service time.');
 if(serviceTimes.some(t=>localDate(t.attributes.starts_at)!==a.expected_date))throw new SafeError('Service date differs from expected_date or spans multiple dates. Review the plan.');
 if(serviceTimes.some(t=>Date.parse(t.attributes.starts_at)<now))throw new SafeError('This plan has a service time in the past.');
 if(serviceTimes.some(t=>Date.parse(t.attributes.starts_at)>now+364*86400000))throw new SafeError('Service is beyond the verified blockout horizon.');
 const role=position.data.attributes.name;
 const existing=roster.data.filter(r=>relation(r,'person')===a.person_id&&relation(r,'team')===team&&r.attributes.team_position_name===role);
 if(existing.length>1)throw new SafeError('Multiple existing assignments match; resolve them in Planning Center.');
 const relevant=times.filter(t=>t.attributes.time_type==='service'||teamInfo?.attributes?.rehearsal_team);
 const blockouts=available.blockout_dates.filter(b=>relevant.some(t=>overlap(b.attributes,t.attributes)));
 const conflicts=available.schedules.filter(s=>relation(s,'plan')!==a.plan_id&&!['D','Declined'].includes(s.attributes.status)).filter(s=>{
  const ids=(s.relationships?.plan_times?.data??[]).map(t=>t.id);return available.plan_times.some(t=>ids.includes(t.id)&&relevant.some(r=>overlap(r.attributes,t.attributes)));
 });
 const occupied=roster.data.filter(r=>relation(r,'team')===team&&r.attributes.team_position_name===role&&relation(r,'person')!==a.person_id&&!['D','Declined'].includes(r.attributes.status));
 return {person:person.data,plan:plan.data,position:position.data,team_id:team,team:teamInfo,service_times:serviceTimes,existing_assignment:existing[0]??null,conflicts:{blockouts,other_schedules:conflicts,occupied_position:occupied},has_conflicts:!!(blockouts.length||conflicts.length||occupied.length),notification_dispatch_supported:false,plan_url:`https://services.planningcenteronline.com/plans/${a.plan_id}`};
}
export async function schedule(api,db,a,actor) {
 const p=await preview(api,a);
 if(p.existing_assignment){if(['D','Declined'].includes(p.existing_assignment.attributes.status))throw new SafeError('This person declined the existing assignment. Do not silently reschedule them.');return {outcome:'already_scheduled',assignment:p.existing_assignment,notification:notification(p.existing_assignment),plan_url:p.plan_url};}
 if(p.has_conflicts&&!a.override_conflicts) return {outcome:'conflict_requires_user_decision',preview:p};
 const key=[a.service_type_id,a.plan_id,a.person_id,p.team_id,p.position.attributes.name].join(':');
 const lock=await db.prepare('INSERT OR IGNORE INTO scheduling_operations (operation_key, actor, state, created_at) VALUES (?, ?, ?, ?)').bind(key,actor,'pending',new Date().toISOString()).run();
 if(!lock.meta.changes)throw new SafeError('A previous attempt exists and its assignment was not found. Inspect Planning Center before any new attempt; duplicate creation has been blocked.');
 try {
  const result=await api(`/services/v2/service_types/${a.service_type_id}/plans/${a.plan_id}/team_members`,'POST',{data:{type:'PlanPerson',attributes:{person_id:a.person_id,team_id:p.team_id,team_position_name:p.position.attributes.name,status:'U',prepare_notification:a.prepare_notification??false}}});
  if(!result.data?.id)throw new SafeError('Creation response has no assignment ID. Inspect the plan before retrying.');
  await db.prepare('UPDATE scheduling_operations SET external_id=?, state=? WHERE operation_key=?').bind(result.data.id,'created',key).run();
  const verified=await api(`/services/v2/people/${a.person_id}/plan_people/${result.data.id}`);
  if(relation(verified.data,'person')!==a.person_id||relation(verified.data,'plan')!==a.plan_id||relation(verified.data,'team')!==p.team_id||verified.data.attributes.team_position_name!==p.position.attributes.name)throw new SafeError('Assignment created but verification differed. Review the plan.');
  await db.prepare('UPDATE scheduling_operations SET state=? WHERE operation_key=?').bind('verified',key).run();
  return {outcome:'scheduled',assignment:verified.data,notification:notification(verified.data),plan_url:p.plan_url};
 } catch(e){await db.prepare('UPDATE scheduling_operations SET state=? WHERE operation_key=?').bind('needs_reconciliation',key).run();throw e;}
}
export async function runTool(name,args,{api,db,actor}) {
 const a=validate(name,args);const start=a.offset??0;
 switch(name){
 case 'connection_status': {const r=await api('/services/v2/');return {connected:true,organization:r.data?.attributes?.name??null,version:VERSION,scheduling:true,prepare_notifications:true,send_notifications:false,timezone:'America/New_York'};}
 case 'find_people':return page(await api(query('/people/v2/people',{'where[first_name]':a.first_name,'where[last_name]':a.last_name,per_page:100,offset:start})),start);
 case 'list_service_types':return page(await api(query('/services/v2/service_types',{per_page:100,offset:start})),start);
 case 'list_plans':return page(await api(query(`/services/v2/service_types/${a.service_type_id}/plans`,{filter:'future',order:'sort_date',include:'plan_times',per_page:25,offset:start})),start);
 case 'list_positions':return page(await api(query(`/services/v2/service_types/${a.service_type_id}/team_positions`,{include:'team',per_page:100,offset:start})),start);
 case 'get_plan': {const b=`/services/v2/service_types/${a.service_type_id}/plans/${a.plan_id}`;const [plan,roster,items]=await Promise.all([api(b+'?include=plan_times'),all(api,b+'/team_members'),all(api,b+'/items')]);return {plan,roster,items};}
 case 'get_person_availability':return availability(api,a.person_id);
 case 'preview_assignment':return preview(api,a);
 case 'schedule_person':if(!db)throw new SafeError('Scheduling journal unavailable. No changes made.');return schedule(api,db,a,actor);
 case 'get_assignment':return api(`/services/v2/people/${a.person_id}/plan_people/${a.assignment_id}`);
 case 'prepare_notification':{const path=`/services/v2/people/${a.person_id}/plan_people/${a.assignment_id}`;const before=await api(path);if(relation(before.data,'person')!==a.person_id)throw new SafeError('Person does not match this assignment.');if(!before.data.attributes.prepare_notification)await api(path,'PATCH',{data:{type:'PlanPerson',id:a.assignment_id,attributes:{prepare_notification:true}}});const after=await api(path);if(!after.data.attributes.prepare_notification)throw new SafeError('Notification preparation did not verify.');return {assignment:after.data,notification:notification(after.data)};}
 }
}
export async function handleMcp(request,context){
 if(request.method!=='POST')return new Response(null,{status:405,headers:{Allow:'POST'}});
 let message;try{const body=await request.text();if(body.length>16384)return new Response('Request too large',{status:413});message=JSON.parse(body);}catch{return Response.json({jsonrpc:'2.0',id:null,error:{code:-32700,message:'Invalid JSON'}},{status:400});}
 if(!message||Array.isArray(message)||message.jsonrpc!=='2.0'||typeof message.method!=='string')return Response.json({jsonrpc:'2.0',id:null,error:{code:-32600,message:'Invalid request'}},{status:400});
 const reply=result=>Response.json({jsonrpc:'2.0',id:message.id??null,result},{headers:{'Cache-Control':'no-store'}});
 if(message.method==='initialize'){
  const protocolVersion=['2025-11-25','2025-06-18','2025-03-26','2024-11-05'].includes(message.params?.protocolVersion)?message.params.protocolVersion:'2025-03-26';
  return reply({protocolVersion,capabilities:{tools:{}},serverInfo:{name:'FirstUPCJax Admins',version:VERSION,...(protocolVersion==='2025-11-25'?{icons:logoIcons}:{})},instructions:'Use America/New_York. Resolve recipients and exact service plan before writes. Do not schedule someone as confirmed on their behalf. Official email dispatch requires the activated FUPCJ worker. Preparing or queueing is not sending. Verify notification_job; never automatically resend needs_review jobs.'});
 }
 if(message.method.startsWith('notifications/'))return new Response(null,{status:202});
 if(message.method==='ping')return reply({});
 if(message.method==='tools/list')return reply({tools});
 if(message.method!=='tools/call')return Response.json({jsonrpc:'2.0',id:message.id??null,error:{code:-32601,message:'Method not found'}});
 const actor=request.headers.get('oai-authenticated-user-id');
 if(!actor)return new Response('Sign in to use Planning Center tools.',{status:401});
 try {
  const name=message.params?.name,args=validate(name,message.params?.arguments??{}),bridge=context.bridge;
  let result;
  if(['link_vision_account','send_scheduling_email','notification_job'].includes(name)){
   if(!bridge)throw new SafeError('FUPCJ Server integration is not configured yet.');
   result=await bridge.call(actor,name,args);
  }else{
   if(bridge?.required)await bridge.call(actor,name,{});
   result=await runTool(name,args,{...context,actor});
   if(name==='connection_status')Object.assign(result,{send_notifications:!!bridge?.required,notification_transport:bridge?.required?'FUPCJ browser worker':'not activated',access_control:bridge?.required?'Vision invite list':'existing private plugin; Vision access not activated'});
  }
  return reply({content:[{type:'text',text:JSON.stringify(result)}],structuredContent:result});
 }catch(e){return reply({isError:true,content:[{type:'text',text:e instanceof SafeError||e instanceof BridgeError?e.message:'Operation failed. Check current Planning Center state before retrying.'}]});}
}
