"""llpro-mcp: a thin stdio bridge to the LLPRO campus MCP server (https://llpro.ai/mcp). Operated by LLPRO LLC.

Reads JSON-RPC messages, one per line, on stdin; sends each one unchanged to the remote server; writes every
JSON-RPC message the server answers, one per line, on stdout. The tool list is read live from the server: this
package holds no copy of it, no secrets, no telemetry and no dependencies (Python standard library only).

  llpro-mcp                     run the stdio bridge (this is what an MCP client starts)
  llpro-mcp tools               list the live tools (name and first line of each description)
  llpro-mcp call NAME [JSON]    call one tool with a JSON object of arguments and print the result
  llpro-mcp --version | --help

Environment (all optional):
  LLPRO_MCP_URL    server address (default https://llpro.ai/mcp); https only, or http on this machine for testing
  LLPRO_AGENT_ID   a stable name for your agent, sent as X-Agent-Id for a return-visit record; nothing is sent unless you set it
  HTTPS_PROXY      honoured, like other Python tools

Every request carries one fixed ?via= tag that says which road the package came from (pypi here; the Docker image
sets docker). It is the same for every install and identifies no one.
"""
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

__version__ = "0.1.0"
DEFAULT_URL = "https://llpro.ai/mcp"
ROAD = "pypi"                                   # this package's one fixed ?via tag
ROADS = ("npm", "pypi", "docker", "hf", "github")   # every road tag LLPRO uses; this package sends pypi, or docker inside the image
IMAGE_ROAD = "docker"                           # the only override: the Docker image sets LLPRO_VIA=docker
TIMEOUT = 120                                   # seconds without any data from the server (idle), same rule as the npm bridge
MAX_LINE = 4 * 1024 * 1024
MAX_BODY = 16 * 1024 * 1024
MAX_INFLIGHT = 8
IN_ORDER = ("initialize", "notifications/initialized")   # sent one at a time, before any later line
_VERSION_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_SESSION_RE = re.compile(r"[\x21-\x7e]{1,256}")
_LINES = re.compile(r"\r\n|\r|\n")


def endpoint(env=None):
    """The server address with this road's ?via tag. Raises ValueError on an address that is not https (or local http)."""
    env = os.environ if env is None else env
    url = (env.get("LLPRO_MCP_URL") or DEFAULT_URL).strip()
    via = IMAGE_ROAD if (env.get("LLPRO_VIA") or "").strip().lower() == IMAGE_ROAD else ROAD
    p = urllib.parse.urlsplit(url)
    local = p.scheme == "http" and p.hostname in ("127.0.0.1", "localhost", "::1")
    if not p.hostname or (p.scheme != "https" and not local) or p.username or p.password:
        raise ValueError("LLPRO_MCP_URL must be an https address (or http on this machine), without a user name or password")
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if k != "via"]
    q.append(("via", via))
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path or "/", urllib.parse.urlencode(q), ""))


def _no_constant(c):
    raise ValueError("%s is not JSON" % c)


def loads(text):
    """Strict JSON (no NaN or Infinity), the same rule as JSON.parse in the npm bridge."""
    return json.loads(text, parse_constant=_no_constant)


def _as_list(x):
    return x if isinstance(x, list) else [x]


def _error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _short(e):
    return (str(e) or e.__class__.__name__).replace("\n", " ")[:200]


def idkey(i):
    """One key per JSON-RPC id, so 1 and 1.0 are the same id (as in JavaScript)."""
    if isinstance(i, bool):
        return ("b", i)
    if isinstance(i, int):
        return ("n", i)
    if isinstance(i, float):
        return ("n", int(i)) if i.is_integer() else ("n", i)
    return ("j", json.dumps(i, sort_keys=True))


def _items(text):
    """[(message, raw)] for one JSON text; raw is the server's own text when it is a single message (kept exactly)."""
    obj = loads(text)
    if isinstance(obj, list):
        return [(m, None) for m in obj]
    return [(obj, " ".join(_LINES.split(text.strip())))]


def parse_sse(body):
    """JSON-RPC messages carried in a whole text/event-stream answer (for tests and the CLI)."""
    out = []
    ev = _SSE()
    for line in _LINES.split(body.decode("utf-8")) + [""]:
        out += [m for m, _ in ev.line(line)]
    return out


class _SSE:
    """Collects data: lines; a blank line ends an event. Lines are split on CR, LF or CRLF only (never on U+2028)."""
    def __init__(self):
        self.data = []

    def line(self, line):
        if line == "":
            if not self.data:
                return []
            text, self.data = "\n".join(self.data), []
            return _items(text)
        if line.startswith("data:"):
            self.data.append(line[6:] if line[5:6] == " " else line[5:])
        return []


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):   # never follow a redirect: the answer must come from the address asked
        return None


