"""Terminal exec boundary: the blocklist rejects destructive commands and the
single source of truth is app.core.config.TERMINAL_BLOCKED."""
from __future__ import annotations

import pytest

from app.application.terminal import runtime as terminal_runtime
from app.core.config import TERMINAL_BLOCKED


def test_blocklist_is_centralised():
    # Consolidation guard: runtime must reference the config list, not a copy.
    assert terminal_runtime.BLOCKED is TERMINAL_BLOCKED


def test_empty_command_is_rejected():
    result = terminal_runtime.exec_command("   ")
    assert result["ok"] is False


@pytest.mark.parametrize("blocked", TERMINAL_BLOCKED)
def test_each_blocked_command_is_refused(blocked: str):
    # The substring guard must refuse the literal blocked command without
    # spawning a subprocess (returns ok=False early).
    result = terminal_runtime.exec_command(blocked)
    assert result["ok"] is False
    assert "error" in result


def test_blocked_substring_is_caught_case_insensitively():
    result = terminal_runtime.exec_command("RM -RF /")
    assert result["ok"] is False


def test_benign_command_runs():
    result = terminal_runtime.exec_command("echo elira_boundary_check")
    assert result["ok"] is True
    assert "elira_boundary_check" in result.get("stdout", "")
