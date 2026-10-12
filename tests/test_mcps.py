import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cpm
from tests import fixture


class TestMcps(unittest.TestCase):
    def setUp(self):
        # 本类断言 v12 形态（命令带 --allow-scripts）；探针置 True，测试结束还原 False
        cpm._allow_scripts_probe = True
        self.addCleanup(setattr, cpm, "_allow_scripts_probe", False)
        self._td = tempfile.TemporaryDirectory()
        self.home = fixture.build_home(Path(self._td.name))
        self.ctx = fixture.test_ctx(self.home)
        self.addCleanup(self._td.cleanup)

    def _by_name(self):
        return {a.name: a for a in cpm.load_mcps(self.ctx)}

    def test_load_scoped_and_unscoped_and_project(self):
        by = self._by_name()
        self.assertEqual(set(by), {"codegraph", "context-mode", "playwright@/home/x/proj"})
        self.assertEqual(by["codegraph"].source, "npm:@colbymchenry/codegraph")  # bin→包名反查
        self.assertEqual(by["codegraph"].current, "0.9.9")
        self.assertIn("--allow-scripts=@colbymchenry/codegraph", by["codegraph"].plan["cmd"])
        # sudo 按平台加（Windows 无 sudo），断言只锁跨平台公共部分
        self.assertIn("npm i -g --allow-scripts=@colbymchenry/codegraph @colbymchenry/codegraph@",
                      by["codegraph"].plan["cmd"])
        self.assertEqual(by["context-mode"].current, "1.0.162")
        self.assertFalse(by["playwright@/home/x/proj"].managed)
        self.assertIn("与官方插件重叠", by["playwright@/home/x/proj"].notes)

    def test_unknown_command_unmanaged(self):
        cfg = json.loads((self.home / ".claude.json").read_text())
        cfg["mcpServers"]["mystery"] = {"command": "mystery-binary", "args": []}
        (self.home / ".claude.json").write_text(json.dumps(cfg))
        by = self._by_name()
        self.assertFalse(by["mystery"].managed)
        self.assertIn("来源未知", by["mystery"].notes)

    def test_check_fills_expected_and_pinned_cmd(self):
        fixture.patch_run(self, {("npm",): lambda a, c: (0, "0.10.0\n", "")})
        assets = cpm.load_mcps(self.ctx)
        cpm.check_mcps(self.ctx, assets)
        by = {a.name: a for a in assets}
        self.assertEqual(by["codegraph"].expected, "0.10.0")
        self.assertTrue(cpm.outdated("0.9.9", by["codegraph"].expected))
        self.assertEqual(by["codegraph"].plan["cmd"],
                         cpm.npm_global_install_cmd("@colbymchenry/codegraph", "0.10.0"))
        self.assertEqual(by["context-mode"].expected, "0.10.0")   # 只断言 expected 已填

    def test_npm_install_cmd_sudo_per_platform(self):
        import os
        # sudo 与 npm 能力各自独立：两种形态都要断言
        for flag_on in (True, False):
            cpm._allow_scripts_probe = flag_on
            cmd = cpm.npm_global_install_cmd("p", "1.2")
            self.assertEqual(cmd.startswith("sudo "), os.name != "nt")  # Windows 无 sudo
            self.assertIn("p@1.2", cmd)
            self.assertEqual("--allow-scripts=p" in cmd, flag_on)

    def test_allow_scripts_probe_detects_config(self):
        state = {"out": 'allow-scripts = ""\nignore-scripts = false'}
        fixture.patch_run(self, {("npm",): lambda a, c: (0, state["out"], "")})
        cpm._allow_scripts_probe = None
        self.assertTrue(cpm.npm_has_allow_scripts())
        state["out"] = "ignore-scripts = false"      # 旧 npm：无该项
        cpm._allow_scripts_probe = None
        self.assertFalse(cpm.npm_has_allow_scripts())

    def test_npm_view_failure_leaves_question(self):
        fixture.patch_run(self, {("npm",): lambda a, c: (1, "", "registry down")})
        assets = cpm.load_mcps(self.ctx)
        cpm.check_mcps(self.ctx, assets)
        self.assertIsNone({a.name: a for a in assets}["codegraph"].expected)


class TestProjectScopedNotes(unittest.TestCase):
    """项目级条目的 notes 须按实际来源判定，不能一刀切写「与官方插件重叠」（真机：
    context-mode 在 D:/ 下是重复注册，与全局条目重复，跟插件无关）。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = fixture.build_home(Path(self._td.name))
        self.ctx = fixture.test_ctx(self.home)
        self.addCleanup(self._td.cleanup)

    def _proj_srv(self, name, cmd, proj="/home/y/proj2"):
        cj = self.home / ".claude.json"
        cfg = json.loads(cj.read_text())
        cfg["projects"].setdefault(proj, {"mcpServers": {}})
        cfg["projects"][proj]["mcpServers"][name] = {"type": "stdio", "command": cmd, "args": []}
        cj.write_text(json.dumps(cfg))
        return {a.name: a for a in cpm.load_mcps(self.ctx)}[f"{name}@{proj}"]

    def test_dup_of_global_says_global(self):
        a = self._proj_srv("context-mode", "context-mode")
        self.assertIn("与全局条目重复", a.notes)
        self.assertNotIn("官方插件", a.notes)

    def test_same_name_other_command_says_override(self):
        a = self._proj_srv("context-mode", "/opt/pinned/context-mode")
        self.assertIn("同名但命令不同", a.notes)

    def test_plugin_overlap_says_plugin(self):
        a = self._proj_srv("playwright", "npx")
        self.assertIn("与官方插件重叠", a.notes)
        self.assertNotIn("全局", a.notes)

    def test_unrelated_says_project_only(self):
        a = self._proj_srv("solo", "some-local-binary")
        self.assertEqual(a.notes, "项目级配置，cpm 不处理")

    def test_missing_ledger_does_not_break_plugin_names(self):
        # 无 plugin 账本时不能炸（load_mcps 可被单独调用）
        self.ctx.plugins_json.unlink()
        self.assertEqual(cpm.plugin_mcp_names(self.ctx), set())
