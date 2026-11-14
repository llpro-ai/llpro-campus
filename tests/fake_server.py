"""A local fake MCP server for the bridge tests. Listens on 127.0.0.1 only; never forwards anywhere."""
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeMCP:
    def __init__(self):
        self.hits = []   # (path, query dict, headers dict, body)
        self.lock = threading.Lock()
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                fake.record(self, b"")
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"no")

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                fake.answer(self, body, fake.record(self, body))

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = "http://127.0.0.1:%d/mcp" % self.port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def record(self, h, body):
        p = urllib.parse.urlsplit(h.path)
        hit = (p.path, dict(urllib.parse.parse_qsl(p.query)), {k.lower(): v for k, v in h.headers.items()}, body)
        with self.lock:
            self.hits.append(hit)
        return hit

    @staticmethod
    def send(h, code, body=b"", ctype="application/json", extra=None):
        h.send_response(code)
        for k, v in (extra or {}).items():
            h.send_header(k, v)
        if body:
            h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        if body:
            h.wfile.write(body)

    def result(self, last, m):
        mid, method, params = m.get("id"), m.get("method"), m.get("params") or {}
        if method == "initialize":
            return {"protocolVersion": params.get("version", "2025-06-18"), "capabilities": {"tools": {}}, "serverInfo": {"name": "fake"}}
        if method == "echo/headers":
            hd = last[2]
            return {"via": last[1].get("via"), "query": last[1], "version": hd.get("mcp-protocol-version"), "session": hd.get("mcp-session-id"),
                    "agent": hd.get("x-agent-id"), "ua": hd.get("user-agent"), "path": last[0]}
        if method == "slow":
            time.sleep(1.5)
            return {"slow": True}
        if method == "tools/list":
            if params.get("cursor") == "p2":
                return {"tools": [{"name": "fake_two", "description": "Second page tool."}]}
            return {"tools": [{"name": "fake_one", "description": "First tool.\nMore lines here."}], "nextCursor": "p2"}
        if method == "tools/call":
            return {"content": [{"type": "text", "text": json.dumps(params.get("arguments"), ensure_ascii=False)}], "isError": params.get("name") == "fails"}
        if method == "echo":
            return params
        return None

    def answer(self, h, body, hit):
        try:
            msg = json.loads(body.decode("utf-8"))
        except ValueError:
            return self.send(h, 400, json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}).encode())
        items = msg if isinstance(msg, list) else [msg]
        first = items[0] if items and isinstance(items[0], dict) else {}
        method = first.get("method")
        if method == "boom":
            return self.send(h, 500, b"<html>oops</html>", "text/html")
        if method == "empty":
            return self.send(h, 200)
        if method == "limit":
            return self.send(h, 429, json.dumps({"jsonrpc": "2.0", "id": first.get("id"), "error": {"code": -32029, "message": "rate limited"}}).encode(), extra={"Retry-After": "60"})
        if method == "redirect":
            return self.send(h, 302, b"<html>moved</html>", "text/html", {"Location": "http://127.0.0.1:%d/elsewhere" % self.port})
        if method == "big":
            return self.send(h, 200, b"[" + b" " * (17 * 1024 * 1024) + b"]")
        if method == "gone":
            return self.send(h, 404)
        if method == "raw":   # answer with exactly this text (surrogates, 1.0 ids, big numbers, repeats)
            p = first.get("params") or {}
            return self.send(h, 200, p["body"].encode("utf-8", "surrogatepass"), p.get("ctype", "application/json"))
        if method == "rawsse":   # stream these events one by one, with a pause between them
            p = first.get("params") or {}
            h.send_response(200)
            h.send_header("Content-Type", "text/event-stream")
            h.end_headers()
            try:
                for i, ev in enumerate(p["events"]):
                    if i:
                        time.sleep(p.get("delay", 0))
                    h.wfile.write(ev.encode("utf-8"))
                    h.wfile.flush()
                end = time.time() + p.get("hold", 0)   # keep the stream open with keepalive comments
                while time.time() < end:
                    time.sleep(0.3)
                    h.wfile.write(b": keepalive\n\n")
                    h.wfile.flush()
            except OSError:   # the bridge closed the stream: expected once it has its answer
                pass
            return None
        out = []
        for m in items:
            if not isinstance(m, dict) or "method" not in m or m.get("id") is None:
                continue
            out.append({"jsonrpc": "2.0", "id": m["id"], "result": self.result(hit, m)})
        extra = {"Mcp-Session-Id": "sess-1"} if method == "initialize" else None
        if not out:
            return self.send(h, 202, extra=extra)
        data = json.dumps(out if isinstance(msg, list) else out[0], ensure_ascii=False).encode("utf-8")
        if method == "sse/test":
            return self.send(h, 200, b"event: message\ndata: " + data + b"\n\n", "text/event-stream", extra)
        return self.send(h, 200, data, extra=extra)


if __name__ == "__main__":   # for the Node.js tests: print the URL, serve until stdin closes
    import sys
    f = FakeMCP()
    print(f.url, flush=True)
    sys.stdin.read()
    f.close()
