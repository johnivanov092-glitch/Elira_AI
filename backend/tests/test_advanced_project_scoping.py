"""The advanced-project read ops accept an explicit `project_root` so the
code-agent drawer (scoped to its own path) stays independent of the chat's
globally-open project."""
from __future__ import annotations

import importlib
import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.advanced import runtime  # noqa: E402


def _seed(d: str, name: str) -> str:
    (Path(d) / f"{name}.py").write_text("x = 1\n", encoding="utf-8")
    return d


def test_explicit_root_does_not_touch_global_open_project():
    importlib.reload(runtime)
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        _seed(a, "alpha")
        _seed(b, "beta")

        # Chat opens project A globally.
        assert runtime.open_project(a)["ok"]
        assert runtime.get_project_info()["path"] == str(Path(a).resolve())

        # Code-agent drawer reads project B by explicit root — must see B…
        tree_b = runtime.project_tree(project_root=b)
        names = {it["name"] for it in tree_b["items"]}
        assert "beta.py" in names and "alpha.py" not in names

        # …and the global open project is STILL A (not clobbered).
        assert runtime.get_project_info()["path"] == str(Path(a).resolve())
        tree_global = runtime.project_tree()
        gnames = {it["name"] for it in tree_global["items"]}
        assert "alpha.py" in gnames and "beta.py" not in gnames


def test_explicit_root_read_file_scoped():
    importlib.reload(runtime)
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        (Path(a) / "f.py").write_text("AAA", encoding="utf-8")
        (Path(b) / "f.py").write_text("BBB", encoding="utf-8")
        runtime.open_project(a)
        # explicit root b -> BBB; global -> AAA
        assert runtime.read_project_file("f.py", project_root=b)["content"] == "BBB"
        assert runtime.read_project_file("f.py")["content"] == "AAA"


def test_bad_explicit_root_errors_without_touching_global():
    importlib.reload(runtime)
    with tempfile.TemporaryDirectory() as a:
        runtime.open_project(a)
        res = runtime.project_tree(project_root="/no/such/dir/zzz")
        assert res["ok"] is False
        # global still intact
        assert runtime.get_project_info()["path"] == str(Path(a).resolve())
