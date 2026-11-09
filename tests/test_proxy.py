import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cpm


class TestProxyFromConfig(unittest.TestCase):
    """cpm.json 的 proxy.{enabled,url} 解析：未启用/缺失/损坏一律当直连。"""

    def _write(self, data):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        p = Path(td.name) / "cpm.json"
        if data is not None:
            p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return p

    def test_missing_file_is_direct(self):
        self.assertIsNone(cpm.proxy_from_config(self._write(None)))

    def test_disabled_is_direct(self):
        self.assertIsNone(cpm.proxy_from_config(
            self._write({"proxy": {"enabled": False, "url": "http://127.0.0.1:20171"}})))

    def test_enabled_without_url_is_direct(self):
        self.assertIsNone(cpm.proxy_from_config(self._write({"proxy": {"enabled": True}})))

    def test_enabled_with_url_returns_url(self):
        self.assertEqual(
            cpm.proxy_from_config(self._write({"proxy": {"enabled": True, "url": "http://p:1"}})),
            "http://p:1")

    def test_corrupt_json_is_direct(self):
        self.assertIsNone(cpm.proxy_from_config(self._write("{oops")))

    def test_ctx_loads_and_resets_module_flag(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        p = Path(td.name) / "cpm.json"
        p.write_text(json.dumps({"proxy": {"enabled": True, "url": "http://p:1"}}),
                     encoding="utf-8")
        cpm.Ctx(td.name, node_modules=Path(td.name) / "nm", cpm_json=p)
        self.assertEqual(cpm._PROXY_URL, "http://p:1")
        # 无代理的 Ctx 会把模块级开关重置回直连（否则测试之间会互相污染）
        cpm.Ctx(td.name, node_modules=Path(td.name) / "nm",
                cpm_json=Path(td.name) / "absent.json")
        self.assertIsNone(cpm._PROXY_URL)


class TestFetchUrlProxy(unittest.TestCase):
    """fetch_url 按 _PROXY_URL 决定走代理还是直连（stub 掉 opener，不打真网）。"""

    def setUp(self):
        cpm._PROXY_URL = None
        self.addCleanup(setattr, cpm, "_PROXY_URL", None)

    def _fetch(self):
        captured = {}

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b"{}"

        class FakeOpener:
            def open(self, req, timeout=None):
                return Resp()

        def fake_build_opener(*handlers):
            captured["handlers"] = handlers
            return FakeOpener()

        real = cpm.urllib.request.build_opener
        cpm.urllib.request.build_opener = fake_build_opener
        self.addCleanup(setattr, cpm.urllib.request, "build_opener", real)
        return cpm.fetch_url("https://example.invalid/x"), captured

    def test_direct_when_unset(self):
        body, captured = self._fetch()
        self.assertEqual(body, b"{}")
        self.assertEqual(captured["handlers"], ())

    def test_proxy_handler_when_set(self):
        cpm._PROXY_URL = "http://p:1"
        body, captured = self._fetch()
        self.assertEqual(body, b"{}")
        h = captured["handlers"][0]
        self.assertIsInstance(h, cpm.urllib.request.ProxyHandler)
        self.assertEqual(h.proxies, {"http": "http://p:1", "https": "http://p:1"})


class TestRunCmdProxy(unittest.TestCase):
    """run_cmd 把 _PROXY_URL 注入子进程环境变量——npm/git 等外部工具也走代理。"""

    def setUp(self):
        cpm._PROXY_URL = None
        self.addCleanup(setattr, cpm, "_PROXY_URL", None)

    def test_env_gets_proxy_when_set(self):
        cpm._PROXY_URL = "http://p:1"
        seen = {}

        class R:
            returncode, stdout, stderr = 0, "", ""

        def fake_run(argv, **kw):
            seen.update(kw.get("env") or {})
            return R()

        real = subprocess.run
        subprocess.run = fake_run
        self.addCleanup(setattr, subprocess, "run", real)
        cpm.run_cmd(["true"])
        self.assertEqual(seen.get("http_proxy"), "http://p:1")
        self.assertEqual(seen.get("https_proxy"), "http://p:1")

    def test_env_untouched_when_unset(self):
        seen = {}

        class R:
            returncode, stdout, stderr = 0, "", ""

        def fake_run(argv, **kw):
            seen.update(kw.get("env") or {})
            return R()

        real = subprocess.run
        subprocess.run = fake_run
        self.addCleanup(setattr, subprocess, "run", real)
        cpm.run_cmd(["true"])
        self.assertNotIn("https_proxy", seen)
