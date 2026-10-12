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
 assert.match(render({status:'completed'}),/no API token charge/);
 assert.doesNotMatch(render({status:'completed'}),/<progress/);
 assert.doesNotMatch(render({status:'in_progress'}),/value=|[0-9]+%/);
 assert.match(render({status:'in_progress',phase:'<unsafe>'}),/&lt;unsafe&gt;/);
});
test('preferences retain account revision, epoch, acknowledgement and provider profiles',()=>{
 const c=fixture();c.ventureAcceptPreferences({preferenceRevision:4,defaultEpoch:'qwen3_8_local_v1',disclosureAcknowledged:true,profiles:{'gpt-6-astra':{maxOutputTokens:16000}}});
 assert.equal(c.venture.preferenceRevision,4);assert.equal(c.venture.localAcknowledged,true);
 assert.equal(c.venture.profiles['gpt-6-astra'].maxOutputTokens,16000);
 assert.match(c.VENTURE_MODEL_SYMBOLS.qwen,/M23\.919 14\.545/);
});
