"""langchain-llpro package rules: stdlib client, three LLPRO endpoints only, via langchain by header, key path on 402."""
import importlib.util
import io
import json
import os
import re
import unittest
import urllib.error
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "langchain")


def read(*p):
    with open(os.path.join(PKG, *p), encoding="utf-8") as f:
        return f.read()


def load_client():
    spec = importlib.util.spec_from_file_location("llpro_lc_client", os.path.join(PKG, "langchain_llpro", "client.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, body):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(json.dumps(body).encode()))


class Package(unittest.TestCase):
    def test_client_is_stdlib_only(self):
        mods = set(re.findall(r"^(?:import|from) ([a-z_.]+)", read("langchain_llpro", "client.py"), re.M))
        self.assertLessEqual(mods, {"http.client", "json", "os", "urllib.error", "urllib.request"})

    def test_tools_import_only_langchain_core_pydantic_and_client(self):
        mods = set(re.findall(r"^(?:import|from) ([a-z_.]+)", read("langchain_llpro", "tools.py"), re.M))
        self.assertLessEqual(mods, {"json", "typing", "langchain_core.tools", "pydantic", "."})

    def test_only_network_addresses_are_the_three_endpoints(self):
        for f in ("client.py", "tools.py", "__init__.py"):
            urls = set(re.findall(r"https?://[^\s'\"`)]+", read("langchain_llpro", f)))
            self.assertLessEqual(urls, {"https://gt.llpro.ai/v1/ask", "https://countersign.llpro.ai/v1/stamp",
                                        "https://agentrecord.llpro.ai/v1/lookup"}, f)

    def test_no_install_scripts_and_versions_match(self):
        self.assertFalse(os.path.exists(os.path.join(PKG, "setup.py")))
        pp = read("pyproject.toml")
        self.assertRegex(pp, r'requires = \["flit_core==[0-9.]+"\]')
        self.assertIn('dependencies = ["langchain-core>=0.3,<2"]', pp)
        v = re.search(r'^version = "([0-9.]+)"', pp, re.M).group(1)
        self.assertIn('__version__ = "%s"' % v, read("langchain_llpro", "client.py"))

    def test_sends_via_header_ua_key_and_agent_id(self):
        c = load_client()
        seen = {}

        def fake(req, timeout=None):
            seen["url"], seen["h"], seen["body"] = req.full_url, dict(req.header_items()), json.loads(req.data)
            return FakeResp(b'{"answer": 1}')
        with mock.patch("urllib.request.urlopen", fake):
            out = c.call("agent_record", {"target": "x"}, api_key="llk_test", agent_id="a1")
        self.assertEqual(out, {"answer": 1})
        self.assertEqual(seen["url"], "https://agentrecord.llpro.ai/v1/lookup")
        self.assertEqual(seen["h"]["X-llpro-via"], "langchain")
        self.assertEqual(seen["h"]["Authorization"], "Bearer llk_test")
        self.assertEqual(seen["h"]["X-agent-id"], "a1")
        self.assertTrue(seen["h"]["User-agent"].startswith("langchain-llpro/"))

    def test_no_key_sends_no_authorization(self):
        c = load_client()
        seen = {}

        def fake(req, timeout=None):
            seen["h"] = dict(req.header_items())
            return FakeResp(b"{}")
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("urllib.request.urlopen", fake):
            c.call("ground_truth", {"topic": "weather"})
        self.assertNotIn("Authorization", seen["h"])

    def test_env_key_used(self):
        c = load_client()
        seen = {}

        def fake(req, timeout=None):
            seen["h"] = dict(req.header_items())
            return FakeResp(b"{}")
        with mock.patch.dict(os.environ, {"LLPRO_API_KEY": "llk_env"}), mock.patch("urllib.request.urlopen", fake):
            c.call("countersign", {"output": "1 + 1 = 2", "producer": {"agent_id": "t"}})
        self.assertEqual(seen["h"]["Authorization"], "Bearer llk_env")

    def test_402_gives_key_path_tagged_langchain(self):
        c = load_client()
        body = {"error": "Free daily allowance used.", "get_a_key": "https://llpro.ai/pay/credit"}
        with mock.patch("urllib.request.urlopen", side_effect=http_error(402, body)):
            with self.assertRaises(c.PaymentRequired) as cm:
                c.call("ground_truth", {"topic": "weather"})
        self.assertEqual(cm.exception.status, 402)
        self.assertIn("https://llpro.ai/pay/credit?via=langchain", str(cm.exception))
        self.assertIn("LLPRO_API_KEY", str(cm.exception))

    def test_402_link_already_tagged_kept(self):
        c = load_client()
        body = {"get_a_key": "https://llpro.ai/pay/credit?via=x402"}
        with mock.patch("urllib.request.urlopen", side_effect=http_error(402, body)):
            with self.assertRaises(c.PaymentRequired) as cm:
                c.call("ground_truth", {"topic": "weather"})
        self.assertIn("credit?via=x402,", str(cm.exception))
        self.assertNotIn("via=langchain", str(cm.exception))

    def test_key_hidden_from_repr_and_dump(self):
        try:
            import langchain_core  # noqa: F401
        except ImportError:
            self.skipTest("langchain-core not installed")
        import sys
        sys.path.insert(0, PKG)
        from langchain_llpro import get_tools
        for tool in get_tools(api_key="llk_SECRETVALUE"):
            self.assertNotIn("llk_SECRETVALUE", repr(tool) + str(tool) + json.dumps(tool.model_dump(), default=str))
            self.assertEqual(tool.api_key, "llk_SECRETVALUE")

    def test_402_without_link_still_explains(self):
        c = load_client()
        with mock.patch("urllib.request.urlopen", side_effect=http_error(402, {})):
            with self.assertRaises(c.PaymentRequired) as cm:
                c.call("ground_truth", {"topic": "weather"})
        self.assertIn("api_key", str(cm.exception))

    def test_400_says_nothing_charged(self):
        c = load_client()
        with mock.patch("urllib.request.urlopen", side_effect=http_error(400, {"error": "unknown topic"})):
            with self.assertRaises(c.LLPROError) as cm:
                c.call("ground_truth", {"topic": "nope"})
        self.assertIn("nothing was charged", str(cm.exception))
        self.assertNotIsInstance(cm.exception, c.PaymentRequired)

    def test_unreachable_is_llpro_error(self):
        c = load_client()
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            with self.assertRaises(c.LLPROError) as cm:
                c.call("ground_truth", {"topic": "weather"})
        self.assertEqual(cm.exception.status, 0)

    def test_bad_arguments_refused_before_network(self):
        c = load_client()
        with mock.patch("urllib.request.urlopen") as u:
            with self.assertRaises(ValueError):
                c.call("nope", {"a": 1})
            with self.assertRaises(ValueError):
                c.call("ground_truth", {})
            u.assert_not_called()


if __name__ == "__main__":
    unittest.main()
