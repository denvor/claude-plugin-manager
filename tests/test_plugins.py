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
        # enabledPlugins 是白名单：无条目=禁用（真机实证：claude plugin list 对
        # 不在字典中的 4 个插件全部显示 disabled，且盘上无 false 条目）
        self.assertFalse(by["bb"].enabled)
        self.assertIn("已禁用", by["bb"].notes)

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


class TestCheckUpdate(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = fixture.build_home(Path(self._td.name))
        self.ctx = fixture.test_ctx(self.home)
        self.calls = []

        def claude(argv, cwd):
            self.calls.append(list(argv))
            return (0, "", "")

        def npm(argv, cwd):
            return (0, "9.9.9\n", "")          # 辅助通道版本号

        fixture.patch_run(self, {("claude",): claude, ("npm",): npm})
        fixture.patch_fetch(self, {"releases/latest": {"tag_name": "v9.9.9"}})  # 任务5回归修补：skills 行需 release 桩
        self.addCleanup(self._td.cleanup)

    def _rows(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cpm.cmd_check_update(self.ctx, as_json=True)
        return rc, {r["name"]: r for r in json.loads(out.getvalue())}

    def test_all_three_source_forms_detected_outdated(self):
        rc, rows = self._rows()
        self.assertEqual(rc, cpm.EXIT_OUTDATED)
        self.assertEqual(["claude", "plugin", "marketplace", "update"], self.calls[0])
        self.assertEqual(rows["aa"]["status"], "可更新")   # path 型：r2 前进
        self.assertEqual(rows["bb"]["status"], "可更新")   # url 型：sha 不等
        self.assertEqual(rows["cc"]["status"], "可更新")   # semver 1.0.0 vs 2.0.0

    def test_auxiliary_npm_and_npx_notes(self):
        _, rows = self._rows()
        self.assertIn("npm 通道 9.9.9", rows["aa"]["notes"])      # cache 有 package.json
        self.assertIn("npx 自更新", rows["aa"]["notes"])           # cache 有 .mcp.json npx
        self.assertNotIn("npm 通道", rows["bb"]["notes"])          # 无 package.json

    def test_marketplace_refresh_failure_forces_partial(self):
        fixture.patch_run(self, {("claude",): lambda a, c: (1, "", "网络断"),
                                 ("npm",): lambda a, c: (0, "9.9.9", "")})
        rc, _ = self._rows()
        self.assertEqual(rc, cpm.EXIT_PARTIAL)

    def test_human_table_shows_arrows(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cpm.cmd_check_update(self.ctx, as_json=False)
        self.assertIn("->", out.getvalue())
        self.assertIn("可更新", out.getvalue())
