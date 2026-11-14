// Tests for the Node.js bridge. Every call goes to a local fake server; nothing here calls llpro.ai.
'use strict';
const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const path = require('node:path');
const { spawn } = require('node:child_process');

const BIN = path.join(__dirname, '..', 'npm', 'bin', 'llpro-mcp.js');
const { endpoint, parseSse, ROAD, ROADS } = require(BIN);

function fakeServer() {
  const hits = [];
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', async () => {
      const body = Buffer.concat(chunks);
      const u = new URL(req.url, 'http://127.0.0.1');
      hits.push({ path: u.pathname, via: u.searchParams.get('via'), headers: req.headers, body });
      const send = (code, data, type, extra) => {
        const h = Object.assign({}, extra || {});
        if (data) h['Content-Type'] = type || 'application/json';
        res.writeHead(code, h); res.end(data || '');
      };
      let msg;
      try { msg = JSON.parse(body.toString('utf8')); } catch (e) { return send(400, JSON.stringify({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'parse error' } })); }
      const items = Array.isArray(msg) ? msg : [msg];
      const m0 = items[0] || {};
      if (m0.method === 'boom') return send(500, '<html>oops</html>', 'text/html');
      if (m0.method === 'empty') return send(200);
      if (m0.method === 'limit') return send(429, JSON.stringify({ jsonrpc: '2.0', id: m0.id, error: { code: -32029, message: 'rate limited' } }));
      if (m0.method === 'redirect') return send(302, '<html>moved</html>', 'text/html', { Location: '/elsewhere' });
      if (m0.method === 'big') return send(200, '[' + ' '.repeat(17 * 1024 * 1024) + ']');
      if (m0.method === 'gone') return send(404);
      if (m0.method === 'raw') return send(200, m0.params.body, m0.params.ctype || 'application/json');
      if (m0.method === 'rawsse') {
        res.writeHead(200, { 'Content-Type': 'text/event-stream' });
        for (let i = 0; i < m0.params.events.length; i++) {
          if (i) await new Promise((r) => setTimeout(r, (m0.params.delay || 0) * 1000));
          res.write(m0.params.events[i]);
        }
        const end = Date.now() + (m0.params.hold || 0) * 1000; // keep the stream open with keepalive comments
        while (Date.now() < end && !res.destroyed && !req.socket.destroyed) {
          await new Promise((r) => setTimeout(r, 300));
          res.write(': keepalive\n\n');
        }
        return res.end();
      }
      if (m0.method === 'slow') await new Promise((r) => setTimeout(r, 1500));
      const out = [];
      for (const m of items) {
        if (!m || m.id === undefined || m.id === null || !m.method) continue;
        let result = m.params || {};
        if (m.method === 'initialize') result = { protocolVersion: (m.params && m.params.version) || '2025-06-18', capabilities: {}, serverInfo: { name: 'fake' } };
        if (m.method === 'echo/headers') result = { via: u.searchParams.get('via'), version: req.headers['mcp-protocol-version'] || null, session: req.headers['mcp-session-id'] || null, agent: req.headers['x-agent-id'] || null, ua: req.headers['user-agent'] };
        if (m.method === 'tools/list') result = m.params && m.params.cursor === 'p2' ? { tools: [{ name: 'fake_two', description: 'Second page tool.' }] } : { tools: [{ name: 'fake_one', description: 'First tool.\nMore lines here.' }], nextCursor: 'p2' };
        if (m.method === 'tools/call') result = { content: [{ type: 'text', text: JSON.stringify(m.params.arguments) }], isError: m.params.name === 'fails' };
        out.push({ jsonrpc: '2.0', id: m.id, result });
      }
      const extra = m0.method === 'initialize' ? { 'Mcp-Session-Id': 'sess-1' } : {};
      if (!out.length) return send(202, '', null, extra);
      const data = JSON.stringify(Array.isArray(msg) ? out : out[0]);
      if (m0.method === 'sse/test') return send(200, 'event: message\ndata: ' + data + '\n\n', 'text/event-stream', extra);
      return send(200, data, 'application/json', extra);
    });
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve({ server, hits, url: `http://127.0.0.1:${server.address().port}/mcp` })));
}

