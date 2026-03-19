"""
agents_service.py v3 — агентный пайплайн.

Фиксы:
  • SSE progress phases: searching → thinking → streaming → done
  • Reflection ТОЛЬКО для code/project (research тоже пропускает — ускорение)
  • Одиночный web_search вместо дублей
"""
from __future__ import annotations

from typing import Any, Generator

from app.services.chat_service import run_chat, run_chat_stream
from app.services.planner_v2_service import PlannerV2Service
from app.services.reflection_loop_service import run_reflection_loop
from app.services.run_history_service import RunHistoryService
from app.services.tool_service import run_tool

_HISTORY = RunHistoryService()

# Reflection ТОЛЬКО для code и project (research — нет, чат — нет)
_REFLECTION_ROUTES = {"code", "project"}
_MAX_HISTORY_PAIRS = 10


def _short(value: Any, limit: int = 600) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "..."


def _append_timeline(timeline, step, title, status, detail):
    timeline.append({"step": step, "title": title, "status": status, "detail": detail})


def _trim_history(history, max_pairs=_MAX_HISTORY_PAIRS):
    if not history:
        return []
    limit = max_pairs * 2
    return list(history[-limit:]) if len(history) > limit else list(history)


def _build_prompt(user_input, plan, context_bundle):
    tools = ", ".join(plan.get("tools", [])) or "no tools"
    route = plan.get("route", "chat")
    parts = [
        "Ты главный агент Jarvis.",
        f"Маршрут: {route}",
        f"Инструменты: {tools}",
        "",
        "Дай практичный ответ. Форматируй Markdown.",
        "Если про код — укажи файлы и изменения.",
        "Если контекста мало — скажи прямо.",
        "",
        f"Запрос:\n{user_input}",
    ]
    if context_bundle.strip():
        parts.extend(["", f"Контекст:\n{context_bundle}"])
    return "\n".join(parts)


def _collect_tool_context(*, profile_name, user_input, tools, tool_results, timeline):
    context_parts = []

    for tool_name in tools:
        try:
            if tool_name == "memory_search":
                result = run_tool("search_memory", {"profile": profile_name, "query": user_input, "limit": 5})
                tool_results.append({"tool": "search_memory", "result": result})
                items = result.get("items", [])
                _append_timeline(timeline, "tool_memory", "Память", "done", f"Найдено: {result.get('count', 0)}")
                if items:
                    context_parts.append("Memory:\n" + "\n".join(f"- {i.get('text','')}" for i in items))

            elif tool_name == "library_context":
                result = run_tool("build_library_context", {})
                tool_results.append({"tool": "build_library_context", "result": result})
                _append_timeline(timeline, "tool_library", "Библиотека", "done" if result.get("context") else "skip", "")
                if result.get("context"):
                    context_parts.append("Library:\n" + str(result["context"]))

            elif tool_name == "web_search":
                from app.services.web_service import search_web
                web_result = search_web(user_input, max_results=8)
                tool_results.append({"tool": "web_search", "result": web_result})
                sources = web_result.get("sources", [])
                engines = web_result.get("engines_used", [])
                _append_timeline(timeline, "tool_web", f"Веб ({', '.join(engines)})", "done" if sources else "warning", f"{len(sources)} результатов")
                if sources:
                    lines = [f"- [{s.get('title','')}]({s.get('url','')}): {s.get('snippet','')}" for s in sources[:6]]
                    context_parts.append("Web:\n" + "\n".join(lines))

            elif tool_name == "project_mode":
                tree = run_tool("list_project_tree", {"max_depth": 3, "max_items": 200})
                search = run_tool("search_project", {"query": user_input, "max_hits": 20})
                tool_results.append({"tool": "list_project_tree", "result": tree})
                tool_results.append({"tool": "search_project", "result": search})
                _append_timeline(timeline, "tool_project", "Проект", "done", f"Файлов: {tree.get('count',0)}, хитов: {search.get('count',0)}")
                snippets = search.get("items") or search.get("results") or []
                if snippets:
                    rendered = [f"- {(i.get('path','') if isinstance(i,dict) else str(i))}: {(i.get('snippet','') or i.get('preview','') if isinstance(i,dict) else '')}" for i in snippets[:10]]
                    context_parts.append("Project:\n" + "\n".join(rendered))

            elif tool_name == "project_patch":
                _append_timeline(timeline, "tool_patch", "Патчинг", "ready", "Доступен")
                context_parts.append("Patch mode: preview_project_patch / apply_project_patch / replace_in_file")

            elif tool_name == "python_executor":
                _append_timeline(timeline, "tool_python", "Python", "ready", "Доступен")

        except Exception as exc:
            _append_timeline(timeline, f"tool_{tool_name}", tool_name, "error", str(exc))

    return "\n\n".join(p for p in context_parts if p.strip())


# ═══════════════════════════════════════════════════════════════
# run_agent — полный ответ (без стрима)
# ═══════════════════════════════════════════════════════════════

