from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from app.services.identity_guard import guard_identity_response
from app.services.provenance_guard import guard_provenance_response


_EXEC_TRIGGERS = [
    "запусти",
    "посчитай",
    "вычисли",
    "выполни",
    "рассчитай",
    "run",
    "execute",
    "calculate",
    "compute",
]


def apply_identity_guard(
    *,
    user_input: str,
    answer_text: str,
    append_timeline_func: Callable[[list[dict[str, Any]], str, str, str, str], None] | None = None,
    timeline: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    guard = guard_identity_response(user_input, answer_text, persona_name="Elira")
    if guard.get("changed") and append_timeline_func and timeline is not None:
        append_timeline_func(
            timeline,
            "identity_guard",
            "Идентичность Elira",
            "done",
            guard.get("reason", "identity_rewrite"),
        )
    return guard


def apply_provenance_guard(
    *,
    user_input: str,
    answer_text: str,
    append_timeline_func: Callable[[list[dict[str, Any]], str, str, str, str], None] | None = None,
    timeline: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    guard = guard_provenance_response(user_input, answer_text)
    if guard.get("changed") and append_timeline_func and timeline is not None:
        append_timeline_func(
            timeline,
            "provenance_guard",
            "Ответ без служебных источников",
            "done",
            guard.get("reason", "source_hidden"),
        )
    return guard


def maybe_auto_exec_python(
    *,
    user_input: str,
    answer: str,
    enabled: bool = True,
    append_timeline_func: Callable[[list[dict[str, Any]], str, str, str, str], None] | None = None,
    timeline: list[dict[str, Any]] | None = None,
) -> str:
    if not enabled:
        return answer

    query_lower = user_input.lower()
    if not any(trigger in query_lower for trigger in _EXEC_TRIGGERS):
        return answer

    match = re.search(r"```python\n([\s\S]*?)```", answer)
    if not match:
        return answer

    code = match.group(1).strip()
    if not code or len(code) < 10:
        return answer

    try:
        from app.services.python_runner import execute_python

        result = execute_python(code)
        if append_timeline_func and timeline is not None:
            append_timeline_func(
                timeline,
                "auto_exec",
                "Python exec",
                "done" if result.get("ok") else "error",
                "",
            )

        parts = ["\n\n**Результат выполнения:**"]
        if result.get("ok"):
            if result.get("stdout"):
                parts.append("```\n" + result["stdout"].strip() + "\n```")
            if result.get("locals"):
                vars_str = ", ".join(f"{key}={value}" for key, value in result["locals"].items())
                parts.append(f"Переменные: `{vars_str}`")
            if not result.get("stdout") and not result.get("locals"):
                parts.append("✓ Код выполнен без вывода")
        else:
            parts.append(f"❌ Ошибка: `{result.get('error', 'Unknown')}`")

        return answer + "\n".join(parts)
    except Exception:
        return answer


@dataclass
class GuardedResponse:
    """Result of applying all post-processing guards to LLM output."""

    text: str
    identity_guard: dict[str, Any]
    provenance_guard: dict[str, Any]
    changed: bool


def apply_response_guards(
    *,
    raw_user_input: str,
    text: str,
    timeline: list[dict[str, Any]],
    use_python_exec: bool = True,
    use_file_gen: bool = True,
    append_timeline_func: Callable | None = None,
    maybe_generate_files_func: Callable | None = None,
) -> GuardedResponse:
    """Run auto-exec, file gen, identity guard, and provenance guard.

    Shared pipeline used by both synchronous and streaming orchestrators.
    """
    original_text = text

    # Auto-execute Python snippets
    try:
        text = maybe_auto_exec_python(
            user_input=raw_user_input,
            answer=text,
            enabled=use_python_exec,
            append_timeline_func=append_timeline_func,
            timeline=timeline,
        )
    except Exception:
        pass

    # Post-generate Word/Excel files from LLM answer
    if maybe_generate_files_func:
        post_files = maybe_generate_files_func(raw_user_input, text, enabled=use_file_gen)
        if post_files:
            text += post_files

    pre_guard_text = text

    identity_guard = apply_identity_guard(
        user_input=raw_user_input,
        answer_text=text,
        append_timeline_func=append_timeline_func,
        timeline=timeline,
    )
    text = identity_guard.get("text", text)

    provenance_guard = apply_provenance_guard(
        user_input=raw_user_input,
        answer_text=text,
        append_timeline_func=append_timeline_func,
        timeline=timeline,
    )
    text = provenance_guard.get("text", text)

    return GuardedResponse(
        text=text,
        identity_guard=identity_guard,
        provenance_guard=provenance_guard,
        changed=text != pre_guard_text,
    )
