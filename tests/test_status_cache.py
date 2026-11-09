import contextlib
import io
import json
import shutil
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


def checked_ctx(test):
    """跑完一次 check-update（网络已注入），返回 ctx——缓存已落盘。"""
    ctx = make_ctx(test)
    fixture.patch_run(test, {("claude",): lambda a, c: (0, "", ""),
                             ("npm",): lambda a, c: (0, "9.9.9", "")})
    fixture.patch_fetch(test, {"releases/latest": {"tag_name": "v9.9.9"}})
    with contextlib.redirect_stdout(io.StringIO()):
        cpm.main(["check-update"], ctx=ctx)
    return ctx


class TestCheckUpdateWritesCache(unittest.TestCase):
    """check-update 完成后须把比对结果落盘，供离线 status 复用。"""

    def setUp(self):
        self._probe = cpm._allow_scripts_probe
        cpm._allow_scripts_probe = False
        self.addCleanup(setattr, cpm, "_allow_scripts_probe", self._probe)
        self.ctx = checked_ctx(self)

    def test_cache_written_with_keys_status_expected(self):
        d = json.loads(self.ctx.cpm_cache.read_text(encoding="utf-8"))
        self.assertRegex(d["checked_at"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        # plugin 用 marketplace id 作键（不同 marketplace 可重名）
        aa = d["assets"][f"plugin:aa@{fixture.MARKET}"]
        self.assertEqual(aa["status"], "可更新")
        self.assertNotEqual(aa["expected"], aa["current"])
        self.assertIn(f"plugin:bb@{fixture.MARKET}", d["assets"])
        self.assertEqual(d["assets"][f"plugin:cc@{fixture.MARKET}"]["status"], "可更新")
        self.assertEqual(d["assets"]["skill:anysearch"]["status"], "可更新")
        self.assertEqual(d["assets"]["skill:orphan"]["status"], "不管理")
        self.assertEqual(d["assets"]["mcp:codegraph"]["status"], "可更新")

    def test_json_output_also_writes_cache(self):
        ctx = make_ctx(self)
        fixture.patch_run(self, {("claude",): lambda a, c: (0, "", ""),
                                 ("npm",): lambda a, c: (0, "9.9.9", "")})
        fixture.patch_fetch(self, {"releases/latest": {"tag_name": "v9.9.9"}})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cpm.main(["check-update", "--json"], ctx=ctx)
        rows = json.loads(out.getvalue())
        cached = json.loads(ctx.cpm_cache.read_text(encoding="utf-8"))["assets"]
        self.assertEqual(len(rows), len(cached))


class TestStatusCacheDisplay(unittest.TestCase):
    """status 离线读缓存：命中行显示上次判定，未命中仍是「待检测」。"""

    def setUp(self):
        self._probe = cpm._allow_scripts_probe
        cpm._allow_scripts_probe = False
        self.addCleanup(setattr, cpm, "_allow_scripts_probe", self._probe)
        self.ctx = checked_ctx(self)

    def test_status_shows_cached_verdict_and_footer(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cpm.main(["status"], ctx=self.ctx)
        self.assertEqual(rc, cpm.EXIT_OK)
        text = out.getvalue()
        self.assertIn("可更新", text)
        self.assertIn("->", text)
        d = json.loads(self.ctx.cpm_cache.read_text(encoding="utf-8"))
        self.assertIn(d["checked_at"], text)      # 表尾注明快照时间

    def test_status_displays_cached_verdict_verbatim(self):
        # 手改缓存判定 → status 照显示，证明它读缓存而不是自行推断
        p = self.ctx.cpm_cache
        d = json.loads(p.read_text(encoding="utf-8"))
        d["assets"][f"plugin:aa@{fixture.MARKET}"]["status"] = "最新"
        p.write_text(json.dumps(d), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cpm.main(["status"], ctx=self.ctx)
        line = [l for l in out.getvalue().splitlines() if "plugin aa" in l][0]
        self.assertIn("最新", line)
        self.assertNotIn("可更新", line)

    def test_uncached_asset_shows_pending(self):
        p = self.ctx.cpm_cache                        # 模拟检查后新装、缓存未收录的资产
        d = json.loads(p.read_text(encoding="utf-8"))
        del d["assets"]["skill:gitskill"]
        p.write_text(json.dumps(d), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cpm.main(["status"], ctx=self.ctx)
        line = [l for l in out.getvalue().splitlines() if "gitskill" in l][0]
        self.assertIn("待检测", line)

    def test_no_cache_is_unchanged(self):
        ctx = make_ctx(self)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cpm.main(["status"], ctx=ctx)
        text = out.getvalue()
        self.assertIn("待检测", text)
        self.assertNotIn("->", text)
        self.assertNotIn("check-update", text)          # 无缓存不注脚

    def test_corrupt_cache_treated_as_absent(self):
        self.ctx.cpm_cache.write_text("{oops", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cpm.main(["status"], ctx=self.ctx)
        self.assertEqual(rc, cpm.EXIT_OK)               # 缓存坏不掉 status
        self.assertIn("待检测", out.getvalue())

    def test_cached_managed_drives_row_shape(self):
        # 快照时刻的可管理性决定箭头形态（orphan 现在不可管，快照里假设可管 → 仍带箭头）
        p = self.ctx.cpm_cache
        d = json.loads(p.read_text(encoding="utf-8"))
        d["assets"]["skill:orphan"]["managed"] = True
        p.write_text(json.dumps(d), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cpm.main(["status"], ctx=self.ctx)
        line = [l for l in out.getvalue().splitlines() if "orphan" in l][0]
        self.assertIn("->", line)

    def test_removed_asset_cache_entry_ignored(self):
        shutil.rmtree(self.ctx.skills / "gitskill")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cpm.main(["status"], ctx=self.ctx)
        self.assertEqual(rc, cpm.EXIT_OK)
        self.assertNotIn("gitskill", out.getvalue())


class TestUpdateInvalidatesCache(unittest.TestCase):
    """update 成功改动资产后缓存必须失效——否则 status 继续显示刚被更新的「可更新」。"""

    def setUp(self):
        self._probe = cpm._allow_scripts_probe
        cpm._allow_scripts_probe = False
        self.addCleanup(setattr, cpm, "_allow_scripts_probe", self._probe)
        self.ctx = checked_ctx(self)

    def test_successful_update_removes_cache_and_status_reverts(self):
        fixture.patch_run(self, {("claude",): lambda a, c: (0, "", ""),
                                 ("npm",): lambda a, c: (0, "9.9.9", "")})
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            cpm.main(["update", "aa"], ctx=self.ctx)
        self.assertFalse(self.ctx.cpm_cache.exists())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cpm.main(["status"], ctx=self.ctx)
        self.assertIn("待检测", out.getvalue())         # 缓存已清，回离线待检测

    def test_failed_update_keeps_cache(self):
        def claude(argv, cwd):
            if argv[:3] == ["claude", "plugin", "update"]:
                return (1, "", "boom")
            return (0, "", "")                          # marketplace 刷新成功
        fixture.patch_run(self, {("claude",): claude,
                                 ("npm",): lambda a, c: (0, "9.9.9", "")})
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            cpm.main(["update", "aa"], ctx=self.ctx)
        self.assertTrue(self.ctx.cpm_cache.exists())    # 什么都没改，缓存仍有效

    def test_dry_run_keeps_cache(self):
        fixture.patch_run(self, {("claude",): lambda a, c: (0, "", ""),
                                 ("npm",): lambda a, c: (0, "9.9.9", "")})
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            cpm.main(["update", "aa", "--dry-run"], ctx=self.ctx)
        self.assertTrue(self.ctx.cpm_cache.exists())
