/* Banners follow upper controls, never the bottom Details button, and stay inside the board. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('web/board.js','utf8');
const body=source.slice(source.indexOf('function positionToast('),source.indexOf('function syncBoardAppearance('));
const box=(left,top,width,height)=>({left,top,width,height,right:left+width,bottom:top+height});
const control=rect=>({rect,hidden:false,display:'block',visibility:'visible',getBoundingClientRect(){return this.rect;}});
const properties={},notice={offsetWidth:280,style:{setProperty:(key,value)=>properties[key]=value},classList:{contains:()=>true}};
const elements={toast:notice,sidebarToggle:control(box(300,792,78,40)),refreshNotice:control(box(150,38,140,30))};
const board=control(box(0,24,390,820));board.isConnected=true;
const header=control(box(0,-180,390,160));header.visibility='hidden';
let scheduled;
const context={board,appHeader:header,headerReveal:control(box(0,0,390,24)),$:id=>elements[id],document:{documentElement:{clientWidth:390}},
 getComputedStyle:element=>element===notice?{right:String(12+Number.parseFloat(properties['--toast-board-inset']||0))}:element,
 cancelAnimationFrame(){},requestAnimationFrame:fn=>{scheduled=fn;return 1;}};
vm.createContext(context);vm.runInContext(body,context);
const position=()=>context.positionToast();
position();assert.equal(properties['--toast-top'],'76px','banner starts below Save and ignores bottom Details');assert.equal(properties['--toast-max-width'],'304px','left toolbar has reserved space');assert.equal(properties['--toast-board-inset'],'0px');assert.equal(typeof scheduled,'function');
header.rect=box(0,24,390,170);header.visibility='visible';scheduled();assert.equal(properties['--toast-top'],'202px','banner follows expanded mobile header');
header.visibility='hidden';elements.refreshNotice.hidden=true;scheduled();assert.equal(properties['--toast-top'],'36px','hidden upper controls leave no stale gap while Details remains visible');
elements.sidebarToggle.rect=box(300,350,78,40);scheduled();assert.equal(properties['--toast-top'],'36px','opening the bottom sheet does not drag the banner downward');
context.document.documentElement.clientWidth=1200;board.rect=box(0,70,910,800);header.rect=box(0,0,1200,70);header.visibility='visible';context.headerReveal.hidden=true;elements.sidebarToggle.rect=box(800,818,98,40);
position();assert.equal(properties['--toast-board-inset'],'290px','desktop inspector controls stay clear');assert.equal(properties['--toast-max-width'],'310px');assert.equal(properties['--toast-top'],'82px','desktop banner stays at the board top above bottom Details');
console.log('PASS: upper-right banner follows header transitions, ignores bottom Details controls, reserves left tools, and stays outside desktop inspector.');
