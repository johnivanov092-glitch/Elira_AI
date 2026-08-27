from __future__ import annotations

import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.tools._files import tool_glob  # noqa: E402
from app.application.code_agent.tools._search import tool_project_map  # noqa: E402


def test_project_map_accepts_an_absolute_directory_outside_project_root() -> None:
    with tempfile.TemporaryDirectory() as project_tmp, tempfile.TemporaryDirectory() as target_tmp:
        project_root = Path(project_tmp)
        target_root = Path(target_tmp)
        (target_root / "README.md").write_text("ABSOLUTE_PATH_CANARY", encoding="utf-8")
        source = target_root / "src"
        source.mkdir()
        (source / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")

        result = tool_project_map(project_root, path=str(target_root))

    assert "ERROR" not in result["text"]
    assert "README.md" in result["text"]
    assert "src/main.py" in result["text"]
    assert "def main" in result["text"]


def test_glob_accepts_an_absolute_pattern_outside_project_root() -> None:
    with tempfile.TemporaryDirectory() as project_tmp, tempfile.TemporaryDirectory() as target_tmp:
        project_root = Path(project_tmp)
        target_root = Path(target_tmp)
        (target_root / "one.txt").write_text("one", encoding="utf-8")
        (target_root / "two.py").write_text("two", encoding="utf-8")

        result = tool_glob(project_root, pattern=str(target_root / "*.txt"))

    assert str(target_root / "one.txt").replace("\\", "/") in result["text"].replace("\\", "/")
