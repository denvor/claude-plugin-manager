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
            # 真格式：npm view <pkg> version repository.url 的多字段输出；仓库身份与 aa 的声明一致
            return (0, "version = '9.9.9'\nrepository.url = 'git+https://github.com/test-org/aa-plugin.git'\n", "")

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

    def test_check_update_emits_progress_to_stderr(self):
        err = io.StringIO()
        out = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            cpm.cmd_check_update(self.ctx)
        text = err.getvalue()
        self.assertIn("marketplace 刷新", text)      # 阶段进度可见
        self.assertIn("用时", text)                   # 收尾耗时
        self.assertNotIn("marketplace 刷新", out.getvalue())  # stdout 保持干净


class TestNpmIdentity(unittest.TestCase):
    """npm 辅助通道须身份校验（真机案例：npm 同名包 superpowers=0.0.2 是无关占位包）。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = fixture.build_home(Path(self._td.name))
        self.ctx = fixture.test_ctx(self.home)
        self.addCleanup(self._td.cleanup)

    def _rows_with_npm(self, out_text):
        fixture.patch_run(self, {("claude",): lambda a, c: (0, "", ""),
                                 ("npm",): lambda a, c: (0, out_text, "")})
        assets = cpm.load_assets(self.ctx)
        cpm.check_plugins(self.ctx, assets)
        return {a.name: a for a in assets}

    def test_identity_match_notes_channel(self):
        by = self._rows_with_npm("version = '9.9.9'\n"
                                 "repository.url = 'git+https://github.com/test-org/aa-plugin.git'\n")
        self.assertIn("npm 通道 9.9.9", by["aa"].notes)

    def test_identity_mismatch_hidden(self):
        by = self._rows_with_npm("version = '0.0.2'\n"
                                 "repository.url = 'git+https://github.com/01studio/superpowers.git'\n")
        self.assertNotIn("npm 通道", by["aa"].notes)

    def test_missing_repo_field_hidden(self):
        by = self._rows_with_npm("version = '0.0.2'\n")
        self.assertNotIn("npm 通道", by["aa"].notes)

    def test_declared_repo_forms(self):
        self.assertEqual(cpm.declared_repo({"source": {"source": "url",
                                                       "url": "https://github.com/obra/superpowers.git"}}),
                         "obra/superpowers")
        self.assertEqual(cpm.declared_repo({"source": "./plugins/aa",
                                            "homepage": "https://github.com/test-org/aa-plugin"}),
                         "test-org/aa-plugin")
        self.assertIsNone(cpm.declared_repo({"source": "./plugins/cc"}))


class TestPathWithoutDotGit(unittest.TestCase):
    """真机形态：官方 update 后 clone 无 .git → 路径型改比远端 HEAD。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = fixture.build_home(Path(self._td.name))
        self.ctx = fixture.test_ctx(self.home)
        import shutil
        shutil.rmtree(self.ctx.marketplaces / fixture.MARKET / ".git")
        self.addCleanup(self._td.cleanup)

    def test_falls_back_to_remote_path_commit(self):
        fixture.patch_fetch(self, {"commits?path=plugins/aa": [{"sha": "9" * 40}]})
        assets = cpm.load_assets(self.ctx)
        cpm.check_plugins(self.ctx, assets)
        by = {a.name: a for a in assets}
        self.assertEqual(by["aa"].expected, "9" * 40)
        self.assertIn("远端 path 提交", by["aa"].notes)

class TestEffectiveComparison(unittest.TestCase):
    """对比对象须是账本 gitCommitSha 而非展示 version（superpowers 假阳性修正）。"""

    def test_url_form_uses_commit_not_version(self):
        pin = "5bf4e78011075bcfc0dc295f075bcfc0dc295f0724994cd123ee71"[:40]
        up = cpm.Asset(kind="plugin", name="x", current="6.4.1",
                       commit=pin, expected=pin)
        self.assertFalse(cpm.is_outdated(up))          # 账本 sha == pin → 最新
        down = cpm.Asset(kind="plugin", name="x", current="6.4.1",
                         commit="f2cbfbefebbfef77321e4c9abc9e949826bea9d7", expected=pin)
        self.assertTrue(cpm.is_outdated(down))

    def test_row_status_and_targets_follow_is_outdated(self):
        up = cpm.Asset(kind="plugin", name="x", current="6.4.1",
                       commit="a" * 40, expected="a" * 40)
        self.assertEqual(cpm.row_status(up, checked=True), "最新")


