from __future__ import annotations

from typing import Any


def _clip(text: str, limit: int) -> str:
    value = (text or "").strip()
    safe_limit = max(0, int(limit))
    if len(value) <= safe_limit:
        return value
    if safe_limit <= 1:
        return value[:safe_limit]
    return value[: safe_limit - 1].rstrip() + "\u2026"


def _is_llm_error(text: str) -> bool:
    value = (text or "").strip()
    return value.startswith("[\u041e\u0448\u0438\u0431\u043a\u0430 LLM:") or value.startswith("[LLM ERROR:")


def run_multi_agent(
    query: str,
    model_name: str = "qwen3:8b",
    context: str = "",
    agents: list[str] | None = None,
    use_reflection: bool = False,
    use_orchestrator: bool = False,
) -> dict[str, Any]:
    from app.application.workflows.multi_agent import run_multi_agent_workflow

    return run_multi_agent_workflow(
        query=query,
        model_name=model_name,
        context=context,
        agents=agents,
        use_reflection=use_reflection,
        use_orchestrator=use_orchestrator,
    )
