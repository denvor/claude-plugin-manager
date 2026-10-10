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
