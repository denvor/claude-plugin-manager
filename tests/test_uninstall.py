import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cpm
from tests import fixture


def make_ctx(test):
    td = tempfile.TemporaryDirectory()
    test.addCleanup(td.cleanup)
    return fixture.test_ctx(fixture.build_home(Path(td.name)))


class TestUninstallPlugin(unittest.TestCase):
    def test_uninstall_plugin_delegates_official(self):
        ctx = make_ctx(self)
        calls = []

        def uninstall(argv, cwd):
            calls.append(list(argv))
            return (0, "", "")

        fixture.patch_run(self, {("claude", "plugin", "uninstall"): uninstall})
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = cpm.main(["uninstall", "aa"], ctx=ctx)
        self.assertEqual(rc, cpm.EXIT_OK)
        self.assertEqual(calls, [["claude", "plugin", "uninstall", f"aa@{fixture.MARKET}"]])
        self.assertIn("✓", out.getvalue())


class TestUninstallSkill(unittest.TestCase):
    def test_skill_moves_to_trash_and_keeps_ledger(self):
        ctx = make_ctx(self)
        cpm.main(["add", "orphan", "https://github.com/foo/orphan-skill"], ctx=ctx)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = cpm.main(["uninstall", "orphan"], ctx=ctx)
        self.assertEqual(rc, cpm.EXIT_OK)
        self.assertFalse((ctx.skills / "orphan").exists())
        self.assertTrue((ctx.skills / "anysearch").exists())   # 其他 skill 不受影响
        entries = list(ctx.cpm_trash.iterdir())
        self.assertEqual(len(entries), 1)
        self.assertTrue((entries[0] / "SKILL.md").exists())
        # cpm.json 补录项保留：restore 后来源信息完整
        data = json.loads(ctx.cpm_json.read_text())
        self.assertEqual(data["skills"]["orphan"]["source"],
                         "https://github.com/foo/orphan-skill")


class TestUninstallMcp(unittest.TestCase):
    def test_mcp_entry_removed_stashed_and_command_printed(self):
        ctx = make_ctx(self)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = cpm.main(["uninstall", "codegraph"], ctx=ctx)
        self.assertEqual(rc, cpm.EXIT_OK)
        cfg = json.loads((ctx.home / ".claude.json").read_text())
        self.assertNotIn("codegraph", cfg["mcpServers"])
        self.assertIn("context-mode", cfg["mcpServers"])          # 其余条目不动
        self.assertEqual(cfg["projects"],                         # projects 等其他字段原样
                         {"/home/x/proj": {"mcpServers": {"playwright": {
                             "type": "stdio", "command": "npx",
                             "args": ["@playwright/mcp@latest"]}}}})
        # 被删条目完整存档，restore 可原样写回
        archives = [p for p in ctx.cpm_trash.iterdir() if p.suffix == ".json"]
        self.assertEqual(len(archives), 1)
        rec = json.loads(archives[0].read_text())
        self.assertEqual(rec["kind"], "mcp")
        self.assertEqual(rec["name"], "codegraph")
        self.assertEqual(rec["server"], {"type": "stdio", "command": "codegraph",
                                         "args": ["serve", "--mcp"]})
        text = out.getvalue()
        self.assertIn("sudo npm rm -g", text)                     # 只打印
        self.assertIn("@colbymchenry/codegraph", text)
        self.assertNotIn("password", json.dumps(rec))


class TestUninstallDryRun(unittest.TestCase):
    def test_dry_run_changes_nothing(self):
        ctx = make_ctx(self)
        calls = []
        fixture.patch_run(self, {("claude",): lambda a, c: (calls.append(list(a)), (0, "", ""))[1]})
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = cpm.main(["uninstall", "aa", "orphan", "codegraph", "--dry-run"], ctx=ctx)
        self.assertEqual(rc, cpm.EXIT_OK)
        self.assertTrue((ctx.skills / "orphan").exists())
        self.assertFalse(ctx.cpm_trash.exists())
        cfg = json.loads((ctx.home / ".claude.json").read_text())
        self.assertIn("codegraph", cfg["mcpServers"])
        self.assertEqual([c for c in calls if c[:3] == ["claude", "plugin", "uninstall"]], [])
        text = out.getvalue()
        self.assertIn("将执行", text)          # plugin 行
        self.assertIn("将移入回收站", text)     # skill 行
        self.assertIn("将移除", text)          # mcp 行


