#!/usr/bin/env node
// llpro-mcp: a thin stdio bridge to the LLPRO campus MCP server (https://llpro.ai/mcp). Operated by LLPRO LLC.
// Reads JSON-RPC messages, one per line, on stdin; sends each one unchanged to the remote server; writes every
// JSON-RPC message the server answers, one per line, on stdout. The tool list is read live from the server: this
// package holds no copy of it, no secrets, no telemetry and no dependencies (Node.js 18+ built-ins only).
'use strict';

const VERSION = '0.1.0';
const DEFAULT_URL = 'https://llpro.ai/mcp';
const ROAD = 'npm'; // this package's one fixed ?via tag: the same for every install, identifies no one
const ROADS = ['npm', 'pypi', 'docker', 'hf', 'github']; // every road tag LLPRO uses; this package always sends npm
const TIMEOUT_MS = 120000; // without any data from the server (idle), same rule as the PyPI bridge
const MAX_LINE = 4 * 1024 * 1024;
const MAX_BODY = 16 * 1024 * 1024;
const MAX_INFLIGHT = 8;
const IN_ORDER = ['initialize', 'notifications/initialized']; // sent one at a time, before any later line
const VERSION_RE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/;
const SESSION_RE = /^[\x21-\x7e]{1,256}$/;
const LINES = /\r\n|\r|\n/;

const HELP = `llpro-mcp ${VERSION}: thin stdio bridge to the LLPRO campus MCP server (https://llpro.ai/mcp). Operated by LLPRO LLC.

  llpro-mcp                     run the stdio bridge (this is what an MCP client starts)
  llpro-mcp tools               list the live tools (name and first line of each description)
  llpro-mcp call NAME [JSON]    call one tool with a JSON object of arguments and print the result
  llpro-mcp --version | --help

Environment (all optional):
  LLPRO_MCP_URL    server address (default https://llpro.ai/mcp); https only, or http on this machine for testing
  LLPRO_AGENT_ID   a stable name for your agent, sent as X-Agent-Id for a return-visit record; nothing is sent unless you set it
  (Node.js fetch does not use HTTPS_PROXY; use the PyPI package where a proxy is required.)

Every request carries one fixed ?via= tag that says which road the package came from (npm here).
It is the same for every install and identifies no one. No telemetry, no secrets, no dependencies.`;

function endpoint(env = process.env) {
  const raw = (env.LLPRO_MCP_URL || DEFAULT_URL).trim();
  let u;
  try { u = new URL(raw); } catch (e) { u = null; }
  const local = u && u.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(u.hostname);
  if (!u || !u.hostname || (u.protocol !== 'https:' && !local) || u.username || u.password) {
    throw new Error('LLPRO_MCP_URL must be an https address (or http on this machine), without a user name or password');
  }
  u.searchParams.delete('via');
  u.searchParams.append('via', ROAD);
  u.hash = '';
  return u.toString();
}

const strictUtf8 = () => new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }); // a BOM stays and fails JSON.parse, as in Python
function asList(x) { return Array.isArray(x) ? x : [x]; }
function rpcError(id, code, message) { return { jsonrpc: '2.0', id, error: { code, message } }; }
function short(e) { return String((e && (e.cause && e.cause.code ? e.message + ' (' + e.cause.code + ')' : e.message)) || e).replace(/\n/g, ' ').slice(0, 200); }
function trimWs(s) { return s.replace(/^[ \t\r\n]+|[ \t\r\n]+$/g, ''); } // JSON whitespace only (String.trim would also drop a BOM)
function idKey(i) { return typeof i === 'number' ? 'n' + i : typeof i === 'string' ? 's' + i : typeof i === 'boolean' ? 'b' + i : 'j' + JSON.stringify(i); }
const plainId = (i) => typeof i === 'number' || typeof i === 'string'; // only these ids can match an answer (as in Python)

