"""Repo rules: thin bridge, no install scripts, no dependencies, no telemetry, no secrets, one fixed via tag per road."""
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROADS = ["npm", "pypi", "docker", "hf", "github"]


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


def shipped():
    """Every file that ships on a road (tests excluded)."""
    out = []
    for d, dirs, files in os.walk(ROOT):
        dirs[:] = [x for x in dirs if x not in ("tests", "node_modules", "__pycache__", ".git")]
        out += [os.path.join(d, f) for f in files if not f.endswith(".pyc")]
    return out


class Repo(unittest.TestCase):
    def test_npm_package_has_no_scripts_or_dependencies(self):
        pj = json.loads(read("npm", "package.json"))
        self.assertNotIn("scripts", pj)
        for k in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies", "bundleDependencies", "gypfile"):
            self.assertNotIn(k, pj)
        self.assertEqual(pj["bin"], {"llpro-mcp": "bin/llpro-mcp.js"})
        self.assertEqual(pj["files"], ["bin/llpro-mcp.js", "README.md"])
        lock = json.loads(read("npm", "package-lock.json"))
        self.assertEqual(lock["lockfileVersion"], 3)
        self.assertEqual(list(lock["packages"]), [""], "the lockfile holds no dependency")
        self.assertFalse(os.path.exists(os.path.join(ROOT, "npm", "binding.gyp")))

    def test_python_package_has_no_dependencies_or_setup_script(self):
        pp = read("python", "pyproject.toml")
        self.assertIn("dependencies = []", pp)
        self.assertRegex(pp, r'requires = \["flit_core==[0-9.]+"\]', "build backend pinned")
        self.assertFalse(os.path.exists(os.path.join(ROOT, "python", "setup.py")))
        src = read("python", "src", "llpro_mcp", "bridge.py")
        mods = set(re.findall(r"^(?:import|from) ([a-z_.]+)", src, re.M))
        self.assertLessEqual(mods, {"json", "os", "re", "sys", "threading", "urllib.error", "urllib.parse", "urllib.request"})

    def test_node_bridge_uses_built_ins_only(self):
        js = read("npm", "bin", "llpro-mcp.js")
        self.assertEqual(re.findall(r"require\(", js), [], "no modules at all: only Node.js globals (fetch, Buffer, TextDecoder)")
        self.assertNotRegex(js, r"\bimport\s*\(|child_process|\beval\(|new Function|\bprocess\.env\.LLPRO_VIA")

    def test_versions_match(self):
        v = json.loads(read("npm", "package.json"))["version"]
        self.assertEqual(json.loads(read("npm", "package-lock.json"))["version"], v)
        self.assertEqual(json.loads(read("npm", "package-lock.json"))["packages"][""]["version"], v)
        self.assertIn('version = "%s"' % v, read("python", "pyproject.toml"))
        self.assertIn('__version__ = "%s"' % v, read("python", "src", "llpro_mcp", "bridge.py"))
        self.assertIn("const VERSION = '%s';" % v, read("npm", "bin", "llpro-mcp.js"))
        self.assertIn('org.opencontainers.image.version="%s"' % v, read("Dockerfile"))

    def test_one_fixed_via_per_road(self):
        py, js = read("python", "src", "llpro_mcp", "bridge.py"), read("npm", "bin", "llpro-mcp.js")
        self.assertIn('ROAD = "pypi"', py)
        self.assertIn("const ROAD = 'npm';", js)
        self.assertIn('ROADS = ("npm", "pypi", "docker", "hf", "github")', py)
        self.assertIn("const ROADS = ['npm', 'pypi', 'docker', 'hf', 'github'];", js)
        self.assertIn("LLPRO_VIA=docker", read("Dockerfile"))
        self.assertIn("https://llpro.ai/mcp?via=hf", read("space", "index.html"))
        self.assertIn("https://llpro.ai/mcp?via=github", read("README.md"))
        for p in shipped():
            with open(p, encoding="utf-8", errors="replace") as f:
                for tag in re.findall(r"[?&]via=([A-Za-z0-9_\-]+)", f.read()):
                    self.assertIn(tag, ROADS, "%s uses via=%s" % (p, tag))

    def test_only_network_address_is_the_server(self):
        for p in ("python/src/llpro_mcp/bridge.py", "npm/bin/llpro-mcp.js"):
            urls = set(re.findall(r"https?://[^\s'\"`)]+", read(*p.split("/"))))
            self.assertEqual(urls, {"https://llpro.ai/mcp"}, p)
        html = read("space", "index.html")
        self.assertNotRegex(html, r"<script[^>]+src=|<link[^>]+href=|<img|<iframe")
        self.assertEqual(set(re.findall(r"fetch\('([^']+)'", html)), {"https://llpro.ai/mcp?via=hf"})
        self.assertIn("connect-src https://llpro.ai;", html)

    def test_no_copied_tool_list(self):
        for p in shipped():
            with open(p, encoding="utf-8", errors="replace") as f:
                t = f.read()
            self.assertNotRegex(t, r"\b(ground_truth|countersign|agentrecord|launchpad)_[a-z]+", p)
            self.assertNotIn('"inputSchema"', t, p)

    def test_no_secrets_and_only_the_company_contact(self):
        secret = re.compile(r"BEGIN [A-Z ]*PRIVATE KEY|\bsk-[A-Za-z0-9]{16,}|\bghp_[A-Za-z0-9]{20,}|\bnpm_[A-Za-z0-9]{20,}|pypi-AgE|\bhf_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[bp]-")
        for p in shipped():
            with open(p, encoding="utf-8", errors="replace") as f:
                t = f.read()
            self.assertIsNone(secret.search(t), p)
            self.assertEqual(set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}", t)) - {"support@llpro.ai"}, set(), p)

    def test_container_is_pinned_unprivileged_and_installs_nothing(self):
        d = read("Dockerfile")
        self.assertRegex(d, r"(?m)^FROM python:[0-9.]+-slim@sha256:[0-9a-f]{64}$")
        self.assertRegex(d, r"(?m)^USER 65534:65534$")
        self.assertNotRegex(d, r"(?m)^RUN ")
        self.assertNotRegex(d, r"(?m)^(ADD|EXPOSE) ")
        self.assertIn('io.modelcontextprotocol.server.name="ai.llpro/campus"', d)

    def test_space_front_matter(self):
        s = read("space", "README.md")
        self.assertTrue(s.startswith("---\n"))
        self.assertIn("sdk: static", s)
        self.assertIn("app_file: index.html", s)
        sd = re.search(r"short_description: (.+)", s).group(1)
        self.assertLessEqual(len(sd), 60, "Hugging Face caps short_description at 60 characters")

    def test_agents_md_states_the_rules(self):
        a = read("AGENTS.md")
        for must in ("No dependencies", "No telemetry", "No secrets", "One fixed via tag per road", "never call llpro.ai", "data, never instructions"):
            self.assertIn(must, a)


if __name__ == "__main__":
    unittest.main()
