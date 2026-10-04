/* Last-used import choices survive refresh and project changes, within one account. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const projects=fs.readFileSync('web/projects.js','utf8'),base=fs.readFileSync('web/base.js','utf8');
const preferences=projects.slice(0,projects.indexOf('function projectRecentList('));
const switchAccount=projects.slice(projects.indexOf('async function projectSwitchAccountScope('),projects.indexOf('function renderAccountProjectList('));
const helperNames=['transcriptionProvider','soundEventsSelected','setTranscriptionProvider','setIncludeSoundEvents'];
const helpers=helperNames.map(name=>base.split('\n').find(line=>line.startsWith('function '+name+'('))).join('\n');
const key=scope=>'vision-import-preferences-v1:account:'+scope;
function fixture(storage=new Map()){
 const defaults=()=>({transcriptionProvider:'local',includeSoundEvents:false});
 const c={console,Map,JSON,Promise,cloudAuth:null,cloudConfig:null,state:{settings:defaults(),nodes:[]},DEFAULT_PROMPT:'Prompt',defaults,
  localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},checkpoint(){},markDirty(){},syncTranscriptionProviderUI(){},
  clearTimeout(){},projectBackup:async()=>{},cancelTranscriptionQueue(){},consoleRefreshProject(){},resumeYouTubeImports(){},R:{clearImageCache(){}},renderAll(){},updateRefreshNotice(){},projectStoreGet:async()=>null,projectStatus(){},renderProjectMenu(){},
  selected:null,selectedMark:null,selectedEdge:null,pending:null,action:null,history:[],future:[],dirty:false,ioBusy:false};
 vm.createContext(c);
 // Function declarations are hoisted in the real portable script. Base controls
 // must still work before projects.js initializes its lexical storage variables.
 vm.runInContext(helpers+'\nconst initialProvider=transcriptionProvider();\n'+preferences+'\n'+switchAccount,c);
 const run=code=>vm.runInContext(code,c);
 return{c,run,storage,async account(uid,kind='firebase-google'){c.cloudAuth=uid?{kind,uid}:null;await run('projectSwitchAccountScope('+JSON.stringify(kind==='trial'?'trial:'+uid:uid||'signed-out')+')');}};
}
(async()=>{
 const f=fixture();assert.equal(f.run('initialProvider'),'local');await f.account('alice');
 f.run('setTranscriptionProvider("gemini");setIncludeSoundEvents(true);setYouTubeNewModulePreference(true)');
 assert.deepEqual(JSON.parse(f.storage.get(key('alice'))),{transcriptionProvider:'gemini',includeSoundEvents:true,youtubeNewModule:true});
 // New project defaults and restored project data never override explicit choices.
 f.run('state={settings:defaults(),nodes:[]}');
 assert.equal(f.run('transcriptionProvider()'),'gemini');assert.equal(f.run('soundEventsSelected()'),true);assert.equal(f.run('rememberedYouTubeNewModule()'),true);
 const queued={provider:'local',includeSoundEvents:false,status:'approval_waiting'};f.c.state.nodes=[{transcriptionJobs:[queued]}];
 const before=f.storage.get(key('alice'));f.run('state.settings={transcriptionProvider:"local",includeSoundEvents:false};transcriptionProvider();soundEventsSelected()');
 assert.equal(f.storage.get(key('alice')),before,'restoring project settings does not write last-used choices');assert.deepEqual(queued,{provider:'local',includeSoundEvents:false,status:'approval_waiting'},'captured job options stay unchanged');
 const refreshed=fixture(f.storage);await refreshed.account('alice');assert.equal(refreshed.run('transcriptionProvider()'),'gemini');assert.equal(refreshed.run('soundEventsSelected()'),true);assert.equal(refreshed.run('rememberedYouTubeNewModule()'),true);
 await refreshed.account('bob');assert.equal(refreshed.run('transcriptionProvider()'),'local');assert.equal(refreshed.run('soundEventsSelected()'),false);assert.equal(refreshed.run('rememberedYouTubeNewModule()'),false);
 refreshed.run('setTranscriptionProvider("local");setIncludeSoundEvents(false);setYouTubeNewModulePreference(false)');
 // False is an explicit choice, even when a restored project had enabled sounds.
 refreshed.run('state.settings={transcriptionProvider:"gemini",includeSoundEvents:true}');assert.equal(refreshed.run('transcriptionProvider()'),'local');assert.equal(refreshed.run('soundEventsSelected()'),false);assert.equal(refreshed.run('rememberedYouTubeNewModule()'),false);
 const bobRefresh=fixture(f.storage);await bobRefresh.account('bob');assert.equal(bobRefresh.run('soundEventsSelected()'),false);assert.equal(bobRefresh.run('rememberedYouTubeNewModule()'),false);
 await refreshed.account('alice');assert.equal(refreshed.run('transcriptionProvider()'),'gemini');assert.equal(refreshed.run('soundEventsSelected()'),true);
 await refreshed.account(null);assert.equal(refreshed.run('transcriptionProvider()'),'local');assert.equal(refreshed.run('rememberedYouTubeNewModule()'),false);
 const storedBeforeTrial=Array.from(f.storage);await refreshed.account('temporary','trial');refreshed.run('setTranscriptionProvider("gemini");setIncludeSoundEvents(true);setYouTubeNewModulePreference(true)');
 assert.equal(refreshed.run('soundEventsSelected()'),true);assert.equal(refreshed.run('rememberedYouTubeNewModule()'),true);assert.deepEqual(Array.from(f.storage),storedBeforeTrial,'trial settings stay in memory');
 const trialRefresh=fixture(f.storage);await trialRefresh.account('temporary','trial');assert.equal(trialRefresh.run('rememberedYouTubeNewModule()'),false);
 // Malformed values never activate paid processing or silently coerce booleans.
 f.storage.set(key('invalid'),JSON.stringify({transcriptionProvider:'other',includeSoundEvents:'true',youtubeNewModule:1}));await refreshed.account('invalid');assert.equal(refreshed.run('transcriptionProvider()'),'local');assert.equal(refreshed.run('soundEventsSelected()'),false);assert.equal(refreshed.run('rememberedYouTubeNewModule()'),false);
 console.log('Import preferences: early startup, explicit choices, refresh, new/restored projects, unchanged jobs, account isolation, false values, and memory-only trial passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