// [{m, raw}] for one JSON text; raw is the server's own text when it is a single message (kept exactly, numbers included).
function items(text) {
  const obj = JSON.parse(text);
  if (Array.isArray(obj)) return obj.map((m) => ({ m, raw: null }));
  return [{ m: obj, raw: trimWs(text).split(LINES).join(' ') }];
}

class Sse { // data: lines; a blank line ends an event; lines split on CR, LF or CRLF only
  constructor() { this.data = []; }
  line(line) {
    if (line === '') {
      if (!this.data.length) return [];
      const text = this.data.join('\n'); this.data = [];
      return items(text);
    }
    if (line.startsWith('data:')) this.data.push(line[5] === ' ' ? line.slice(6) : line.slice(5));
    return [];
  }
}

function parseSse(text) {
  const ev = new Sse(); const out = [];
  for (const line of text.split(LINES).concat([''])) out.push(...ev.line(line).map((x) => x.m));
  return out;
}

const isMsg = (x) => x.m && typeof x.m === 'object' && !Array.isArray(x.m) && x.m.jsonrpc === '2.0';

class Bridge {
  constructor(url, out, agentId) {
    this.url = url; this.out = out; this.version = ''; this.session = '';
    this.agent = String(agentId || '').replace(/[^A-Za-z0-9_.:\-]/g, '').slice(0, 64);
  }

  headers() {
    const h = { 'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream', 'User-Agent': 'llpro-mcp/' + VERSION };
    if (this.version) h['MCP-Protocol-Version'] = this.version;
    if (this.session) h['Mcp-Session-Id'] = this.session;
    if (this.agent) h['X-Agent-Id'] = this.agent;
    return h;
  }

  // Send one message; call emit([{m, raw}]) as answers arrive (event by event for a stream). Throws when there is no usable answer.
  async post(body, emit, done = () => false) {
    const ac = new AbortController();
    let timer = null;
    const idle = () => { clearTimeout(timer); timer = setTimeout(() => ac.abort(new Error(`no data from the server for ${TIMEOUT_MS / 1000} s`)), TIMEOUT_MS); };
    idle();
    try {
      const res = await fetch(this.url, { method: 'POST', headers: this.headers(), body, redirect: 'manual', signal: ac.signal });
      const status = res.status;
      const sid = res.headers.get('mcp-session-id') || '';
      if (SESSION_RE.test(sid)) this.session = sid;
      else if (status === 404 && this.session) this.session = ''; // the server forgot the session
      const ctype = (res.headers.get('content-type') || '').toLowerCase();
      const reader = res.body ? res.body.getReader() : null;
      const redir = status >= 300 && status < 400 ? ' (redirects are not followed)' : '';
      if (status < 300 && ctype.includes('text/event-stream') && reader) return await this.stream(reader, emit, idle, () => { if (!done()) return false; ac.abort(new Error('answered')); return true; });
      const parts = []; let n = 0;
      while (reader) {
        const { done, value } = await reader.read();
        if (done) break;
        idle();
        n += value.length;
        if (n > MAX_BODY) { try { await reader.cancel(); } catch (e) { /* ignore */ } throw new Error(`answer larger than ${MAX_BODY} bytes`); }
        parts.push(Buffer.from(value));
      }
      const buf = Buffer.concat(parts);
      if (!trimWs(buf.toString('latin1'))) {
        if (status >= 200 && status < 300) return;
        throw new Error(`HTTP ${status}${redir}`);
      }
      let got;
      try { got = items(strictUtf8().decode(buf)).filter(isMsg); } catch (e) { throw new Error(`HTTP ${status}, the answer is not JSON-RPC${redir}`); }
      if (!got.length && status >= 300) throw new Error(`HTTP ${status}${redir}`);
      emit(got);
    } finally {
      clearTimeout(timer);
    }
  }