function run(url, lines, envExtra, args) {
  assert.ok(url.startsWith('http://127.0.0.1:'), 'tests must never call a real server');
  const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('LLPRO_')));
  Object.assign(env, { LLPRO_MCP_URL: url }, envExtra || {});
  return new Promise((resolve) => {
    const p = spawn(process.execPath, [BIN].concat(args || []), { env });
    let out = ''; let err = '';
    p.stdout.on('data', (d) => { out += d; });
    p.stderr.on('data', (d) => { err += d; });
    p.on('close', (code) => resolve({ code, out, err, msgs: args && args.length ? null : out.split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l)) }));
    p.stdin.end(lines.map((l) => l + '\n').join(''));
  });
}

const rq = (id, method, params) => JSON.stringify({ jsonrpc: '2.0', id, method, params: params || {} });

test('endpoint: default, roads and unsafe addresses', () => {
  assert.strictEqual(endpoint({}), 'https://llpro.ai/mcp?via=npm');
  assert.strictEqual(endpoint({ LLPRO_VIA: 'docker' }), 'https://llpro.ai/mcp?via=npm', 'the npm road never changes its tag');
  assert.strictEqual(endpoint({ LLPRO_VIA: 'user-42' }), 'https://llpro.ai/mcp?via=npm');
  assert.strictEqual(endpoint({ LLPRO_MCP_URL: 'https://llpro.ai/mcp/trust?via=other&k=1#frag' }), 'https://llpro.ai/mcp/trust?k=1&via=npm');
  for (const bad of ['http://llpro.ai/mcp', 'ftp://llpro.ai/mcp', 'https://user:pw@llpro.ai/mcp', 'file:///etc/passwd', 'llpro.ai/mcp', 'http://10.0.0.1/mcp']) {
    assert.throws(() => endpoint({ LLPRO_MCP_URL: bad }), /https/, bad);
  }
  assert.deepStrictEqual(ROADS, ['npm', 'pypi', 'docker', 'hf', 'github']);
  assert.strictEqual(ROAD, 'npm');
});

test('parseSse', () => {
  const ids = parseSse(': keepalive\nevent: message\ndata: {"jsonrpc":"2.0","id":1,\ndata: "result":{}}\n\ndata: [{"jsonrpc":"2.0","id":2,"result":1}]\n').map((m) => m.id);
  assert.deepStrictEqual(ids, [1, 2]);
});

