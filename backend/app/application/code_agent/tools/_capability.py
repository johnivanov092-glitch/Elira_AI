from __future__ import annotations

from typing import Any

from app.application.code_agent.capabilities import (
    CAPABILITY_GROUPS,
    capability_catalog_text,
)


def tool_capability_load(*, group: str) -> dict[str, Any]:
    """Validate one model-selected prompt capability group."""
    normalized = str(group or "").strip().lower()
    tools = CAPABILITY_GROUPS.get(normalized)
    if tools is None:
        return {
            "ok": False,
            "error": "unknown_capability_group",
            "text": (
                f"ERROR: unknown capability group '{normalized}'. Available groups:\n"
                + capability_catalog_text()
            ),
        }
    return {
        "ok": True,
        "capability_group": normalized,
        "activated_tools": sorted(tools),
        "text": (
            f"Capability group '{normalized}' loaded for this run. "
            "Its tool schemas are available on the next model turn."
        ),
    }
