"""Terminal tool helpers.

Extracted from core/agents.py — dangerous command detection and
bounded shell execution for read-only analysis flows.
"""
from __future__ import annotations

import subprocess

from app.core.config import APP_DIR, TERMINAL_BLOCKED


_OUTPUT_LIMIT = 16000


def is_dangerous_command(cmd: str) -> bool:
    low = (cmd or "").lower().strip()
    return any(blocked in low for blocked in TERMINAL_BLOCKED)


def run_terminal(cmd: str, timeout: int = 25) -> str:
    if is_dangerous_command(cmd):
        return f"$ {cmd}\n\nERROR: blocked dangerous command"
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(APP_DIR),
        )
        stdout = (proc.stdout or "")[:_OUTPUT_LIMIT]
        stderr = (proc.stderr or "")[:_OUTPUT_LIMIT]
        return f"$ {cmd}\n\nSTDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"
    except subprocess.TimeoutExpired:
        return f"$ {cmd}\n\nКоманда остановлена по таймауту ({timeout} сек.)"
    except Exception as exc:
        return f"Ошибка терминала: {exc}"
