const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function setup(url){
 const events={},calls=[],root={dataset:{}},manifest={};
 const c={URL,URLSearchParams,Set,location:new URL(url),document:{documentElement:root,querySelector:selector=>selector.startsWith('link')?manifest:null},CustomEvent:class{constructor(type,init){this.type=type;this.detail=init.detail;}}};
 c.window=c;c.history={pushState(_,__,url){calls.push(['push',url]);c.location=new URL(url);},replaceState(_,__,url){calls.push(['replace',url]);c.location=new URL(url);}};
 c.addEventListener=(type,fn)=>{(events[type]??=[]).push(fn);};c.dispatchEvent=e=>(events[e.type]||[]).forEach(fn=>fn(e));
 vm.createContext(c);vm.runInContext(fs.readFileSync('web/workspace-route.js','utf8'),c);
 return {c,route:c.VisionWorkspaceRoute,root,manifest,calls,events};
}
test('direct Venture and legacy links choose Venture before UI initialization',()=>{
 for(const path of ['/vision/venture/','/vision/venture/index.html','/vision/venture','/vision/?view=venture','/vision/Vision.html?view=venture']){
  const {route,root,manifest,c}=setup('https://example.test'+path);
  assert.equal(route.chosen(),'venture');assert.equal(root.dataset.workspacePage,'venture');assert.equal(c.document.title,'Venture — by Jordan R.');
  assert.equal(route.rootURL().href,'https://example.test/vision/');assert.equal(manifest.href,'https://example.test/vision/venture/manifest.webmanifest');
 }
});
test('chevron navigation pushes real page URLs without reloading and avoids duplicate history',()=>{
 const {route,root,calls,c}=setup('https://example.test/vision/?view=vision');
 route.commit('venture');assert.equal(c.location.href,'https://example.test/vision/venture/');assert.equal(calls[0][0],'push');
 route.commit('venture');assert.equal(calls.length,1);
 route.commit('vision');assert.equal(c.location.href,'https://example.test/vision/?view=vision');assert.equal(calls.length,2);assert.equal(root.dataset.workspacePage,'vision');
});
test('browser history switches the page without adding a new history entry',()=>{
 const {route,c,calls,root}=setup('https://example.test/vision/venture/');
 c.addEventListener('vision-workspace-route',e=>route.commit(e.detail.view));
 c.location=new URL('https://example.test/vision/?view=vision');c.dispatchEvent({type:'popstate'});
 assert.equal(root.dataset.workspacePage,'vision');assert.equal(calls.length,0);
 c.location=new URL('https://example.test/vision/venture/');c.dispatchEvent({type:'popstate'});
 assert.equal(root.dataset.workspacePage,'venture');assert.equal(calls.length,0);
});
test('portable local app switches pages without rewriting file URLs',()=>{
 const {route,c,root,calls}=setup('file:///Downloads/Vision.html');route.commit('venture');assert.equal(root.dataset.workspacePage,'venture');assert.equal(c.location.href,'file:///Downloads/Vision.html');assert.equal(calls.length,0);
});
test('legacy deep links normalize without adding a history entry',()=>{
 const {route,c,calls}=setup('https://example.test/vision/Vision.html?view=venture');route.commit('venture');assert.equal(c.location.pathname,'/vision/venture/');assert.equal(calls[0][0],'replace');
});
