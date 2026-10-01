/* Browser-level regression checks. All remote traffic is intercepted; no paid API calls. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const ROOT = path.resolve(__dirname, '..');
const URL = process.env.VISION_TEST_URL || 'https://vision.test/';

async function openApp(browser, options = {}, mock = null) {
  const context = await browser.newContext(options);
  const errors = [];
  const requests = [];
  await context.route('**/*', async route => {
    const req = route.request(), u = new global.URL(req.url());
    if (u.pathname.startsWith('/api/')) {
      requests.push({method:req.method(), path:u.pathname});
      if (mock) return mock(route, req, u);
      return route.fulfill({status:503, contentType:'application/json', body:JSON.stringify({error:'Integration test processor is offline.'})});
    }
    if (req.url() === URL) {
      let html = fs.readFileSync(path.join(ROOT, 'Vision.html'), 'utf8');
      const end = html.lastIndexOf('})();');
      assert.ok(end > 0, 'application closure exists');
      const hooks = `window.__visionIntegration = {
        snapshot: () => snapshot(),
        getState: () => state,
        checkpoint: () => checkpoint(),
        markDirty: () => markDirty(),
        render: () => renderAll(),
        validate: raw => validateProject(raw),
        exportFiles: () => buildExportFiles(orderedNodes(), false),
        createNode: () => createModuleNode('node', 'Integration module'),
        select: id => selectNode(id),
        undo: redo => undo(redo),
        flush: () => flushProjectSave(),
        backup: () => projectBackup(),
        pollRuns: () => consolePollRuns(),
        saveStatus: () => ({pending:projectPending, conflict:projectConflict})
      };\n`;
      html = html.slice(0, end) + hooks + html.slice(end);
      return route.fulfill({status:200, contentType:'text/html', body:html});
    }
    if (u.origin === new global.URL(URL).origin) return route.continue();
    return route.abort();
  });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => dialog.accept());
  await page.goto(URL, {waitUntil:'domcontentloaded'});
  await page.waitForFunction(() => !!window.__visionIntegration);
  return {context,page,errors,requests};
}

async function boardAndExport(browser) {
  const {context,page,errors} = await openApp(browser, {viewport:{width:1280,height:900}});
  try {
    await page.evaluate(async () => {
      await window.__visionIntegration.createNode();
      await window.__visionIntegration.createNode();
    });
    assert.equal(await page.locator('#world .node').count(), 2);
    assert.equal(await page.evaluate(() => window.__visionIntegration.snapshot().edges.length), 1, 'second module auto-connects');
    await page.locator('#board').focus();
    await page.keyboard.press('Control+z');
    assert.equal(await page.locator('#world .node').count(), 1, 'board undo removes latest module');
    await page.keyboard.press('Control+Shift+z');
    assert.equal(await page.locator('#world .node').count(), 2, 'board redo restores module');
    await page.locator('#projectTitle').fill('Draft title');
    await page.keyboard.press('Control+z');
    assert.equal(await page.locator('#world .node').count(), 2, 'native text undo must not undo board operations');
    const exportCheck = await page.evaluate(async () => {
      const marker = 'integration-project-secret-do-not-export-0123456789abcdef';
      window.__visionIntegration.getState().projectCloud.key = marker;
      const files = await window.__visionIntegration.exportFiles();
      let leaked = false;
      for (const f of files) {
        let content = typeof f.data === 'string' ? f.data : f.data instanceof Blob ? await f.data.text() : new TextDecoder().decode(f.data);
        if (content.includes(marker)) leaked = true;
      }
      return {leaked, names:files.map(f=>f.name)};
    });
    assert.equal(exportCheck.leaked, false, 'AI export must not include project capability secret');
    assert.ok(exportCheck.names.includes('MAIN_PROMPT.txt'));
    assert.deepEqual(errors, [], 'desktop has no JavaScript runtime errors');
    return ['board undo/redo', 'text editing undo isolation', 'automatic module connection', 'AI export credential isolation'];
  } finally { await context.close(); }
}

