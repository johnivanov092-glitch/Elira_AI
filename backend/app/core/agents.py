"""Backward-compat re-exports — all logic lives in domain/application modules.

Remove this file once all external callers import directly.
"""
from app.application.code_agent.python_lab import (  # noqa: F401
    execute_python_with_capture,
    generate_file_code,
    run_build_loop,
    self_heal_python_code,
)
from app.application.media.image_generation import (  # noqa: F401
    generate_image_flux_schnell,
    generate_image_sdxl_turbo,
    prepare_image_prompt,
    stop_ollama_model,
)
from app.application.memory.persistence import persist_web_knowledge  # noqa: F401
from app.application.memory.web_knowledge import (  # noqa: F401
    build_browser_rag_records,
    build_web_knowledge_records,
)
from app.domain.agents.orchestrator import (  # noqa: F401
    run_agent_v8,
    run_self_improving_agent,
)
from app.domain.agents.planner import (  # noqa: F401
    make_task_graph,
    run_planner_agent,
    run_task_graph,
)
from app.domain.agents.reflection import (  # noqa: F401
    get_fallback_node_v8,
    reflect_and_improve_answer,
    reflection_v2,
    regenerate_answer_from_context,
    run_graph_with_retry_v8,
)
from app.domain.agents.router import (  # noqa: F401
    TASK_GRAPH_TEMPLATES_V8,
    route_task,
)
from app.domain.tools.browser_action_tool import (  # noqa: F401
    browser_actions_from_goal,
    run_browser_actions,
    sync_playwright_available,
)
from app.domain.tools.browser_agent_tool import run_browser_agent  # noqa: F401
from app.domain.tools.terminal_tool import is_dangerous_command, run_terminal  # noqa: F401


def run_multi_agent(
    task, model_name, memory_profile, num_ctx=4096,
    progress_callback=None, project_context="", file_context="",
):
    """Kept as lazy import to avoid potential circular dep with workflow modules."""
    from app.application.workflows.multi_agent import run_legacy_multi_agent_workflow

    return run_legacy_multi_agent_workflow(
        task=task, model_name=model_name, memory_profile=memory_profile,
        num_ctx=num_ctx, progress_callback=progress_callback,
        project_context=project_context, file_context=file_context,
    )
