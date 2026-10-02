/* Exercise the real Run UI with local fixtures; no AI or processor requests. */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const read=file=>fs.readFileSync(path.join(root,file),'utf8');
const base=read('web/base.js');
const downloadFunction=base.slice(base.indexOf('function download('),base.indexOf('\n',base.indexOf('function download(')));
const fixture=`<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${read('web/styles.css')}\n${read('web/console.css')}</style></head><body>
<button id="runProjectBtn">Run</button><input id="screenshotMode" hidden><section id="visionConsole" class="vision-console"></section>
<script>
const $=id=>document.getElementById(id),state={title:'Test project',nodes:[],settings:{},consoleSession:null};let busy=false,ioBusy=false;
const R={safeFilename:name=>name};
function escapeHTML(value){return String(value).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));}
function bytesFromDataURL(value){return Uint8Array.from(atob(value.split(',')[1]),c=>c.charCodeAt(0));}
function markDirty(){}function toast(){}function checkpoint(){}function refreshExport(){}function renderAll(){renderConsole();}
${downloadFunction}
${read('web/console.js')}
${read('web/key-visibility.js')}
window.testRunUI={
 seed(){state.consoleSession=normalizeConsoleSession({projectId:'project-one',runs:[{runId:'run-one',clientRequestId:'request-one',model:'Test model',status:'in_progress',text:'A useful response.',artifacts:[{id:'file-one',name:'answer.txt',mime:'text/plain',data:'data:text/plain;base64,SGVsbG8=',ready:true},{id:'file-two',name:'next.csv',ready:false}]}]});renderConsole();},
 stream(){const run=state.consoleSession.runs[0];run.text+=' More response text.';state.consoleSession.runs[0]=consoleMergeRemote({...run,artifacts:[...run.artifacts,{id:'file-three',name:'third.txt',ready:true}]},run);renderConsole();},
 update(){state.consoleSession.runs[0].text+=' Updated.';renderConsole();},
 next(){state.consoleSession.runs.push(normalizeConsoleRun({runId:'run-two',status:'completed',text:'Next response.',artifacts:[{id:'file-four',name:'another.txt'}]}));renderConsole();},
 project(){state.consoleSession=normalizeConsoleSession({projectId:'project-two',runs:[{runId:'run-one',clientRequestId:'request-one',status:'completed',text:'Other project.',artifacts:[{id:'file-one',name:'other.txt'}]}]});renderConsole();},
 state(){return state;}
};
</script></body></html>`;

(async()=>{
 for(const width of [390,1280]){
  const browser=await chromium.launch({headless:true,args:['--single-process','--no-zygote','--disable-gpu'],...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
  try{
   const context=await browser.newContext({viewport:{width,height:844},isMobile:width<500,hasTouch:width<500,acceptDownloads:true});
   const errors=[],unexpected=[];
   await context.route('**/*',route=>{if(route.request().url()==='https://vision.test/')return route.fulfill({status:200,contentType:'text/html',body:fixture});unexpected.push(route.request().url());return route.abort();});
   const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));await page.goto('https://vision.test/');
   await page.evaluate(()=>window.testRunUI.seed());
   const turn=page.locator('[data-turn="0"]'),files=turn.locator('.console-turn-files'),toggle=turn.locator('.console-files-toggle');
   assert.equal(await toggle.getAttribute('aria-expanded'),'false');assert.equal(await files.isVisible(),false);
   assert.match(await toggle.innerText(),/Files\s+2/);assert.match(await turn.innerText(),/A useful response/);
   await toggle.focus();await toggle.press('Space');assert.equal(await files.isVisible(),true);
   await page.evaluate(()=>window.testRunUI.stream());assert.equal(await files.isVisible(),true,'streaming and new files preserve expansion');assert.match(await toggle.innerText(),/Files\s+3/);
   assert.equal(await files.locator('[data-console-action="download"][data-artifact="1"]').isDisabled(),true);
   const downloadReady=page.waitForEvent('download');await files.locator('[data-console-action="download"]').first().click();const download=await downloadReady;
   assert.equal(download.suggestedFilename(),'answer.txt');assert.equal(fs.readFileSync(await download.path(),'utf8'),'Hello');
   await files.locator('input[data-console-action="include"]').first().check();await page.waitForFunction(()=>window.testRunUI.state().consoleSession.runs[0].artifacts[0].include);
   assert.equal(await files.isVisible(),true,'including a file preserves expansion');
   await toggle.click();await page.evaluate(()=>window.testRunUI.update());assert.equal(await files.isVisible(),false,'streaming preserves collapse');
   await toggle.click();await page.evaluate(()=>window.testRunUI.next());assert.equal(await files.isVisible(),true);assert.equal(await page.locator('[data-turn="1"] .console-turn-files').isVisible(),false,'new responses start collapsed');
   await page.evaluate(()=>window.testRunUI.project());assert.equal(await files.isVisible(),false,'another project does not inherit expansion');
   await page.locator('#consoleSettingsToggle').click();const key=page.locator('#consoleKey'),eye=page.getByRole('button',{name:'Show OpenAI API key'});
   const sample='not-a-real-key-'+String('x').repeat(110);await key.fill(sample);assert.equal(await key.getAttribute('type'),'password');
   await eye.click();assert.equal(await key.getAttribute('type'),'text');assert.equal(await key.inputValue(),sample);assert.equal(await page.getByRole('button',{name:'Hide OpenAI API key'}).getAttribute('aria-pressed'),'true');
   assert.ok(await key.evaluate(input=>{const r=input.parentElement.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth;}),'key and eye fit mobile and desktop');
   assert.equal(await page.evaluate(()=>sessionStorage.length),0,'revealing does not store credentials');assert.equal(await page.evaluate(()=>JSON.stringify(window.testRunUI.state())).then(value=>value.includes(sample)),false,'key is not added to project');
   await page.locator('#consoleSettingsToggle').click();await page.locator('#consoleSettingsToggle').click();assert.equal(await key.getAttribute('type'),'password','closing settings masks the key');
   await page.getByRole('button',{name:'Show OpenAI API key'}).click();await page.locator('#consoleBack').click();assert.equal(await key.getAttribute('type'),'password','returning to board masks the key');
   assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);await context.close();
  }finally{await browser.close();}
 }
 console.log('PASS mobile/desktop Files collapse, streaming persistence, project isolation, real download, include checkbox, keyboard toggle, and API key visibility without storage changes');
})().catch(error=>{console.error(error);process.exitCode=1;});