  // Pass events on as they arrive; once every asked id has its answer, close the stream so a server that keeps it open
  // with keepalives never holds a slot.
  async stream(reader, emit, idle, finished) {
    const ev = new Sse(); const dec = strictUtf8(); let total = 0; let pend = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (!done) {
        idle();
        total += value.length;
        if (total > MAX_BODY) { try { await reader.cancel(); } catch (e) { /* ignore */ } throw new Error(`answer larger than ${MAX_BODY} bytes`); }
        pend += dec.decode(value, { stream: true });
      } else {
        pend += dec.decode();
      }
      // a trailing CR may be the first half of CRLF: hold it, with the unfinished line, until the next chunk
      let work = pend; let hold = '';
      if (!done && work.endsWith('\r')) { work = work.slice(0, -1); hold = '\r'; }
      const lines = work.split(LINES);
      pend = done ? '' : lines.pop() + hold;
      if (pend.length > MAX_LINE) throw new Error(`stream line larger than ${MAX_LINE} bytes`);
      if (done) lines.push('');
      for (const line of lines) {
        const got = ev.line(line).filter(isMsg);
        if (got.length) {
          emit(got);
          if (finished()) return; // finished() aborted the request: the connection is closed (reader.cancel alone keeps it open)
        }
      }
      if (done) return;
    }
  }

  write(pairs) {
    if (pairs.length) this.out.write(pairs.map((x) => (x.raw !== null && x.raw !== undefined ? x.raw : JSON.stringify(x.m)) + '\n').join(''));
  }

  async handle(line) {
    let msg;
    try { msg = JSON.parse(line); } catch (e) { this.write([{ m: rpcError(null, -32700, 'parse error'), raw: null }]); return; }
    const list = asList(msg).filter((m) => m && typeof m === 'object' && !Array.isArray(m));
    const asked = new Map();
    for (const m of list) {
      if ('method' in m && m.id !== undefined && m.id !== null && idKey(m.id) && !asked.has(idKey(m.id))) asked.set(idKey(m.id), m);
    }
    if (list.some((m) => m.method === 'initialize')) { this.session = ''; this.version = ''; } // a new session: never carry the old one into it
    const answered = new Set();
    const emit = (got) => {
      const out = [];
      for (const x of got) {
        if ('method' in x.m) { out.push(x); continue; } // a request or notification from the server: pass it on
        const k = plainId(x.m.id) ? idKey(x.m.id) : null;
        if (k && asked.has(k) && !answered.has(k)) { // exactly one answer per asked id; strays and repeats are dropped
          answered.add(k); out.push(x);
          const res = x.m.result;
          if (asked.get(k).method === 'initialize' && res && typeof res === 'object') {
            const v = res.protocolVersion;
            if (typeof v === 'string' && VERSION_RE.test(v)) this.version = v;
          }
        }
      }
      this.write(out);
    };
    let why = 'llpro-mcp: the server sent no answer for this request';
    try {
      await this.post(line, emit, () => asked.size > 0 && answered.size === asked.size);
    } catch (e) {
      why = `llpro-mcp could not get an answer from ${new URL(this.url).hostname}: ${short(e)}`;
    }
    this.write([...asked].filter(([k]) => !answered.has(k)).map(([, m]) => ({ m: rpcError(m.id, -32603, why), raw: null })));
  }

  static inOrder(line) {
    try { const m = JSON.parse(line); return !!m && typeof m === 'object' && IN_ORDER.includes(m.method); } catch (e) { return false; }
  }

  // stdin is split on LF only (a CR inside a message is JSON whitespace) and decoded as strict UTF-8, as in Python.
  run(input) {
    return new Promise((resolve) => {
      const pending = new Set(); const queue = []; let closed = false; let buf = Buffer.alloc(0); let skipping = false; let solo = false;
      const finish = () => { if (closed && !pending.size && !queue.length) resolve(); };
      const pump = () => {
        while (!solo && queue.length && pending.size < MAX_INFLIGHT) {
          const line = queue[0];
          const alone = Bridge.inOrder(line);
          if (alone && pending.size) break; // let earlier lines finish, then send this one alone
          queue.shift();
          if (alone) solo = true;
          const p = this.handle(line).catch((e) => process.stderr.write('llpro-mcp: ' + short(e) + '\n'))
            .finally(() => { pending.delete(p); if (alone) solo = false; pump(); finish(); });
          pending.add(p);
        }
        if (queue.length >= MAX_INFLIGHT) input.pause(); else input.resume();
      };
      const take = (raw) => {
        let line;
        try { line = trimWs(strictUtf8().decode(raw)); } catch (e) { this.write([{ m: rpcError(null, -32700, 'parse error'), raw: null }]); return; }
        if (line) { queue.push(line); pump(); }
      };
      input.on('data', (chunk) => {
        buf = buf.length ? Buffer.concat([buf, chunk]) : chunk;
        let i;
        while ((i = buf.indexOf(10)) >= 0) {
          const raw = buf.subarray(0, i); buf = buf.subarray(i + 1);
          if (skipping) { skipping = false; continue; }
          if (raw.length > MAX_LINE) { this.write([{ m: rpcError(null, -32600, `message larger than ${MAX_LINE} bytes`), raw: null }]); continue; }
          take(raw);
        }
        if (buf.length > MAX_LINE) {
          if (!skipping) this.write([{ m: rpcError(null, -32600, `message larger than ${MAX_LINE} bytes`), raw: null }]);
          skipping = true; buf = Buffer.alloc(0);
        }
      });
      input.on('end', () => { if (buf.length && !skipping) take(buf); buf = Buffer.alloc(0); closed = true; finish(); });
      input.on('error', () => { closed = true; finish(); });
      input.resume();
    });
  }
}

