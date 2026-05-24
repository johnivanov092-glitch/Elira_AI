"""Answer evaluation, reflection, and regeneration.

Extracted from core/agents.py — reflection_v2 evaluation loop,
reflect_and_improve_answer critic/improver, count_false_flags,
regenerate_answer_from_context, and v8 graph helpers
(get_fallback_node_v8, run_graph_with_retry_v8).
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict

from app.core.llm import ask_model, clean_code_fence, safe_json_parse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe_json_object(text: str) -> dict:
    """Parse *text* as JSON; return {} on any failure."""
    try:
        data = safe_json_parse((text or "").strip())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def count_false_flags(reflection: dict) -> int:
    """Count how many quality checks failed in a reflection result."""
    checks = [
        reflection.get("answered", True),
        reflection.get("grounded", True),
        reflection.get("complete", True),
        reflection.get("actionable", True),
        reflection.get("safe", True),
    ]
    return sum(1 for x in checks if not bool(x))


# ---------------------------------------------------------------------------
# reflect_and_improve_answer — lightweight critic + optional rewrite
# ---------------------------------------------------------------------------

def reflect_and_improve_answer(
    task: str,
    draft: str,
    model_name: str,
    profile_name: str = "\u041e\u0440\u043a\u0435\u0441\u0442\u0440\u0430\u0442\u043e\u0440",
    extra_context: str = "",
    num_ctx: int = 4096,
) -> Dict[str, str]:
    critic_prompt = (
        "\u0422\u044b reflection-critic. \u041a\u043e\u0440\u043e\u0442\u043a\u043e \u043f\u0440\u043e\u0432\u0435\u0440\u044c \u0447\u0435\u0440\u043d\u043e\u0432\u0438\u043a \u043e\u0442\u0432\u0435\u0442\u0430 \u043d\u0430: \u043f\u043e\u043b\u043d\u043e\u0442\u0443, \u0444\u0430\u043a\u0442\u0438\u0447\u0435\u0441\u043a\u0443\u044e \u043e\u043f\u043e\u0440\u0443 \u043d\u0430 \u043a\u043e\u043d\u0442\u0435\u043a\u0441\u0442, "
        "\u043f\u043e\u043b\u0435\u0437\u043d\u043e\u0441\u0442\u044c \u0438 \u043a\u043e\u043d\u043a\u0440\u0435\u0442\u043d\u043e\u0441\u0442\u044c. \u0412\u0435\u0440\u043d\u0438 \u0422\u041e\u041b\u042c\u041a\u041e JSON \u0431\u0435\u0437 markdown: "
        '{"score":0-10,"issues":["..."],"improve":"yes|no","brief":"..."}.\n\n'
        f"\u0417\u0410\u0414\u0410\u0427\u0410:\n{task}\n\n"
        f"\u041a\u041e\u041d\u0422\u0415\u041a\u0421\u0422:\n{extra_context[:6000]}\n\n"
        f"\u0427\u0415\u0420\u041d\u041e\u0412\u0418\u041a:\n{draft[:8000]}"
    )
    raw = ask_model(
        model_name=model_name,
        profile_name="\u0410\u043d\u0430\u043b\u0438\u0442\u0438\u043a",
        user_input=critic_prompt,
        include_history=False,
        temp=0.05,
        num_ctx=min(num_ctx, 4096),
    )
    critique = safe_json_parse(clean_code_fence(raw))
    if not isinstance(critique, dict):
        critique = {
            "score": 7,
            "issues": ["\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0440\u0430\u0441\u043f\u0430\u0440\u0441\u0438\u0442\u044c critique"],
            "improve": "yes",
            "brief": str(raw)[:500],
        }

    improve_flag = (
        str(critique.get("improve", "yes")).lower() == "yes"
        or float(critique.get("score", 7)) < 8
    )
    improved = draft
    if improve_flag:
        improve_prompt = (
            "\u0423\u043b\u0443\u0447\u0448\u0438 \u043e\u0442\u0432\u0435\u0442 \u043f\u043e\u0441\u043b\u0435 self-reflection. \u0421\u043e\u0445\u0440\u0430\u043d\u0438 \u0444\u0430\u043a\u0442\u044b, \u0443\u0431\u0435\u0440\u0438 \u0441\u043b\u0430\u0431\u044b\u0435 \u043c\u0435\u0441\u0442\u0430, \u0441\u0434\u0435\u043b\u0430\u0439 \u043e\u0442\u0432\u0435\u0442 \u0442\u043e\u0447\u043d\u0435\u0435 \u0438 \u043f\u0440\u0430\u043a\u0442\u0438\u0447\u043d\u0435\u0435.\n\n"
            f"\u0417\u0410\u0414\u0410\u0427\u0410:\n{task}\n\n"
            f"\u041a\u041e\u041d\u0422\u0415\u041a\u0421\u0422:\n{extra_context[:6000]}\n\n"
            f"\u0427\u0415\u0420\u041d\u041e\u0412\u0418\u041a:\n{draft[:8000]}\n\n"
            f"CRITIQUE:\n{json.dumps(critique, ensure_ascii=False, indent=2)}"
        )
        improved = ask_model(
            model_name=model_name,
            profile_name=profile_name,
            user_input=improve_prompt,
            include_history=False,
            temp=0.15,
            num_ctx=num_ctx,
        )
    return {
        "draft": draft,
        "final": improved,
        "critique": json.dumps(critique, ensure_ascii=False, indent=2),
    }


# ---------------------------------------------------------------------------
# reflection_v2 — full quality evaluation with structured flags
# ---------------------------------------------------------------------------

def reflection_v2(
    task: str,
    answer: str,
    model_name: str,
    memory_context: str = "",
    kb_context: str = "",
    profile_name: str = "",
    num_ctx: int = 4096,
) -> dict:
    from app.domain.memory.task_tracking import record_reflection

    context = "\n\n".join(x for x in [memory_context, kb_context] if x.strip())
    prompt = (
        "\u0422\u044b evaluator.\n\n"
        "\u0412\u0435\u0440\u043d\u0438 \u0422\u041e\u041b\u042c\u041a\u041e JSON-\u043e\u0431\u044a\u0435\u043a\u0442 \u0444\u043e\u0440\u043c\u0430\u0442\u0430:\n"
        "{\n"
        '  "answered": true,\n'
        '  "grounded": true,\n'
        '  "complete": true,\n'
        '  "actionable": true,\n'
        '  "safe": true,\n'
        '  "needs_retry": false,\n'
        '  "notes": "\u043a\u043e\u0440\u043e\u0442\u043a\u043e\u0435 \u043e\u0431\u044a\u044f\u0441\u043d\u0435\u043d\u0438\u0435",\n'
        '  "improved_answer": "\u0443\u043b\u0443\u0447\u0448\u0435\u043d\u043d\u0430\u044f \u0432\u0435\u0440\u0441\u0438\u044f \u043e\u0442\u0432\u0435\u0442\u0430"\n'
        "}\n\n"
        "\u041f\u0440\u0430\u0432\u0438\u043b\u0430:\n"
        "- answered = \u043e\u0442\u0432\u0435\u0442\u0438\u043b \u043b\u0438 \u043e\u0442\u0432\u0435\u0442 \u043d\u0430 \u0438\u0441\u0445\u043e\u0434\u043d\u0443\u044e \u0437\u0430\u0434\u0430\u0447\u0443.\n"
        "- grounded = \u043e\u043f\u0438\u0440\u0430\u0435\u0442\u0441\u044f \u043b\u0438 \u043e\u0442\u0432\u0435\u0442 \u043d\u0430 \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u044b\u0439 \u043a\u043e\u043d\u0442\u0435\u043a\u0441\u0442.\n"
        "- complete = \u043d\u0435\u0442 \u043b\u0438 \u0437\u0430\u043c\u0435\u0442\u043d\u044b\u0445 \u043f\u0440\u043e\u043f\u0443\u0441\u043a\u043e\u0432.\n"
        "- actionable = \u0435\u0441\u0442\u044c \u043b\u0438 \u043f\u043e\u043b\u0435\u0437\u043d\u044b\u0439 \u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0438\u0439 \u0448\u0430\u0433 \u0438\u043b\u0438 \u043f\u0440\u0430\u043a\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u0432\u044b\u0432\u043e\u0434.\n"
        "- safe = \u043d\u0435\u0442 \u043b\u0438 \u044f\u0432\u043d\u044b\u0445 \u0433\u0430\u043b\u043b\u044e\u0446\u0438\u043d\u0430\u0446\u0438\u0439, \u043e\u043f\u0430\u0441\u043d\u044b\u0445 \u0438\u043b\u0438 \u043d\u0435\u043e\u0431\u043e\u0441\u043d\u043e\u0432\u0430\u043d\u043d\u044b\u0445 \u0443\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u0439.\n"
        "- needs_retry = true, \u0435\u0441\u043b\u0438 \u043e\u0442\u0432\u0435\u0442 \u043d\u0443\u0436\u043d\u043e \u043f\u0435\u0440\u0435\u0441\u043e\u0431\u0440\u0430\u0442\u044c \u0437\u0430\u043d\u043e\u0432\u043e.\n"
        "- improved_answer = \u043b\u0438\u0431\u043e \u0443\u043b\u0443\u0447\u0448\u0435\u043d\u043d\u044b\u0439 \u043e\u0442\u0432\u0435\u0442, \u043b\u0438\u0431\u043e \u0438\u0441\u0445\u043e\u0434\u043d\u044b\u0439, \u0435\u0441\u043b\u0438 \u0443\u043b\u0443\u0447\u0448\u0435\u043d\u0438\u0435 \u043d\u0435 \u0442\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044f.\n\n"
        f"\u0417\u0410\u0414\u0410\u0427\u0410:\n{task}\n\n"
        f"\u041a\u041e\u041d\u0422\u0415\u041a\u0421\u0422:\n{context[:7000]}\n\n"
        f"\u041e\u0422\u0412\u0415\u0422:\n{(answer or '')[:9000]}"
    )
    raw = ask_model(
        model_name=model_name,
        profile_name="\u0410\u043d\u0430\u043b\u0438\u0442\u0438\u043a",
        user_input=prompt,
        memory_context=memory_context,
        use_memory=True,
        include_history=False,
        temp=0.05,
        num_ctx=num_ctx,
    )
    raw = clean_code_fence((raw or "").strip())
    data = safe_json_object(raw)
    result = {
        "answered": bool(data.get("answered", True)),
        "grounded": bool(data.get("grounded", True)),
        "complete": bool(data.get("complete", True)),
        "actionable": bool(data.get("actionable", True)),
        "safe": bool(data.get("safe", True)),
        "needs_retry": bool(data.get("needs_retry", False)),
        "notes": str(data.get("notes", "") or ""),
        "improved_answer": str(data.get("improved_answer", "") or answer or ""),
    }
    try:
        record_reflection(task, answer, result, profile_name=profile_name)
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# regenerate_answer_from_context — full rewrite when reflection fails
# ---------------------------------------------------------------------------

def regenerate_answer_from_context(
    task: str,
    model_name: str,
    memory_context: str = "",
    kb_context: str = "",
    prior_answer: str = "",
    reflection_notes: str = "",
    num_ctx: int = 4096,
) -> str:
    prompt = (
        "\u041f\u0435\u0440\u0435\u0441\u043e\u0431\u0435\u0440\u0438 \u043e\u0442\u0432\u0435\u0442 \u043b\u0443\u0447\u0448\u0435.\n\n"
        f"\u0418\u0441\u0445\u043e\u0434\u043d\u0430\u044f \u0437\u0430\u0434\u0430\u0447\u0430:\n{task}\n\n"
        f"\u041f\u0440\u043e\u0431\u043b\u0435\u043c\u044b \u043f\u0440\u043e\u0448\u043b\u043e\u0433\u043e \u043e\u0442\u0432\u0435\u0442\u0430:\n{reflection_notes}\n\n"
        f"\u041f\u0440\u043e\u0448\u043b\u044b\u0439 \u043e\u0442\u0432\u0435\u0442:\n{prior_answer[:4000]}\n\n"
        "\u0422\u0440\u0435\u0431\u043e\u0432\u0430\u043d\u0438\u044f:\n"
        "- \u0434\u0430\u0439 \u0431\u043e\u043b\u0435\u0435 \u0442\u043e\u0447\u043d\u044b\u0439 \u0438 \u043f\u043e\u043b\u0435\u0437\u043d\u044b\u0439 \u043e\u0442\u0432\u0435\u0442,\n"
        "- \u043d\u0435 \u0432\u044b\u0434\u0443\u043c\u044b\u0432\u0430\u0439 \u0444\u0430\u043a\u0442\u044b,\n"
        "- \u0435\u0441\u043b\u0438 \u0434\u0430\u043d\u043d\u044b\u0445 \u043d\u0435 \u0445\u0432\u0430\u0442\u0430\u0435\u0442 \u2014 \u0441\u043a\u0430\u0436\u0438 \u044d\u0442\u043e \u044f\u0432\u043d\u043e,\n"
        "- \u0435\u0441\u043b\u0438 \u0443\u043c\u0435\u0441\u0442\u043d\u043e, \u0434\u0430\u0439 \u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0438\u0439 \u043f\u0440\u0430\u043a\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u0448\u0430\u0433."
    )
    return ask_model(
        model_name=model_name,
        profile_name="\u041e\u0440\u043a\u0435\u0441\u0442\u0440\u0430\u0442\u043e\u0440",
        user_input=prompt,
        memory_context="\n\n".join(x for x in [memory_context, kb_context] if x.strip()),
        use_memory=True,
        include_history=False,
        temp=0.15,
        num_ctx=num_ctx,
    )


# ---------------------------------------------------------------------------
# V8 graph execution helpers
# ---------------------------------------------------------------------------

def get_fallback_node_v8(node_name: str, state: dict) -> str:
    mapping = {
        "task_graph": "planner",
        "planner": "finalize",
        "reflection_v2": "finalize",
        "finalize": "finalize",
    }
    return mapping.get(node_name, "")


def run_graph_with_retry_v8(
    graph: list,
    handlers: dict,
    state: dict,
    max_retries: int = 2,
) -> dict:
    state.setdefault("errors", [])
    state.setdefault("retries", {})
    state.setdefault("timeline", [])
    for node in graph:
        tries = 0
        while tries <= max_retries:
            started = time.time()
            try:
                state = handlers[node](state)
                elapsed = round(time.time() - started, 3)
                state["timeline"].append({"node": node, "status": "ok", "seconds": elapsed})
                break
            except Exception as e:
                tries += 1
                elapsed = round(time.time() - started, 3)
                state["errors"].append({"node": node, "error": str(e)})
                state["retries"][node] = tries
                state["timeline"].append({
                    "node": node, "status": "error",
                    "seconds": elapsed, "error": str(e),
                })
                fallback = get_fallback_node_v8(node, state)
                if fallback and fallback in handlers and fallback != node:
                    try:
                        state = handlers[fallback](state)
                        state["timeline"].append({"node": fallback, "status": "fallback_ok"})
                        break
                    except Exception as e2:
                        state["errors"].append({"node": fallback, "error": str(e2)})
                        state["timeline"].append({
                            "node": fallback, "status": "fallback_error",
                            "error": str(e2),
                        })
                if tries > max_retries:
                    state["failed_node"] = node
                    return state
    return state
