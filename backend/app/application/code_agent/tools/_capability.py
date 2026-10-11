from __future__ import annotations

from typing import Any
import json

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
    from app.application.agent_kernel.runtime_context import current_runtime_context

    try:
        visible = json.loads(current_runtime_context().get("visible_builtin_tools", "[]"))
    except (TypeError, ValueError):
        visible = []
    if isinstance(visible, list) and tools.issubset({name for name in visible if isinstance(name, str)}):
        return {
            "ok": True,
            "capability_group": normalized,
            "activated_tools": sorted(tools),
            "already_available": True,
            "text": f"Инструменты группы '{normalized}' уже доступны; загружать её не нужно.",
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
