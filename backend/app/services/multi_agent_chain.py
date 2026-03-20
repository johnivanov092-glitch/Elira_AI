"""
multi_agent_chain.py v2 — мульти-агентный пайплайн.

Режимы:
  1. Multi-agent: Исследователь → Программист → Аналитик (3 вызова)
  2. + Рефлексия: ... → проверка итогового отчёта (4 вызова)
  3. + Оркестратор: План → агенты → отчёт (4 вызова)
  4. Все три: Оркестратор → агенты → рефлексия (5 вызовов)
"""
from __future__ import annotations
import logging
from typing import Any

import ollama

from app.core.config import AGENT_PROFILES

logger = logging.getLogger(__name__)


def _call_llm(model: str, system: str, prompt: str, max_tokens: int = 2048) -> str:
    try:
        resp = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            options={"num_predict": max_tokens, "temperature": 0.7},
        )
        return resp.get("message", {}).get("content", "")
    except Exception as e:
        logger.warning(f"LLM call failed: {e}")
        return f"[Ошибка LLM: {e}]"


# ═══════════════════════════════════════════════════════════════
# Агенты
# ═══════════════════════════════════════════════════════════════

def _orchestrator_plan(query: str, context: str, model: str) -> dict:
    """Оркестратор: анализирует задачу и создаёт план для агентов."""
    system = AGENT_PROFILES.get("Оркестратор", "Ты планировщик задач.")
    prompt = (
        f"Задача: {query}\n\n"
        f"{'Контекст:\n' + context[:2000] + chr(10) + chr(10) if context else ''}"
        "Ты — Оркестратор. Проанализируй задачу и создай план.\n"
        "Ответь СТРОГО в формате:\n"
        "ЦЕЛЬ: [одно предложение]\n"
        "ИССЛЕДОВАТЕЛЬ: [что именно исследовать]\n"
        "ПРОГРАММИСТ: [что именно решить технически]\n"
        "АНАЛИТИК: [что проанализировать, какие риски]\n"
        "КРИТЕРИЙ УСПЕХА: [как понять что задача решена]"
    )
    plan_text = _call_llm(model, system, prompt, max_tokens=800)

    plan = {"raw": plan_text, "researcher_task": "", "programmer_task": "", "analyst_task": ""}
    for line in plan_text.split("\n"):
        line = line.strip()
        up = line.upper()
        if up.startswith("ИССЛЕДОВАТЕЛЬ:"):
            plan["researcher_task"] = line.split(":", 1)[1].strip()
        elif up.startswith("ПРОГРАММИСТ:"):
            plan["programmer_task"] = line.split(":", 1)[1].strip()
        elif up.startswith("АНАЛИТИК:"):
            plan["analyst_task"] = line.split(":", 1)[1].strip()
    return plan


def _researcher(query: str, context: str, model: str, task_hint: str = "") -> str:
    system = AGENT_PROFILES.get("Исследователь", "Ты исследователь.")
    prompt = f"Задача: {query}"
    if task_hint:
        prompt = f"Задание от Оркестратора: {task_hint}\n\nОригинальный запрос: {query}"
    if context:
        prompt = f"Контекст:\n{context[:2000]}\n\n---\n{prompt}\n\nПроанализируй данные и выдели ключевые факты."
    return _call_llm(model, system, prompt)


def _programmer(query: str, context: str, research: str, model: str, task_hint: str = "") -> str:
    system = AGENT_PROFILES.get("Программист", "Ты программист.")
    parts = [f"Задача: {query}"]
    if task_hint:
        parts.insert(0, f"Задание от Оркестратора: {task_hint}")
    if research:
        parts.append(f"\nРезультат исследования:\n{research[:1500]}")
    if context:
        parts.append(f"\nДополнительный контекст:\n{context[:1000]}")
    parts.append("\nНапиши код или техническое решение.")
    return _call_llm(model, system, "\n".join(parts))


def _analyst(query: str, research: str, code: str, model: str, task_hint: str = "") -> str:
    system = AGENT_PROFILES.get("Аналитик", "Ты аналитик.")
    parts = [f"Задача: {query}"]
    if task_hint:
        parts.insert(0, f"Задание от Оркестратора: {task_hint}")
    if research:
        parts.append(f"\nИсследование:\n{research[:1500]}")
    if code:
        parts.append(f"\nТехническое решение:\n{code[:1500]}")
    parts.append("\nИтоговый анализ: выводы, риски, рекомендации.")
    return _call_llm(model, system, "\n".join(parts))


