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
from pathlib import Path
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


def _ok_check(stdout: str, stderr: str, returncode: int) -> bool:
    """Facade -- delegates to application.code_agent.python_lab."""
    from app.application.code_agent.python_lab import ok_check as _ok_check_impl

    return _ok_check_impl(stdout=stdout, stderr=stderr, returncode=returncode)


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


def _run_in_dir(cmd: str, cwd: Path, timeout: int = 60) -> str:
    """Facade -- delegates to application.code_agent.python_lab."""
    from app.application.code_agent.python_lab import run_in_dir as _run_in_dir_impl

    return _run_in_dir_impl(cmd=cmd, cwd=cwd, timeout=timeout)


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

def _extract_first_url(text: str) -> str:
    """Facade -- delegates to domain.agents.planner."""
    from app.domain.agents.planner import extract_first_url
    return extract_first_url(text)


def _planner_safe_terminal_command(cmd: str) -> bool:
    """Facade -- delegates to domain.agents.planner."""
    from app.domain.agents.planner import planner_safe_terminal_command
    return planner_safe_terminal_command(cmd)


def _planner_default_steps(task: str) -> List[dict]:
    """Facade -- delegates to domain.agents.planner."""
    from app.domain.agents.planner import planner_default_steps
    return planner_default_steps(task)


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


def _task_graph_default(task: str) -> List[dict]:
    """Facade -- delegates to domain.agents.planner."""
    from app.domain.agents.planner import task_graph_default
    return task_graph_default(task)


def _normalize_task_graph(raw_graph: Any, task: str) -> List[dict]:
    """Facade -- delegates to domain.agents.planner."""
    from app.domain.agents.planner import normalize_task_graph
    return normalize_task_graph(raw_graph, task)


def make_task_graph(
    task: str,
    model_name: str,
    memory_profile: str,
    num_ctx: int = 4096,
) -> List[dict]:
    """Facade -- delegates to domain.agents.planner."""
    from app.domain.agents.planner import make_task_graph as _make
    return _make(task, model_name, memory_profile, num_ctx=num_ctx)


def _task_graph_context_from_deps(node: dict, node_results: Dict[str, dict]) -> str:
    """Facade -- delegates to domain.agents.planner."""
    from app.domain.agents.planner import task_graph_context_from_deps
    return task_graph_context_from_deps(node, node_results)


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


def _torch_gc():
    """Facade -- delegates to application.media.image_generation."""
    from app.application.media.image_generation import torch_gc as _app_torch_gc

    return _app_torch_gc()


def _strip_ansi(text: str) -> str:
    """Facade -- delegates to application.media.image_generation."""
    from app.application.media.image_generation import strip_ansi as _app_strip_ansi

    return _app_strip_ansi(text)


def _contains_cyrillic(text: str) -> bool:
    """Facade -- delegates to application.media.image_generation."""
    from app.application.media.image_generation import contains_cyrillic as _app_contains_cyrillic

    return _app_contains_cyrillic(text)


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


def _hf_access_hint(exc_text: str) -> str:
    """Facade -- delegates to application.media.image_generation."""
    from app.application.media.image_generation import hf_access_hint as _app_hf_access_hint

    return _app_hf_access_hint(exc_text)


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

def _goal_keywords(goal: str) -> List[str]:
    """Facade -- delegates to domain.tools.browser_agent_tool."""
    from app.domain.tools.browser_agent_tool import goal_keywords as _goal_keywords_impl

    return _goal_keywords_impl(goal)


def _extract_page_payload(page, max_chars: int = 9000) -> str:
    """Facade -- delegates to domain.tools.browser_agent_tool."""
    from app.domain.tools.browser_agent_tool import extract_page_payload as _extract_page_payload_impl

    return _extract_page_payload_impl(page, max_chars=max_chars)


def _collect_links(page, base_url: str) -> List[Dict[str, Any]]:
    """Facade -- delegates to domain.tools.browser_agent_tool."""
    from app.domain.tools.browser_agent_tool import collect_links as _collect_links_impl

    return _collect_links_impl(page, base_url)


def _score_link(link: Dict[str, Any], goal_keywords: List[str]) -> int:
    """Facade -- delegates to domain.tools.browser_agent_tool."""
    from app.domain.tools.browser_agent_tool import score_link as _score_link_impl

    return _score_link_impl(link, goal_keywords)


def _rank_links(links: List[Dict[str, Any]], goal: str, limit: int) -> List[Dict[str, Any]]:
    """Facade -- delegates to domain.tools.browser_agent_tool."""
    from app.domain.tools.browser_agent_tool import rank_links as _rank_links_impl

    return _rank_links_impl(links, goal, limit)


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


def _browser_runtime_hint(exc: Exception | str) -> str:
    """Facade -- delegates to domain.tools.browser_action_tool."""
    from app.domain.tools.browser_action_tool import browser_runtime_hint as _runtime_hint

    return _runtime_hint(exc)


def _sanitize_browser_actions(actions: List[dict]) -> List[dict]:
    """Facade -- delegates to domain.tools.browser_action_tool."""
    from app.domain.tools.browser_action_tool import sanitize_browser_actions as _sanitize_actions

    return _sanitize_actions(actions)


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

def _clean_browser_text(text: str) -> str:
    """Facade -- delegates to application.memory.web_knowledge."""
    from app.application.memory.web_knowledge import clean_browser_text as _clean_text

    return _clean_text(text)


def _chunk_browser_text(text: str, size: int = 1200):
    """Facade -- delegates to application.memory.web_knowledge."""
    from app.application.memory.web_knowledge import chunk_browser_text as _chunk_text

    return _chunk_text(text, size=size)


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



def _safe_json_object(text: str) -> dict:
    """Facade -- delegates to domain.agents.reflection."""
    from app.domain.agents.reflection import safe_json_object
    return safe_json_object(text)


def reflection_v2(task: str, answer: str, model_name: str, memory_context: str = "", kb_context: str = "", profile_name: str = "", num_ctx: int = 4096) -> dict:
    """Facade -- delegates to domain.agents.reflection."""
    from app.domain.agents.reflection import reflection_v2 as _refl
    return _refl(task, answer, model_name, memory_context=memory_context, kb_context=kb_context, profile_name=profile_name, num_ctx=num_ctx)


def _count_false_flags(reflection: dict) -> int:
    """Facade -- delegates to domain.agents.reflection."""
    from app.domain.agents.reflection import count_false_flags
    return count_false_flags(reflection)


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

