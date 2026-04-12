"""
agents.py вЂ” РІСЃРµ Р°РіРµРЅС‚РЅС‹Рµ РјРѕРґСѓР»Рё.

РљР»СЋС‡РµРІС‹Рµ СѓР»СѓС‡С€РµРЅРёСЏ v7.1:
  вЂў execute_python_with_capture  вЂ” subprocess-РёР·РѕР»СЏС†РёСЏ + С‚Р°Р№РјР°СѓС‚ + matplotlib С‡РµСЂРµР· С„Р°Р№Р»С‹
  вЂў self_heal_python_code        вЂ” Р°РІС‚Рѕ-РёСЃРїСЂР°РІР»РµРЅРёРµ РґРѕ N РїРѕРїС‹С‚РѕРє
  вЂў run_build_loop               вЂ” РёР·РѕР»РёСЂРѕРІР°РЅРЅР°СЏ temp-dir, СѓР»СѓС‡С€РµРЅРЅС‹Р№ ok-check
  вЂў run_multi_agent              вЂ” РїСЂРѕРіСЂРµСЃСЃ-Р±Р°СЂ, РЅР°РґС‘Р¶РЅС‹Р№ fallback РїР»Р°РЅР°
  вЂў Browser / Terminal           вЂ” Р±РµР· РёР·РјРµРЅРµРЅРёР№
"""
# Legacy monolith: keep behavior stable and prefer extraction into
# smaller modules over new feature work in this file.
# All logic has been extracted; only thin facade delegates remain.
from typing import List, Dict, Any, Tuple


# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
# PYTHON LAB вЂ” РёР·РѕР»РёСЂРѕРІР°РЅРЅС‹Р№ subprocess
# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ

def execute_python_with_capture(
    code: str,
    extra_globals: dict = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Facade -- delegates to application.code_agent.python_lab."""
    from app.application.code_agent.python_lab import execute_python_with_capture as _execute_python_with_capture

    return _execute_python_with_capture(
        code=code,
        extra_globals=extra_globals,
        timeout=timeout,
    )


def self_heal_python_code(
    generated_code: str,
    task: str,
    file_path: str,
    schema_text: str,
    model_name: str,
    max_retries: int = 2,
    num_ctx: int = 4096,
) -> Tuple[str, Dict, List]:
    """Facade -- delegates to application.code_agent.python_lab."""
    from app.application.code_agent.python_lab import self_heal_python_code as _self_heal_python_code

    return _self_heal_python_code(
        generated_code=generated_code,
        task=task,
        file_path=file_path,
        schema_text=schema_text,
        model_name=model_name,
        max_retries=max_retries,
        num_ctx=num_ctx,
    )


def generate_file_code(
    target_file: str,
    task: str,
    model_name: str,
    project_context: str,
    file_context: str,
    num_ctx: int = 4096,
) -> str:
    """Facade -- delegates to application.code_agent.python_lab."""
    from app.application.code_agent.python_lab import generate_file_code as _generate_file_code

    return _generate_file_code(
        target_file=target_file,
        task=task,
        model_name=model_name,
        project_context=project_context,
        file_context=file_context,
        num_ctx=num_ctx,
    )


def run_build_loop(
    target_file: str,
    task: str,
    run_command: str,
    model_name: str,
    max_retries: int,
    project_context: str,
    file_context: str,
    num_ctx: int = 4096,
) -> Tuple[str, str, List]:
    """Facade -- delegates to application.code_agent.python_lab."""
    from app.application.code_agent.python_lab import run_build_loop as _run_build_loop

    return _run_build_loop(
        target_file=target_file,
        task=task,
        run_command=run_command,
        model_name=model_name,
        max_retries=max_retries,
        project_context=project_context,
        file_context=file_context,
        num_ctx=num_ctx,
    )


# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
# MULTI-AGENT ORCHESTRATOR
# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ

def reflect_and_improve_answer(
    task: str,
    draft: str,
    model_name: str,
    profile_name: str = "Оркестратор",
    extra_context: str = "",
    num_ctx: int = 4096,
) -> Dict[str, str]:
    """Facade -- delegates to domain.agents.reflection."""
    from app.domain.agents.reflection import reflect_and_improve_answer as _reflect
    return _reflect(task, draft, model_name, profile_name=profile_name, extra_context=extra_context, num_ctx=num_ctx)

def run_planner_agent(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    progress_callback=None,
) -> Dict[str, Any]:
    """Facade -- delegates to domain.agents.planner."""
    from app.domain.agents.planner import run_planner_agent as _run
    return _run(task, model_name, memory_profile, num_ctx=num_ctx, progress_callback=progress_callback)


def make_task_graph(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
) -> List[dict]:
    """Facade -- delegates to domain.agents.planner."""
    from app.domain.agents.planner import make_task_graph as _make
    return _make(task, model_name, memory_profile, num_ctx=num_ctx)


def run_task_graph(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    progress_callback=None,
) -> Dict[str, Any]:
    """Facade -- delegates to domain.agents.planner."""
    from app.domain.agents.planner import run_task_graph as _run
    return _run(task, model_name, memory_profile, num_ctx=num_ctx, progress_callback=progress_callback)


def prepare_image_prompt(
    prompt: str,
    model_name: str,
    auto_translate: bool = True,
    num_ctx: int = 2048,
) -> Dict[str, str]:
    """Facade -- delegates to application.media.image_generation."""
    from app.application.media.image_generation import prepare_image_prompt as _app_prepare_image_prompt

    return _app_prepare_image_prompt(
        prompt=prompt,
        model_name=model_name,
        auto_translate=auto_translate,
        num_ctx=num_ctx,
    )


def stop_ollama_model(model_name: str) -> Dict[str, Any]:
    """Facade -- delegates to application.media.image_generation."""
    from app.application.media.image_generation import stop_ollama_model as _app_stop_ollama_model

    return _app_stop_ollama_model(model_name)


def generate_image_sdxl_turbo(
    prompt: str,
    negative_prompt: str = "",
    model_name_to_unload: str = "",
    seed: int | None = None,
    width: int = 512,
    height: int = 512,
    num_inference_steps: int = 4,
    guidance_scale: float = 0.0,
    output_path: str | None = None,
    model_id: str | None = None,
) -> Dict[str, Any]:
    """Facade -- delegates to application.media.image_generation."""
    from app.application.media.image_generation import generate_image_sdxl_turbo as _app_generate_image_sdxl_turbo

    return _app_generate_image_sdxl_turbo(
        prompt=prompt,
        negative_prompt=negative_prompt,
        model_name_to_unload=model_name_to_unload,
        seed=seed,
        width=width,
        height=height,
        num_inference_steps=num_inference_steps,
        guidance_scale=guidance_scale,
        output_path=output_path,
        model_id=model_id,
    )


def generate_image_flux_schnell(
    prompt: str,
    negative_prompt: str = "",
    model_name_to_unload: str = "",
    seed: int | None = None,
    width: int = 896,
    height: int = 512,
    num_inference_steps: int = 4,
    guidance_scale: float = 0.0,
    output_path: str | None = None,
    model_id: str | None = None,
    max_sequence_length: int = 160,
) -> Dict[str, Any]:
    """Facade -- delegates to application.media.image_generation."""
    from app.application.media.image_generation import generate_image_flux_schnell as _app_generate_image_flux_schnell

    return _app_generate_image_flux_schnell(
        prompt=prompt,
        negative_prompt=negative_prompt,
        model_name_to_unload=model_name_to_unload,
        seed=seed,
        width=width,
        height=height,
        num_inference_steps=num_inference_steps,
        guidance_scale=guidance_scale,
        output_path=output_path,
        model_id=model_id,
        max_sequence_length=max_sequence_length,
    )


# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
# BROWSER AGENT
# в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ

def run_browser_agent(start_url: str, goal: str, max_pages: int = 3) -> Dict[str, Any]:
    """Facade -- delegates to domain.tools.browser_agent_tool."""
    from app.domain.tools.browser_agent_tool import run_browser_agent as _run_browser_agent

    return _run_browser_agent(start_url, goal, max_pages=max_pages)


def is_dangerous_command(cmd: str) -> bool:
    """Facade -- delegates to domain.tools.terminal_tool."""
    from app.domain.tools.terminal_tool import is_dangerous_command as _is_dangerous_command

    return _is_dangerous_command(cmd)


def run_terminal(cmd: str, timeout: int = 25) -> str:
    """Facade -- delegates to domain.tools.terminal_tool."""
    from app.domain.tools.terminal_tool import run_terminal as _run_terminal

    return _run_terminal(cmd, timeout=timeout)


def browser_actions_from_goal(goal: str, model_name: str) -> List[dict]:
    """Facade -- delegates to domain.tools.browser_action_tool."""
    from app.domain.tools.browser_action_tool import browser_actions_from_goal as _browser_actions_from_goal

    return _browser_actions_from_goal(goal, model_name=model_name)


def run_browser_actions(start_url: str, actions: List[dict]) -> Dict[str, Any]:
    """Facade -- delegates to domain.tools.browser_action_tool."""
    from app.domain.tools.browser_action_tool import run_browser_actions as _run_browser_actions

    return _run_browser_actions(start_url, actions)


def sync_playwright_available() -> bool:
    """Facade -- delegates to domain.tools.browser_action_tool."""
    from app.domain.tools.browser_action_tool import sync_playwright_available as _sync_playwright_available

    return _sync_playwright_available()



# ================================
# Browser в†’ RAG helpers
# ================================

def build_browser_rag_records(url: str, goal: str, summary: str, page_text: str):
    """Facade -- delegates to application.memory.web_knowledge."""
    from app.application.memory.web_knowledge import build_browser_rag_records as _build_browser_rag_records

    return _build_browser_rag_records(url=url, goal=goal, summary=summary, page_text=page_text)


def build_web_knowledge_records(query: str, web_context: str, source_kind: str = "web_search", max_chars: int = 14000):
    """Facade -- delegates to application.memory.web_knowledge."""
    from app.application.memory.web_knowledge import build_web_knowledge_records as _build_web_knowledge_records

    return _build_web_knowledge_records(
        query=query,
        web_context=web_context,
        source_kind=source_kind,
        max_chars=max_chars,
    )


def persist_web_knowledge(
    query: str,
    web_context: str,
    profile_name: str,
    source_kind: str = "web_search",
    url: str = "",
    title: str = "",
):
    """Facade -- delegates to application.memory.persistence."""
    from app.application.memory.persistence import persist_web_knowledge as _persist_web_knowledge

    return _persist_web_knowledge(
        query=query,
        web_context=web_context,
        profile_name=profile_name,
        source_kind=source_kind,
        url=url,
        title=title,
    )




def route_task(user_text: str, model_name: str = "", memory_profile: str = "", num_ctx: int = 4096) -> dict:
    """Facade -- delegates to domain.agents.router."""
    from app.domain.agents.router import route_task as _route
    return _route(user_text, model_name=model_name, memory_profile=memory_profile, num_ctx=num_ctx)


# Facade — canonical definition in domain.agents.router
from app.domain.agents.router import TASK_GRAPH_TEMPLATES_V8  # noqa: E402



def reflection_v2(task: str, answer: str, model_name: str, memory_context: str = "", kb_context: str = "", profile_name: str = "", num_ctx: int = 4096) -> dict:
    """Facade -- delegates to domain.agents.reflection."""
    from app.domain.agents.reflection import reflection_v2 as _refl
    return _refl(task, answer, model_name, memory_context=memory_context, kb_context=kb_context, profile_name=profile_name, num_ctx=num_ctx)


def regenerate_answer_from_context(task: str, model_name: str, memory_context: str = "", kb_context: str = "", prior_answer: str = "", reflection_notes: str = "", num_ctx: int = 4096) -> str:
    """Facade -- delegates to domain.agents.reflection."""
    from app.domain.agents.reflection import regenerate_answer_from_context as _regen
    return _regen(task, model_name, memory_context=memory_context, kb_context=kb_context, prior_answer=prior_answer, reflection_notes=reflection_notes, num_ctx=num_ctx)


def get_fallback_node_v8(node_name: str, state: dict) -> str:
    """Facade -- delegates to domain.agents.reflection."""
    from app.domain.agents.reflection import get_fallback_node_v8 as _fallback
    return _fallback(node_name, state)


def run_graph_with_retry_v8(graph: list, handlers: dict, state: dict, max_retries: int = 2) -> dict:
    """Facade -- delegates to domain.agents.reflection."""
    from app.domain.agents.reflection import run_graph_with_retry_v8 as _run_graph
    return _run_graph(graph, handlers, state, max_retries=max_retries)


def run_agent_v8(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    progress_callback=None,
    force_strategy: str | None = None,
) -> dict:
    """Facade -- delegates to domain.agents.orchestrator."""
    from app.domain.agents.orchestrator import run_agent_v8 as _run
    return _run(task, model_name, memory_profile, num_ctx=num_ctx, progress_callback=progress_callback, force_strategy=force_strategy)


# ═══════════════════════════════════════════════════════════════════════════════
# SELF-IMPROVING AGENT
# ═══════════════════════════════════════════════════════════════════════════════

def run_self_improving_agent(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    max_iters: int = 2,
    progress_callback=None,
    base_force_strategy: str | None = None,
) -> Dict[str, Any]:
    """Facade -- delegates to domain.agents.orchestrator."""
    from app.domain.agents.orchestrator import run_self_improving_agent as _run
    return _run(task, model_name, memory_profile, num_ctx=num_ctx, max_iters=max_iters, progress_callback=progress_callback, base_force_strategy=base_force_strategy)


def run_multi_agent(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
    progress_callback=None,
    project_context: str = "",
    file_context: str = "",
) -> Dict[str, Any]:
    from app.services.workflow_engine import run_legacy_multi_agent_workflow

    return run_legacy_multi_agent_workflow(
        task=task,
        model_name=model_name,
        memory_profile=memory_profile,
        num_ctx=num_ctx,
        progress_callback=progress_callback,
        project_context=project_context,
        file_context=file_context,
    )

