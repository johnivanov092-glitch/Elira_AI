"""Runtime helpers for the self-improving agent loop."""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Tuple

from app.core.llm import ask_model, clean_code_fence, safe_json_parse


IterationProgressCallback = Callable[[int, str], None] | None


def load_self_improve_context(
    *,
    task: str,
    memory_profile: str,
    run_id: str,
    working_context: str = "",
) -> Tuple[str, str, str]:
    # Legacy: pulled mem_ctx + kb_ctx + working_context from memory.db
    # (memories, knowledge_chunks, working_memory tables). All gone.
    # Self-improving agent now runs without those side-channels; chat
    # memory enrichment happens upstream via smart_memory + rag_memory.
    return "", "", working_context or ""


def build_self_improve_combined_context(
    *,
    mem_ctx: str,
    kb_ctx: str,
    working_context: str,
) -> str:
    return (mem_ctx or "") + "\n\n" + (kb_ctx or "") + "\n\n" + (working_context or "")


def build_self_improve_critique_prompt(
    *,
    task: str,
    answer: str,
    reflection: Any,
    combined_context: str,
) -> str:
    return (
        "\u0422\u044b self-improve critic.\n"
        "\u0412\u0435\u0440\u043d\u0438 \u0422\u041e\u041b\u042c\u041a\u041e JSON:\n"
        "{\n"
        '  "improve": true,\n'
        '  "score": 0.0,\n'
        '  "issues": ["..."],\n'
        '  "focus": "\u0447\u0442\u043e \u0443\u043b\u0443\u0447\u0448\u0438\u0442\u044c"\n'
        "}\n\n"
        f"\u0417\u0410\u0414\u0410\u0427\u0410:\n{task}\n\n"
        f"\u0422\u0415\u041a\u0423\u0429\u0418\u0419 \u041e\u0422\u0412\u0415\u0422:\n{answer[:9000]}\n\n"
        f"REFLECTION:\n{json.dumps(reflection, ensure_ascii=False)}\n\n"
        f"\u041a\u041e\u041d\u0422\u0415\u041a\u0421\u0422:\n{combined_context[:9000]}"
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
    mem_ctx: str,
    kb_ctx: str,
    working_context: str,
) -> str:
    return (
        "\u0423\u043b\u0443\u0447\u0448\u0438 \u043e\u0442\u0432\u0435\u0442 \u043f\u043e\u0441\u043b\u0435 self-improving loop.\n\n"
        f"\u0418\u0441\u0445\u043e\u0434\u043d\u0430\u044f \u0437\u0430\u0434\u0430\u0447\u0430:\n{task}\n\n"
        f"\u0422\u0435\u043a\u0443\u0449\u0438\u0439 \u043e\u0442\u0432\u0435\u0442:\n{answer[:9000]}\n\n"
        f"\u041f\u0440\u043e\u0431\u043b\u0435\u043c\u044b / focus:\n{json.dumps(critique, ensure_ascii=False, indent=2)}\n\n"
        f"Reflection:\n{json.dumps(reflection, ensure_ascii=False, indent=2)}\n\n"
        f"\u041a\u043e\u043d\u0442\u0435\u043a\u0441\u0442 \u043f\u0430\u043c\u044f\u0442\u0438:\n{mem_ctx[:4000]}\n\n"
        f"\u041a\u043e\u043d\u0442\u0435\u043a\u0441\u0442 KB:\n{kb_ctx[:3000]}\n\n"
        f"\u0420\u0430\u0431\u043e\u0447\u0430\u044f \u043f\u0430\u043c\u044f\u0442\u044c:\n{working_context[:3000]}\n\n"
        "\u0422\u0440\u0435\u0431\u043e\u0432\u0430\u043d\u0438\u044f:\n"
        "- \u0421\u0434\u0435\u043b\u0430\u0439 \u043e\u0442\u0432\u0435\u0442 \u0442\u043e\u0447\u043d\u0435\u0435 \u0438 \u043f\u0440\u0430\u043a\u0442\u0438\u0447\u043d\u0435\u0435.\n"
        "- \u041d\u0435 \u0432\u044b\u0434\u0443\u043c\u044b\u0432\u0430\u0439 \u0444\u0430\u043a\u0442\u044b.\n"
        "- \u0415\u0441\u043b\u0438 \u0434\u0430\u043d\u043d\u044b\u0445 \u043d\u0435 \u0445\u0432\u0430\u0442\u0430\u0435\u0442 \u2014 \u0441\u043a\u0430\u0436\u0438 \u044d\u0442\u043e \u044f\u0432\u043d\u043e.\n"
        "- \u0421\u043e\u0445\u0440\u0430\u043d\u0438 \u0441\u0438\u043b\u044c\u043d\u044b\u0435 \u0447\u0430\u0441\u0442\u0438 \u043f\u0440\u043e\u0448\u043b\u043e\u0433\u043e \u043e\u0442\u0432\u0435\u0442\u0430."
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
    working_context: str = "",
    progress_callback: IterationProgressCallback = None,
) -> Dict[str, Any]:
    from app.domain.agents.reflection import reflection_v2

    current_answer = (answer or "").strip()
    current_reflection: Any = reflection or {}
    current_working_context = working_context or ""
    iterations: List[Dict[str, Any]] = []

    for idx in range(1, max(0, int(max_iters)) + 1):
        if progress_callback:
            progress_callback(idx, f"\U0001faa9 Self-Improve {idx}")

        mem_ctx, kb_ctx, current_working_context = load_self_improve_context(
            task=task,
            memory_profile=memory_profile,
            run_id=run_id,
            working_context=current_working_context,
        )
        combined_context = build_self_improve_combined_context(
            mem_ctx=mem_ctx,
            kb_ctx=kb_ctx,
            working_context=current_working_context,
        )
        critique_prompt = build_self_improve_critique_prompt(
            task=task,
            answer=current_answer,
            reflection=current_reflection,
            combined_context=combined_context,
        )
        raw_crit = ask_model(
            model_name=model_name,
            profile_name="\u0410\u043d\u0430\u043b\u0438\u0442\u0438\u043a",
            user_input=critique_prompt,
            memory_context=mem_ctx,
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
            item = {
                "iteration": idx,
                "changed": False,
                "answer": current_answer,
                "critique": critique,
                "reflection": current_reflection,
            }
            iterations.append(item)
            # Legacy: record_self_improve_run → memory.db. Gone.
            break

        improve_prompt = build_self_improve_prompt(
            task=task,
            answer=current_answer,
            critique=critique,
            reflection=current_reflection,
            mem_ctx=mem_ctx,
            kb_ctx=kb_ctx,
            working_context=current_working_context,
        )
        improved = ask_model(
            model_name=model_name,
            profile_name="\u041e\u0440\u043a\u0435\u0441\u0442\u0440\u0430\u0442\u043e\u0440",
            user_input=improve_prompt,
            memory_context="\n\n".join(
                x for x in [mem_ctx, kb_ctx, current_working_context] if x.strip()
            ),
            use_memory=True,
            include_history=False,
            temp=0.15,
            num_ctx=num_ctx,
        ).strip() or current_answer

        current_reflection = reflection_v2(
            task=task,
            answer=improved,
            model_name=model_name,
            memory_context="\n\n".join(
                x for x in [mem_ctx, current_working_context] if x.strip()
            ),
            kb_context=kb_ctx,
            profile_name=memory_profile,
            num_ctx=num_ctx,
        )
        current_answer = improved
        item = {
            "iteration": idx,
            "changed": True,
            "answer": current_answer,
            "critique": critique,
            "reflection": current_reflection,
        }
        iterations.append(item)
        # Legacy: record_self_improve_run → memory.db. Gone.

        if is_self_improve_complete(current_reflection):
            break

    return {
        "answer": current_answer,
        "iterations": iterations,
        "reflection": current_reflection,
        "working_context": current_working_context,
    }
