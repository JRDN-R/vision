/* Reopen a PC transcription in the real portable app. No provider requests. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const APP_URL = 'https://vision.test/';
const ROOT = path.resolve(__dirname, '..');

async function main() {
  const browser = await chromium.launch({headless:true, args:['--single-process','--no-zygote','--disable-gpu'], ...(process.env.CHROMIUM_PATH ? {executablePath:process.env.CHROMIUM_PATH} : {})});
  const context = await browser.newContext({viewport:{width:390,height:844}, isMobile:true, hasTouch:true});
  const projects = new Map(), jobs = new Map(), errors = [], external = [];
  let ready = true, complete = false, submissions = 0;
  const json = (route,status,data) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (request.url() === APP_URL) {
      let html = fs.readFileSync(path.join(ROOT,'Vision.html'),'utf8');
      const end = html.lastIndexOf('})();');
      const hooks = `window.__asrSmoke = {
        state:()=>state, backup:()=>projectBackup(), flush:()=>flushProjectSave(), pump:()=>pumpTranscriptionQueue(),
        add:async()=>{await createModuleNode('node','Speech sample'); const n=state.nodes[state.nodes.length-1];
          const source={id:uid(),name:'speech.wav',mime:'audio/wav',size:4,data:'data:audio/wav;base64,UklGRg==',role:'audio',status:'queued'};
          n.attachments.push(source);n.transcriptionJobs=[{id:uid(),provider:'local',sourceId:source.id,sourceName:source.name,sourceKind:'audio',offsetSeconds:0,createdAt:new Date().toISOString(),status:'waiting',error:null,
            sections:[{start:0,end:2,mimeType:'audio/wav',audioData:source.data,text:null,done:false},{start:2,end:4,mimeType:'audio/wav',audioData:source.data,text:null,done:false}]}];
          queueChanged(n);scheduleTranscriptionQueue();return source.id;},
        show:()=>{$('activityDialog').showModal();renderActivity();}
      };\n`;
      html = html.slice(0,end)+hooks+html.slice(end);
      return route.fulfill({status:200,contentType:'text/html',body:html});
    }
    if (url.pathname === '/api/health') return json(route,200,{ok:true,capabilities:{persistentProjects:true,projectRevision:true,persistentRuns:true,localTranscription:ready},localTranscription:{ready,maxRequestBytes:100*1024*1024}});
    const m=url.pathname.match(/^\/api\/projects\/([^/]+)(.*)$/);
    if (m) {
      const [,id,suffix]=m, key=request.headers()['x-vision-project-key'];
      if (!key) return json(route,403,{error:'Project key missing'});
      const previous=projects.get(id);
      if (previous && previous.key!==key) return json(route,403,{error:'Project key mismatch'});
      if (!suffix && request.method()==='PUT') {
        const payload=request.postDataJSON();
        if (payload.revision!==(previous?.revision||0)) return json(route,409,{error:'Revision conflict'});
        projects.set(id,{key,project:payload.project,revision:payload.revision+1});
        return json(route,200,{revision:payload.revision+1});
      }
      if (!suffix && request.method()==='GET') return previous?json(route,200,previous):json(route,404,{error:'Not found'});
      if (suffix==='/transcriptions' && request.method()==='POST') {
        submissions++;
        const payload=request.postDataJSON();
        assert.equal(payload.sections.length,2,'all audio sections arrive before acceptance');
        assert.ok(previous,'project is saved before audio submission');
        const jobId='local-'+payload.clientRequestId;
        if (!jobs.has(jobId)) jobs.set(jobId,{projectId:id,payload});
        return json(route,202,{id:jobId,status:'queued'});
      }
      if (suffix.startsWith('/transcriptions/') && request.method()==='GET') {
        const jobId=suffix.split('/').pop(), job=jobs.get(jobId);
        if (!job || job.projectId!==id) return json(route,404,{error:'Not found'});
        return json(route,200,{id:jobId,status:complete?'complete':'processing',phase:complete?'Complete':'Transcribing on PC',progress:complete?100:20,...(complete?{result:{sections:job.payload.sections.map((s,i)=>({start:s.start,end:s.end,text:`[00:00:0${i*2}.000] Offline speech ${i+1}.`}))}}:{})});
      }
      if (suffix==='/runs') return json(route,200,{runs:[]});
      return json(route,404,{error:'No mock endpoint'});
    }
    if (url.origin!==new URL(APP_URL).origin) external.push(url.hostname);
    return route.abort();
  });
  const open = async()=>{const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto(APP_URL,{waitUntil:'domcontentloaded'});await page.waitForFunction(()=>!!window.__asrSmoke);return page;};
  try {
    let page=await open();
    const sourceId=await page.evaluate(()=>window.__asrSmoke.add());
    await page.waitForFunction(()=>window.__asrSmoke.state().nodes.some(n=>n.transcriptionJobs?.some(j=>j.remoteId)));
    const identity=await page.evaluate(()=>window.__asrSmoke.state().projectCloud.id);
    await page.evaluate(async()=>{await window.__asrSmoke.flush();await window.__asrSmoke.backup();});
    assert.equal(submissions,1);
    await page.close();
    complete=true; // The PC finishes while there is no browser tab.
    page=await open();
    await page.waitForFunction(id=>window.__asrSmoke.state().nodes.some(n=>n.attachments.some(a=>a.transcriptOf===id&&a.data&&atob(a.data.split(',')[1]).includes('Offline speech 2'))),sourceId);
    assert.equal(await page.evaluate(()=>window.__asrSmoke.state().projectCloud.id),identity);
    assert.equal(submissions,1,'reopening retrieves the existing job without submitting again');
    assert.equal(jobs.size,1);
    await page.evaluate(()=>window.__asrSmoke.show());
    assert.equal(await page.locator('#activityProvider select').inputValue(),'local');
    const fits=await page.locator('#activityDialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1);
    assert.ok(fits,'mobile activity controls fit the dialog');
    await page.locator('#activityProvider select').selectOption('gemini');
    await page.evaluate(async()=>{await window.__asrSmoke.flush();await window.__asrSmoke.backup();});
    await page.reload({waitUntil:'domcontentloaded'});
    await page.waitForFunction(()=>window.__asrSmoke?.state().settings.transcriptionProvider==='gemini');
    assert.equal(submissions,1,'changing a future preference does not submit finished audio');
    assert.deepEqual(external,[],'no Google or other provider request was made');
    assert.deepEqual(errors,[],'no browser runtime errors');
    console.log('PASS local upload acceptance, project persistence, closed-tab completion/reopen, no duplicate work, mobile controls, provider preference persistence, no cloud fallback');
  } finally {await context.close();await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
