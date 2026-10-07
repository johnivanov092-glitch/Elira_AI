"""Multi-line inline scripts reach the interpreter intact (cmd.exe cuts them at a newline)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from app.application.code_agent.tools._run import _inline_script_argv, tool_run_bash

# As the model writes it: the script starts on a new line, so cmd.exe kept nothing (exit 0, no output).
SCRIPT = "\nprint('первая строка')\nprint('вторая строка')\n"


def test_trailing_stderr_merge_keeps_the_no_shell_path() -> None:
    argv = _inline_script_argv(f'python -c "{SCRIPT}" 2>&1')

    assert argv == ["python", "-c", SCRIPT]
    assert _inline_script_argv(f'python -c "{SCRIPT}" > out.txt') is None
    assert _inline_script_argv('python -c "print(1)" 2>&1') is None  # single line works through the shell


def test_multiline_script_with_stderr_merge_prints_every_line(tmp_path: Path, monkeypatch) -> None:
    # Plain `python`, exactly as the model types it (a quoted full path parses differently in cmd.exe).
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))
    result = tool_run_bash(tmp_path, command=f'python -c "{SCRIPT}" 2>&1')

    assert result.get("ok", True), result
    stdout = result["text"].partition("STDOUT:")[2]  # the text also echoes the command itself
    assert "первая строка" in stdout and "вторая строка" in stdout
