import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cpm


def mk(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class TestTreeEqual(unittest.TestCase):
    def test_identical_trees_are_equal(self):
        a = tempfile.mkdtemp()
        b = tempfile.mkdtemp()
        mk(a, "SKILL.md", "v1")
        mk(a, "sub/x.json", "{}")
        mk(b, "SKILL.md", "v1")
        mk(b, "sub/x.json", "{}")
        self.assertTrue(cpm.trees_equal(Path(a), Path(b)))

    def test_content_change_detected(self):
        a = tempfile.mkdtemp()
        b = tempfile.mkdtemp()
        mk(a, "SKILL.md", "v1")
        mk(b, "SKILL.md", "v2")
        self.assertFalse(cpm.trees_equal(Path(a), Path(b)))

    def test_official_lifecycle_markers_ignored(self):
        # 官方 CLI 在安装目录写 .in_use/<pid>、.orphaned_at 锁标记——非插件内容，不得影响比对
        a = tempfile.mkdtemp()
        b = tempfile.mkdtemp()
        mk(a, "SKILL.md", "v1")
        mk(b, "SKILL.md", "v1")
        mk(a, ".in_use/452889", "")
        mk(a, ".orphaned_at", "1697000000")
        self.assertTrue(cpm.trees_equal(Path(a), Path(b)))

    def test_added_file_detected(self):
        a = tempfile.mkdtemp()
        b = tempfile.mkdtemp()
        mk(a, "SKILL.md", "v1")
        mk(b, "SKILL.md", "v1")
        mk(b, "extra.md", "new")
        self.assertFalse(cpm.trees_equal(Path(a), Path(b)))


import shutil

from tests import fixture


class TestPluginExpectedContent(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.home = fixture.build_home(Path(self._td.name))
        self.ctx = fixture.test_ctx(self.home)
        self.mp = self.ctx.marketplaces / fixture.MARKET

    def test_content_match_returns_current_as_expected(self):
        inst = Path(self._td.name) / "inst-match"
        shutil.copytree(self.mp / "plugins/aa", inst)     # 与 clone 路径内容完全一致
        exp, src = cpm.plugin_expected(
            self.mp, {"name": "aa", "source": "./plugins/aa"},
            install_path=str(inst), current="4a5d5524630a")
        self.assertEqual(exp, "4a5d5524630a")             # 一致→应有即现有→最新
        self.assertIn("内容与安装版一致", src)


class TestPluginExpectedContentDiff(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.home = fixture.build_home(Path(self._td.name))
        self.ctx = fixture.test_ctx(self.home)
        self.mp = self.ctx.marketplaces / fixture.MARKET

    def test_content_diff_expected_is_clone_head(self):
        # 内容不同 → expected = clone HEAD；用「只动别的路径再提交」制造
        # path-last-commit(r2) ≠ HEAD(r3)，鉴别旧 git log -- path 的维度错配
        (self.mp / "other.txt").write_text("unrelated advance\n")
        fixture.sh(["git", "add", "-A"], self.mp)
        fixture.sh(["git", *fixture.GIT_ID, "commit", "-q", "-m", "r3"], self.mp)
        head = subprocess.run(["git", "-C", str(self.mp), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        exp, src = cpm.plugin_expected(
            self.mp, {"name": "aa", "source": "./plugins/aa"},
            install_path=str(self.ctx.skills), current="4a5d5524630a")
        self.assertEqual(exp, head)
        self.assertIn("较安装版有变化", src)


class TestCheckPluginsUsesContent(unittest.TestCase):
    """check_plugins 须把 installPath/current 传进 plugin_expected（集成闸门）。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.home = fixture.build_home(Path(self._td.name))
        self.ctx = fixture.test_ctx(self.home)
        self.mp = self.ctx.marketplaces / fixture.MARKET

    def test_check_plugins_marks_content_equal_plugin_latest(self):
        # 把 aa 的安装目录做成与 clone 路径内容一致（浅克隆/HEAD 前进都该判最新）
        ip = Path(self.ctx.plugins_json.parent.parent)  # not used; compute from plan below
        assets = cpm.load_plugins(self.ctx)
        aa = [a for a in assets if a.name == "aa"][0]
        ip = Path(aa.plan["installPath"])
        shutil.rmtree(ip)
        shutil.copytree(self.mp / "plugins/aa", ip)
        cpm.check_plugins(self.ctx, assets)
        aa = [a for a in assets if a.name == "aa"][0]
        self.assertEqual(aa.expected, aa.current)
        self.assertEqual(cpm.row_status(aa, checked=True), "最新")


class TestNoGitContentEqual(unittest.TestCase):
    """官方 update 后 clone 无 .git 是常态：内容一致必须本地判「最新」，不碰远端 API。"""

    def test_equal_content_without_dotgit_no_remote_fallback(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        ctx = fixture.test_ctx(fixture.build_home(Path(td.name)))
        fixture.rm_rf(ctx.marketplaces / fixture.MARKET / ".git")
        assets = cpm.load_plugins(ctx)
        aa = [a for a in assets if a.name == "aa"][0]
        shutil.rmtree(aa.plan["installPath"])
        shutil.copytree(ctx.marketplaces / fixture.MARKET / "plugins/aa",
                        Path(aa.plan["installPath"]))
        fixture.patch_fetch(self, {})          # 任何远端查询都会返回 None → 掉进 fallback 即失败
        cpm.check_plugins(ctx, assets)
        aa = [a for a in assets if a.name == "aa"][0]
        self.assertEqual(cpm.row_status(aa, checked=True), "最新")
        self.assertNotIn("远端 path 提交", aa.notes)


class TestContentBasisVisible(unittest.TestCase):
    """比对结论须出现在行 notes——四行同哈希的困惑根源是没有判定依据可见。"""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.ctx = fixture.test_ctx(fixture.build_home(Path(td.name)))
        self.assets = cpm.load_plugins(self.ctx)

    def test_diff_annotates_change(self):
        cpm.check_plugins(self.ctx, self.assets)
        aa = [a for a in self.assets if a.name == "aa"][0]
        self.assertIn("path 内容较安装版有变化", aa.notes)

    def test_equal_annotates_same(self):
        aa = [a for a in self.assets if a.name == "aa"][0]
        shutil.rmtree(aa.plan["installPath"])
        shutil.copytree(self.ctx.marketplaces / fixture.MARKET / "plugins/aa",
                        Path(aa.plan["installPath"]))
        cpm.check_plugins(self.ctx, self.assets)
        self.assertIn("path 内容与安装版一致", aa.notes)
