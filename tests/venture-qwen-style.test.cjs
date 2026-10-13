const assert=require('node:assert/strict');
const fs=require('node:fs');
const test=require('node:test');

test('Qwen Local keeps a solid blue label with a slow, orbiting glow',()=>{
 const css=fs.readFileSync('web/venture.css','utf8');
 const nameRule=css.match(/\.model-qwen \.venture-model-name\{([^}]*)\}/)?.[1];
 assert.ok(nameRule,'Qwen model name styling is present');
 assert.match(nameRule,/color:#487dff/);
 assert.match(nameRule,/animation:qwen-name-halo 7\.2s ease-in-out infinite/);
 assert.doesNotMatch(nameRule,/background|gradient|transparent/i);
 assert.match(css,/@keyframes qwen-name-halo\{/);
 for(const frame of ['0%,100%','25%','50%','75%'])assert.ok(css.includes(frame+'{text-shadow:'),frame+' halo position');
 assert.doesNotMatch(css,/qwen-name-shift|#ffc300/,'Old yellow gradient must not return');
});

test('Qwen identity keeps its white symbol and a stationary reduced-motion glow',()=>{
 const css=fs.readFileSync('web/venture.css','utf8');
 assert.match(css,/\.model-qwen \.venture-model-symbol\{background:#fff;/);
 assert.match(css,/@media\(prefers-reduced-motion:reduce\)\{\.model-qwen \.venture-model-name\{animation:none;text-shadow:0 0 7px #6988ff55\}\}/);
});