class TestUninstallNotHandled(unittest.TestCase):
    def test_project_and_raw_mcp_not_handled(self):
        ctx = make_ctx(self)
        cj = ctx.home / ".claude.json"
        cfg = json.loads(cj.read_text())
        cfg["mcpServers"]["raw"] = {"command": "python3", "args": ["srv.py"]}
        cj.write_text(json.dumps(cfg))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = cpm.main(["uninstall", "raw", "playwright@/home/x/proj"], ctx=ctx)
        self.assertEqual(rc, cpm.EXIT_PARTIAL)
        self.assertIn("不处理", out.getvalue())
        cfg2 = json.loads(cj.read_text())
        self.assertIn("raw", cfg2["mcpServers"])          # 配置原样未动
        self.assertFalse(ctx.cpm_trash.exists())


class TestUninstallUnknown(unittest.TestCase):
    def test_unknown_name_warned_but_not_counted(self):
        ctx = make_ctx(self)
        err, out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            rc = cpm.main(["uninstall", "nosuch"], ctx=ctx)
        self.assertIn("未找到资产: nosuch", err.getvalue())
        self.assertEqual(rc, cpm.EXIT_OK)     # 与 update 现状一致：未知名只提示不计数


class TestTrashList(unittest.TestCase):
    def _uninstalled_ctx(self):
        ctx = make_ctx(self)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cpm.main(["uninstall", "orphan", "codegraph"], ctx=ctx)
        return ctx

    def test_trash_lists_skill_and_mcp_entries(self):
        ctx = self._uninstalled_ctx()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = cpm.main(["trash"], ctx=ctx)
        self.assertEqual(rc, cpm.EXIT_OK)
        text = out.getvalue()
        self.assertIn("orphan", text)
        self.assertIn("codegraph", text)
        self.assertIn("skill", text)
        self.assertIn("mcp", text)

    def test_trash_empty_is_ok_and_says_so(self):
        ctx = make_ctx(self)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = cpm.main(["trash"], ctx=ctx)
        self.assertEqual(rc, cpm.EXIT_OK)
        self.assertIn("空", out.getvalue())


class TestRestoreSkill(unittest.TestCase):
    def test_restore_skill_roundtrip(self):
        ctx = make_ctx(self)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cpm.main(["uninstall", "orphan"], ctx=ctx)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = cpm.main(["restore", "orphan"], ctx=ctx)
        self.assertEqual(rc, cpm.EXIT_OK)
        d = ctx.skills / "orphan"
        self.assertTrue(d.is_dir())
        self.assertIn("orphan", (d / "SKILL.md").read_text())
        self.assertFalse((d / ".cpm_removed.json").exists())   # 标记不残留在恢复目录里
        self.assertEqual(cpm.list_trash(ctx), [])              # 条目已出库
        self.assertIn("恢复", out.getvalue())


class TestRestoreMcp(unittest.TestCase):
    def test_restore_mcp_roundtrip(self):
        ctx = make_ctx(self)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cpm.main(["uninstall", "codegraph"], ctx=ctx)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = cpm.main(["restore", "codegraph"], ctx=ctx)
        self.assertEqual(rc, cpm.EXIT_OK)
        cfg = json.loads((ctx.home / ".claude.json").read_text())
        self.assertEqual(cfg["mcpServers"]["codegraph"],
                         {"type": "stdio", "command": "codegraph", "args": ["serve", "--mcp"]})
        self.assertEqual(cpm.list_trash(ctx), [])
        self.assertIn("sudo npm i -g", out.getvalue())      # npm 包本体只打印重装命令
        self.assertIn("@colbymchenry/codegraph", out.getvalue())


class TestRestoreConflicts(unittest.TestCase):
    def test_restore_skill_refuses_existing_target(self):
        ctx = make_ctx(self)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cpm.main(["uninstall", "orphan"], ctx=ctx)
        (ctx.skills / "orphan").mkdir()
        (ctx.skills / "orphan" / "SKILL.md").write_text("brand new local version")
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = cpm.main(["restore", "orphan"], ctx=ctx)
        self.assertEqual(rc, cpm.EXIT_PARTIAL)
        self.assertIn("不覆盖", err.getvalue())
        self.assertIn("brand new local version", (ctx.skills / "orphan" / "SKILL.md").read_text())
        self.assertEqual(len(cpm.list_trash(ctx)), 1)      # 回收条目保留，用户可自行处置

    def test_restore_mcp_refuses_existing_entry(self):
        ctx = make_ctx(self)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cpm.main(["uninstall", "codegraph"], ctx=ctx)
        cj = ctx.home / ".claude.json"
        cfg = json.loads(cj.read_text())
        cfg["mcpServers"]["codegraph"] = {"command": "handcrafted"}
        cj.write_text(json.dumps(cfg))
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = cpm.main(["restore", "codegraph"], ctx=ctx)
        self.assertEqual(rc, cpm.EXIT_PARTIAL)
        self.assertIn("不覆盖", err.getvalue())
        self.assertEqual(json.loads(cj.read_text())["mcpServers"]["codegraph"],
                         {"command": "handcrafted"})
        self.assertEqual(len(cpm.list_trash(ctx)), 1)