test('bridge behaviour against a fake server', async (t) => {
  const f = await fakeServer();
  t.after(() => f.server.close());

  await t.test('headers, via and agent id', async () => {
    let r = await run(f.url, [rq(1, 'echo/headers')]);
    assert.strictEqual(r.msgs[0].result.via, 'npm');
    assert.strictEqual(r.msgs[0].result.agent, null);
    assert.match(r.msgs[0].result.ua, /^llpro-mcp\//);
    r = await run(f.url, [rq(1, 'echo/headers')], { LLPRO_AGENT_ID: 'my agent<script>', LLPRO_VIA: 'docker' });
    assert.strictEqual(r.msgs[0].result.agent, 'myagentscript');
    assert.strictEqual(r.msgs[0].result.via, 'npm');
  });

  await t.test('version and session follow initialize', async () => {
    const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('LLPRO_')));
    env.LLPRO_MCP_URL = f.url;
    const p = spawn(process.execPath, [BIN], { env });
    let buf = '';
    const lines = [];
    const got = new Promise((resolve) => {
      p.stdout.on('data', (d) => {
        buf += d;
        let i;
        while ((i = buf.indexOf('\n')) >= 0) { lines.push(JSON.parse(buf.slice(0, i))); buf = buf.slice(i + 1); if (lines.length === 1) p.stdin.end(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n' + rq(2, 'echo/headers') + '\n'); }
      });
      p.on('close', resolve);
    });
    p.stdin.write(rq(1, 'initialize') + '\n');
    await got;
    assert.strictEqual(lines.length, 2, 'a notification gets no answer');
    assert.strictEqual(lines[1].result.version, '2025-06-18');
    assert.strictEqual(lines[1].result.session, 'sess-1');
  });

  await t.test('parse error, blank lines, unchanged body, unicode', async () => {
    f.hits.length = 0;
    const line = rq(5, 'echo', { t: 'café 東京 ✓' });
    const r = await run(f.url, ['{not json', '', '  ', line]);
    assert.deepStrictEqual(r.msgs[0], { jsonrpc: '2.0', id: null, error: { code: -32700, message: 'parse error' } });
    assert.strictEqual(r.msgs[1].result.t, 'café 東京 ✓');
    assert.strictEqual(f.hits.length, 1);
    assert.strictEqual(f.hits[0].body.toString('utf8'), line);
  });

  await t.test('faults: 500, empty, 429, redirect, too big, unreachable', async () => {
    f.hits.length = 0;
    let r = await run(f.url, [rq('a', 'boom')]);
    assert.strictEqual(r.msgs[0].id, 'a'); assert.strictEqual(r.msgs[0].error.code, -32603); assert.match(r.msgs[0].error.message, /HTTP 500/);
    r = await run(f.url, [rq(3, 'empty')]);
    assert.match(r.msgs[0].error.message, /no answer/);
    r = await run(f.url, [rq(4, 'limit')]);
    assert.deepStrictEqual(r.msgs[0], { jsonrpc: '2.0', id: 4, error: { code: -32029, message: 'rate limited' } });
    f.hits.length = 0;
    r = await run(f.url, [rq(9, 'redirect')]);
    assert.match(r.msgs[0].error.message, /redirects are not followed/);
    assert.deepStrictEqual(f.hits.map((h) => h.path), ['/mcp']);
    r = await run(f.url, [rq(10, 'big')]);
    assert.match(r.msgs[0].error.message, /larger than/);
    r = await run('http://127.0.0.1:9/mcp', [rq(11, 'echo'), JSON.stringify({ jsonrpc: '2.0', method: 'notifications/x' })]);
    assert.strictEqual(r.msgs.length, 1); assert.strictEqual(r.msgs[0].id, 11);
    assert.match(r.msgs[0].error.message, /could not get an answer from 127\.0\.0\.1/);
  });

  await t.test('slow call does not block; exit waits; batch; sse', async () => {
    const t0 = Date.now();
    let r = await run(f.url, [rq(1, 'slow'), rq(2, 'echo', { b: 2 })]);
    assert.deepStrictEqual(r.msgs.map((m) => m.id), [2, 1]);
    assert.ok(Date.now() - t0 > 1400);
    r = await run(f.url, [JSON.stringify([{ jsonrpc: '2.0', id: 1, method: 'echo' }, { jsonrpc: '2.0', method: 'notifications/y' }, { jsonrpc: '2.0', id: 2, method: 'echo' }])]);
    assert.deepStrictEqual(r.msgs.map((m) => m.id).sort(), [1, 2]);
    r = await run(f.url, [rq(7, 'sse/test')]);
    assert.strictEqual(r.msgs[0].id, 7);
  });

  await t.test('no extra calls', async () => {
    f.hits.length = 0;
    await run(f.url, [0, 1, 2, 3, 4].map((i) => rq(i, 'echo')).concat([JSON.stringify({ jsonrpc: '2.0', method: 'notifications/z' })]));
    assert.strictEqual(f.hits.length, 6);
    assert.deepStrictEqual([...new Set(f.hits.map((h) => h.path + '?' + h.via))], ['/mcp?npm']);
  });

  await t.test('cli tools and call', async () => {
    let r = await run(f.url, [], {}, ['tools']);
    assert.strictEqual(r.code, 0);
    assert.deepStrictEqual(r.out.trim().split(/\r?\n/), ['fake_one  First tool. More lines here.', 'fake_two  Second page tool.']);
    r = await run(f.url, [], {}, ['call', 'x', '{"q": "é"}']);
    assert.strictEqual(r.code, 0);
    assert.deepStrictEqual(JSON.parse(JSON.parse(r.out).content[0].text), { q: 'é' });
    r = await run(f.url, [], {}, ['call', 'fails']);
    assert.strictEqual(r.code, 1);
    r = await run(f.url, [], {}, ['call', 'x', '[1]']);
    assert.strictEqual(r.code, 1); assert.match(r.err, /JSON object/);
  });
});

test('cli refuses a non-https address and prints the version', async () => {
  const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('LLPRO_')));
  env.LLPRO_MCP_URL = 'http://example.com/mcp';
  const once = (args) => new Promise((resolve) => {
    const p = spawn(process.execPath, [BIN].concat(args), { env });
    let out = ''; let err = '';
    p.stdout.on('data', (d) => { out += d; }); p.stderr.on('data', (d) => { err += d; });
    p.on('close', (code) => resolve({ code, out, err }));
    p.stdin.end();
  });
  let r = await once(['tools']);
  assert.strictEqual(r.code, 2); assert.match(r.err, /https/);
  r = await once(['nonsense']);
  assert.strictEqual(r.code, 2);
  r = await once(['--version']);
  assert.match(r.out, /^llpro-mcp \d+\.\d+\.\d+/);
});

