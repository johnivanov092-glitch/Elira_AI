"""
agents_service.py — thin routing facade.

All orchestration logic lives in:
  app/application/chat/service.py        (non-streaming)
  app/application/chat/stream_service.py (streaming)

This file keeps backward-compatible public API (run_agent, run_agent_stream)
and legacy helpers that are still called from other modules.
"""
from __future__ import annotations

import logging
from typing import Any, Generator

# ─── application layer re-exports (used by callers of this module) ────────────
from app.application.chat.auto_skills import (
    _EXEC_TRIGGERS,
    _FILE_TRIGGERS_EXCEL,
    _FILE_TRIGGERS_WORD,
    _build_prompt,
    _get_and_clear_attachments,
    _maybe_auto_exec_python,
    _maybe_generate_files,
    _pending_attachments,
    _run_auto_skills,
)
from app.application.chat.context_builder import (
    collect_context as build_chat_context,
    strip_frontend_project_context as build_strip_frontend_project_context,
)
from app.application.chat.prompt_builder import (
    build_runtime_datetime_context as _build_runtime_datetime_context,
    compose_human_style_rules as _compose_human_style_rules,
    wants_explicit_datetime_answer as _wants_explicit_datetime_answer,
)
from app.application.chat.service import (
    _DIRECT_PERSONAL_MEMORY_RE,
    _HISTORY,
    _MAX_HISTORY_PAIRS,
    _REFLECTION_ROUTES,
    _apply_identity_guard,
    _apply_provenance_guard,
    _collect_context,
    _do_temporal_web_search,
    _emit_agent_os_event,
    _get_memory_recall_limits,
    _is_direct_personal_memory_query,
    _record_agent_os_monitoring,
    _should_recall_memory_context,
    _strip_frontend_project_context,
    _tl,
    _trim_history,
    execute_chat_agent,
)
from app.application.chat.stream_service import execute_chat_agent_stream

# ─── infrastructure search facades ───────────────────────────────────────────
from app.infrastructure.search.web_search import (
    clean_query as _infra_clean_query,
    do_temporal_web_search as _infra_do_temporal_web_search,
    do_temporal_web_search_legacy as _infra_do_temporal_web_search_legacy,
    do_web_search as _infra_do_web_search,
    do_web_search_legacy as _infra_do_web_search_legacy,
    get_web_search_result as _infra_get_web_search_result,
    is_strict_web_only_query as _infra_is_strict_web_only_query,
)

# ─── other service imports (kept for any remaining callers) ──────────────────
from app.application.monitoring.agent_monitor import record_agent_run_metric
from app.application.monitoring.agent_sandbox import (
    SandboxPolicyError,
    preflight_or_raise,
    resolve_effective_agent_id,
)
from app.application.chat.chat_service import run_chat, run_chat_stream
from app.application.policy.identity_guard import guard_identity_response
from app.application.persona.persona_service import observe_dialogue
from app.application.planning.planner_v2_service import PlannerV2Service
from app.application.policy.provenance_guard import guard_provenance_response
from app.application.agents.reflection_loop_service import run_reflection_loop
from app.infrastructure.cache.response_cache import get_cached, set_cached, should_cache
from app.infrastructure.db.run_history_service import RunHistoryService
from app.application.memory.smart_memory import extract_and_save, get_relevant_context, is_memory_command
from app.application.planning.temporal_intent import detect_temporal_intent
from app.application.tools.tool_service import run_tool
from app.core.config import pick_model_for_route, DEFAULT_MODEL

try:
    from app.application.memory.rag_memory_service import add_to_rag, get_rag_context
    _HAS_RAG = True
except ImportError:
    _HAS_RAG = False

    def get_rag_context(*a, **kw):  # type: ignore[misc]
        return ""

    def add_to_rag(*a, **kw):  # type: ignore[misc]
        return {}

logger = logging.getLogger(__name__)


# ─── simple local helpers ─────────────────────────────────────────────────────


def _clean_query(query):
    return _infra_clean_query(query)


def _short(v, limit=600):
    t = str(v or "")
    return t if len(t) <= limit else t[:limit] + "..."


# ─── web search facades ───────────────────────────────────────────────────────


def _is_strict_web_only_query(user_input: str) -> bool:
    return _infra_is_strict_web_only_query(user_input)


def _get_web_search_result(tool_results):
    return _infra_get_web_search_result(tool_results)


def _build_single_web_subquery_context(subquery):
    from app.infrastructure.search.web_search import build_single_web_subquery_context
    return build_single_web_subquery_context(subquery)


def _do_web_search_legacy(query, timeline, tool_results):
    return _infra_do_web_search_legacy(query, timeline, tool_results, tl=_tl)


def _do_temporal_web_search_legacy(query, timeline, tool_results, temporal=None):
    return _infra_do_temporal_web_search_legacy(
        query, timeline, tool_results, temporal=temporal, tl=_tl
    )


def _do_web_search(query, timeline, tool_results, web_plan=None):
    return _infra_do_web_search(query, timeline, tool_results, web_plan=web_plan, tl=_tl)


# ─── legacy context helpers (kept for backward compatibility) ─────────────────


def _strip_frontend_project_context_legacy(user_input: str) -> str:
    text = user_input or ""
    marker = "\n\nОткрыт проект:"
    pos = text.find(marker)
    if pos >= 0:
        return text[:pos].rstrip()
    return text


