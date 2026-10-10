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


class TestPluginExpected(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = fixture.build_home(Path(self._td.name))
        self.ctx = fixture.test_ctx(self.home)
        self.mp = self.ctx.marketplaces / fixture.MARKET
        self.addCleanup(self._td.cleanup)

    def test_semver_wins_over_path_form(self):
        exp, src = cpm.plugin_expected(self.mp,
                                       {"name": "cc", "version": "2.0.0", "source": "./plugins/cc"})
        self.assertEqual(exp, "2.0.0")
        self.assertIn("semver", src)

    def test_url_source_returns_pinned_sha(self):
        entry = {"name": "bb", "source": {"source": "url",
                                         "url": "https://github.com/x/bb.git",
                                         "sha": "deadbeef00000000000000000000000000000000"}}
        exp, src = cpm.plugin_expected(self.mp, entry)
        self.assertEqual(exp, "deadbeef" + "0" * 32)
        self.assertIn("url 钉", src)

    def test_path_source_uses_last_commit_touching_plugin_dir(self):
        exp, src = cpm.plugin_expected(self.mp, {"name": "aa", "source": "./plugins/aa"})
        self.assertEqual(len(exp), 40)                      # r2 完整 sha
        ledger = cpm.load_json(self.ctx.plugins_json)["plugins"][f"aa@{fixture.MARKET}"][0]
        self.assertNotEqual(exp, ledger["gitCommitSha"])   # marketplace 已前进（r2 ≠ r1）
        self.assertIn("path", src)

    def test_missing_marketplace_entry(self):
        entry, mp_dir = cpm.find_marketplace_entry(self.ctx, fixture.MARKET, "zzz")
        self.assertIsNone(entry)
        entry, mp_dir = cpm.find_marketplace_entry(self.ctx, "不存在的marketplace", "aa")
        self.assertIsNone(entry)

    def test_outdated_rules(self):
        self.assertFalse(cpm.outdated("abc123", "abc123def"))      # sha 前缀
        self.assertTrue(cpm.outdated("cafebabe0000", "deadbeef0000"))
        self.assertFalse(cpm.outdated("1.0.0", "1.0.0"))
        self.assertTrue(cpm.outdated("1.0.0", "2.0.0"))
        self.assertFalse(cpm.outdated("v3.1.1", "3.1.1"))          # v 前缀剥离
        self.assertFalse(cpm.outdated("3.1.1", "v3.1.1"))
        self.assertIsNone(cpm.outdated("", "2.0.0"))               # 空值不可判
        self.assertIsNone(cpm.outdated("2.0.0", None))
