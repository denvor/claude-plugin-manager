import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cpm
from tests import fixture


class TestSkillDiscovery(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = fixture.build_home(Path(self._td.name))
        self.ctx = fixture.test_ctx(self.home)
        self.by = {a.name: a for a in cpm.load_skills(self.ctx)}
        self.addCleanup(self._td.cleanup)

    def test_readme_pattern_source(self):
        a = self.by["anysearch"]
        self.assertEqual(a.source, "anysearch-ai/anysearch-skill")
        self.assertEqual(a.current, "3.1.1")           # frontmatter
        self.assertTrue(a.managed)
        self.assertEqual(a.plan["type"], "zip")        # 无 .git → release 换装
        self.assertEqual(Path(a.dir).name, "anysearch")

    def test_git_config_source(self):
        g = self.by["gitskill"]
        self.assertEqual(g.source, "foo/gitskill")     # remote url 去 .git 后缀
        self.assertEqual(g.plan["type"], "git")
        self.assertEqual(g.current, "0.1.0")

    def test_orphan_unmanaged_with_hint(self):
        o = self.by["orphan"]
        self.assertFalse(o.managed)
        self.assertIsNone(o.plan)
        self.assertIn("cpm add", o.notes)

    def test_override_wins(self):
        self.ctx.cpm_json.write_text(json.dumps(
            {"skills": {"orphan": {"source": "https://github.com/foo/orphan-skill"}}}))
        o = {a.name: a for a in cpm.load_skills(self.ctx)}["orphan"]
        self.assertTrue(o.managed)
        self.assertEqual(o.source, "foo/orphan-skill")

    def test_ambiguous_repos_flagged(self):
        d = self.ctx.skills / "ambig"
        d.mkdir()
        (d / "README.md").write_text(
            "Install from https://github.com/a/one and https://github.com/b/two\n")
        a = {x.name: x for x in cpm.load_skills(self.ctx)}["ambig"]
        self.assertFalse(a.managed)
        self.assertIn("歧义", a.notes)


class TestSkillCheck(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = fixture.build_home(Path(self._td.name))
        self.ctx = fixture.test_ctx(self.home)
        self.addCleanup(self._td.cleanup)

    def test_releases_latest_and_degrade_to_tags(self):
        fixture.patch_fetch(self, {
            "anysearch-skill/releases/latest": {"tag_name": "v3.2.0"},
            "foo/gitskill/releases/latest": {"message": "Not Found"},
            "foo/gitskill/tags": [{"name": "v0.2.0"}, {"name": "v0.1.0"}],
        })
        assets = cpm.load_skills(self.ctx)
        cpm.check_skills(self.ctx, assets)
        by = {a.name: a for a in assets}
        self.assertEqual(by["anysearch"].expected, "v3.2.0")
        self.assertEqual(by["gitskill"].expected, "v0.2.0")   # 退化到 tags
        self.assertIsNone(by["orphan"].expected)              # 不管理，跳过

    def test_check_update_rows(self):
        fixture.patch_fetch(self, {"releases/latest": {"tag_name": "v9.9.9"}})
        assets = cpm.load_assets(self.ctx)
        cpm.check_skills(self.ctx, assets)
        by = {a.name: a for a in assets}
        self.assertTrue(cpm.outdated(by["anysearch"].current, by["anysearch"].expected))
        self.assertEqual(cpm.row_status(by["orphan"], checked=True), "不管理")


class TestSkillUpdate(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = fixture.build_home(Path(self._td.name))
        self.ctx = fixture.test_ctx(self.home)
        self.lines = []
        self.last_call = None
        self.addCleanup(self._td.cleanup)

    def _record(self, argv):
        self.last_call = list(argv)
        return (0, "", "")

    def _skill(self, name):
        return {a.name: a for a in cpm.load_skills(self.ctx)}[name]

    def test_git_flow_success_divergence_and_dryrun(self):
        g = self._skill("gitskill")
        seen = []

        def git(argv, cwd):
            seen.append(list(argv))
            return (0, "更新完成", "") if "pull" in argv else (0, "", "")

        fixture.patch_run(self, {("git",): git})
        ok, msg = cpm.update_asset(self.ctx, g, dry_run=False, out=self.lines.append)
        self.assertTrue(ok)
        self.assertEqual(seen[0][2:], [g.dir, "pull", "--ff-only"])
        # 分叉：pull 失败 → False，不动目录
        fixture.patch_run(self, {("git",): lambda a, c: (1, "", "fatal: Not possible to fast-forward")})
        ok, msg = cpm.update_asset(self.ctx, g, dry_run=False, out=self.lines.append)
        self.assertFalse(ok)
        self.assertIn("fast-forward", msg)
        # dry-run 不执行
        seen2 = []
        fixture.patch_run(self, {("git",): lambda a, c: seen2.append(a) or (0, "", "")})
        ok, _ = cpm.update_asset(self.ctx, g, dry_run=True, out=self.lines.append)
        self.assertTrue(ok)
        self.assertEqual(seen2, [])

    def test_zip_flow_updates_and_preserves_local_files(self):
        a = self._skill("anysearch")
        a.expected = "v3.2.0"
        new_skill = b"---\nname: anysearch\nversion: 3.2.0\n---\n# new\n"
        fixture.patch_fetch(self, {
            "archive/refs/tags/v3.2.0.zip": fixture.zip_bytes(
                "anysearch-skill-3.2.0",
                {"SKILL.md": new_skill, "scripts/x.py": b"print(1)\n"}),
        })
        ok, msg = cpm.update_asset(self.ctx, a, dry_run=False, out=self.lines.append)
        self.assertTrue(ok, msg)
        d = Path(a.dir)
        self.assertIn(b"3.2.0", (d / "SKILL.md").read_bytes())          # 新版落地
        self.assertEqual((d / "runtime.conf").read_text(), "python3 /opt/cli.py\n")  # gitignore 文件保留
        self.assertFalse((d.parent / (d.name + "__cpm_bak")).exists())  # 备份清干净
        self.assertIn(".gitignore", [p.name for p in d.iterdir()])      # 新包没带就回补旧的

    def test_zip_checksum_failure_leaves_install_untouched(self):
        a = self._skill("anysearch")
        a.expected = "v3.2.0"
        # 手工构造内容与 SHA256SUMS 不一致的包（不能用 fixture.zip_bytes——它生成的和恒正确）
        import io as _io
        import zipfile as _zf
        buf = _io.BytesIO()
        with _zf.ZipFile(buf, "w") as z:
            z.writestr("anysearch-skill-3.2.0/SKILL.md", b"tampered\n")
            z.writestr("anysearch-skill-3.2.0/SHA256SUMS.txt",
                       "0" * 64 + "  SKILL.md\n")
        fixture.patch_fetch(self, {"archive/refs/tags/v3.2.0.zip": buf.getvalue()})
        ok, msg = cpm.update_asset(self.ctx, a, dry_run=False, out=self.lines.append)
        self.assertFalse(ok)
        self.assertIn("SHA256", msg)
        self.assertIn(b"3.1.1", (Path(a.dir) / "SKILL.md").read_bytes())  # 现装未动

    def test_zip_dryrun_no_network(self):
        a = self._skill("anysearch")
        a.expected = "v3.2.0"
        fixture.patch_fetch(self, {})   # 任何请求返回 None；dry-run 不应触网
        ok, msg = cpm.update_asset(self.ctx, a, dry_run=True, out=self.lines.append)
        self.assertTrue(ok)
        self.assertIn("SHA256SUMS", self.lines[-1])

    def test_plugin_and_print_plans_via_update_asset(self):
        p = cpm.Asset(kind="plugin", name="aa", plan={"type": "claude-plugin",
                                                      "id": "aa@claude-plugins-official"})
        fixture.patch_run(self, {("claude",): lambda a, c: self._record(a) or (0, "", "")})
        ok, _ = cpm.update_asset(self.ctx, p, dry_run=False, out=self.lines.append)
        self.assertTrue(ok)
        self.assertEqual(self.last_call, ["claude", "plugin", "update", "aa@claude-plugins-official"])
        m = cpm.Asset(kind="mcp", name="codegraph", managed=True,
                      plan={"type": "print", "pkg": "x", "cmd": "sudo npm i -g x@1.2"})
        ok, msg = cpm.update_asset(self.ctx, m, dry_run=False, out=self.lines.append)
        self.assertTrue(ok)
        self.assertIn("sudo npm i -g x@1.2", self.lines[-1])   # 只打印，不执行


class TestSkillNoReleaseFallback(unittest.TestCase):
    """真实案例：op7418/Humanizer-zh 无 release 也无 tag（纯分支发布）→ 退化 API commits/HEAD。
    本环境 github.com:443 不可达而 api.github.com 可达，ls-remote 不可用（真机实证）。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = fixture.build_home(Path(self._td.name))
        self.ctx = fixture.test_ctx(self.home)
        self.addCleanup(self._td.cleanup)

    def test_git_skill_falls_back_to_api_head(self):
        fixture.patch_fetch(self, {   # 无 release 无 tag；commits/HEAD 返回默认分支 sha
            "foo/gitskill/commits/HEAD": {"sha": "d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8091a2b3c4d"},
        })
        assets = cpm.load_skills(self.ctx)
        cpm.check_skills(self.ctx, assets)
        by = {a.name: a for a in assets}
        self.assertEqual(by["gitskill"].expected, "d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8091a2b3c4d")
        self.assertFalse(cpm.outdated("d4e5f6a", by["gitskill"].expected))  # 短前缀=最新
        self.assertTrue(cpm.outdated("f4518a8", by["gitskill"].expected))   # 落后=可更新
        # zip 型不做分支兜底（换装依赖 release tag，无 tag 等于无法更新）
        self.assertIsNone(by["anysearch"].expected)

    def test_api_head_failure_stays_question_with_note(self):
        fixture.patch_fetch(self, {})   # 全部网络失败
        assets = cpm.load_skills(self.ctx)
        cpm.check_skills(self.ctx, assets)
        g = {a.name: a for a in assets}["gitskill"]
        self.assertIsNone(g.expected)
        self.assertIn("无 release/tag", g.notes)
