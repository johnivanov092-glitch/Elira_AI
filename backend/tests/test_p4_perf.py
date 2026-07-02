from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


class ProjectContextWalkTest(unittest.TestCase):
    """FIX-25: the open-project file listing prunes blocked dirs and caps at 50."""

    def test_prunes_blocked_dirs_and_caps(self):
        from app.application.chat import context_builder
        with tempfile.TemporaryDirectory() as tmp:
            # a real file, a blocked dir (must NOT appear), and >50 files
            os.makedirs(os.path.join(tmp, "node_modules", "pkg"))
            with open(os.path.join(tmp, "node_modules", "pkg", "index.js"), "w") as fh:
                fh.write("x")
            with open(os.path.join(tmp, "a_real.py"), "w") as fh:  # sorts first
                fh.write("x")
            for i in range(5):
                with open(os.path.join(tmp, f"f{i}.txt"), "w") as fh:
                    fh.write("x")
            with patch("app.application.advanced.runtime._project_path", tmp):
                out = context_builder._build_project_context_from_open_project()
        self.assertIn("a_real.py", out)         # a real top-level file is listed
        self.assertNotIn("node_modules", out)   # blocked dir pruned, never descended
        self.assertLessEqual(out.count("\n- "), 30)  # capped summary


if __name__ == "__main__":
    unittest.main()