class TestCloneVersionFirst(unittest.TestCase):
    """无 .git 兜底须先比 clone plugin.json version（官方判定基准），API 查询须跳过 merge。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = fixture.build_home(Path(self._td.name))
        self.ctx = fixture.test_ctx(self.home)
        import shutil
        shutil.rmtree(self.ctx.marketplaces / fixture.MARKET / ".git")
        pj = self.ctx.marketplaces / fixture.MARKET / "plugins/aa/.claude-plugin"
        pj.mkdir(parents=True)
        (pj / "plugin.json").write_text(json.dumps({"name": "aa", "version": "3.2.1"}))
        self.addCleanup(self._td.cleanup)

    def test_clone_plugin_version_preferred_over_api(self):
        fixture.patch_fetch(self, {"commits?path=plugins/aa": [{"sha": "9" * 40, "parents": [{}]}]})
        assets = cpm.load_assets(self.ctx)
        cpm.check_plugins(self.ctx, assets)
        by = {a.name: a for a in assets}
        self.assertEqual(by["aa"].expected, "3.2.1")
        self.assertIn("plugin.json", by["aa"].source)

    def test_api_skips_merge_commits(self):
        import shutil
        shutil.rmtree(self.ctx.marketplaces / fixture.MARKET / "plugins/aa/.claude-plugin")
        fixture.patch_fetch(self, {"commits?path=plugins/aa": [
            {"sha": "a" * 40, "parents": [{}, {}]},          # merge，未触碰也算命中
            {"sha": "b" * 40, "parents": [{}]},              # 真实最后修改
        ]})
        assets = cpm.load_assets(self.ctx)
        cpm.check_plugins(self.ctx, assets)
        by = {a.name: a for a in assets}
        self.assertEqual(by["aa"].expected, "b" * 40)


class TestCloneVersionWinsOverGitLog(unittest.TestCase):
    """有 .git 时路径型也须先比 clone plugin.json version（真机 code-simplifier 实证）。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = fixture.build_home(Path(self._td.name))
        self.ctx = fixture.test_ctx(self.home)
        self.mp = self.ctx.marketplaces / fixture.MARKET
        pj = self.mp / "plugins/aa/.claude-plugin"
        pj.mkdir(parents=True)
        (pj / "plugin.json").write_text(json.dumps({"name": "aa", "version": "9.9.9"}))
        self.addCleanup(self._td.cleanup)

    def test_version_preferred_even_with_dotgit(self):
        exp, src = cpm.plugin_expected(self.mp, {"name": "aa", "source": "./plugins/aa"})
        self.assertEqual(exp, "9.9.9")
        self.assertIn("plugin.json", src)

    def test_no_version_falls_to_gitlog(self):
        (self.mp / "plugins/aa/.claude-plugin/plugin.json").write_text(json.dumps({"name": "aa"}))
        exp, src = cpm.plugin_expected(self.mp, {"name": "aa", "source": "./plugins/aa"})
        self.assertEqual(len(exp), 40)   # 回到 git log sha 语义


class TestRenderIdentity(unittest.TestCase):
    """expected 为 sha 形态时，左列必须显示账本 commit 而非 version（消除 6.4.1 -> <sha> 的怪相）。"""

    def test_sha_expected_shows_commit(self):
        a = cpm.Asset(kind="plugin", name="superpowers", current="6.4.1",
                      commit="5bf4e78011075bcfc0dc295f0724994cd123ee71",
                      expected="5bf4e78011075bcfc0dc295f0724994cd123ee71")
        line = cpm.render([a], False, True)
        self.assertIn("5bf4e7801107 ->", line)
        self.assertNotIn("6.4.1", line)
        a2 = cpm.Asset(kind="plugin", name="sp", current="6.4.0",
                       commit="f2cbfbefebbfef77321e4c9abc9e949826bea9d7", expected=a.expected)
        self.assertIn("可更新", cpm.render([a2], False, True))