async function request(url, method, params, agent) {
  const got = [];
  await new Bridge(url, null, agent).post(JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }), (g) => got.push(...g));
  for (const { m } of got) {
    if (!('method' in m) && m.id === 1) {
      if (m.error) throw new Error(`server error ${m.error.code}: ${m.error.message}`);
      return m.result;
    }
  }
  throw new Error('the server sent no answer');
}

async function main(argv) {
  const a = argv.slice();
  if (['-h', '--help', 'help'].includes(a[0])) { console.log(HELP); return 0; }
  if (['-V', '--version'].includes(a[0])) { console.log('llpro-mcp ' + VERSION); return 0; }
  let url;
  try { url = endpoint(); } catch (e) { process.stderr.write('llpro-mcp: ' + e.message + '\n'); return 2; }
  const agent = process.env.LLPRO_AGENT_ID || '';
  try {
    if (!a.length || (a.length === 1 && a[0] === 'serve')) { await new Bridge(url, process.stdout, agent).run(process.stdin); return 0; }
    if (a.length === 1 && a[0] === 'tools') {
      let cursor = null;
      for (let page = 0; page < 20; page++) {
        const res = (await request(url, 'tools/list', cursor ? { cursor } : {}, agent)) || {};
        for (const t of res.tools || []) {
          const d = String(t.description || '').split(/\s+/).join(' ').trim();
          console.log(`${t.name}  ${d.length > 120 ? d.slice(0, 117) + '...' : d}`);
        }
        cursor = res.nextCursor;
        if (!cursor) break;
      }
      return 0;
    }
    if (a[0] === 'call' && (a.length === 2 || a.length === 3)) {
      const args = a.length === 3 ? JSON.parse(a[2]) : {};
      if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('arguments must be a JSON object');
      const res = await request(url, 'tools/call', { name: a[1], arguments: args }, agent);
      console.log(JSON.stringify(res, null, 2));
      return res && res.isError ? 1 : 0;
    }
  } catch (e) {
    process.stderr.write('llpro-mcp: ' + short(e) + '\n');
    return 1;
  }
  process.stderr.write(HELP + '\n');
  return 2;
}

module.exports = { endpoint, parseSse, Bridge, VERSION, ROAD, ROADS };

if (require.main === module) {
  process.stdout.on('error', (e) => { if (e && e.code === 'EPIPE') process.exit(0); throw e; }); // reader went away: stop quietly
  main(process.argv.slice(2)).then((code) => { process.exitCode = code; });
}