function processorMock() {
  const projects = new Map();
  const counts = {put:0,post:0,runsGet:0};
  const control = {loseAcceptance:false, runsOffline:false, apiKeyLeaked:false};
  const runs = [];
  const json = (route,status,body) => route.fulfill({status,contentType:'application/json',headers:{'Access-Control-Allow-Origin':'*'},body:JSON.stringify(body)});
  const handler = async (route,req,url) => {
    if (url.pathname === '/api/health') return json(route,200,{ok:true,capabilities:{persistentProjects:true,projectRevision:true,persistentSessions:true}});
    const match = url.pathname.match(/^\/api\/projects\/([^/]+)(.*)$/);
    if (!match) return json(route,404,{error:'No test endpoint'});
    const [,id,suffix] = match, key = req.headers()['x-vision-project-key'];
    if (!key) return json(route,403,{error:'Missing project key'});
    const saved = projects.get(id);
    if (saved && saved.key !== key) return json(route,403,{error:'Wrong project key'});
    if (!suffix && req.method() === 'PUT') {
      counts.put++;
      const body = req.postDataJSON();
      control.apiKeyLeaked ||= JSON.stringify(body).includes('sk-integration-fake-key');
      if (body.revision !== (saved?.revision || 0)) return json(route,409,{error:'This project changed on another device.', revision:saved.revision});
      const row = {key, project:body.project, revision:body.revision+1};
      projects.set(id,row);
      return json(route,200,{revision:row.revision,updatedAt:Date.now()/1000});
    }
    if (!suffix && req.method() === 'GET') return saved ? json(route,200,{project:saved.project,revision:saved.revision}) : json(route,404,{error:'Not saved'});
    if (suffix === '/runs' && req.method() === 'GET') {
      counts.runsGet++;
      return control.runsOffline ? json(route,503,{error:'Test connection interrupted'}) : json(route,200,{runs:runs.filter(r=>r.projectId===id)});
    }
    if (suffix === '/runs' && req.method() === 'POST') {
      counts.post++;
      const body = req.postDataBuffer().toString();
      const optionMatch = body.match(/name="options"\r\n\r\n([^\r]+)/);
      assert.ok(optionMatch, 'run options are sent in multipart body');
      const options = JSON.parse(optionMatch[1]);
      let run = runs.find(r => r.projectId===id && r.clientRequestId===options.clientRequestId);
      if (!run) {
        run = {runId:'mock-run-'+counts.post,projectId:id,clientRequestId:options.clientRequestId,status:'completed',phase:'Complete',text:'Durable response survived the browser disconnect.',message:options.message,model:options.model,responseId:'resp_mock_'+counts.post,sequence:8,createdAt:Date.now()/1000,updatedAt:Date.now()/1000,artifacts:[],attachments:[]};
        runs.push(run);
      }
      if (control.loseAcceptance) return route.abort('connectionreset');
      return json(route,202,run);
    }
    return json(route,404,{error:'No test endpoint'});
  };
  return {handler,projects,counts,control,runs};
}

