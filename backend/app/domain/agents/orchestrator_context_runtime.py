"""Context-loading handlers for the V8 orchestrator graph."""
from __future__ import annotations

from typing import Any, Callable, Dict


WorkingMemoryRecorder = Callable[[str, str, str, float], None]
WorkingContextRefresher = Callable[[], None]
StepLabelCallback = Callable[[str], None]


def build_tool_hint_text(preferences: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for pref in preferences:
        tool = pref.get("tool", pref.get("tool_name", "unknown"))
        success_rate = pref.get("success_rate")
        if success_rate is None:
            runs = max(int(pref.get("runs", 0) or pref.get("uses", 0) or 0), 1)
            success_rate = round(float(pref.get("success", 0)) / runs, 2)
        uses = pref.get("uses", pref.get("runs", 0))
        lines.append(f"- {tool}: success_rate={success_rate}, uses={uses}")
    return (
        "\u041f\u0440\u0435\u0434\u043f\u043e\u0447\u0442\u0438\u0442\u0435\u043b\u044c\u043d\u044b\u0435 "
        "\u0438\u043d\u0441\u0442\u0440\u0443\u043c\u0435\u043d\u0442\u044b \u043f\u043e "
        "\u043f\u0440\u043e\u0448\u043b\u043e\u043c\u0443 \u043e\u043f\u044b\u0442\u0443:\n"
        + "\n".join(lines)
    )


def handle_retrieve_memory(
    state: Dict[str, Any],
    *,
    task: str,
    memory_profile: str,
    progress_callback: StepLabelCallback,
    record_working_memory: WorkingMemoryRecorder,
    refresh_working_context: WorkingContextRefresher,
) -> Dict[str, Any]:
    from app.application.memory.context import build_default_memory_context

    progress_callback("\U0001f9e0 \u041f\u0430\u043c\u044f\u0442\u044c")
    state["memory_context"] = build_default_memory_context(
        query=task,
        profile_name=memory_profile,
        top_k=8,
    )
    if state["memory_context"].strip():
        record_working_memory(
            "retrieve_memory",
            "finding",
            state["memory_context"][:2000],
            0.9,
        )
    refresh_working_context()
    return state


def handle_retrieve_kb(
    state: Dict[str, Any],
    *,
    task: str,
    memory_profile: str,
    progress_callback: StepLabelCallback,
    record_working_memory: WorkingMemoryRecorder,
    refresh_working_context: WorkingContextRefresher,
) -> Dict[str, Any]:
    from app.domain.memory.knowledge_base import build_kb_context

    progress_callback("\U0001f4da KB")
    state["kb_context"] = build_kb_context(task, profile_name=memory_profile, top_k=4)
    if state["kb_context"].strip():
        record_working_memory(
            "retrieve_kb",
            "source",
            state["kb_context"][:2000],
            0.85,
        )
    refresh_working_context()
    return state


def handle_retrieve_working_memory(
    state: Dict[str, Any],
    *,
    progress_callback: StepLabelCallback,
    refresh_working_context: WorkingContextRefresher,
) -> Dict[str, Any]:
    progress_callback("\U0001f9e9 Working memory")
    refresh_working_context()
    return state


def handle_tool_hint(
    state: Dict[str, Any],
    *,
    task: str,
    memory_profile: str,
    progress_callback: StepLabelCallback,
    record_working_memory: WorkingMemoryRecorder,
    refresh_working_context: WorkingContextRefresher,
) -> Dict[str, Any]:
    from app.domain.memory.knowledge_base import get_tool_preferences

    progress_callback("\U0001f6e0 Tool memory")
    try:
        preferences = get_tool_preferences(task, profile_name=memory_profile, limit=3)
    except Exception:
        preferences = []
    if preferences:
        state["tool_hint"] = build_tool_hint_text(preferences)
        record_working_memory(
            "tool_hint",
            "decision",
            state["tool_hint"][:1800],
            0.75,
        )
    else:
        state["tool_hint"] = ""
    refresh_working_context()
    return state
