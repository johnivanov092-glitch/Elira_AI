"""Shared text-output truncation (single source of truth).

These were copy-pasted across tools, and the copies DRIFTED — a silent bug of the
exact kind this consolidation prevents: ssh_provider's `_truncate_for_llm` had
quietly become a head-only cut that dropped the END of the output (the exit code /
last error — the most useful part of a command result), while loop_helpers' kept
the head AND tail. One implementation now; a fix or tweak lands once.
"""
from __future__ import annotations


def truncate_middle(text: str, limit: int) -> str:
    """Keep the head AND the tail, drop the middle. For command / tool output the
    exit code and last error at the bottom matter as much as the start."""
    if len(text) <= limit:
        return text
    budget = max(400, limit - 100)
    head_size = int(budget * 0.65)
    tail_size = budget - head_size
    removed = len(text) - head_size - tail_size
    return (
        text[:head_size]
        + f"\n[... truncated {removed} chars from middle ...]\n"
        + text[-tail_size:]
    )


def truncate_text(text: str, max_chars: int = 12000) -> str:
    """Head-only cut for page/search text shown to the model, with a visible mark."""
    text = (text or "").strip()
    return text if len(text) <= max_chars else text[:max_chars] + "\n\n[Текст обрезан]"
