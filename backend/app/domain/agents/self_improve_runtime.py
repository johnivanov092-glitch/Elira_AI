"""Runtime helpers for the self-improving agent loop."""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List

from app.core.llm import ask_model, clean_code_fence, safe_json_parse


IterationProgressCallback = Callable[[int, str], None] | None


def build_self_improve_critique_prompt(
    *,
    task: str,
    answer: str,
    reflection: Any,
) -> str:
    return (
        "Ты self-improve critic.\n"
        "Верни ТОЛЬКО JSON:\n"
        "{\n"
        '  "improve": true,\n'
        '  "score": 0.0,\n'
        '  "issues": ["..."],\n'
        '  "focus": "что улучшить"\n'
        "}\n\n"
        f"ЗАДАЧА:\n{task}\n\n"
        f"ТЕКУЩИЙ ОТВЕТ:\n{answer[:9000]}\n\n"
        f"REFLECTION:\n{json.dumps(reflection, ensure_ascii=False)}"
    )


def should_self_improve(
    *,
    iteration: int,
    critique: dict[str, Any],
    reflection: Any,
) -> bool:
    should_improve = bool(critique.get("improve", iteration == 1))
    if isinstance(reflection, dict) and (
        reflection.get("needs_retry")
        or not reflection.get("complete", True)
    ):
        should_improve = True
    return should_improve


def build_self_improve_prompt(
    *,
    task: str,
    answer: str,
    critique: dict[str, Any],
    reflection: Any,
) -> str:
    return (
        "Улучши ответ после self-improving loop.\n\n"
        f"Исходная задача:\n{task}\n\n"
        f"Текущий ответ:\n{answer[:9000]}\n\n"
        f"Проблемы / focus:\n{json.dumps(critique, ensure_ascii=False, indent=2)}\n\n"
        f"Reflection:\n{json.dumps(reflection, ensure_ascii=False, indent=2)}\n\n"
        "Требования:\n"
        "- Сделай ответ точнее и практичнее.\n"
        "- Не выдумывай факты.\n"
        "- Если данных не хватает — скажи это явно.\n"
        "- Сохрани сильные части прошлого ответа."
    )


def is_self_improve_complete(reflection: Any) -> bool:
    return (
        isinstance(reflection, dict)
        and reflection.get("complete", True)
        and reflection.get("answered", True)
        and not reflection.get("needs_retry", False)
    )


def run_self_improve_iterations(
    *,
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    max_iters: int = 2,
    run_id: str = "",
    answer: str = "",
    reflection: Any = None,
    progress_callback: IterationProgressCallback = None,
) -> Dict[str, Any]:
    from app.domain.agents.reflection import reflection_v2

    current_answer = (answer or "").strip()
    current_reflection: Any = reflection or {}
    iterations: List[Dict[str, Any]] = []

    for idx in range(1, max(0, int(max_iters)) + 1):
        if progress_callback:
            progress_callback(idx, f"\U0001faa9 Self-Improve {idx}")

        critique_prompt = build_self_improve_critique_prompt(
            task=task,
            answer=current_answer,
            reflection=current_reflection,
        )
        raw_crit = ask_model(
            model_name=model_name,
            profile_name="Аналитик",
            user_input=critique_prompt,
            memory_context="",
            use_memory=True,
            include_history=False,
            temp=0.05,
            num_ctx=min(num_ctx, 4096),
        )
        critique = safe_json_parse(clean_code_fence(raw_crit)) or {}

        if not should_self_improve(
            iteration=idx,
            critique=critique,
            reflection=current_reflection,
        ):
            iterations.append({
                "iteration": idx,
                "changed": False,
                "answer": current_answer,
                "critique": critique,
                "reflection": current_reflection,
            })
            break

        improve_prompt = build_self_improve_prompt(
            task=task,
            answer=current_answer,
            critique=critique,
            reflection=current_reflection,
        )
        improved = ask_model(
            model_name=model_name,
            profile_name="Оркестратор",
            user_input=improve_prompt,
            memory_context="",
            use_memory=True,
            include_history=False,
            temp=0.15,
            num_ctx=num_ctx,
        ).strip() or current_answer

        current_reflection = reflection_v2(
            task=task,
            answer=improved,
            model_name=model_name,
            profile_name=memory_profile,
            num_ctx=num_ctx,
        )
        current_answer = improved
        iterations.append({
            "iteration": idx,
            "changed": True,
            "answer": current_answer,
            "critique": critique,
            "reflection": current_reflection,
        })

        if is_self_improve_complete(current_reflection):
            break

    return {
        "answer": current_answer,
        "iterations": iterations,
        "reflection": current_reflection,
    }