class TestRestoreNoRecord(unittest.TestCase):
    def test_restore_unknown_or_plugin_says_no_backup(self):
        ctx = make_ctx(self)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = cpm.main(["restore", "aa"], ctx=ctx)     # aa 是 plugin：官方卸载无备份
        self.assertEqual(rc, cpm.EXIT_PARTIAL)
        self.assertIn("无备份", err.getvalue())
        self.assertIn("claude plugin install", err.getvalue())


class TestUninstallTwice(unittest.TestCase):
    def test_second_uninstall_keeps_both_and_restore_picks_latest(self):
        ctx = make_ctx(self)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cpm.main(["uninstall", "orphan"], ctx=ctx)
        (ctx.skills / "orphan").mkdir()
        (ctx.skills / "orphan" / "SKILL.md").write_text("second content")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cpm.main(["uninstall", "orphan"], ctx=ctx)
        entries = cpm.list_trash(ctx)
        self.assertEqual(len(entries), 2)                     # 两条都保留，无覆盖
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = cpm.main(["restore", "orphan"], ctx=ctx)
        self.assertEqual(rc, cpm.EXIT_OK)
        self.assertIn("second content", (ctx.skills / "orphan" / "SKILL.md").read_text())
        self.assertEqual(len(cpm.list_trash(ctx)), 1)         # 只取最新一条出库


class TestNpmCompat(unittest.TestCase):
    """卸载/恢复打印的 npm 命令须走平台前缀 + 能力探测（对齐 Win/Linux 兼容提交）。"""

    def setUp(self):
        cpm._allow_scripts_probe = None      # 强制探测，不吃别的测试留下的缓存
        self.addCleanup(setattr, cpm, "_allow_scripts_probe", None)

    def _uninstall_codegraph(self, ctx):
        calls = []

        def npm(argv, cwd):
            calls.append(list(argv))
            if argv[1:3] == ["config", "ls"]:
                return (0, "allow-scripts = true", "")
            return (0, "", "")

        fixture.patch_run(self, {("npm",): npm})
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            cpm.main(["uninstall", "codegraph"], ctx=ctx)
        return out.getvalue(), calls

    def test_uninstall_rm_has_no_allow_scripts_and_platform_prefix(self):
        ctx = make_ctx(self)
        text, calls = self._uninstall_codegraph(ctx)
        import os
        # 卸载不跑 install 脚本，rm 命令不该带 --allow-scripts
        self.assertIn("npm rm -g @colbymchenry/codegraph", text)
        self.assertNotIn("--allow-scripts", text)
        self.assertEqual("sudo" in text, os.name != "nt")
        # 探测用了 npm config ls -l，不是硬编码
        self.assertIn(["npm", "config", "ls", "-l"], calls)

    def test_restore_install_cmd_follows_probe(self):
        ctx = make_ctx(self)
        self._uninstall_codegraph(ctx)
        cpm._allow_scripts_probe = False     # 模拟旧 npm：重装命令不应带 flag
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = cpm.main(["restore", "codegraph"], ctx=ctx)
        self.assertEqual(rc, cpm.EXIT_OK)
        self.assertIn("npm i -g @colbymchenry/codegraph@0.9.9", out.getvalue())
        self.assertNotIn("--allow-scripts", out.getvalue())

    def test_rm_cmd_helper_platform_prefix(self):
        """全局 patch os.name 会把 Path 换成 ntpath、弄坏夹具，
        故对助手做主机自适应断言（与兼容提交的 install_cmd 测试同法）。"""
        import os
        cmd = cpm.npm_global_remove_cmd("some-pkg")
        self.assertTrue(cmd.endswith("npm rm -g some-pkg".lstrip()) or
                        cmd.endswith("rm -g some-pkg"))
        self.assertEqual(cmd.startswith("sudo "), os.name != "nt")
