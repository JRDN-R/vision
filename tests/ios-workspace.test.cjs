const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('ios/App/WorkspaceScripts.swift', 'utf8');
const script = name => {
  const match = source.match(new RegExp('static let '+name+' = #"""([\\s\\S]*?)"""#'));
  assert.ok(match, name+' script exists');
  return match[1];
};
const selectors = ['#workspaceMenu .top-actions', '#ventureSidebar .workspace-menu-footer', '#navigation .nav-footer'];
function fixture({native = true, menus = true, subframe = false} = {}) {
  const ids = new Map(), containers = new Map(), observers = [], messages = [], events = new Map();
  class Element {
    constructor() { this.children = []; this.attributes = {}; this.listeners = {}; }
    set id(value) { this._id = value; ids.set(value, this); }
    get id() { return this._id; }
    appendChild(child) { this.children.push(child); }
    setAttribute(key,value) { this.attributes[key] = value; }
    getAttribute(key) { return this.attributes[key]; }
    addEventListener(name,handler) { this.listeners[name] = handler; }
    click() { return this.listeners.click?.({preventDefault(){}}); }
  }
  function addMenus() { for (const selector of selectors) containers.set(selector, new Element()); }
  if (menus) addMenus();
  const document = {
    getElementById: id => ids.get(id) || null,
    querySelector: selector => containers.get(selector) || null,
    createElement: () => new Element(), head: new Element(), documentElement: new Element()
  };
  const toggle = new Element(); toggle.id='workspaceMenuButton'; toggle.setAttribute('aria-expanded','true');
  let dismissals=0; toggle.addEventListener('click',()=>{dismissals++; toggle.setAttribute('aria-expanded','false');});
  const context = {document, Promise, queueMicrotask,
    MutationObserver: class { constructor(callback) { this.callback=callback; observers.push(this); } observe(){} disconnect(){} },
    addEventListener: (name,handler) => events.set(name,handler),
    webkit: {messageHandlers: {vision: {postMessage: body => { messages.push(body); return Promise.resolve(true); }}}}
  };
  context.window=context; context.top=subframe?{}:context;
  vm.createContext(context);
  if(native) vm.runInContext(script('bridge'),context);
  const install=()=>vm.runInContext(script('sidebar'),context);
  return {context,ids,containers,observers,messages,events,addMenus,install,get dismissals(){return dismissals;}};
}

test('original sidebars get native actions without replacing existing controls',async()=>{
  const f=fixture(); const original={id:'original-projects',onclick(){}};
  f.containers.get(selectors[0]).appendChild(original); f.install();
  assert.equal(f.containers.get(selectors[0]).children[0],original);
  for(const prefix of ['vision-iphone','venture-iphone','vortex-iphone']) {
    for(const action of ['downloads','settings','reload']) {
      const button=f.ids.get(prefix+'-'+action); assert.ok(button);
      button.click();
      assert.equal(f.messages.at(-1).type,action);
    }
  }
  assert.equal(f.dismissals,1);
  assert.equal(f.ids.get('vision-iphone-downloads').type,'button');
});

test('reinstalling and observing late-created menus is idempotent',async()=>{
  const f=fixture({menus:false});f.install();
  f.addMenus();f.observers[0].callback();await new Promise(resolve=>queueMicrotask(resolve));
  f.install();f.observers[0].callback();await new Promise(resolve=>queueMicrotask(resolve));
  for(const selector of selectors) assert.equal(f.containers.get(selector).children.length,3);
  assert.equal(f.observers.length,1);
});

test('ordinary browser and subframes never get native sidebar controls',()=>{
  for(const options of [{native:false},{subframe:true}]) {
    const f=fixture(options);f.install();
    for(const selector of selectors) assert.equal(f.containers.get(selector).children.length,0);
    assert.equal(f.messages.length,0);
  }
});

test('native API installation is immutable and repeatable',()=>{
  const f=fixture();const first=f.context.VisionNative;
  vm.runInContext(script('bridge'),f.context);
  assert.equal(f.context.VisionNative,first);
  assert.equal(Object.isFrozen(first),true);
  first.adoptVortexJob('a'.repeat(24));
  assert.equal(f.messages.at(-1).jobID,'a'.repeat(24));
});

test('native shell keeps the website navigation and has bounded recovery',()=>{
  const root=fs.readFileSync('ios/App/VisionRootView.swift','utf8');
  const web=fs.readFileSync('ios/App/VisionWebView.swift','utf8');
  assert.doesNotMatch(root,/ForEach\(VisionPage|safeAreaInset\(edge:\s*\.bottom|navigationTitle\(web\.page/);
  assert.match(root,/\.task\s*\{\s*web\.start\(\)/);
  assert.match(root,/Button\("Retry"\)/);
  assert.match(web,/Task\.sleep\(nanoseconds: 60_000_000_000\)/);
  assert.match(web,/didFail navigation: WKNavigation!/);
  assert.doesNotMatch(web,/merge the iOS update/);
});
