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
    # `;`, tool names and `set` inside a quoted program or in data are not cmd syntax.
    'python -c "import sys; sys.exit(1)"',
    'node -e "const head = 1; process.exit(head)"',
    'set PATH=C:\\a;C:\\b&& missing-tool',
    'set "X=v" && missing-tool',
    'set /a N=1 && missing-tool',
    'type catalog.txt', 'dir heads', 'echo sleep',
])
def test_program_data_and_explicit_other_shells_do_not_get_cmd_hints(command):
    assert _cmd_syntax_hint(command) == ""


@pytest.mark.parametrize("command,expected", [
    # Verbatim from runs a14fefa0 (step 11, 28), cdd5f892 (step 122), 7d9e1a7a (step 5).
    ("where node python python3 2>&1 | head -5; node -v 2>&1; python -V 2>&1", "; не разделяет команды"),
    ("where node python python3 2>&1 | head -5; node -v 2>&1; python -V 2>&1", "убери | head"),
    ("npx tsc --noEmit && npm run build 2>&1 | tail -15", "убери | tail"),
    (r'set ELIRA_BACKEND_ROOT=D:\AIWork\Elira_AI\backend && "D:\x\python.exe" vision.py', 'set "ИМЯ=значение"'),
    ('if exist "a.txt" (echo yes) else (echo no); echo after', "; не разделяет команды"),
    ("ls -la", "dir /b"), ("cat notes.txt", "type"), ("rm -rf build", "rmdir /s /q"),
    ("dir /b | grep test", "findstr /i"), ("export X=1", 'set "ИМЯ=значение"'), ("sleep 3", "ping -n"),
])
def test_bash_habits_get_the_cmd_replacement(command, expected, monkeypatch):
    monkeypatch.setattr("app.application.code_agent.tools._run.shutil.which", lambda name: None)
    hint = _cmd_syntax_hint(command)
    assert "Это cmd.exe:" in hint and expected in hint


def test_unix_tool_present_on_this_machine_gets_no_replacement_advice(monkeypatch):
    monkeypatch.setattr("app.application.code_agent.tools._run.shutil.which", lambda name: f"C:/gnu/{name}.exe")
    assert _cmd_syntax_hint("dir /b | grep test") == ""
    assert "убери | head" not in _cmd_syntax_hint("dir /b | head -3; echo done")


@pytest.mark.skipif(os.name != "nt", reason="cmd integration requires Windows")
def test_actual_nonzero_command_gets_hint_and_success_does_not(tmp_path):
    failed = tool_run_bash(tmp_path, command='type nonexistent.txt 2>/dev/null')
    assert failed["ok"] is False and failed["error"] == "nonzero_exit"
    assert "Это cmd.exe:" in failed["text"] and "NUL" in failed["text"]
    successful = tool_run_bash(tmp_path, command='echo /dev/null')
    assert successful["ok"] is True and "Это cmd.exe:" not in successful["text"]


@pytest.mark.skipif(os.name != "nt", reason="cmd integration requires Windows")
def test_actual_bash_style_chain_fails_with_the_separator_hint(tmp_path):
    failed = tool_run_bash(tmp_path, command="dir /b nosuchdir; echo after")
    assert failed["ok"] is False and "; не разделяет команды" in failed["text"]
