"""Pseudo-call arguments keep Windows paths and Cyrillic intact (review defect 83b841a4798a).

`unicode_escape` decoded UTF-8 as Latin-1 (Cyrillic -> mojibake), turned `C:\\temp\\notes`
into TAB/LF and raised on `C:\\Users` (truncated \\U escape), ending the whole run.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import pytest  # noqa: E402

from app.application.code_agent.inline_tool_calls import _parse_call_expr_args  # noqa: E402


@pytest.mark.parametrize(("source", "expected"), [
    (r'path="C:\Users\Root\a.txt"', r"C:\Users\Root\a.txt"),
    (r'path="C:\temp\notes.txt"', r"C:\temp\notes.txt"),
    (r'path="D:\\Проект\\a.txt"', r"D:\Проект\a.txt"),
    (r'path="D:\Проект\отчёт.docx"', r"D:\Проект\отчёт.docx"),
    (r'project_path="C:\new\report.docx"', r"C:\new\report.docx"),
])
def test_path_arguments_keep_backslashes_and_cyrillic(source, expected):
    key = source.split("=", 1)[0]
    assert _parse_call_expr_args(source) == {key: expected}


def test_command_with_windows_path_is_literal():
    assert _parse_call_expr_args(r'command="type C:\temp\new.txt"') == {"command": r"type C:\temp\new.txt"}


def test_text_arguments_still_understand_common_escapes():
    parsed = _parse_call_expr_args(r'content="строка1\nстрока2\tконец", path="x.txt"')
    assert parsed == {"content": "строка1\nстрока2\tконец", "path": "x.txt"}
    assert _parse_call_expr_args(r'content="say \"hi\" \u0416"') == {"content": 'say "hi" Ж'}
    assert _parse_call_expr_args(r'content="keep \q as is"') == {"content": r"keep \q as is"}
