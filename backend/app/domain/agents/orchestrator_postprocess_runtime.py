"""Post-processing handlers for the V8 orchestrator graph."""
from __future__ import annotations

import json
from typing import Any, Callable, Dict

from app.core.llm import ask_model


WorkingMemoryRecorder = Callable[[str, str, str, float], None]
WorkingContextRefresher = Callable[[], None]
StepLabelCallback = Callable[[str], None]


def build_reflection_kb_context(state: Dict[str, Any]) -> str:
    return "\n\n".join(
        value
        for value in [state.get("kb_context", ""), state.get("working_context", "")]
        if value.strip()
    )


def build_reflection_memory_context(state: Dict[str, Any]) -> str:
    return "\n\n".join(
        value
        for value in [state.get("memory_context", ""), state.get("working_context", "")]
        if value.strip()
    )


def build_finalize_prompt(*, task: str, state: Dict[str, Any]) -> str:
    return (
        "\u0421\u043e\u0431\u0435\u0440\u0438 \u0444\u0438\u043d\u0430\u043b\u044c\u043d\u044b\u0439 "
        "\u043e\u0442\u0432\u0435\u0442 \u043f\u043e \u0437\u0430\u0434\u0430\u0447\u0435.\n\n"
        f"\u0417\u0430\u0434\u0430\u0447\u0430:\n{task}\n\n"
        f"\u041a\u043e\u043d\u0442\u0435\u043a\u0441\u0442 "
        f"\u043f\u0430\u043c\u044f\u0442\u0438:\n{state.get('memory_context', '')[:5000]}\n\n"
        f"\u041a\u043e\u043d\u0442\u0435\u043a\u0441\u0442 "
        f"KB:\n{state.get('kb_context', '')[:4000]}\n\n"
        f"\u0420\u0430\u0431\u043e\u0447\u0430\u044f "
        f"\u043f\u0430\u043c\u044f\u0442\u044c:\n{state.get('working_context', '')[:4000]}\n\n"
        f"\u041f\u043e\u0434\u0441\u043a\u0430\u0437\u043a\u0430 \u043f\u043e "
        f"\u0438\u043d\u0441\u0442\u0440\u0443\u043c\u0435\u043d\u0442\u0430\u043c:\n"
        f"{state.get('tool_hint', '')[:1500]}\n\n"
        "\u0422\u0440\u0435\u0431\u043e\u0432\u0430\u043d\u0438\u044f:\n"
        "- \u043e\u0442\u0432\u0435\u0442 \u0434\u043e\u043b\u0436\u0435\u043d "
        "\u0431\u044b\u0442\u044c \u043a\u043e\u043d\u043a\u0440\u0435\u0442\u043d\u044b\u043c,\n"
        "- \u043d\u0435 \u0432\u044b\u0434\u0443\u043c\u044b\u0432\u0430\u0439 "
        "\u0444\u0430\u043a\u0442\u044b,\n"
        "- \u0435\u0441\u043b\u0438 \u0434\u0430\u043d\u043d\u044b\u0445 "
        "\u043c\u0430\u043b\u043e, \u0442\u0430\u043a \u0438 \u0441\u043a\u0430\u0436\u0438,\n"
        "- \u0434\u0430\u0439 \u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0438\u0439 "
        "\u043f\u0440\u0430\u043a\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u0448\u0430\u0433."
    )


def handle_reflection_v2(
    state: Dict[str, Any],
    *,
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int,
    progress_callback: StepLabelCallback,
    record_working_memory: WorkingMemoryRecorder,
    refresh_working_context: WorkingContextRefresher,
) -> Dict[str, Any]:
    from app.domain.agents.reflection import (
        count_false_flags,
        reflection_v2,
        regenerate_answer_from_context,
    )

    progress_callback("\U0001faa9 Reflection v2")
    if state.get("selected_strategy") == "self_improve" and state.get("answer", "").strip():
        return state

    reflection = reflection_v2(
        task=task,
        answer=state.get("answer", ""),
        model_name=model_name,
        memory_context=state.get("memory_context", ""),
        kb_context=build_reflection_kb_context(state),
        profile_name=memory_profile,
        num_ctx=num_ctx,
    )
    false_count = count_false_flags(reflection)
    if false_count >= 3 or reflection.get("needs_retry"):
        regenerated = regenerate_answer_from_context(
            task=task,
            model_name=model_name,
            memory_context=build_reflection_memory_context(state),
            kb_context=state.get("kb_context", ""),
            prior_answer=state.get("answer", ""),
            reflection_notes=reflection.get("notes", ""),
            num_ctx=num_ctx,
        )
        state["answer"] = regenerated
        reflection["regenerated"] = True
    else:
        improved = reflection.get("improved_answer", "").strip()
        if improved:
            state["answer"] = improved
        reflection["regenerated"] = False

    state["reflection"] = reflection
    record_working_memory(
        "reflection_v2",
        "decision",
        json.dumps(reflection, ensure_ascii=False)[:2500],
        0.9,
    )
    if state.get("answer", "").strip():
        record_working_memory(
            "reflection_v2",
            "finding",
            state["answer"][:2500],
            0.8,
        )
    refresh_working_context()
    return state


def handle_finalize(
    state: Dict[str, Any],
    *,
    task: str,
    model_name: str,
    num_ctx: int,
    progress_callback: StepLabelCallback,
    record_working_memory: WorkingMemoryRecorder,
    refresh_working_context: WorkingContextRefresher,
) -> Dict[str, Any]:
    progress_callback("\u2705 Final")
    if not state.get("answer", "").strip():
        state["answer"] = ask_model(
            model_name=model_name,
            profile_name="\u041e\u0440\u043a\u0435\u0441\u0442\u0440\u0430\u0442\u043e\u0440",
            user_input=build_finalize_prompt(task=task, state=state),
            memory_context=build_reflection_memory_context(state),
            use_memory=True,
            include_history=False,
            temp=0.15,
            num_ctx=num_ctx,
        )
    if state.get("answer", "").strip():
        record_working_memory("finalize", "finding", state["answer"][:3000], 0.95)
    refresh_working_context()
    return state
