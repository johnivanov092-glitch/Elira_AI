"""Canonical validation for results crossing an agent tool boundary."""
from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger(__name__)


def ensure_tool_result(value: Any, *, source: str) -> dict[str, Any]:
    """Return a valid structured result, failing closed on contract drift."""
    if isinstance(value, dict) and isinstance(value.get("ok"), bool):
        return value
    logger.error("%s violated the tool result contract", source)
    return {
        "ok": False,
        "error": "invalid_tool_result",
        "text": "ERROR: tool result contract requires a boolean 'ok' field.",
    }
