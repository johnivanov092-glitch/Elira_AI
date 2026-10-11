from __future__ import annotations

import os

import pytest

from app.application.code_agent.tools._run import _cmd_syntax_hint, tool_run_bash


@pytest.mark.parametrize("command,expected", [
    (r'findstr "a\|b" missing.txt', "findstr"),
    ('curl -w "%{http_code}" https://example.org', "проценты"),
    ('unknown-command 2>/dev/null', "NUL"),
    ("type 'missing file.txt'", "одинарные кавычки"),
])
def test_cmd_failures_have_targeted_help(command, expected):
    hint = _cmd_syntax_hint(command)
    assert "Это cmd.exe:" in hint and expected in hint and "powershell.exe -NoProfile -Command" in hint


@pytest.mark.parametrize("command", [
    'python -c "raise ValueError(\'data\')"',
    'powershell.exe -NoProfile -Command "Get-Content \'missing.txt\'"',
    'bash -c "echo x > /dev/null"', 'ssh host "echo x >/dev/null"',
    'dir missing.txt', 'findstr "a b" missing.txt',
])
def test_program_data_and_explicit_other_shells_do_not_get_cmd_hints(command):
    assert _cmd_syntax_hint(command) == ""


@pytest.mark.skipif(os.name != "nt", reason="cmd integration requires Windows")
def test_actual_nonzero_command_gets_hint_and_success_does_not(tmp_path):
    failed = tool_run_bash(tmp_path, command='type nonexistent.txt 2>/dev/null')
    assert failed["ok"] is False and failed["error"] == "nonzero_exit"
    assert "Это cmd.exe:" in failed["text"] and "NUL" in failed["text"]
    successful = tool_run_bash(tmp_path, command='echo /dev/null')
    assert successful["ok"] is True and "Это cmd.exe:" not in successful["text"]
