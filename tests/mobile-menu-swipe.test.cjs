/* Touch-only, browser-independent regression tests for all three menus.
 * Run with: node tests/mobile-menu-swipe.test.cjs
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../web/mobile-menu-swipe.js'), 'utf8');
class MockElement {
 constructor(flags = [], {parent = null, scrollWidth = 0, clientWidth = 400, overflowX = 'visible'} = {}) {
  this.flags = flags; this.parentElement = parent; this.scrollWidth = scrollWidth; this.clientWidth = clientWidth; this.overflowX = overflowX;
 }
 closest(selector) {
  for (let node = this; node; node = node.parentElement) {
   if (node.flags.some(flag => selector.includes(flag))) return node;
  }
  return null;
 }
}
function harness({mobile = true, enabled = true, target = new MockElement()} = {}) {
 let time = 0, dialog = false, open = false;
 const calls = [], listeners = new Map(), root = new MockElement();
 target.parentElement ||= root;
 const document = {
  documentElement:root,
  querySelector(selector) { return selector === 'dialog[open]' && dialog ? {} : null; },
  addEventListener(type, listener) {
   if (!listeners.has(type)) listeners.set(type, []);
   listeners.get(type).push(listener);
  }
 };
 const window = {};
 vm.runInNewContext(source, {window, document, Element:MockElement, matchMedia:()=>({matches:mobile}),
  getComputedStyle:el=>({overflowX:el.overflowX}), performance:{now:()=>time}});
 window.MobileMenuSwipe.install({
  enabled:()=>enabled,
  isOpen:()=>open,
  open:()=>{calls.push('open');open=true;},
  close:()=>{calls.push('close');open=false;},
  ignoreOpeningFrom:el=>!!el.closest('.node,.port,.tools')
 });
 const fire=(type,event)=>{for(const listener of listeners.get(type)||[])listener(event);};
 const touch=(x,y,id=1)=>({identifier:id,clientX:x,clientY:y});
 function swipe(x1,y1,x2,y2,{subject=target,multi=false,duration=200,verticalFirst=false}={}) {
  const start=touch(x1,y1),finish=touch(x2,y2);
  fire('touchstart',{target:subject,touches:[start],changedTouches:[start]});
  if (multi) fire('touchstart',{target:subject,touches:[start,touch(x1+10,y1,2)],changedTouches:[touch(x1+10,y1,2)]});
  if(verticalFirst) fire('touchmove',{target:subject,touches:[touch(x1+2,y1+65)],changedTouches:[touch(x1+2,y1+65)]});
  time += duration / 2;
  fire('touchmove',{target:subject,touches:[finish],changedTouches:[finish]});
  time += duration / 2;
  fire('touchend',{target:subject,touches:[],changedTouches:[finish]});
 }
 return {calls,swipe,fire,touch,setDialog:value=>{dialog=value;},setEnabled:value=>{enabled=value;},
  advance:amount=>{time+=amount;}, isOpen:()=>open};
}
function check(label, fn) { fn(); console.log('PASS', label); }
check('Left-to-right opens, right-to-left closes, reversed directions do nothing', () => {
 const h=harness();
 h.swipe(180,310,70,308); assert.deepEqual(h.calls,[]);
 h.swipe(40,300,150,309); assert.equal(h.isOpen(),true);
 h.swipe(40,300,150,309); assert.deepEqual(h.calls,['open']);
 h.swipe(190,300,55,305); assert.deepEqual(h.calls,['open','close']);
});
check('Vertical, diagonal, short, and slow movements are not swipes', () => {
 const h=harness();
 h.swipe(30,100,40,250);
 h.swipe(20,100,100,160);
 h.swipe(20,100,73,102);
 h.swipe(20,100,160,105,{duration:1200});
 assert.deepEqual(h.calls,[]);
});
check('Pinches and vertical scrolling never open the menu', () => {
 const h=harness();
 h.swipe(20,120,180,120,{multi:true});
 h.swipe(20,120,180,125,{verticalFirst:true});
 assert.deepEqual(h.calls,[]);
});
check('Dialogs, text fields, and draggable board elements stay untouched', () => {
 const h=harness();
 h.setDialog(true);h.swipe(20,150,170,150);h.setDialog(false);
 for(const flag of ['input','.node','.tools','[role="slider"]']) {
  h.swipe(20,150,170,150,{subject:new MockElement([flag])});
 }
 assert.deepEqual(h.calls,[]);
});
check('Horizontally scrolling content retains its native swipe', () => {
 const h=harness();
 h.swipe(20,150,170,150,{subject:new MockElement([],{scrollWidth:800,clientWidth:200,overflowX:'auto'})});
 assert.deepEqual(h.calls,[]);
});
check('Disabled accounts and desktop mode ignore touch gestures', () => {
 const h=harness({enabled:false});h.swipe(20,150,170,150);
 const desktop=harness({mobile:false});desktop.swipe(20,150,170,150);
 assert.deepEqual(h.calls,[]);assert.deepEqual(desktop.calls,[]);
});
check('Synthetic click after a swipe is consumed, not the next tap', () => {
 const h=harness();h.swipe(20,150,170,150);
 let cancelled=0,stopped=0;
 const click=()=>({clientX:170,clientY:150,preventDefault(){cancelled++;},stopImmediatePropagation(){stopped++;}});
 h.fire('click',click());assert.equal(cancelled,1);assert.equal(stopped,1);
 h.fire('click',click());assert.equal(cancelled,1);
});
check('Integration files wire Vision/Venture, Vortex, and portable build', () => {
 const navigation=fs.readFileSync(path.join(__dirname,'../web/navigation.js'),'utf8');
 const vortex=fs.readFileSync(path.join(__dirname,'../web/vortex/app.js'),'utf8');
 const build=fs.readFileSync(path.join(__dirname,'../web/build.py'),'utf8');
 assert.match(navigation,/MobileMenuSwipe\.install\(/);
 assert.match(vortex,/import ['"]\.\.\/mobile-menu-swipe\.js/);
 assert.match(vortex,/MobileMenuSwipe\.install\(/);
 assert.match(build,/'mobile-menu-swipe\.js', 'navigation\.js'/);
});