async function durableReconnect(browser) {
  const mock = processorMock();
  mock.control.loseAcceptance = true;
  mock.control.runsOffline = true;
  const {context,page,errors} = await openApp(browser, {viewport:{width:1280,height:900}}, mock.handler);
  try {
    await page.evaluate(async () => {
      await window.__visionIntegration.createNode();
      await window.__visionIntegration.flush();
    });
    const identity = await page.evaluate(() => window.__visionIntegration.snapshot().projectCloud);
    await page.locator('#runProjectBtn').click();
    await page.locator('#consoleSettingsToggle').click();
    await page.locator('#consoleKey').fill('sk-integration-fake-key');
    await page.locator('#consoleIncludeBoard').uncheck();
    await page.locator('#consoleMessage').fill('Run one durable test.');
    await page.locator('#consoleRun').click();
    await page.waitForFunction(() => window.__visionIntegration.snapshot().consoleSession?.runs?.[0]?.phase !== 'Sending to your PC…' && window.__visionIntegration.snapshot().consoleSession?.runs?.length === 1);
    await page.waitForTimeout(300);
    assert.equal(mock.counts.post,1,'one paid submission attempt');
    assert.equal(await page.locator('#consoleRun').isDisabled(),true,'ambiguous acceptance blocks a new paid submission');
    // A tab can close after the PC accepts the request but before the response
    // handler saves a run ID. Restore that real, persisted intermediate state.
    await page.evaluate(async () => {
      const run = window.__visionIntegration.getState().consoleSession.runs[0];
      run.status='preparing'; run.submissionUnknown=false; run.phase='Sending to your PC…';
      await window.__visionIntegration.backup();
    });
    await page.reload({waitUntil:'domcontentloaded'});
    await page.waitForFunction(() => window.__visionIntegration?.snapshot().consoleSession?.runs?.[0]?.submissionUnknown === true);
    assert.equal(await page.locator('#consoleRun').isDisabled(),true,'reload without an acceptance ID still blocks duplicate submission');
    mock.control.runsOffline = false;
    await page.evaluate(() => window.__visionIntegration.pollRuns());
    await page.waitForFunction(() => window.__visionIntegration.snapshot().consoleSession?.runs?.[0]?.text?.includes('survived'));
    await page.evaluate(async () => {await window.__visionIntegration.flush(); await window.__visionIntegration.backup();});
    await page.reload({waitUntil:'domcontentloaded'});
    await page.waitForFunction(() => window.__visionIntegration?.snapshot().consoleSession?.runs?.[0]?.text?.includes('survived'));
    const restored = await page.evaluate(() => window.__visionIntegration.snapshot().projectCloud);
    assert.equal(restored.id,identity.id,'same project restored after reload');
    assert.equal(restored.key,identity.key,'same private project capability restored after reload');
    assert.equal(mock.counts.post,1,'reopening observes existing run without submitting again');
    assert.equal(mock.control.apiKeyLeaked,false,'billing API key never enters saved project');
    assert.deepEqual(errors,[],'reconnect has no JavaScript runtime errors');
    return ['ambiguous acceptance cannot duplicate submission','reopening restores project and response without API key','saved project excludes API key'];
  } finally {await context.close();}
}

async function revisionConflict(browser) {
  const mock = processorMock();
  const {context,page,errors} = await openApp(browser,{viewport:{width:1280,height:900}},mock.handler);
  try {
    await page.evaluate(async () => {await window.__visionIntegration.createNode(); await window.__visionIntegration.flush();});
    const id = await page.evaluate(() => window.__visionIntegration.snapshot().projectCloud.id);
    const remote = mock.projects.get(id);
    remote.revision++;
    remote.project.title = 'Other device copy';
    await page.locator('#projectTitle').fill('Unsynced local edit');
    await page.evaluate(() => window.__visionIntegration.flush().catch(()=>{}));
    assert.equal(await page.evaluate(() => window.__visionIntegration.saveStatus().conflict),true,'409 produces explicit conflict');
    const writesAfterConflict = mock.counts.put;
    await page.locator('#projectTitle').fill('Keep my additional local edit');
    await page.waitForTimeout(1700);
    assert.equal(mock.counts.put,writesAfterConflict,'edits after conflict cannot silently overwrite PC copy');
    assert.equal(remote.project.title,'Other device copy');
    assert.equal(await page.locator('#projectTitle').inputValue(),'Keep my additional local edit','local changes preserved');
    assert.deepEqual(errors,[]);
    return ['revision conflict preserves both copies and stops automatic overwrite'];
  } finally {await context.close();}
}

async function main() {
  const passed = [];
  for (const test of [boardAndExport,durableReconnect,revisionConflict]) {
    const browser = await chromium.launch({headless:true, args:['--single-process','--no-zygote','--disable-gpu'], ...(process.env.CHROMIUM_PATH ? {executablePath:process.env.CHROMIUM_PATH}: {})});
    try {passed.push(...await test(browser)); console.log(test.name + ' passed');}
    finally {await browser.close();}
  }
  console.log('Integration checks passed: ' + passed.join('; '));
}
if (require.main === module) main().catch(error => {console.error(error.message); process.exitCode = 1;});
module.exports = {openApp,boardAndExport,processorMock,durableReconnect,revisionConflict};
