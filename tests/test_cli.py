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
