"""Compatibility facade for the multi-agent workflow runtime."""
from __future__ import annotations

from typing import Any

from app.application.workflows.multi_agent import run_multi_agent_workflow


def run_multi_agent(
    query: str,
    model_name: str = "qwen3:8b",
    context: str = "",
    agents: list[str] | None = None,
    use_reflection: bool = False,
    use_orchestrator: bool = False,
) -> dict[str, Any]:
    return run_multi_agent_workflow(
        query=query,
        model_name=model_name,
        context=context,
        agents=agents,
        use_reflection=use_reflection,
        use_orchestrator=use_orchestrator,
    )


__all__ = ["run_multi_agent"]