def _collect_context_legacy(
    *,
    profile_name,
    user_input,
    tools,
    tool_results,
    timeline,
    use_reflection=False,
    temporal=None,
    web_plan=None,
):
    parts = []
    for tool_name in tools:
        try:
            if tool_name == "memory_search":
                result = run_tool("search_memory", {"profile": profile_name, "query": user_input, "limit": 5})
                tool_results.append({"tool": "search_memory", "result": result})
                items = result.get("items", [])
                _tl(timeline, "tool_memory", "Память", "done", str(result.get("count", 0)))
                if items:
                    parts.append("Из памяти:\n" + "\n".join("- " + i.get("text", "") for i in items))

            elif tool_name == "library_context":
                _tl(timeline, "tool_library", "Библиотека", "skip", "Фронтенд")

            elif tool_name == "web_search":
                web_ctx = _do_temporal_web_search(
                    user_input, timeline, tool_results, temporal=temporal, web_plan=web_plan
                )
                if web_ctx:
                    parts.append(web_ctx)

            elif tool_name == "project_mode":
                project_ctx = ""
                try:
                    tree = run_tool("list_project_tree", {"max_depth": 3, "max_items": 200})
                    search = run_tool("search_project", {"query": user_input, "max_hits": 20})
                    tool_results.append({"tool": "project", "result": {"tree": tree.get("count", 0), "hits": search.get("count", 0)}})
                    snippets = search.get("items") or search.get("results") or []
                    if snippets:
                        rendered = [
                            "- " + (
                                item.get("path", "") + ": " + (item.get("snippet", "") or item.get("preview", ""))
                                if isinstance(item, dict) else str(item)
                            )
                            for item in snippets[:10]
                        ]
                        project_ctx = "Из проекта:\n" + "\n".join(rendered)
                except Exception:
                    pass

                if not project_ctx:
                    try:
                        from app.application.projects.project_explorer import _project_path
                        if _project_path:
                            from pathlib import Path
                            root = Path(_project_path)
                            if root.exists():
                                file_list = []
                                for f in sorted(root.rglob("*"))[:50]:
                                    if f.is_file() and not any(
                                        b in str(f) for b in [".git", "node_modules", "__pycache__", ".venv", "dist"]
                                    ):
                                        file_list.append(str(f.relative_to(root)))
                                project_ctx = (
                                    f"Открыт проект: {root.name}\nФайлы ({len(file_list)}):\n"
                                    + "\n".join("- " + f for f in file_list[:30])
                                )
                    except Exception:
                        pass

                if project_ctx:
                    parts.append(project_ctx)
                    _tl(timeline, "tool_project", "Проект", "done", "Контекст загружен")
                else:
                    _tl(timeline, "tool_project", "Проект", "skip", "Не открыт")

            elif tool_name == "python_executor":
                _tl(timeline, "tool_python", "Python", "ready", "Выполнение по запросу")

            elif tool_name == "project_patch":
                _tl(timeline, "tool_patch", "Патчинг", "ready", "")

        except Exception as exc:
            _tl(timeline, "tool_" + tool_name, tool_name, "error", str(exc))

    return "\n\n".join(p for p in parts if p.strip())


# ─── public API ──────────────────────────────────────────────────────────────


def run_agent(
    *,
    model_name,
    profile_name,
    user_input,
    session_id=None,
    agent_id=None,
    use_memory=True,
    use_library=True,
    use_reflection=False,
    history=None,
    num_ctx=8192,
    use_web_search=True,
    use_python_exec=True,
    use_image_gen=True,
    use_file_gen=True,
    use_http_api=True,
    use_sql=True,
    use_screenshot=True,
    use_encrypt=True,
    use_archiver=True,
    use_converter=True,
    use_regex=True,
    use_translator=True,
    use_csv=True,
    use_webhook=True,
    use_plugins=True,
):
    return execute_chat_agent(
        model_name=model_name,
        profile_name=profile_name,
        user_input=user_input,
        session_id=session_id,
        agent_id=agent_id,
        use_memory=use_memory,
        use_library=use_library,
        use_reflection=use_reflection,
        history=history,
        num_ctx=num_ctx,
        use_web_search=use_web_search,
        use_python_exec=use_python_exec,
        use_image_gen=use_image_gen,
        use_file_gen=use_file_gen,
        use_http_api=use_http_api,
        use_sql=use_sql,
        use_screenshot=use_screenshot,
        use_encrypt=use_encrypt,
        use_archiver=use_archiver,
        use_converter=use_converter,
        use_regex=use_regex,
        use_translator=use_translator,
        use_csv=use_csv,
        use_webhook=use_webhook,
        use_plugins=use_plugins,
    )


def run_agent_stream(
    *,
    model_name,
    profile_name,
    user_input,
    session_id=None,
    use_memory=True,
    use_library=True,
    use_reflection=False,
    history=None,
    num_ctx=8192,
    use_web_search=True,
    use_python_exec=True,
    use_image_gen=True,
    use_file_gen=True,
    use_http_api=True,
    use_sql=True,
    use_screenshot=True,
    use_encrypt=True,
    use_archiver=True,
    use_converter=True,
    use_regex=True,
    use_translator=True,
    use_csv=True,
    use_webhook=True,
    use_plugins=True,
) -> Generator[dict[str, Any], None, None]:
    yield from execute_chat_agent_stream(
        model_name=model_name,
        profile_name=profile_name,
        user_input=user_input,
        session_id=session_id,
        use_memory=use_memory,
        use_library=use_library,
        use_reflection=use_reflection,
        history=history,
        num_ctx=num_ctx,
        use_web_search=use_web_search,
        use_python_exec=use_python_exec,
        use_image_gen=use_image_gen,
        use_file_gen=use_file_gen,
        use_http_api=use_http_api,
        use_sql=use_sql,
        use_screenshot=use_screenshot,
        use_encrypt=use_encrypt,
        use_archiver=use_archiver,
        use_converter=use_converter,
        use_regex=use_regex,
        use_translator=use_translator,
        use_csv=use_csv,
        use_webhook=use_webhook,
        use_plugins=use_plugins,
    )
