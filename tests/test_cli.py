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


class TestCheckUpdateFull(unittest.TestCase):
    def test_all_kinds_rows_present(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        home = fixture.build_home(Path(td.name))
        ctx = fixture.test_ctx(home)
        fixture.patch_run(self, {("claude",): lambda a, c: (0, "", ""),
                                 ("npm",): lambda a, c: (0, "9.9.9", "")})
        fixture.patch_fetch(self, {"releases/latest": {"tag_name": "v9.9.9"}})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cpm.main(["check-update", "--json"], ctx=ctx)
        rows = {r["kind"] for r in json.loads(out.getvalue())}
        self.assertEqual({"plugin", "skill", "mcp"}, rows)
        self.assertEqual(rc, cpm.EXIT_OUTDATED)   # aa/bb/cc/anysearch/gitskill 均可更新，无失败行


class TestUpdateCommand(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.home = fixture.build_home(Path(td.name))
        self.ctx = fixture.test_ctx(self.home)
        self.lines = []

    def test_requires_names_or_all(self):
        out = io.StringIO()
        with contextlib.redirect_stderr(out):
            rc = cpm.main(["update"], ctx=self.ctx)
        self.assertEqual(rc, cpm.EXIT_BROKEN)
        self.assertIn("--all", out.getvalue())

    def test_update_named_dryrun_does_not_execute(self):
        calls = []

        def claude(argv, cwd):
            calls.append(list(argv))
            return (0, "", "")

        fixture.patch_run(self, {("claude",): claude, ("npm",): lambda a, c: (0, "9.9.9", "")})
        fixture.patch_fetch(self, {"releases/latest": {"tag_name": "v9.9.9"}})
        with contextlib.redirect_stdout(io.StringIO()):
            rc = cpm.main(["update", "aa", "--dry-run"], ctx=self.ctx)
        self.assertEqual(rc, cpm.EXIT_OK)
        updates = [c for c in calls if c[:3] == ["claude", "plugin", "update"]]
        self.assertEqual(updates, [])   # dry-run: 只有 marketplace refresh，无真实 update

    def test_update_all_continues_after_failure_and_exits_partial(self):
        def claude(argv, cwd):
            if argv[:3] == ["claude", "plugin", "update"]:
                return (1, "", "boom")          # 所有插件更新失败
            return (0, "", "")                  # marketplace refresh 成功

        # git: pull 走 stub（成功）；log 返回 None 放行真实（plugin_expected 依赖真实仓库）
        fixture.patch_run(self, {("claude",): claude,
                                 ("npm",): lambda a, c: (0, "9.9.9", ""),
                                 ("git",): lambda a, c: (0, "pulled", "") if "pull" in a else None})
        new_skill = b"---\nname: anysearch\nversion: 3.2.0\n---\n"
        fixture.patch_fetch(self, {
            "anysearch-skill/releases/latest": {"tag_name": "v3.2.0"},
            "foo/gitskill/releases/latest": {"tag_name": "v0.2.0"},
            "archive/refs/tags/v3.2.0.zip": fixture.zip_bytes(
                "anysearch-skill-3.2.0", {"SKILL.md": new_skill}),
        })
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cpm.main(["update", "--all"], ctx=self.ctx)
        self.assertEqual(rc, cpm.EXIT_PARTIAL)
        text = out.getvalue()
        self.assertIn("失败 3", text)    # aa/bb/cc 三个插件更新失败
        self.assertIn("anysearch", text)
        # 失败未中断其余：skill 换装实际发生，本地文件保留
        self.assertIn(b"3.2.0", (self.ctx.skills / "anysearch/SKILL.md").read_bytes())
        self.assertEqual((self.ctx.skills / "anysearch/runtime.conf").read_text(),
                         "python3 /opt/cli.py\n")

    def test_unknown_name_reported(self):
        fixture.patch_run(self, {("claude",): lambda a, c: (0, "", ""),
                                 ("npm",): lambda a, c: (0, "0.0", "")})
        fixture.patch_fetch(self, {})
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = cpm.main(["update", "nosuch"], ctx=self.ctx)
        self.assertIn("未找到资产: nosuch", out.getvalue())


class TestAddCommand(unittest.TestCase):
    def test_add_then_skill_becomes_managed(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        home = fixture.build_home(Path(td.name))
        ctx = fixture.test_ctx(home)
        rc = cpm.main(["add", "orphan", "https://github.com/foo/orphan-skill"], ctx=ctx)
        self.assertEqual(rc, cpm.EXIT_OK)
        data = json.loads(ctx.cpm_json.read_text())
        self.assertEqual(data["skills"]["orphan"]["source"], "https://github.com/foo/orphan-skill")
        o = {a.name: a for a in cpm.load_skills(ctx)}["orphan"]
        self.assertTrue(o.managed)
        self.assertEqual(o.source, "foo/orphan-skill")
