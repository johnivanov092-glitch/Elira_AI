"""glob says when its list is cut (review defect 5578cf58f458).

The loop treats glob output as the complete file set; a silent cut at 200 let the
model conclude "the file does not exist".
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.tools._files import tool_glob  # noqa: E402


def test_glob_marks_an_incomplete_list(tmp_path: Path) -> None:
    for index in range(205):
        (tmp_path / f"file_{index:03}.txt").write_text("x", encoding="utf-8")
    result = tool_glob(tmp_path, pattern="*.txt")
    lines = result["text"].splitlines()
    assert result["ok"] is True and result["truncated"] is True and result["total"] == 205
    assert len(lines) == 201 and lines[0] == "file_000.txt"
    assert "200 из 205" in lines[-1] and "НЕ полный" in lines[-1]


def test_glob_complete_list_has_no_notice(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    result = tool_glob(tmp_path, pattern="*.txt")
    assert result == {"ok": True, "text": "a.txt", "total": 1}
