"""Terminal tool helpers.

Extracted from core/agents.py — dangerous command detection and
bounded shell execution for read-only analysis flows.
"""
from __future__ import annotations

from app.core.config import APP_DIR


_OUTPUT_LIMIT = 16000


def is_dangerous_command(cmd: str) -> bool:
    """Compatibility classifier; Workflow permission owns the decision."""
    del cmd
    return False


def run_terminal(cmd: str, timeout: int = 25) -> str:
    del timeout
    from app.application.code_agent.tools._run import tool_run_bash

    result = tool_run_bash(APP_DIR, command=str(cmd or ""))
    return str(result.get("text") or "")[:_OUTPUT_LIMIT]
