import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cpm
from tests import fixture


class TestScaffold(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = fixture.build_home(Path(self._td.name))
        self.addCleanup(self._td.cleanup)

    def test_ctx_paths_point_into_fake_home(self):
        ctx = cpm.Ctx(self.home, node_modules=self.home / "node_modules")
        self.assertTrue(ctx.plugins_json.exists())
        self.assertTrue(ctx.marketplaces.exists())
        self.assertTrue(ctx.claude_json.exists())
        self.assertFalse(ctx.cpm_json.exists())  # 尚未 add 过

    def test_missing_state_file_raises_broken(self):
        ctx = cpm.Ctx(self.home / "nope", node_modules=self.home / "node_modules")
        with self.assertRaises(cpm.Broken):
            cpm.load_json(ctx.plugins_json)

    def test_load_json_corrupt_raises_broken(self):
        bad = self.home / ".claude.json"
        bad.write_text("{oops")
        with self.assertRaises(cpm.Broken):
            cpm.load_json(bad)