def _reflect_on_report(query: str, report: str, model: str) -> str:
    """Рефлексия: перепроверяет итоговый отчёт."""
    system = (
        "Ты — критик и рецензент. Проверь отчёт на:\n"
        "1. Фактические ошибки\n"
        "2. Логические пробелы\n"
        "3. Неполные ответы\n"
        "4. Качество кода (если есть)\n"
        "5. Недостающие риски\n\n"
        "Если отчёт хороший — скажи что хорошо и почему.\n"
        "Если есть проблемы — укажи их и предложи исправления."
    )
    prompt = f"Запрос: {query}\n\nОтчёт:\n{report[:4000]}\n\nПроверь и оцени."
    return _call_llm(model, system, prompt, max_tokens=2000)


# ═══════════════════════════════════════════════════════════════
# Главная функция
# ═══════════════════════════════════════════════════════════════

def run_multi_agent(
    query: str,
    model_name: str = "qwen3:8b",
    context: str = "",
    agents: list[str] | None = None,
    use_reflection: bool = False,
    use_orchestrator: bool = False,
) -> dict[str, Any]:
    agents = agents or ["researcher", "programmer", "analyst"]
    results = {}
    timeline = []
    plan = None

    # Фаза 0: Оркестратор
    if use_orchestrator:
        timeline.append({"agent": "orchestrator", "status": "running", "label": "🎯 Оркестратор планирует..."})
        plan = _orchestrator_plan(query, context, model_name)
        results["orchestrator"] = plan["raw"]
        timeline[-1]["status"] = "done"
        timeline[-1]["length"] = len(plan["raw"])

    # Фаза 1: Исследователь
    research = ""
    if "researcher" in agents:
        timeline.append({"agent": "researcher", "status": "running", "label": "🔎 Исследователь..."})
        research = _researcher(query, context, model_name, task_hint=plan["researcher_task"] if plan else "")
        results["researcher"] = research
        timeline[-1]["status"] = "done"
        timeline[-1]["length"] = len(research)

    # Фаза 2: Программист
    code = ""
    if "programmer" in agents:
        timeline.append({"agent": "programmer", "status": "running", "label": "💻 Программист..."})
        code = _programmer(query, context, research, model_name, task_hint=plan["programmer_task"] if plan else "")
        results["programmer"] = code
        timeline[-1]["status"] = "done"
        timeline[-1]["length"] = len(code)

    # Фаза 3: Аналитик
    analysis = ""
    if "analyst" in agents:
        timeline.append({"agent": "analyst", "status": "running", "label": "📊 Аналитик..."})
        analysis = _analyst(query, research, code, model_name, task_hint=plan["analyst_task"] if plan else "")
        results["analyst"] = analysis
        timeline[-1]["status"] = "done"
        timeline[-1]["length"] = len(analysis)

    # Сборка отчёта
    parts = []
    if plan:
        parts.append(f"## 🎯 План Оркестратора\n{plan['raw']}")
    if research:
        parts.append(f"## 🔎 Исследование\n{research}")
    if code:
        parts.append(f"## 💻 Техническое решение\n{code}")
    if analysis:
        parts.append(f"## 📊 Анализ\n{analysis}")
    report = "\n\n---\n\n".join(parts)

    # Фаза 4: Рефлексия
    if use_reflection and report.strip():
        timeline.append({"agent": "reflection", "status": "running", "label": "🪞 Рефлексия проверяет..."})
        reflection = _reflect_on_report(query, report, model_name)
        results["reflection"] = reflection
        timeline[-1]["status"] = "done"
        timeline[-1]["length"] = len(reflection)
        report += f"\n\n---\n\n## 🪞 Рефлексия\n{reflection}"

    return {
        "ok": True,
        "report": report,
        "results": results,
        "timeline": timeline,
        "agents_used": agents,
        "orchestrator_used": use_orchestrator,
        "reflection_used": use_reflection,
    }
