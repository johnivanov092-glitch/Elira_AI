"""Post-processing handlers for the V8 orchestrator graph."""
from __future__ import annotations

from typing import Any, Callable, Dict

from app.core.llm import ask_model


StepLabelCallback = Callable[[str], None]


def build_finalize_prompt(*, task: str) -> str:
    return (
        "Собери финальный "
        "ответ по задаче.\n\n"
        f"Задача:\n{task}\n\n"
        "Требования:\n"
        "- ответ должен "
        "быть конкретным,\n"
        "- не выдумывай "
        "факты,\n"
        "- если данных "
        "мало, так и скажи,\n"
        "- дай следующий "
        "практический шаг."
    )


def handle_reflection_v2(
    state: Dict[str, Any],
    *,
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int,
    progress_callback: StepLabelCallback,
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
        memory_context="",
        kb_context="",
        profile_name=memory_profile,
        num_ctx=num_ctx,
    )
    false_count = count_false_flags(reflection)
    if false_count >= 3 or reflection.get("needs_retry"):
        regenerated = regenerate_answer_from_context(
            task=task,
            model_name=model_name,
            memory_context="",
            kb_context="",
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
    return state


def handle_finalize(
    state: Dict[str, Any],
    *,
    task: str,
    model_name: str,
    num_ctx: int,
    progress_callback: StepLabelCallback,
) -> Dict[str, Any]:
    progress_callback("✅ Final")
    if not state.get("answer", "").strip():
        state["answer"] = ask_model(
            model_name=model_name,
            profile_name="Оркестратор",
            user_input=build_finalize_prompt(task=task),
            memory_context="",
            use_memory=True,
            include_history=False,
            temp=0.15,
            num_ctx=num_ctx,
        )
    return state
