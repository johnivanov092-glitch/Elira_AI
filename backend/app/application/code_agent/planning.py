"""Bounded planning stage for «Мозг» (thinking=true) structural runs.

Contract (see the batch spec):
  - thinking=true on a STRUCTURED task becomes ONE bounded planning stage, then
    every execution/verification model call runs thinking OFF. Raw
    enable_thinking is never re-enabled per step.
  - The planner reads the task + TaskSpec + a BOUNDED read-only project context
    and returns a validated PlanArtifact — no free chain-of-thought, no
    side-effect tools, no second checklist/runtime.
  - Invalid / empty / timeout planner output → durable planning_fallback; the
    run then executes normally with thinking OFF. It never hangs on planning.

This is a LEAF module: it defines the PlanArtifact schema + a single bounded
planner call and pure helpers. It owns no runtime, no executor, no registry —
the caller (agent_loop) feeds it the already-built chat_fn and a read-only
recon closure, and persists the result through the existing RunJournal.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "PlanArtifact",
    "planner_limits",
    "plan_artifact_from_dict",
    "parse_plan_from_text",
    "build_planning_messages",
    "plan_context_block",
    "error_fingerprint",
    "recovery_hint",
    "recovery_next_step",
]

PLANNING_SYSTEM_PROMPT = (
    "Ты — планировщик инженерной задачи. У тебя РОВНО один ход: прочитать цель, "
    "критерии готовности и краткий контекст проекта, затем вернуть СТРОГО ОДИН "
    "JSON-объект плана и больше ничего. Не рассуждай вслух, не пиши прозу до или "
    "после JSON, не вызывай инструменты, не изменяй файлы.\n\n"
    "Схема (все поля обязательны):\n"
    "{\n"
    '  "goal": "одна строка — что должно быть сделано",\n'
    '  "current_state": "одна-две строки — что уже есть в проекте по факту",\n'
    '  "ordered_steps": ["короткие конкретные шаги в порядке выполнения"],\n'
    '  "acceptance_checks": ["проверки, доказывающие готовность: typecheck/test/build/…"],\n'
    '  "risks": ["короткие риски/подводные камни"],\n'
    '  "current_step": 1\n'
    "}\n\n"
    "ordered_steps и acceptance_checks непустые. current_step — 1-базовый индекс "
    "первого шага. Верни только JSON."
)

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_PLAN_FIELDS = frozenset({
    "goal", "current_state", "ordered_steps", "acceptance_checks", "risks", "current_step",
})


@dataclass
class PlanArtifact:
    """Validated planning output. Same dataclass style as TaskSpec — a durable,
    structured plan, NOT a free-text scratchpad."""

    goal: str = ""
    current_state: str = ""
    ordered_steps: list[str] = field(default_factory=list)
    acceptance_checks: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    current_step: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "current_state": self.current_state,
            "ordered_steps": list(self.ordered_steps),
            "acceptance_checks": list(self.acceptance_checks),
            "risks": list(self.risks),
            "current_step": int(self.current_step),
        }

    def is_valid(self) -> bool:
        return bool(self.goal.strip()) and bool(self.ordered_steps) and bool(self.acceptance_checks)


def _clean_str_list(value: Any, *, cap: int = 20, item_cap: int = 300) -> list[str] | None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return None
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(text[:item_cap])
        if len(out) >= cap:
            break
    return out


def plan_artifact_from_dict(data: Any) -> PlanArtifact | None:
    """Coerce+validate a plan dict. Returns None on anything unusable — the
    caller treats None as planning_fallback (never a hang, never a re-plan)."""
    if not isinstance(data, dict) or set(data) != _PLAN_FIELDS:
        return None
    steps = _clean_str_list(data.get("ordered_steps"))
    checks = _clean_str_list(data.get("acceptance_checks"))
    risks = _clean_str_list(data.get("risks"), cap=10)
    goal_raw = data.get("goal")
    state_raw = data.get("current_state")
    current_raw = data.get("current_step")
    if (
        not isinstance(goal_raw, str)
        or not isinstance(state_raw, str)
        or not isinstance(current_raw, int)
        or isinstance(current_raw, bool)
        or steps is None
        or checks is None
        or risks is None
    ):
        return None
    goal = goal_raw.strip()[:400]
    current_state = state_raw.strip()[:400]
    if not goal or not current_state or not steps or not checks:
        return None
    current = max(1, min(current_raw, len(steps)))
    return PlanArtifact(
        goal=goal,
        current_state=current_state,
        ordered_steps=steps,
        acceptance_checks=checks,
        risks=risks,
        current_step=current,
    )


def planner_limits(context_profile: dict[str, Any]) -> tuple[int, int]:
    """Return ``(max_output_tokens, project_context_chars)`` from the active
    context profile.

    The fractions are window-agnostic: a larger live server window gives the
    planner proportionally more room without a table of known 64K/128K/etc.
    The output remains inside the profile's reserved output budget.
    """
    safe_input = max(1024, int(context_profile.get("safe_input_budget") or 1024))
    reserved_output = max(512, int(context_profile.get("reserved_output_tokens") or 512))
    max_output = max(512, min(reserved_output, safe_input // 48))
    project_chars = max(2048, safe_input // 8)
    return max_output, project_chars


def parse_plan_from_text(text: str) -> PlanArtifact | None:
    """Extract the single JSON object from a planner reply and validate it.
    Tolerant of a stray ```json fence or leading prose (some local models leak
    a sentence); still rejects a reply with no parseable JSON object."""
    if not text:
        return None
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except (ValueError, TypeError):
        return None
    return plan_artifact_from_dict(data)


def build_planning_messages(
    *, taskspec_context: str, project_context: str, project_char_cap: int,
    system_prompt: str = PLANNING_SYSTEM_PROMPT,
) -> list[dict[str, Any]]:
    """The planner sees: its system contract + the TaskSpec goal/criteria + a
    BOUNDED read-only project context. No accumulated tool-output, no history."""
    user = taskspec_context.strip()
    ctx = (project_context or "").strip()
    if ctx:
        user += "\n\n[Контекст проекта (только чтение)]\n" + ctx[:max(1, int(project_char_cap))]
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user},
    ]


def plan_context_block(plan: PlanArtifact) -> str:
    """Deterministic compact plan block injected into execution turns — the
    goal, the current step, the ordered steps and the acceptance checks. Small
    by construction; it replaces raw chain-of-thought, not the TaskSpec."""
    idx = max(1, min(plan.current_step, len(plan.ordered_steps)))
    lines = [
        "[ПЛАН]",
        f"Цель: {plan.goal}",
    ]
    if plan.current_state:
        lines.append(f"Текущее состояние: {plan.current_state}")
    lines.append("Шаги:")
    for i, step in enumerate(plan.ordered_steps, 1):
        mark = "→" if i == idx else ("✓" if i < idx else "·")
        lines.append(f"  {mark} {i}. {step}")
    lines.append(f"Текущий шаг: {idx}. {plan.ordered_steps[idx - 1]}")
    lines.append("Проверки готовности: " + "; ".join(plan.acceptance_checks))
    lines.append(
        "После изменения переходи к указанной проверке — не делай серию правок "
        "без обратной связи."
    )
    return "\n".join(lines)


# ── Bounded recovery: normalized error fingerprint + precise diagnostics ─────

_NUM_RE = re.compile(r"\d+")


def error_fingerprint(tool: str, args: dict[str, Any], tool_meta: dict[str, Any]) -> str:
    """Stable identity of a FAILURE for bounded recovery: tool + operation/path
    + stable error/exit. Numbers are normalized so a shifting line number does
    not defeat the match. Returns '' when the call did not fail."""
    ok = bool((tool_meta or {}).get("ok", True))
    exit_code = (tool_meta or {}).get("exit_code")
    failed = (not ok) or (isinstance(exit_code, int) and exit_code != 0)
    if not failed:
        return ""
    a = args or {}
    target = str(a.get("path") or a.get("command") or a.get("pattern") or "").strip()
    error = str((tool_meta or {}).get("error") or "").strip()
    if not error:
        text = str((tool_meta or {}).get("text") or "")
        error = text.split("\n", 1)[0][:80]
    stable = _NUM_RE.sub("N", f"{error}").lower().strip()
    if isinstance(exit_code, int):
        stable = f"exit={exit_code}|{stable}"
    return f"{tool}|{target}|{stable}"


def recovery_hint(
    *, tool: str, path: str, error_text: str, times: int, already_read: bool, open_criterion: str = "",
) -> str:
    """A PRECISE, diagnosis-driven recovery message after a repeated identical
    failure — never the generic «прочитай файл или используй примитив» when the
    journal shows the file was already read."""
    head = (
        f"[recovery] Повторная ошибка ({times}×) на {tool}({path}): {error_text[:160]}. "
    )
    old_string_missing = bool(re.search(
        r"old[_ ]string[_ ]not[_ ]found", error_text.lower(), re.I,
    ))
    if tool == "edit_file" and old_string_missing:
        if already_read:
            body = (
                f"Файл {path} уже читался в этом прогоне, но old_string не совпадает — "
                "значит содержимое отличается от того, что ты подставляешь. "
                f"Перечитай ТЕКУЩЕЕ содержимое {path} прямо сейчас (read_file) и скопируй "
                "old_string дословно из свежего вывода, либо перезапиши файл целиком "
                "через write_file. Хватит повторять ту же замену."
            )
        else:
            body = (
                f"Сначала прочитай {path} (read_file), затем возьми old_string дословно "
                "из его текущего содержимого."
            )
    elif isinstance_exit_failure(error_text):
        body = (
            "Прочитай точную диагностику команды выше (первая ошибка/стек), исправь "
            "именно её, затем повтори эту же проверку — не переходи к другой стратегии, "
            "пока не понял конкретную причину."
        )
    else:
        body = (
            "Прочитай точную ошибку выше и исправь конкретную причину, затем повтори "
            "эту же операцию один раз."
        )
    tail = f" Оставшийся критерий: {open_criterion}." if open_criterion else ""
    return head + body + tail


def recovery_next_step(
    *, tool: str, path: str, error_text: str, already_read: bool,
) -> str:
    """Concrete deterministic continuation for a stopped failed operation."""
    target = path or "целевой объект"
    if tool == "edit_file" and re.search(
        r"old[_ ]string[_ ]not[_ ]found", error_text.lower(), re.I,
    ):
        prefix = "Перечитай текущее содержимое" if already_read else "Прочитай содержимое"
        return (
            f"{prefix} {target}, возьми old_string дословно из свежего вывода и "
            "повтори одну точную замену; если замена неуместна, перезапиши файл целиком."
        )
    detail = error_text.strip() or "операция завершилась ошибкой"
    return f"Исправь причину сбоя {tool} для {target}: {detail[:160]}; затем повтори проверку."


def isinstance_exit_failure(error_text: str) -> bool:
    low = (error_text or "").lower()
    return "exit=" in low or "traceback" in low or "error ts" in low or "failed" in low
