"""
multi_agent_chain.py — мульти-агентный пайплайн.

Цепочка: Исследователь → Программист → Аналитик
Каждый агент получает результат предыдущего как контекст.

Использование:
  result = run_multi_agent(query, model_name, history)
"""
from __future__ import annotations
import logging
from typing import Any

import ollama

from app.core.config import AGENT_PROFILES

logger = logging.getLogger(__name__)


def _call_llm(model: str, system: str, prompt: str, max_tokens: int = 2048) -> str:
    """Вызов Ollama с system prompt."""
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

def _researcher(query: str, context: str, model: str) -> str:
    """Исследователь: собирает факты, анализирует данные."""
    system = AGENT_PROFILES.get("Исследователь", "Ты исследователь.")
    prompt = f"Задача: {query}"
    if context:
        prompt = f"Контекст (данные из интернета и памяти):\n{context}\n\n---\nЗадача: {query}\n\nПроанализируй данные и выдели ключевые факты."
    return _call_llm(model, system, prompt)


def _programmer(query: str, context: str, research: str, model: str) -> str:
    """Программист: пишет код на основе исследования."""
    system = AGENT_PROFILES.get("Программист", "Ты программист.")
    parts = [f"Задача: {query}"]
    if research:
        parts.append(f"\nРезультат исследования:\n{research}")
    if context:
        parts.append(f"\nДополнительный контекст:\n{context}")
    parts.append("\nНапиши код или техническое решение на основе исследования.")
    return _call_llm(model, system, "\n".join(parts))


def _analyst(query: str, research: str, code: str, model: str) -> str:
    """Аналитик: делает выводы, оценивает риски."""
    system = AGENT_PROFILES.get("Аналитик", "Ты аналитик.")
    parts = [f"Задача: {query}"]
    if research:
        parts.append(f"\nИсследование:\n{research[:1500]}")
    if code:
        parts.append(f"\nТехническое решение:\n{code[:1500]}")
    parts.append("\nСделай итоговый анализ: выводы, риски, рекомендации. Кратко и по делу.")
    return _call_llm(model, system, "\n".join(parts))


# ═══════════════════════════════════════════════════════════════
# Оркестратор
# ═══════════════════════════════════════════════════════════════

def run_multi_agent(
    query: str,
    model_name: str = "qwen3:8b",
    context: str = "",
    agents: list[str] | None = None,
) -> dict[str, Any]:
    """
    Запускает цепочку агентов.
    
    agents: список из ["researcher", "programmer", "analyst"]
    По умолчанию: все три.
    """
    agents = agents or ["researcher", "programmer", "analyst"]
    results = {}
    timeline = []

    # Исследователь
    if "researcher" in agents:
        timeline.append({"agent": "researcher", "status": "running"})
        research = _researcher(query, context, model_name)
        results["researcher"] = research
        timeline[-1]["status"] = "done"
        timeline[-1]["length"] = len(research)
    else:
        research = ""

    # Программист
    if "programmer" in agents:
        timeline.append({"agent": "programmer", "status": "running"})
        code = _programmer(query, context, research, model_name)
        results["programmer"] = code
        timeline[-1]["status"] = "done"
        timeline[-1]["length"] = len(code)
    else:
        code = ""

    # Аналитик
    if "analyst" in agents:
        timeline.append({"agent": "analyst", "status": "running"})
        analysis = _analyst(query, research, code, model_name)
        results["analyst"] = analysis
        timeline[-1]["status"] = "done"
        timeline[-1]["length"] = len(analysis)
    else:
        analysis = ""

    # Финальный отчёт
    report_parts = []
    if research:
        report_parts.append(f"## 🔎 Исследование\n{research}")
    if code:
        report_parts.append(f"## 💻 Техническое решение\n{code}")
    if analysis:
        report_parts.append(f"## 📊 Анализ\n{analysis}")

    return {
        "ok": True,
        "report": "\n\n---\n\n".join(report_parts),
        "results": results,
        "timeline": timeline,
        "agents_used": agents,
    }