def run_agent(*, model_name, profile_name, user_input, use_memory=True, use_library=True, history=None):
    history = _trim_history(history or [])
    timeline, tool_results = [], []
    planner = PlannerV2Service()
    run = _HISTORY.start_run(user_input)

    try:
        plan = planner.plan(user_input)
        _HISTORY.add_event(run["run_id"], "planner", plan)
        route = plan.get("route", "chat")
        _append_timeline(timeline, "planner", "Планировщик", "done", f"route={route}")

        selected = [t for t in plan.get("tools", [])
                     if not (t == "memory_search" and not use_memory)
                     and not (t == "library_context" and not use_library)]

        ctx = _collect_tool_context(profile_name=profile_name, user_input=user_input, tools=selected, tool_results=tool_results, timeline=timeline)
        prompt = _build_prompt(user_input, plan, ctx)

        draft = run_chat(model_name=model_name, profile_name=profile_name, user_input=prompt, history=history)
        if not draft.get("ok"):
            raise RuntimeError("; ".join(draft.get("warnings", [])) or "LLM failed")
        answer = draft.get("answer", "")

        if route in _REFLECTION_ROUTES:
            ref = run_reflection_loop(model_name=model_name, profile_name=profile_name, user_input=user_input, draft_text=answer, review_text="Проверь ответ на ясность и конкретность.", context=ctx)
            answer = ref.get("answer") or answer

        result = {"ok": True, "answer": answer, "timeline": timeline, "tool_results": tool_results, "meta": {"model_name": model_name, "profile_name": profile_name, "route": route, "tools": selected, "run_id": run["run_id"]}}
        _HISTORY.finish_run(run["run_id"], result)
        return result
    except Exception as exc:
        err = {"ok": False, "answer": "", "timeline": timeline + [{"step": "error", "title": "Ошибка", "status": "error", "detail": str(exc)}], "tool_results": tool_results, "meta": {"error": str(exc), "run_id": run["run_id"]}}
        _HISTORY.finish_run(run["run_id"], err)
        return err


# ═══════════════════════════════════════════════════════════════
# run_agent_stream — SSE генератор с фазами прогресса
# ═══════════════════════════════════════════════════════════════

def run_agent_stream(*, model_name, profile_name, user_input, use_memory=True, use_library=True, history=None):
    history = _trim_history(history or [])
    timeline, tool_results = [], []
    planner = PlannerV2Service()
    run = _HISTORY.start_run(user_input)

    try:
        plan = planner.plan(user_input)
        _HISTORY.add_event(run["run_id"], "planner", plan)
        route = plan.get("route", "chat")

        selected = [t for t in plan.get("tools", [])
                     if not (t == "memory_search" and not use_memory)
                     and not (t == "library_context" and not use_library)]

        # ── Фаза 1: сообщаем фронту что начали инструменты ──
        has_web = "web_search" in selected
        if has_web:
            yield {"token": "", "done": False, "phase": "searching", "message": "Поиск в интернете..."}
        elif selected:
            yield {"token": "", "done": False, "phase": "tools", "message": "Подготовка контекста..."}

        ctx = _collect_tool_context(profile_name=profile_name, user_input=user_input, tools=selected, tool_results=tool_results, timeline=timeline)

        # ── Фаза 2: инструменты готовы, начинаем LLM ──
        yield {"token": "", "done": False, "phase": "thinking", "message": "Генерация ответа..."}

        prompt = _build_prompt(user_input, plan, ctx)

        # ── Фаза 3: стриминг токенов ──
        full_text = ""
        for token in run_chat_stream(model_name=model_name, profile_name=profile_name, user_input=prompt, history=history):
            full_text += token
            yield {"token": token, "done": False}

        # ── Фаза 4: reflection (только code/project) ──
        if route in _REFLECTION_ROUTES and full_text.strip():
            yield {"token": "", "done": False, "phase": "reflecting", "message": "Проверка ответа..."}
            ref = run_reflection_loop(model_name=model_name, profile_name=profile_name, user_input=user_input, draft_text=full_text, review_text="Проверь ответ на ясность и конкретность.", context=ctx)
            refined = ref.get("answer", "")
            if refined and refined != full_text:
                full_text = refined
                yield {"token": "", "done": False, "phase": "reflection_replace", "full_text": refined}

        meta = {"model_name": model_name, "profile_name": profile_name, "route": route, "tools": selected, "run_id": run["run_id"]}
        _HISTORY.finish_run(run["run_id"], {"ok": True, "answer": full_text, "meta": meta})

        yield {"token": "", "done": True, "full_text": full_text, "meta": meta, "timeline": timeline}

    except Exception as exc:
        _HISTORY.finish_run(run["run_id"], {"ok": False, "error": str(exc)})
        yield {"token": "", "done": True, "error": str(exc), "full_text": ""}
