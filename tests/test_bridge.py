"""Tests for the Python bridge. Every call goes to a local fake server; nothing here calls llpro.ai."""
import json
import os
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "python", "src")
sys.path.insert(0, HERE)
sys.path.insert(0, SRC)
from fake_server import FakeMCP  # noqa: E402
from llpro_mcp import bridge  # noqa: E402


def run(url, lines, env_extra=None, args=(), timeout=30):
    assert url.startswith("http://127.0.0.1:"), "tests must never call a real server"
    env = {k: v for k, v in os.environ.items() if not k.startswith("LLPRO_") and not k.lower().endswith("_proxy")}
    env.update({"LLPRO_MCP_URL": url, "PYTHONPATH": SRC}, **(env_extra or {}))
    data = "".join(l + "\n" for l in lines).encode("utf-8")
    p = subprocess.run([sys.executable, "-m", "llpro_mcp"] + list(args), input=data, capture_output=True, env=env, timeout=timeout)
    lines_out = p.stdout.decode("utf-8").split("\n")   # never splitlines(): it also splits on U+2028 inside a JSON string
    return p.returncode, [json.loads(l) for l in lines_out if l.strip()] if not args else p.stdout.decode("utf-8"), p.stderr.decode("utf-8")


def rq(mid, method, params=None):
    return json.dumps({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})


class Endpoint(unittest.TestCase):
    def test_default_and_roads(self):
        self.assertEqual(bridge.endpoint({}), "https://llpro.ai/mcp?via=pypi")
        self.assertEqual(bridge.endpoint({"LLPRO_VIA": "docker"}), "https://llpro.ai/mcp?via=docker")
        self.assertEqual(bridge.endpoint({"LLPRO_VIA": "evil-123"}), "https://llpro.ai/mcp?via=pypi")
        self.assertEqual(bridge.endpoint({"LLPRO_VIA": "npm"}), "https://llpro.ai/mcp?via=pypi", "only the image may change the road")
        u = bridge.endpoint({"LLPRO_MCP_URL": "https://llpro.ai/mcp/trust?via=other&k=1#frag"})
        self.assertEqual(u, "https://llpro.ai/mcp/trust?k=1&via=pypi")

    def test_refuses_unsafe_addresses(self):
        for bad in ("http://llpro.ai/mcp", "ftp://llpro.ai/mcp", "https://user:pw@llpro.ai/mcp", "file:///etc/passwd", "llpro.ai/mcp", "http://10.0.0.1/mcp"):
            with self.assertRaises(ValueError, msg=bad):
                bridge.endpoint({"LLPRO_MCP_URL": bad})
        self.assertTrue(bridge.endpoint({"LLPRO_MCP_URL": "http://localhost:9/mcp"}).startswith("http://localhost:9/mcp?via="))

    def test_fixed_roads(self):
        self.assertEqual(bridge.ROADS, ("npm", "pypi", "docker", "hf", "github"))
        self.assertEqual(bridge.ROAD, "pypi")

    def test_parse_sse(self):
        body = b': keepalive\nevent: message\ndata: {"jsonrpc":"2.0","id":1,\ndata: "result":{}}\n\ndata: [{"jsonrpc":"2.0","id":2,"result":1}]\n'
        self.assertEqual([m["id"] for m in bridge.parse_sse(body)], [1, 2])


class Bridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = FakeMCP()

    @classmethod
    def tearDownClass(cls):
        cls.f.close()

    def setUp(self):
        self.f.hits.clear()

    def test_session_flow_and_headers(self):
        code, out, _ = run(self.f.url, [rq(1, "echo/headers"), ])
        self.assertEqual(code, 0)
        r = out[0]["result"]
        self.assertEqual(r["via"], "pypi")
        self.assertEqual(r["path"], "/mcp")
        self.assertIsNone(r["version"])
        self.assertIsNone(r["agent"])
        self.assertTrue(r["ua"].startswith("llpro-mcp/"))
        code, out, _ = run(self.f.url, [rq(1, "initialize", {"protocolVersion": "2025-06-18"})])
        self.assertEqual(out[0]["result"]["protocolVersion"], "2025-06-18")

    def test_version_and_session_follow_initialize(self):
        # initialize goes out alone and first; later lines wait for its answer
        lines = [rq(1, "initialize")]
        env = {k: v for k, v in os.environ.items() if not k.startswith("LLPRO_") and not k.lower().endswith("_proxy")}
        env.update({"LLPRO_MCP_URL": self.f.url, "PYTHONPATH": SRC})
        p = subprocess.Popen([sys.executable, "-m", "llpro_mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
        p.stdin.write((lines[0] + "\n").encode())
        p.stdin.flush()
        first = json.loads(p.stdout.readline())
        p.stdin.write((json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n" + rq(2, "echo/headers") + "\n").encode())
        p.stdin.close()
        rest = [json.loads(l) for l in p.stdout.read().decode().splitlines() if l.strip()]
        p.wait(20)
        p.stdout.close()
        self.assertEqual(first["id"], 1)
        self.assertEqual(len(rest), 1, "a notification gets no answer")
        self.assertEqual(rest[0]["result"]["version"], "2025-06-18")
        self.assertEqual(rest[0]["result"]["session"], "sess-1")

    def test_road_override_for_docker_only_from_fixed_set(self):
        _, out, _ = run(self.f.url, [rq(1, "echo/headers")], {"LLPRO_VIA": "docker"})
        self.assertEqual(out[0]["result"]["via"], "docker")
        _, out, _ = run(self.f.url, [rq(1, "echo/headers")], {"LLPRO_VIA": "user-42"})
        self.assertEqual(out[0]["result"]["via"], "pypi")

    def test_agent_id_only_when_set_and_cleaned(self):
        _, out, _ = run(self.f.url, [rq(1, "echo/headers")], {"LLPRO_AGENT_ID": "my agent<script>"})
        self.assertEqual(out[0]["result"]["agent"], "myagentscript")

    def test_parse_error_and_blank_lines(self):
        _, out, _ = run(self.f.url, ["{not json", "", "   ", rq(5, "echo", {"a": 1})])
        self.assertEqual(out[0], {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
        self.assertEqual(out[1]["result"], {"a": 1})
        self.assertEqual(len(self.f.hits), 1, "a line that is not JSON is never sent")

    def test_sse_answer(self):
        _, out, _ = run(self.f.url, [rq(7, "sse/test")])
        self.assertEqual(out[0]["id"], 7)

    def test_server_fault_gives_error_with_id(self):
        _, out, _ = run(self.f.url, [rq("a", "boom")])
        self.assertEqual(out[0]["id"], "a")
        self.assertEqual(out[0]["error"]["code"], -32603)
        self.assertIn("HTTP 500", out[0]["error"]["message"])

    def test_empty_answer_for_request(self):
        _, out, _ = run(self.f.url, [rq(3, "empty")])
        self.assertEqual(out[0]["id"], 3)
        self.assertIn("no answer", out[0]["error"]["message"])

    def test_rate_limit_passes_through(self):
        _, out, _ = run(self.f.url, [rq(4, "limit")])
        self.assertEqual(out[0], {"jsonrpc": "2.0", "id": 4, "error": {"code": -32029, "message": "rate limited"}})

    def test_redirect_not_followed(self):
        _, out, _ = run(self.f.url, [rq(9, "redirect")])
        self.assertIn("redirects are not followed", out[0]["error"]["message"])
        self.assertEqual([h[0] for h in self.f.hits], ["/mcp"])

    def test_oversized_answer_refused(self):
        _, out, _ = run(self.f.url, [rq(10, "big")])
        self.assertIn("larger than", out[0]["error"]["message"])

    def test_unreachable_server(self):
        _, out, _ = run("http://127.0.0.1:9/mcp", [rq(11, "echo"), json.dumps({"jsonrpc": "2.0", "method": "notifications/x"})])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], 11)
        self.assertIn("could not get an answer from 127.0.0.1", out[0]["error"]["message"])

    def test_slow_call_does_not_block_and_exit_waits(self):
        t = time.time()
        _, out, _ = run(self.f.url, [rq(1, "slow"), rq(2, "echo", {"b": 2})])
        self.assertEqual([m["id"] for m in out], [2, 1])
        self.assertGreater(time.time() - t, 1.4)

    def test_batch(self):
        batch = json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "echo", "params": {"x": 1}}, {"jsonrpc": "2.0", "method": "notifications/y"},
                            {"jsonrpc": "2.0", "id": 2, "method": "echo", "params": {"x": 2}}])
        _, out, _ = run(self.f.url, [batch])
        self.assertEqual(sorted(m["id"] for m in out), [1, 2])

    def test_unicode_round_trip_and_unchanged_body(self):
        line = rq(1, "echo", {"t": "café 東京 ✓"})
        _, out, _ = run(self.f.url, [line])
        self.assertEqual(out[0]["result"]["t"], "café 東京 ✓")
        self.assertEqual(self.f.hits[0][3], line.encode("utf-8"), "the message is sent unchanged")

    def test_no_extra_calls(self):
        run(self.f.url, [rq(i, "echo") for i in range(5)] + [json.dumps({"jsonrpc": "2.0", "method": "notifications/z"})])
        self.assertEqual(len(self.f.hits), 6)
        self.assertEqual({h[0] for h in self.f.hits}, {"/mcp"})
        self.assertEqual({h[1]["via"] for h in self.f.hits}, {"pypi"})

    def test_cli_tools_and_call(self):
        code, out, _ = run(self.f.url, [], args=["tools"])
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(), ["fake_one  First tool. More lines here.", "fake_two  Second page tool."])
        code, out, _ = run(self.f.url, [], args=["call", "x", '{"q": "é"}'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(json.loads(out)["content"][0]["text"]), {"q": "é"})
        code, out, _ = run(self.f.url, [], args=["call", "fails"])
        self.assertEqual(code, 1)
        code, _, err = run(self.f.url, [], args=["call", "x", "[1]"])
        self.assertEqual(code, 1)
        self.assertIn("JSON object", err)

    def test_cli_bad_address_and_usage(self):
        env ={k: v for k, v in os.environ.items() if not k.startswith("LLPRO_")}
        env.update({"LLPRO_MCP_URL": "http://example.com/mcp", "PYTHONPATH": SRC})
        p = subprocess.run([sys.executable, "-m", "llpro_mcp", "tools"], capture_output=True, env=env, timeout=20)
        self.assertEqual(p.returncode, 2)
        self.assertIn(b"https", p.stderr)
        p = subprocess.run([sys.executable, "-m", "llpro_mcp", "nonsense"], capture_output=True, env=env, timeout=20)
        self.assertEqual(p.returncode, 2)
        p = subprocess.run([sys.executable, "-m", "llpro_mcp", "--version"], capture_output=True, env=env, timeout=20)
        self.assertEqual(p.stdout.decode().strip(), "llpro-mcp " + bridge.__version__)


def raw(mid, body, ctype=None):
    p = {"body": body}
    if ctype:
        p["ctype"] = ctype
    return rq(mid, "raw", p)


class ReviewCases(unittest.TestCase):
    """Cases from the independent review of 14 Nov 2026: every asked id gets exactly one answer, both bridges alike."""

    @classmethod
    def setUpClass(cls):
        cls.f = FakeMCP()

    @classmethod
    def tearDownClass(cls):
        cls.f.close()

    def setUp(self):
        self.f.hits.clear()

    def run_raw(self, lines):
        env = {k: v for k, v in os.environ.items() if not k.startswith("LLPRO_") and not k.lower().endswith("_proxy")}
        env.update({"LLPRO_MCP_URL": self.f.url, "PYTHONPATH": SRC})
        p = subprocess.run([sys.executable, "-m", "llpro_mcp"], input=b"".join(lines), capture_output=True, env=env, timeout=30)
        return p.stdout.decode("utf-8")

    def test_lone_surrogate_in_answer_is_delivered(self):
        _, out, _ = run(self.f.url, [raw(1, '{"jsonrpc":"2.0","id":1,"result":{"t":"x\\ud83d"}}')])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["result"]["t"], "x\ud83d")

    def test_sse_with_u2028_inside_a_string(self):
        body = 'data: {"jsonrpc":"2.0","id":2,"result":{"t":"a b c\u0085d"}}\n\n'
        _, out, _ = run(self.f.url, [raw(2, body, "text/event-stream")])
        self.assertEqual(out[0]["result"]["t"], "a b c\u0085d")

    def test_odd_protocol_version_is_ignored(self):
        _, out, _ = run(self.f.url, [rq(1, "initialize", {"version": "２０２５-０６-１８"}), rq(2, "echo/headers")])
        self.assertEqual(out[1]["result"]["version"], None)
        self.assertNotIn("error", out[1])

    def test_float_id_and_repeated_answers_give_one_answer(self):
        _, out, _ = run(self.f.url, [raw(1, '{"jsonrpc":"2.0","id":1.0,"result":"ok"}')])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["result"], "ok")
        _, out, _ = run(self.f.url, [raw(3, '[{"jsonrpc":"2.0","id":3,"result":"a"},{"jsonrpc":"2.0","id":3,"result":"b"},{"jsonrpc":"2.0","id":99,"result":"stray"}]')])
        self.assertEqual([m["result"] for m in out], ["a"])

    def test_big_numbers_kept_exactly(self):
        text = self.run_raw([(raw(1, '{"jsonrpc":"2.0","id":1,"result":{"n":12345678901234567890,"f":1e400}}') + "\n").encode()])
        self.assertIn("12345678901234567890", text)
        self.assertIn("1e400", text)

    def test_input_rules_match_the_npm_bridge(self):
        lines = [b'{"jsonrpc":"2.0","id":1,"method":"echo","params":{"x":NaN}}\n',
                 b'\xef\xbb\xbf{"jsonrpc":"2.0","id":2,"method":"echo"}\n',
                 b'{"jsonrpc":"2.0","id":3,"method":"echo","params":{"t":"\xff"}}\n',
                 b'{"jsonrpc":"2.0",\r"id":4,"method":"echo","params":{"cr":1}}\n',
                 b"[" * 200000 + b"\n"]
        out = [json.loads(l) for l in self.run_raw(lines).splitlines() if l.strip()]
        errs = [m for m in out if m.get("error", {}).get("code") == -32700]
        self.assertEqual(len(errs), 4, out)
        self.assertEqual([m["result"] for m in out if m.get("id") == 4], [{"cr": 1}])
        self.assertEqual(len(self.f.hits), 1, "only the valid message is sent")

    def test_oversized_line_refused_and_next_line_works(self):
        big = b'{"jsonrpc":"2.0","id":1,"method":"echo","params":{"t":"' + b"a" * (bridge.MAX_LINE + 10) + b'"}}\n'
        out = [json.loads(l) for l in self.run_raw([big, (rq(2, "echo", {"ok": 1}) + "\n").encode()]).splitlines() if l.strip()]
        self.assertEqual(out[0]["error"]["code"], -32600)
        self.assertEqual(out[1]["result"], {"ok": 1})
        self.assertEqual(len(self.f.hits), 1)

    def test_initialize_first_and_alone(self):
        _, out, _ = run(self.f.url, [rq(1, "initialize"), json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}), rq(2, "echo/headers")])
        self.assertEqual([m["id"] for m in out], [1, 2])
        self.assertEqual((out[1]["result"]["version"], out[1]["result"]["session"]), ("2025-06-18", "sess-1"))

    def test_new_initialize_and_404_drop_the_old_session(self):
        _, out, _ = run(self.f.url, [rq(1, "initialize"), rq(2, "initialize"), rq(3, "gone")])
        init_hits = [h for h in self.f.hits if b'"initialize"' in h[3]]
        self.assertNotIn("mcp-session-id", init_hits[1][2], "a fresh initialize carries no old session")
        env = {k: v for k, v in os.environ.items() if not k.startswith("LLPRO_") and not k.lower().endswith("_proxy")}
        env.update({"LLPRO_MCP_URL": self.f.url, "PYTHONPATH": SRC})
        p = subprocess.Popen([sys.executable, "-m", "llpro_mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
        for line in (rq(1, "initialize"), rq(2, "gone")):
            p.stdin.write((line + "\n").encode())
            p.stdin.flush()
            json.loads(p.stdout.readline())
        p.stdin.write((rq(3, "echo/headers") + "\n").encode())
        p.stdin.close()
        last = json.loads(p.stdout.readline())
        p.wait(20)
        p.stdout.close()
        self.assertIsNone(last["result"]["session"], "after a 404 the old session is not sent again")

    def test_stream_kept_open_with_keepalives_does_not_hold_slots(self):
        held = rq(0, "rawsse", {"events": ['data: {"jsonrpc":"2.0","id":0,"result":"r"}\n\n'], "hold": 20})
        lines = [held.replace('"id": 0', '"id": %d' % i).replace('\\"id\\":0', '\\"id\\":%d' % i) for i in range(10)] + [rq(99, "echo", {"z": 1})]
        t0 = time.time()
        _, out, _ = run(self.f.url, lines, timeout=60)
        self.assertLess(time.time() - t0, 10, "the bridge closed each stream once it had the answer")
        self.assertEqual(sorted(m["id"] for m in out), list(range(10)) + [99])

    def test_object_and_huge_ids_still_get_one_answer(self):
        big = int("9" * 400)
        _, out, _ = run(self.f.url, [json.dumps({"jsonrpc": "2.0", "id": {"a": 1}, "method": "echo"}),
                                     json.dumps({"jsonrpc": "2.0", "id": big, "method": "echo"})])
        self.assertEqual(len(out), 2, out)
        self.assertEqual(sorted(str(m["id"]) for m in out), sorted([str({"a": 1}), str(big)]))

    def test_stream_events_are_passed_on_as_they_arrive(self):
        events = ['data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"progress":1}}\n\n',
                  'data: {"jsonrpc":"2.0","id":"q1","method":"elicitation/create","params":{}}\r\n\r\n',
                  'data: {"jsonrpc":"2.0","id":5,"result":"done"}\n\n']
        env = {k: v for k, v in os.environ.items() if not k.startswith("LLPRO_") and not k.lower().endswith("_proxy")}
        env.update({"LLPRO_MCP_URL": self.f.url, "PYTHONPATH": SRC})
        p = subprocess.Popen([sys.executable, "-m", "llpro_mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
        t0 = time.time()
        p.stdin.write((rq(5, "rawsse", {"events": events, "delay": 1.0}) + "\n").encode())
        p.stdin.close()
        first = json.loads(p.stdout.readline())
        t1 = time.time() - t0
        rest = [json.loads(l) for l in p.stdout.read().decode().splitlines() if l.strip()]
        p.wait(20)
        p.stdout.close()
        self.assertEqual(first["method"], "notifications/progress")
        self.assertLess(t1, 0.9, "the first event is not held back until the stream ends")
        self.assertEqual([m.get("method") or m.get("result") for m in rest], ["elicitation/create", "done"])


if __name__ == "__main__":
    unittest.main()