class Bridge:
    def __init__(self, url, out=None, agent_id=""):
        self.url = url
        self.out = out
        self.lock = threading.Lock()
        self.version = ""
        self.session = ""
        self.agent = re.sub(r"[^A-Za-z0-9_.:\-]", "", agent_id or "")[:64]
        self.opener = urllib.request.build_opener(_NoRedirect)
        self.slots = threading.BoundedSemaphore(MAX_INFLIGHT)

    def headers(self):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             "User-Agent": "llpro-mcp/" + __version__}
        if self.version:
            h["MCP-Protocol-Version"] = self.version
        if self.session:
            h["Mcp-Session-Id"] = self.session
        if self.agent:
            h["X-Agent-Id"] = self.agent
        return h

    def post(self, data, emit, done=lambda: False):
        """Send one message (bytes); call emit([(message, raw)]) as answers arrive (event by event for a stream).
        Raises when the server gives no usable answer."""
        rq = urllib.request.Request(self.url, data=data, headers=self.headers(), method="POST")
        try:
            r = self.opener.open(rq, timeout=TIMEOUT)
            status = r.status
        except urllib.error.HTTPError as e:
            r, status = e, e.code
        with r:
            sid = r.headers.get("Mcp-Session-Id") or ""
            if _SESSION_RE.fullmatch(sid):
                self.session = sid
            elif status == 404 and self.session:   # the server forgot the session: the client's next initialize starts clean
                self.session = ""
            ctype = (r.headers.get("Content-Type") or "").lower()
            if status < 300 and "text/event-stream" in ctype:
                return self._stream(r, emit, done)
            body = r.read(MAX_BODY + 1) or b""
        if len(body) > MAX_BODY:
            raise ValueError("answer larger than %d bytes" % MAX_BODY)
        redir = " (redirects are not followed)" if 300 <= status < 400 else ""
        if not body.strip():
            if status < 300:
                return
            raise ValueError("HTTP %d%s" % (status, redir))
        try:
            got = _items(body.decode("utf-8"))
        except Exception:
            raise ValueError("HTTP %d, the answer is not JSON-RPC%s" % (status, redir))
        got = [(m, raw) for m, raw in got if isinstance(m, dict) and m.get("jsonrpc") == "2.0"]
        if not got and status >= 300:
            raise ValueError("HTTP %d%s" % (status, redir))
        emit(got)

    def _stream(self, r, emit, done):
        """Pass events on as they arrive; stop reading (the caller closes the stream) once every asked id has its answer,
        so a server that keeps the stream open with keepalives never holds a slot."""
        ev, total, part = _SSE(), 0, b""
        while True:
            chunk = r.readline(MAX_LINE + 1)
            total += len(chunk)
            if total > MAX_BODY:
                raise ValueError("answer larger than %d bytes" % MAX_BODY)
            if chunk and not chunk.endswith(b"\n"):
                part += chunk
                if len(part) > MAX_LINE:
                    raise ValueError("stream line larger than %d bytes" % MAX_LINE)
                continue
            text = (part + chunk).decode("utf-8")
            part = b""
            lines = _LINES.split(text)
            if chunk:
                lines = lines[:-1] if lines and lines[-1] == "" else lines
            else:
                lines = (lines if text else []) + [""]   # end of stream ends the last event
            for line in lines:
                got = [(m, raw) for m, raw in ev.line(line) if isinstance(m, dict) and m.get("jsonrpc") == "2.0"]
                if got:
                    emit(got)
                    if done():
                        return
            if not chunk:
                return

    def write(self, pairs):
        if not pairs:
            return
        text = "".join((raw if raw is not None else json.dumps(m, ensure_ascii=True, separators=(",", ":"))) + "\n" for m, raw in pairs)
        with self.lock:
            self.out.write(text.encode("utf-8", "backslashreplace"))
            self.out.flush()

    def handle(self, line):
        try:
            msg = loads(line.decode("utf-8"))
        except Exception:   # not UTF-8, not strict JSON, nested too deep
            self.write([(_error(None, -32700, "parse error"), None)])
            return
        items = [m for m in _as_list(msg) if isinstance(m, dict)]
        asked = {}
        for m in items:
            if "method" in m and m.get("id") is not None:
                asked.setdefault(idkey(m["id"]), m)
        if any(m.get("method") == "initialize" for m in items):   # a new session: never carry the old one into it
            self.session, self.version = "", ""
        answered = set()

        def emit(pairs):
            out = []
            for m, raw in pairs:
                if "method" in m:                 # a request or notification from the server: pass it on
                    out.append((m, raw))
                    continue
                k = idkey(m.get("id")) if isinstance(m.get("id"), (str, int, float)) else None
                if k in asked and k not in answered:   # exactly one answer per asked id; strays and repeats are dropped
                    answered.add(k)
                    out.append((m, raw))
                    res = m.get("result")
                    if asked[k].get("method") == "initialize" and isinstance(res, dict):
                        v = res.get("protocolVersion")
                        if isinstance(v, str) and _VERSION_RE.fullmatch(v):
                            self.version = v
            self.write(out)

        why = "llpro-mcp: the server sent no answer for this request"
        try:
            self.post(line, emit, lambda: bool(asked) and len(answered) == len(asked))
        except Exception as e:   # network, HTTP or protocol fault: every request still gets an answer
            why = "llpro-mcp could not get an answer from %s: %s" % (urllib.parse.urlsplit(self.url).hostname, _short(e))
        self.write([(_error(m["id"], -32603, why), None) for k, m in asked.items() if k not in answered])

    def _one(self, line):
        try:
            self.handle(line)
        except Exception as e:   # last guard: never leave the client waiting without a word
            sys.stderr.write("llpro-mcp: %s\n" % _short(e))
        finally:
            self.slots.release()

    @staticmethod
    def _in_order(line):
        try:
            m = loads(line.decode("utf-8"))
        except Exception:
            return False
        return isinstance(m, dict) and m.get("method") in IN_ORDER

    def run(self, inp):
        threads = []
        while True:
            line = inp.readline(MAX_LINE + 1)
            if not line:
                break
            if len(line) > MAX_LINE and not line.endswith(b"\n"):
                while line and not line.endswith(b"\n"):
                    line = inp.readline(MAX_LINE + 1)
                self.write([(_error(None, -32600, "message larger than %d bytes" % MAX_LINE), None)])
                continue
            line = line.strip(b" \t\r\n")
            if not line:
                continue
            self.slots.acquire()
            if self._in_order(line):   # initialize and initialized go out alone and in order: earlier lines finish first, later lines wait
                for t in threads:
                    t.join()
                threads = []
                self._one(line)
                continue
            t = threading.Thread(target=self._one, args=(line,), daemon=True)
            t.start()
            threads = [x for x in threads if x.is_alive()] + [t]
        for t in threads:
            t.join()


