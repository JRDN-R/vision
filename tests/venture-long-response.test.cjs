'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const {test} = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../web/venture-ultra.js'), 'utf8');
function setup(capability) {
  const elements = new Map();
  const sandbox = {
    venture: {settings: {model: 'qwen-ultra-local', qwenUltra: {}}, localCapability: {ultra: capability}},
    $: id => {if (!elements.has(id)) elements.set(id, {}); return elements.get(id);},
    ventureActive: run => !['completed', 'incomplete', 'cancelled', 'error'].includes(run.status),
    escapeHTML: value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c])),
    ventureLoadingLabels: () => ['Working', 'Working']
  };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  return {sandbox, elements};
}

test('new setting is not sent to a server that does not support it', () => {
  const {sandbox} = setup({capabilities: {}});
  assert.equal(Object.hasOwn(sandbox.ventureUltraOptions({longResponses: true}), 'longResponses'), false);
  sandbox.venture.localCapability.ultra.capabilities.longResponses = true;
  assert.equal(sandbox.ventureUltraOptions({}).longResponses, true);
  assert.equal(sandbox.ventureUltraOptions({longResponses: false}).longResponses, false);
});

test('Deep defaults on supported servers and an explicit Standard choice persists', () => {
  const {sandbox, elements} = setup({capabilities: {deepReasoning: true, longResponses: true}});
  assert.equal(sandbox.ventureUltraOptions({}).reasoningMode, 'deep');
  sandbox.venturePaintUltra();
  assert.equal(elements.get('ventureUltraReasoning').value, 'deep');
  assert.equal(sandbox.ventureReadUltra().reasoningMode, 'deep');
  assert.match(elements.get('ventureUltraReasoningHelp').textContent, /same model/);
  sandbox.venture.settings.qwenUltra = {reasoningMode: 'standard'};
  sandbox.venturePaintUltra();
  assert.equal(sandbox.ventureReadUltra().reasoningMode, 'standard');
  sandbox.venture.localCapability.ultra = null;
  sandbox.venturePaintUltra();
  assert.equal(sandbox.ventureReadUltra().reasoningMode, 'standard');
  sandbox.venture.localCapability.ultra = {capabilities: {}};
  sandbox.venturePaintUltra();
  assert.equal(elements.get('ventureUltraReasoning').disabled, true);
  assert.match(elements.get('ventureUltraReasoningHelp').textContent, /Update FUPCJ/);
  assert.equal(Object.hasOwn(sandbox.ventureUltraOptions({reasoningMode: 'deep'}), 'reasoningMode'), false);
  assert.equal(Object.hasOwn(sandbox.ventureReadUltra(), 'reasoningMode'), false);
});

test('Deep reports real stages and partial reviews without a single-pass ETA', () => {
  const {sandbox} = setup({capabilities: {deepReasoning: true}});
  const run = {runId: 'deep', status: 'in_progress', localQueue: {reasoningMode: 'deep',
    estimatedTotalSeconds: 1, estimatedCompletionAt: 1, metrics: {reasoning: {stage: 'review'}}}};
  const markup = sandbox.ventureUltraProgress(run);
  assert.match(markup, /Deep · 2 of 3 · Reviewing/);
  assert.match(markup, /final answer appears after/);
  assert.doesNotMatch(markup, /aria-valuenow|Estimated completion|width:/);
  run.localQueue.metrics.reasoning = {stage: 'complete', limitedStages: ['review']};
  run.status = 'completed';
  assert.match(sandbox.ventureUltraProgress(run), /check may be incomplete/);
  run.localQueue.metrics.reasoning.limitedStages = [];
  assert.equal(sandbox.ventureUltraProgress(run), '');
});

test('long response progress reports saved passes without a made-up completion percentage', () => {
  const {sandbox} = setup({capabilities: {longResponses: true}});
  const markup = sandbox.ventureUltraProgress({runId: 'fixture', status: 'in_progress', localQueue: {
    longResponse: true, estimatedTotalSeconds: 1, estimatedCompletionAt: 1, processingSeconds: 100,
    metrics: {generation: {passNumber: 3, completedPasses: 2}}
  }});
  assert.match(markup, /Continuing your answer/);
  assert.match(markup, /2 processing passes finished/);
  assert.match(markup, /progress is saved/);
  assert.doesNotMatch(markup, /aria-valuenow|Estimated completion|width:/);
});

test('queued, stopping and limited continuation context are labelled', () => {
  const {sandbox} = setup({capabilities: {longResponses: true}});
  const run = {runId: 'fixture', status: 'queued', localQueue: {longResponse: true, metrics: {}}};
  assert.match(sandbox.ventureUltraProgress(run), /Waiting for your turn/);
  run.status = 'in_progress';
  run.phase = 'Stopping';
  assert.match(sandbox.ventureUltraProgress(run), /Stopping your response/);
  run.phase = 'Continuing';
  run.localQueue.metrics.generation = {continuationContextTruncated: true};
  assert.match(sandbox.ventureUltraProgress(run), /Earlier sections remain saved/);
  run.status = 'completed';
  assert.match(sandbox.ventureUltraProgress(run), /Review continuity across sections/);
  assert.doesNotMatch(sandbox.ventureUltraProgress(run), /venture-loading-symbol/);
  run.localQueue.metrics.generation.continuationContextTruncated = false;
  assert.equal(sandbox.ventureUltraProgress(run), '');
});

test('checkbox preserves an off choice across capability loading and disables known old servers', () => {
  const {sandbox, elements} = setup(null);
  assert.match(sandbox.ventureUltraControls(), /id="ventureUltraLongResponses"/);
  sandbox.venture.settings.qwenUltra = {longResponses: false};
  assert.equal(sandbox.ventureUltraOptions(sandbox.venture.settings.qwenUltra).longResponses, false);
  sandbox.venturePaintUltra();
  assert.equal(sandbox.venture.settings.qwenUltra.longResponses, false);
  assert.equal(sandbox.ventureReadUltra().longResponses, false);
  sandbox.venture.localCapability.ultra = {contexts: [4096, 8192, 32768], capabilities: {longResponses: true}};
  sandbox.venturePaintUltra();
  assert.equal(elements.get('ventureUltraLongResponses').checked, false);
  assert.equal(elements.get('ventureUltraLongResponses').disabled, false);
  assert.equal(sandbox.ventureReadUltra().longResponses, false);
  assert.match(elements.get('ventureUltraContext').innerHTML, /32,768 tokens/);
  sandbox.venture.localCapability.ultra.capabilities = {};
  sandbox.venturePaintUltra();
  assert.equal(elements.get('ventureUltraLongResponses').disabled, true);
  assert.equal(Object.hasOwn(sandbox.ventureReadUltra(), 'longResponses'), false);
  assert.equal(Object.hasOwn(sandbox.venture.settings.qwenUltra, 'longResponses'), false);
});
