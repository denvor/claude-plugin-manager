import io
import contextlib
import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cpm
from tests import fixture


class TestLoadPlugins(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = fixture.build_home(Path(self._td.name))
        self.ctx = fixture.test_ctx(self.home)
        self.addCleanup(self._td.cleanup)

    def _by_name(self):
        return {a.name: a for a in cpm.load_plugins(self.ctx)}

    def test_three_plugins_ledger_fields(self):
        by = self._by_name()
        self.assertEqual({"aa", "bb", "cc"}, set(by))
        inst = json.loads((self.home / ".claude/plugins/installed_plugins.json").read_text())
        self.assertEqual(by["aa"].current, inst["plugins"][f"aa@{fixture.MARKET}"][0]["version"])
        self.assertEqual(by["aa"].plan["id"], f"aa@{fixture.MARKET}")
        self.assertEqual(by["aa"].plan["marketplace"], fixture.MARKET)
        self.assertTrue(Path(by["aa"].plan["installPath"]).exists())

    def test_enabled_state_from_settings(self):
        by = self._by_name()
        self.assertTrue(by["aa"].enabled)          # 显式 true
        self.assertFalse(by["cc"].enabled)         # 显式 false
        self.assertIn("已禁用", by["cc"].notes)
        self.assertTrue(by["bb"].enabled)          # 未声明 → 默认启用

    def test_status_table_offline_no_expected(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cpm.main(["status"], ctx=self.ctx)
        self.assertEqual(rc, cpm.EXIT_OK)
        text = out.getvalue()
        self.assertIn("plugin aa", text)          # kind 列宽 6，"plugin"恰好满格，后接单空格
        self.assertIn("待检测", text)
        self.assertNotIn("->", text)               # status 不做比对

    def test_main_broken_exit_on_corrupt_ledger(self):
        # 承接任务 1 的说明：Broken→main 退出码 3 的端到端路径从本任务起可达
        (self.home / ".claude/plugins/installed_plugins.json").write_text("{oops")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = cpm.main(["status"], ctx=self.ctx)
        self.assertEqual(rc, cpm.EXIT_BROKEN)
        self.assertIn("损坏", err.getvalue())