def request(url, method, params, agent=""):
    """One JSON-RPC call outside a client session (used by the tools and call commands)."""
    got = []
    Bridge(url, None, agent).post(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8"), got.extend)
    for m, _ in got:
        if "method" not in m and m.get("id") == 1:
            if "error" in m:
                raise RuntimeError("server error %s: %s" % ((m["error"] or {}).get("code"), (m["error"] or {}).get("message")))
            return m.get("result")
    raise RuntimeError("the server sent no answer")


def _print(text):
    sys.stdout.buffer.write((text + "\n").encode("utf-8", "backslashreplace"))
    sys.stdout.buffer.flush()


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if a and a[0] in ("-h", "--help", "help"):
        _print(__doc__.strip())
        return 0
    if a and a[0] in ("-V", "--version"):
        _print("llpro-mcp " + __version__)
        return 0
    try:
        url = endpoint()
    except ValueError as e:
        sys.stderr.write("llpro-mcp: %s\n" % e)
        return 2
    agent = os.environ.get("LLPRO_AGENT_ID", "")
    try:
        if not a or a == ["serve"]:
            Bridge(url, sys.stdout.buffer, agent).run(sys.stdin.buffer)
            return 0
        if a == ["tools"]:
            cursor, pages = None, 0
            while pages < 20:
                res = request(url, "tools/list", {"cursor": cursor} if cursor else {}, agent) or {}
                for t in res.get("tools") or []:
                    d = " ".join(str(t.get("description") or "").split())
                    _print("%s  %s" % (t.get("name"), d[:117] + "..." if len(d) > 120 else d))
                cursor, pages = res.get("nextCursor"), pages + 1
                if not cursor:
                    break
            return 0
        if a[:1] == ["call"] and len(a) in (2, 3):
            args = loads(a[2]) if len(a) == 3 else {}
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
            res = request(url, "tools/call", {"name": a[1], "arguments": args}, agent)
            _print(json.dumps(res, indent=2, ensure_ascii=False))
            return 1 if isinstance(res, dict) and res.get("isError") else 0
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:   # the reader went away (for example `llpro-mcp tools | head`): stop quietly
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        return 0
    except Exception as e:
        sys.stderr.write("llpro-mcp: %s\n" % _short(e))
        return 1
    sys.stderr.write(__doc__.strip() + "\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
