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
        self.assertIn("sudo npm i -g @colbymchenry/codegraph@", by["codegraph"].plan["cmd"])
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
        self.assertEqual(by["codegraph"].plan["cmd"], "sudo npm i -g @colbymchenry/codegraph@0.10.0")
        self.assertEqual(by["context-mode"].expected, "0.10.0")   # 只断言 expected 已填

    def test_npm_view_failure_leaves_question(self):
        fixture.patch_run(self, {("npm",): lambda a, c: (1, "", "registry down")})
        assets = cpm.load_mcps(self.ctx)
        cpm.check_mcps(self.ctx, assets)
        self.assertIsNone({a.name: a for a in assets}["codegraph"].expected)
