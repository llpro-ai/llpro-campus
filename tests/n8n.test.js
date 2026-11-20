'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { Llpro, ENDPOINTS } = require('../n8n/nodes/Llpro/Llpro.node.js');
const { LlproApi } = require('../n8n/credentials/LlproApi.credentials.js');
const pkg = require('../n8n/package.json');

function ctx(params, { key, response, continueOnFail = false } = {}) {
  const calls = [];
  const c = {
    getInputData: () => [{ json: {} }],
    getNodeParameter: (n, i, d) => (n in params ? params[n] : d),
    getCredentials: async () => { if (!key) throw new Error('no credentials'); return { apiKey: key }; },
    continueOnFail: () => continueOnFail,
    helpers: { httpRequest: async (o) => { calls.push(o); return response; } },
  };
  return { c, calls };
}
const run = (c) => new Llpro().execute.call(c);

test('package is a community node package with fixed paths', () => {
  assert.ok(pkg.keywords.includes('n8n-community-node-package'));
  assert.match(pkg.name, /^n8n-nodes-/);
  assert.strictEqual(pkg.dependencies, undefined);
  assert.strictEqual(pkg.scripts.install, undefined);
  assert.strictEqual(pkg.scripts.postinstall, undefined);
  for (const p of [...pkg.n8n.nodes, ...pkg.n8n.credentials]) require('../n8n/' + p);
  assert.strictEqual(new LlproApi().properties[0].typeOptions.password, true);
});

test('ground truth: place call, via header, no key without credential', async () => {
  const { c, calls } = ctx({ operation: 'groundTruth', topic: 'river_level', place: 'Lafayette, LA' },
    { response: { statusCode: 200, body: { answer: 'x' } } });
  const [out] = await run(c);
  assert.deepStrictEqual(out[0].json, { answer: 'x' });
  assert.strictEqual(calls[0].url, ENDPOINTS.groundTruth);
  assert.deepStrictEqual(calls[0].body, { topic: 'river_level', place: 'Lafayette, LA' });
  assert.strictEqual(calls[0].headers['X-LLPRO-Via'], 'n8n');
  assert.strictEqual(calls[0].headers.Authorization, undefined);
  assert.strictEqual(calls[0].disableFollowRedirect, true);
});

test('ground truth: lat/lon when no place', async () => {
  const { c, calls } = ctx({ operation: 'groundTruth', topic: 'weather', place: '', lat: 30.2, lon: -92 },
    { response: { statusCode: 200, body: {} } });
  await run(c);
  assert.deepStrictEqual(calls[0].body, { topic: 'weather', lat: 30.2, lon: -92 });
});

test('countersign sends producer, kind only when set; key sent as bearer', async () => {
  const { c, calls } = ctx({ operation: 'countersign', output: '12 * 4 = 48', kind: 'calculation', producerAgentId: 'wf' },
    { key: 'llk_secret123', response: { statusCode: 200, body: { verdict: 'pass' } } });
  await run(c);
  assert.strictEqual(calls[0].url, ENDPOINTS.countersign);
  assert.deepStrictEqual(calls[0].body, { output: '12 * 4 = 48', producer: { agent_id: 'wf' }, kind: 'calculation' });
  assert.strictEqual(calls[0].headers.Authorization, 'Bearer llk_secret123');
});

test('agent record', async () => {
  const { c, calls } = ctx({ operation: 'agentRecord', target: 'https://gt.llpro.ai/mcp' },
    { response: { statusCode: 200, body: { listings: [] } } });
  await run(c);
  assert.strictEqual(calls[0].url, ENDPOINTS.agentRecord);
  assert.deepStrictEqual(calls[0].body, { target: 'https://gt.llpro.ai/mcp' });
});

test('402 says how to pay, tags the link with via=n8n, never shows the key', async () => {
  const { c } = ctx({ operation: 'agentRecord', target: 'x' },
    { key: 'llk_secret123', response: { statusCode: 402, body: { error: 'Free allowance used up for llk_secret123', get_a_key: 'https://llpro.ai/pay/credit' } } });
  await assert.rejects(run(c), (e) => {
    assert.match(e.message, /https:\/\/llpro\.ai\/pay\/credit\?via=n8n/);
    assert.ok(!e.message.includes('llk_secret123'));
    return true;
  });
});

test('402 keeps an existing via tag', async () => {
  const { c } = ctx({ operation: 'agentRecord', target: 'x' },
    { response: { statusCode: 402, body: { get_a_key: 'https://llpro.ai/pay/credit?via=xsell' } } });
  await assert.rejects(run(c), (e) => { assert.ok(e.message.includes('?via=xsell') && !e.message.includes('via=n8n')); return true; });
});

test('400 says nothing was charged; continueOnFail returns error item', async () => {
  const { c } = ctx({ operation: 'agentRecord', target: 'x' },
    { continueOnFail: true, response: { statusCode: 400, body: { error: 'bad target' } } });
  const [out] = await run(c);
  assert.match(out[0].json.error, /nothing was charged: bad target/);
});

test('empty inputs fail before any call', async () => {
  const { c, calls } = ctx({ operation: 'countersign', output: '  ' }, { response: { statusCode: 200, body: {} } });
  await assert.rejects(run(c), /empty/);
  assert.strictEqual(calls.length, 0);
});

test('only the three LLPRO https endpoints exist', () => {
  assert.deepStrictEqual(Object.values(ENDPOINTS).sort(), [
    'https://agentrecord.llpro.ai/v1/lookup', 'https://countersign.llpro.ai/v1/stamp', 'https://gt.llpro.ai/v1/ask']);
});