// Cases from the independent review of 14 Nov 2026: every asked id gets exactly one answer, both bridges alike.
function runRaw(url, bufs) {
  assert.ok(url.startsWith('http://127.0.0.1:'), 'tests must never call a real server');
  const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('LLPRO_')));
  env.LLPRO_MCP_URL = url;
  return new Promise((resolve) => {
    const p = spawn(process.execPath, [BIN], { env });
    let out = '';
    p.stdout.on('data', (d) => { out += d; });
    p.on('close', () => resolve(out));
    p.stdin.end(Buffer.concat(bufs));
  });
}
const raw = (id, body, ctype) => rq(id, 'raw', ctype ? { body, ctype } : { body });

test('review cases', async (t) => {
  const f = await fakeServer();
  t.after(() => f.server.close());

  await t.test('lone surrogate, U+2028 in a stream, odd version', async () => {
    let r = await run(f.url, [raw(1, '{"jsonrpc":"2.0","id":1,"result":{"t":"x\\ud83d"}}')]);
    assert.strictEqual(r.msgs.length, 1); assert.strictEqual(r.msgs[0].result.t, 'x\ud83d');
    r = await run(f.url, [raw(2, 'data: {"jsonrpc":"2.0","id":2,"result":{"t":"a b c\u0085d"}}\n\n', 'text/event-stream')]);
    assert.strictEqual(r.msgs[0].result.t, 'a b c\u0085d');
    r = await run(f.url, [rq(1, 'initialize', { version: '２０２５-０６-１８' }), rq(2, 'echo/headers')]);
    assert.strictEqual(r.msgs[1].result.version, null);
  });

  await t.test('float id and repeated answers give one answer', async () => {
    let r = await run(f.url, [raw(1, '{"jsonrpc":"2.0","id":1.0,"result":"ok"}')]);
    assert.deepStrictEqual(r.msgs.map((m) => m.result), ['ok']);
    r = await run(f.url, [raw(3, '[{"jsonrpc":"2.0","id":3,"result":"a"},{"jsonrpc":"2.0","id":3,"result":"b"},{"jsonrpc":"2.0","id":99,"result":"stray"}]')]);
    assert.deepStrictEqual(r.msgs.map((m) => m.result), ['a']);
  });

  await t.test('big numbers kept exactly', async () => {
    const out = await runRaw(f.url, [Buffer.from(raw(1, '{"jsonrpc":"2.0","id":1,"result":{"n":12345678901234567890,"f":1e400}}') + '\n')]);
    assert.match(out, /12345678901234567890/); assert.match(out, /1e400/);
  });

  await t.test('input rules match the PyPI bridge', async () => {
    f.hits.length = 0;
    const out = (await runRaw(f.url, [
      Buffer.from('{"jsonrpc":"2.0","id":1,"method":"echo","params":{"x":NaN}}\n'),
      Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), Buffer.from('{"jsonrpc":"2.0","id":2,"method":"echo"}\n')]),
      Buffer.concat([Buffer.from('{"jsonrpc":"2.0","id":3,"method":"echo","params":{"t":"'), Buffer.from([0xff]), Buffer.from('"}}\n')]),
      Buffer.from('{"jsonrpc":"2.0",\r"id":4,"method":"echo","params":{"cr":1}}\n'),
      Buffer.from('['.repeat(200000) + '\n'),
    ])).split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l));
    assert.strictEqual(out.filter((m) => m.error && m.error.code === -32700).length, 4, JSON.stringify(out));
    assert.deepStrictEqual(out.filter((m) => m.id === 4).map((m) => m.result), [{ cr: 1 }]);
    assert.strictEqual(f.hits.length, 1, 'only the valid message is sent');
  });

  await t.test('oversized line refused and next line works', async () => {
    f.hits.length = 0;
    const big = Buffer.concat([Buffer.from('{"jsonrpc":"2.0","id":1,"method":"echo","params":{"t":"'), Buffer.alloc(4 * 1024 * 1024 + 10, 'a'), Buffer.from('"}}\n')]);
    const out = (await runRaw(f.url, [big, Buffer.from(rq(2, 'echo', { ok: 1 }) + '\n')])).split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l));
    assert.strictEqual(out[0].error.code, -32600);
    assert.deepStrictEqual(out[1].result, { ok: 1 });
    assert.strictEqual(f.hits.length, 1);
  });

  await t.test('initialize first and alone; new initialize drops the old session', async () => {
    let r = await run(f.url, [rq(1, 'initialize'), JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }), rq(2, 'echo/headers')]);
    assert.deepStrictEqual(r.msgs.map((m) => m.id), [1, 2]);
    assert.strictEqual(r.msgs[1].result.version, '2025-06-18'); assert.strictEqual(r.msgs[1].result.session, 'sess-1');
    f.hits.length = 0;
    await run(f.url, [rq(1, 'initialize'), rq(2, 'initialize')]);
    assert.strictEqual(f.hits[1].headers['mcp-session-id'], undefined);
  });

  await t.test('after a 404 the old session is not sent again', async () => {
    const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('LLPRO_')));
    env.LLPRO_MCP_URL = f.url;
    const p = spawn(process.execPath, [BIN], { env });
    const lines = []; let buf = '';
    const steps = [rq(2, 'gone'), rq(3, 'echo/headers')];
    const done = new Promise((resolve) => {
      p.stdout.on('data', (d) => {
        buf += d; let i;
        while ((i = buf.indexOf('\n')) >= 0) {
          lines.push(JSON.parse(buf.slice(0, i))); buf = buf.slice(i + 1);
          const next = steps.shift();
          if (next) p.stdin.write(next + '\n'); if (!steps.length && lines.length === 3) p.stdin.end();
        }
      });
      p.on('close', resolve);
    });
    p.stdin.write(rq(1, 'initialize') + '\n');
    await done;
    assert.strictEqual(lines[2].result.session, null);
  });

  await t.test('stream kept open with keepalives does not hold slots', async () => {
    const lines = [];
    for (let i = 0; i < 10; i++) lines.push(rq(i, 'rawsse', { events: [`data: {"jsonrpc":"2.0","id":${i},"result":"r"}\n\n`], hold: 20 }));
    lines.push(rq(99, 'echo', { z: 1 }));
    const t0 = Date.now();
    const r = await run(f.url, lines);
    assert.ok(Date.now() - t0 < 10000, 'the bridge closed each stream once it had the answer');
    assert.deepStrictEqual(r.msgs.map((m) => m.id).sort((a, b) => a - b), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 99]);
  });

  await t.test('object and huge ids still get one answer', async () => {
    const r = await run(f.url, ['{"jsonrpc":"2.0","id":{"a":1},"method":"echo"}', '{"jsonrpc":"2.0","id":' + '9'.repeat(400) + ',"method":"echo"}']);
    assert.strictEqual(r.msgs.length, 2, JSON.stringify(r.msgs));
  });

  await t.test('stream events are passed on as they arrive', async () => {
    const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('LLPRO_')));
    env.LLPRO_MCP_URL = f.url;
    const events = ['data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"progress":1}}\n\n',
      'data: {"jsonrpc":"2.0","id":"q1","method":"elicitation/create","params":{}}\r\n\r\n',
      'data: {"jsonrpc":"2.0","id":5,"result":"done"}\n\n'];
    const p = spawn(process.execPath, [BIN], { env });
    const t0 = Date.now(); let firstAt = null; let out = '';
    const done = new Promise((resolve) => {
      p.stdout.on('data', (d) => { if (firstAt === null) firstAt = Date.now() - t0; out += d; });
      p.on('close', resolve);
    });
    p.stdin.end(rq(5, 'rawsse', { events, delay: 1.0 }) + '\n');
    await done;
    const msgs = out.split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l));
    assert.ok(firstAt < 900, 'first event not held back: ' + firstAt);
    assert.deepStrictEqual(msgs.map((m) => m.method || m.result), ['notifications/progress', 'elicitation/create', 'done']);
  });
});
