/* Real pointer/keyboard interactions, with source-composed HTML and no network. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..'),url='https://vision-board.test/';
function appHTML(){
 let html=fs.readFileSync(path.join(root,'Vision.html'),'utf8');
 const build=fs.readFileSync(path.join(root,'web/build.py'),'utf8');
 const names=key=>[...build.match(new RegExp(key+' = \\[(.*?)\\]'))[1].matchAll(/'([^']+)'/g)].map(m=>m[1]);
 const script=names('SCRIPTS').map(name=>fs.readFileSync(path.join(root,'web',name),'utf8').replace(/\nrenderAll\(\);\s*$/,'\n')).join('\n');
 const hook=`
 markDirty=function(){dirty=true;updateHistory();};
 window.__boardTest={
  edges:()=>state.edges,selected:()=>selected,routes:()=>exportGraph(orderedNodes()),
  setup:async(edges=[])=>{const src=await nodePoster();cancelTranscriptionQueue();state.nodes=['A','B','C','D'].map((id,i)=>({id,kind:'node',title:id,prompt:'',caption:'',src,width:480,height:300,x:i%2?480:50,y:i<2?110:420,annotations:[],attachments:[]}));state.edges=edges;state.view={x:50,y:0,scale:1};history=[];future=[];selected=null;selectedMark=null;selectedEdge=null;pending=null;action=null;dirty=false;renderAll();setSidebar(false);},
  select:(from,to)=>selectConnection(from,to),focus:id=>focusBoardNode(id),
  mobile:()=>{state.nodes=state.nodes.slice(0,3);state.nodes.forEach((n,i)=>{n.x=20;n.y=90+270*i;});state.view={x:70,y:0,scale:.7};state.edges=[{from:'A',to:'B',condition:'',conditionEnabled:false}];renderAll();setSidebar(false);},
  legacy:()=>validateProject({format:'Vision',version:4,nodes:state.nodes,edges:[{from:'A',to:'B',condition:''},{from:'A',to:'C',condition:'approved'}],settings:state.settings,view:state.view})
 };
 renderAll();
 })();`;
 const scripts=[...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)],last=scripts.at(-1),start=last.index+last[0].indexOf('>')+1;
 html=html.slice(0,start)+script+hook+html.slice(start+last[1].length);
 const css=names('STYLES').map(name=>fs.readFileSync(path.join(root,'web',name),'utf8')).join('\n');
 return html.replace(/(<style\b[^>]*>)[\s\S]*?(<\/style>)/,(_,a,b)=>a+css+b);
}
async function run(){
 const browser=await chromium.launch({headless:true,args:['--disable-gpu'],...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
 const errors=[];
 try{
  const context=await browser.newContext({viewport:{width:1200,height:950}});
  await context.route('**/*',route=>route.request().url()===url?route.fulfill({status:200,contentType:'text/html',body:appHTML()}):route.abort());
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto(url,{waitUntil:'domcontentloaded'});await page.waitForFunction(()=>!!window.__boardTest);
  const setup=edges=>page.evaluate(edges=>window.__boardTest.setup(edges),edges||[]);
  const edges=()=>page.evaluate(()=>window.__boardTest.edges());
  const center=async selector=>{const rect=await page.locator(selector).boundingBox();assert.ok(rect,selector);return{x:rect.x+rect.width/2,y:rect.y+rect.height/2};};
  const drag=async(from,to)=>{const start=await center(from),end=typeof to==='string'?await center(to):to;await page.mouse.move(start.x,start.y);await page.mouse.down();await page.mouse.move(end.x,end.y,{steps:10});await page.mouse.up();};
  const handle=(from,to,side)=>`.wire-end-hit[data-from="${from}"][data-to="${to}"][data-side="${side}"]`;
  const port=(id,side)=>`#node-${id} [data-port="${side}"]`;
  await setup();assert.equal(await page.locator('#boardUndo,#boardRedo').count(),0,'only the left toolbar has history controls');
  await drag(port('A','out'),port('B','in'));await drag(port('A','out'),port('C','in'));
  assert.equal((await edges()).length,2);assert.ok((await edges()).every(e=>e.conditionEnabled===false));assert.equal(await page.locator('.wire-condition').count(),0,'parallel routes do not force IF');
  assert.deepEqual(await page.evaluate(()=>window.__boardTest.routes().map(e=>e.mode)),['always','always']);
  await drag(handle('A','B','in'),port('D','in'));assert.ok((await edges()).some(e=>e.from==='A'&&e.to==='D'),'grab and move the destination without first selecting a wire');
  await drag(handle('A','D','out'),port('B','out'));assert.ok((await edges()).some(e=>e.from==='B'&&e.to==='D'),'grab and move the source');
  const before=await edges();await drag(handle('B','D','in'),{x:1060,y:800});assert.deepEqual(await edges(),before,'dropping on empty canvas preserves the original wire');
  await page.keyboard.press('Control+z');assert.ok((await edges()).some(e=>e.from==='A'&&e.to==='D'),'canceled drag adds no undo step');
  await page.keyboard.press('Control+Shift+z');assert.deepEqual(await edges(),before,'Ctrl Shift Z redoes');
  await page.keyboard.press('Meta+z');assert.ok((await edges()).some(e=>e.from==='A'&&e.to==='D'),'Cmd Z undoes');await page.keyboard.press('Meta+Shift+z');assert.deepEqual(await edges(),before,'Cmd Shift Z redoes');
  await setup([{from:'A',to:'B',condition:'approved',conditionEnabled:true},{from:'B',to:'C',condition:'',conditionEnabled:false}]);
  const loopBefore=await edges();await drag(handle('A','B','out'),port('C','out'));assert.deepEqual(await edges(),loopBefore,'moving a wire cannot create a cycle');
  await page.locator('.wire-condition').click();assert.equal((await edges())[0].conditionEnabled,false);assert.equal((await edges())[0].condition,'approved','turning IF off preserves its draft');assert.equal(await page.locator('.wire-condition').count(),0);
  assert.equal((await page.evaluate(()=>window.__boardTest.routes()))[0].mode,'always');
  await page.evaluate(()=>window.__boardTest.select('A','B'));await page.locator('#branchConditional').check();assert.equal((await edges())[0].conditionEnabled,true);assert.equal((await page.evaluate(()=>window.__boardTest.routes()))[0].mode,'conditional');
  const legacy=await page.evaluate(async()=>{const project=await window.__boardTest.legacy();return project.edges;});assert.ok(legacy.every(e=>e.conditionEnabled),'legacy IF routing is preserved when loading saved projects');
  await setup([{from:'A',to:'B',condition:'',conditionEnabled:false},{from:'B',to:'C',condition:'',conditionEnabled:false}]);
  await page.evaluate(()=>window.__boardTest.focus('B'));await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>window.__boardTest.selected()),'C');await page.keyboard.press('Shift+Tab');assert.equal(await page.evaluate(()=>window.__boardTest.selected()),'B');
  assert.ok(await page.locator('#node-B').evaluate(el=>{const r=el.getBoundingClientRect(),b=document.getElementById('board').getBoundingClientRect();return r.left>=b.left&&r.right<=b.right&&r.top>=b.top&&r.bottom<=b.bottom;}),'navigation reveals the whole selected module');
  await page.evaluate(()=>document.getElementById('sidebarToggle').click());await page.locator('#captionInput').focus();await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>window.__boardTest.selected()),'B','Tab in fields keeps ordinary focus behavior');
  await page.locator('#helpBtn').click();await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>window.__boardTest.selected()),'B','dialog Tab does not navigate the board');await page.locator('#helpDone').click();
  await context.close();
  const mobile=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  await mobile.route('**/*',route=>route.request().url()===url?route.fulfill({status:200,contentType:'text/html',body:appHTML()}):route.abort());
  const touchPage=await mobile.newPage();touchPage.on('pageerror',e=>errors.push(e.message));await touchPage.goto(url,{waitUntil:'domcontentloaded'});await touchPage.waitForFunction(()=>!!window.__boardTest);await touchPage.evaluate(async()=>{await window.__boardTest.setup();window.__boardTest.mobile();});
  const session=await mobile.newCDPSession(touchPage);
  const touchDrag=async(from,to)=>{const a=await touchPage.locator(from).boundingBox(),b=typeof to==='string'?await touchPage.locator(to).boundingBox():to;const x=a.x+a.width/2,y=a.y+a.height/2+12,end={x:b.x+(b.width||0)/2,y:b.y+(b.height||0)/2};await session.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y,id:1}]});for(let i=1;i<=8;i++)await session.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x+(end.x-x)*i/8,y:y+(end.y-y)*i/8,id:1}]});await session.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});};
  await touchDrag(handle('A','B','in'),port('C','in'));assert.equal((await touchPage.evaluate(()=>window.__boardTest.edges()))[0].to,'C','touch can grab the broad endpoint target without panning');
  await touchDrag(handle('A','C','in'),{x:340,y:690});assert.equal((await touchPage.evaluate(()=>window.__boardTest.edges()))[0].to,'C','touch release on empty board snaps back');
  await mobile.close();assert.deepEqual(errors,[]);
  console.log('PASS mouse/touch wire endpoints, cancel, cycle prevention, explicit IF and export, legacy paths, single history toolbar, Ctrl/Cmd undo/redo, and scoped Tab navigation.');
 }finally{await browser.close();}
}
run().catch(e=>{console.error(e);process.exitCode=1;});
