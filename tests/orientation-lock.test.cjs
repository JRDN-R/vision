const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const script = fs.readFileSync(path.join(root, 'web/orientation-lock.js'), 'utf8');

function load({mobile = true, landscape = false, nativeLock = false, rejectLock = false, touchscreenLaptop = false} = {}) {
  const elements = new Map();
  const listeners = new Map();
  const calls = [];
  const classes = new Map();
  let currentLandscape = landscape;
  const on = (name, callback) => listeners.set(name, callback);
  const document = {
    readyState: 'complete',
    visibilityState: 'visible',
    head: {appendChild(element) {elements.set(element.id, element);}},
    body: {appendChild(element) {elements.set(element.id, element);}},
    documentElement: {classList: {toggle(name, enabled) {classes.set(name, enabled);}}},
    createElement(tag) {return {tagName: tag, hidden: false, setAttribute() {}};},
    getElementById(id) {return elements.get(id) || null;},
    addEventListener: on
  };
  const window = {
    innerWidth: 844,
    innerHeight: 390,
    matchMedia(query) {
      return {matches: query.includes('orientation: landscape') ? currentLandscape :
        query.includes('pointer: coarse') ? mobile && !touchscreenLaptop :
        query.includes('hover: none') ? mobile && !touchscreenLaptop : false};
    },
    addEventListener: on
  };
  const screen = {
    width: mobile || touchscreenLaptop ? 844 : 1920,
    height: mobile || touchscreenLaptop ? 390 : 1080,
    orientation: {
      addEventListener: on,
      ...(nativeLock ? {lock(type) {
        calls.push(type);
        return rejectLock ? Promise.reject(new Error('not permitted')) : Promise.resolve();
      }} : {})
    }
  };
  const navigator = {maxTouchPoints: mobile || touchscreenLaptop ? 5 : 0};
  vm.runInNewContext(script, {window, document, screen, navigator, Promise, Math});
  return {
    guard() {return elements.get('mobilePortraitGuard');},
    classes,
    calls,
    rotate(toLandscape) {currentLandscape = toLandscape; listeners.get('resize')();},
    fire(name) {listeners.get(name)();},
    document
  };
}

test('mobile landscape shows a portrait guard and restores the app on rotation', () => {
  const page = load({landscape: true});
  assert.equal(page.guard().hidden, false);
  assert.equal(page.classes.get('mobile-portrait-required'), true);
  page.rotate(false);
  assert.equal(page.guard().hidden, true);
  assert.equal(page.classes.get('mobile-portrait-required'), false);
  page.rotate(true);
  assert.equal(page.guard().hidden, false);
});

test('portrait and desktop layouts are not obstructed', () => {
  assert.equal(load().guard(), undefined);
  assert.equal(load({mobile: false, landscape: true}).guard(), undefined);
  assert.equal(load({mobile: false, touchscreenLaptop: true, landscape: true}).guard(), undefined);
});

test('native portrait lock is attempted on mobile and denied permissions do not break the guard', async () => {
  const page = load({landscape: true, nativeLock: true, rejectLock: true});
  assert.deepEqual(page.calls, ['portrait-primary']);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(page.guard().hidden, false);
  page.document.visibilityState = 'hidden';
  page.fire('visibilitychange');
  assert.equal(page.calls.length, 1);
  page.document.visibilityState = 'visible';
  page.fire('visibilitychange');
  assert.deepEqual(page.calls, ['portrait-primary', 'portrait-primary']);
  assert.deepEqual(load({mobile: false, nativeLock: true}).calls, []);
});

test('Vision/Venture and Vortex include the handler and portrait PWA manifests', () => {
  for (const file of ['manifest.webmanifest', 'vortex/manifest.webmanifest']) {
    const manifest = JSON.parse(fs.readFileSync(path.join(root, file), 'utf8'));
    assert.equal(manifest.orientation, 'portrait-primary');
    assert.equal(manifest.display, 'standalone');
  }
  const build = fs.readFileSync(path.join(root, 'web/build.py'), 'utf8');
  const vision = fs.readFileSync(path.join(root, 'web/document.html'), 'utf8');
  const loader = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const vortex = fs.readFileSync(path.join(root, 'vortex/index.html'), 'utf8');
  assert.match(build, /SCRIPTS = \['orientation-lock\.js'/);
  assert.match(vision, /rel="manifest" href="\.\/manifest\.webmanifest"/);
  assert.match(loader, /rel="manifest" href="\.\/manifest\.webmanifest"/);
  assert.match(vortex, /rel="manifest" href="\.\/manifest\.webmanifest"/);
  assert.match(vortex, /src="\.\.\/web\/orientation-lock\.js"/);
});
