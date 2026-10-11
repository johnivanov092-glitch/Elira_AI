"""Compatibility entry for search callers; instructions choose the batch language."""
from __future__ import annotations

from typing import Callable


def search_with_language_policy(queries: list[str], audience: str, execute: Callable[[], dict]) -> dict:
    return execute()
