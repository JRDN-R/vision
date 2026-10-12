const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const test=require('node:test');

function fixture(){
 const c={venture:{settings:{model:'qwen3.8-local'},epoch:1},
  VENTURE_MODEL_SYMBOLS:{},ventureActive:r=>['queued','in_progress'].includes(r.status),
  escapeHTML:s=>String(s).replaceAll('<','&lt;').replaceAll('>','&gt;'),
  Math,Promise,Date,structuredClone};
 vm.createContext(c);vm.runInContext(fs.readFileSync('web/venture-local.js','utf8'),c);
 return c;
}
test('queue states show actual counts and elapsed time without made-up percent or ETA',()=>{
 const c=fixture();
 const render=run=>c.ventureLocalProgress({...run,localQueue:{requestsAhead:run.ahead||0,waitingSeconds:15,processingSeconds:3}});
 assert.match(render({status:'queued',ahead:2}),/2 requests ahead/);
 assert.match(render({status:'queued'}),/next in line/);
 assert.match(render({status:'in_progress'}),/request is running/);
 assert.equal(render({status:'completed'}),'');
 assert.match(c.ventureLocalProgress({status:'completed',localQueue:{processingSeconds:42}},true),/no API token charge/);
 assert.doesNotMatch(render({status:'completed'}),/<progress/);
 assert.doesNotMatch(render({status:'in_progress'}),/value=|[0-9]+%/);
 assert.match(render({status:'in_progress',phase:'<unsafe>'}),/&lt;unsafe&gt;/);
});
test('completed local replies use an icon beside the model and a compact optional timing panel',()=>{
 const c=fixture(),run={status:'completed',localQueue:{waitingSeconds:0,processingSeconds:42}};
 assert.equal(c.ventureLocalProgress(run),'');
 const icon=c.ventureLocalDetailsButton(run);
 assert.match(icon,/data-v-action="local-info"/);assert.match(icon,/aria-label="Response information"/);
 assert.match(icon,/<svg/);assert.doesNotMatch(icon,/Show details|show details/);
 assert.match(c.ventureLocalProgress(run,true),/Processing 42s/);
});
test('preferences retain account revision, epoch, acknowledgement and provider profiles',()=>{
 const c=fixture();c.ventureAcceptPreferences({preferenceRevision:4,defaultEpoch:'qwen3_8_local_v1',disclosureAcknowledged:true,profiles:{'gpt-6-astra':{maxOutputTokens:16000}}});
 assert.equal(c.venture.preferenceRevision,4);assert.equal(c.venture.localAcknowledged,true);
 assert.equal(c.venture.profiles['gpt-6-astra'].maxOutputTokens,16000);
 assert.match(c.VENTURE_MODEL_SYMBOLS.qwen,/M23\.919 14\.545/);
});
test('combined identity advertises vision only on explicit backend confirmation',()=>{
 const c=fixture();
 assert.equal(c.ventureLocalVisionReady(),false);
 assert.match(c.ventureLocalSubtitle(),/vision not ready/);
 assert.match(c.ventureLocalDisclosure(),/not yet confirmed/);
 c.venture.localCapability={capabilities:{vision:true}};
 assert.equal(c.ventureLocalSubtitle(),'9B Distill + Vision');
 assert.match(c.ventureLocalDisclosure(),/source-linked findings and timestamps/);
 assert.match(c.ventureLocalDisclosure(),/run in sequence/);
 assert.match(c.ventureLocalDisclosure(),/no automatic cloud fallback/);
 c.venture.localCapability={capabilities:{vision:'true'}};
 assert.equal(c.ventureLocalVisionReady(),false);
});
test('visual evidence uses escaped labels and indexed authenticated actions',()=>{
 const c=fixture();
 const html=c.ventureLocalEvidence({localVisualEvidence:[{name:'<script>',location:'Video 00:12',crop:'whole image',finding:'<img src=x>'}]});
 assert.match(html,/data-v-action="local-visual"/);
 assert.match(html,/data-index="0"/);
 assert.match(html,/&lt;script&gt;/);
 assert.doesNotMatch(html,/<script>|<img|https?:/);
});
test('citations only open recorded local sources, never model-supplied arbitrary paths',()=>{
 const c=fixture(),url='/api/projects/project1/runs/run1/local-visual/0';
 const run={localVisualEvidence:[{sourceURL:url}]};
 assert.match(c.ventureLocalSourceLink('Photo',url,run),/data-index="0"/);
 assert.doesNotMatch(c.ventureLocalSourceLink('Unknown',url.replace('project1','another-account'),run),/<button/);
});
test('a backend restart or lost connection removes visual advertising',()=>{
 const c=fixture();c.venture.rendered=new Map([['a','cached']]);c.venturePaintModelPicker=()=>{};c.venturePaintTurns=()=>{};
 c.venture.localCapability={capabilities:{vision:true}};
 c.ventureAcceptLocalCapability({localModel:{capabilities:{vision:false}}});
 assert.equal(c.ventureLocalVisionReady(),false);assert.equal(c.venture.rendered.size,0);
 c.ventureAcceptLocalCapability({localModel:null});assert.match(c.ventureLocalSubtitle(),/not ready/);
});
test('enabled but unconfirmed image path has a distinct status without advertising vision',()=>{
 const c=fixture();let paints=0;c.venture.rendered=new Map();c.venturePaintModelPicker=()=>paints++;c.venturePaintTurns=()=>{};
 c.ventureAcceptLocalCapability({localModel:{enabled:true,visual:{status:'awaiting-integrated-check'},capabilities:{vision:false}}});
 assert.equal(c.ventureLocalSubtitle(),'9B Distill · image check pending');
 assert.equal(c.ventureLocalVisionReady(),false);assert.equal(paints,1);
 c.ventureAcceptLocalCapability({localModel:null});
 assert.match(c.ventureLocalSubtitle(),/vision not ready/);assert.equal(paints,2);
});
test('loading uses measured ranges and never turns elapsed time into a percent',()=>{
 const c=fixture();
 const run={status:'in_progress',phase:'Inspecting image',localQueue:{processingSeconds:65,waitingSeconds:2,estimateStatus:'estimated',estimatedRemainingRange:[30,120],estimateSamples:4}};
 let html=c.ventureLocalProgress(run);
 assert.match(html,/About 30s–2m remaining/);assert.match(html,/based on 4 similar answers/);
 assert.match(html,/Inspecting image/);assert.match(html,/Processing 1m 5s/);
 assert.match(html,/venture-thinking-orb/);assert.doesNotMatch(html,/aria-valuenow|[0-9]+%/);
 run.localQueue.estimateStatus='overrun';html=c.ventureLocalProgress(run);
 assert.match(html,/Taking longer than recent answers/);assert.doesNotMatch(html,/0s remaining/);
 run.localQueue.estimateStatus='learning';assert.match(c.ventureLocalProgress(run),/after 3 similar answers/);
});
