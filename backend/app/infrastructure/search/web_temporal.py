from __future__ import annotations

from typing import Any, Callable

from app.infrastructure.search.web_runtime import default_tl


TimelineAppender = Any
CleanQueryFunc = Callable[[str], str]
SearchFunc = Callable[..., str]
GetWebResultFunc = Callable[[list[dict]], dict[str, Any]]


def compute_freshness(
    *,
    temporal: dict[str, Any],
    has_current_evidence: bool,
    news_count: int,
    fetched_pages: int,
    deeper_search: bool,
) -> tuple[str, str]:
    """Return freshness_state and freshness_note based on temporal flags."""
    if temporal.get("freshness_sensitive"):
        is_fresh = has_current_evidence and (news_count > 0 or fetched_pages >= 2 or deeper_search)
        state = "fresh_checked" if is_fresh else "unverified_current"
        note = (
            "Freshness status: fresh_checked. Use current web findings as the main evidence."
            if is_fresh
            else "Freshness status: unverified_current. If confidence is limited, say that the data may be outdated or not fully verified."
        )
    elif temporal.get("stable_historical"):
        state = "historical_or_stable"
        note = "Freshness status: historical_or_stable. Treat this as a mostly stable historical topic."
    else:
        state = "standard_web"
        note = "Freshness status: standard_web. Use the web findings naturally without exposing internal formatting."
    return state, note


def try_deep_research(
    query: str,
    temporal: dict[str, Any],
    timeline: list,
    *,
    clean_query_func: CleanQueryFunc,
    tl: TimelineAppender | None = None,
) -> str:
    """Attempt an additional deep research pass if coverage is weak."""
    _tl_fn = tl or default_tl
    try:
        from app.core.web import research_web

        deep_engines = (
            ("wikipedia", "tavily", "duckduckgo")
            if temporal.get("stable_historical")
            else ("tavily", "duckduckgo", "wikipedia")
        )
        deep_context = research_web(
            clean_query_func(query),
            max_results=8,
            pages_to_read=4,
            engines=deep_engines,
            intent_kind="historical" if temporal.get("stable_historical") else "general_web",
        )
        if deep_context:
            _tl_fn(
                timeline,
                "tool_web_deep",
                "РЈРіР»СѓР±Р»РµРЅРЅС‹Р№ РІРµР±-РїРѕРёСЃРє",
                "done",
                "Р”РѕРїРѕР»РЅРёС‚РµР»СЊРЅР°СЏ РїСЂРѕРІРµСЂРєР° РёСЃС‚РѕС‡РЅРёРєРѕРІ",
            )
            return deep_context
    except Exception as exc:
        _tl_fn(
            timeline,
            "tool_web_deep",
            "РЈРіР»СѓР±Р»РµРЅРЅС‹Р№ РІРµР±-РїРѕРёСЃРє",
            "error",
            str(exc),
        )
    return ""


def do_temporal_web_search_legacy(
    query: str,
    timeline: list,
    tool_results: list,
    temporal: dict[str, Any] | None = None,
    *,
    do_web_search_legacy_func: SearchFunc,
    get_web_search_result_func: GetWebResultFunc,
    clean_query_func: CleanQueryFunc,
    tl: TimelineAppender | None = None,
) -> str:
    """Legacy single-pass temporal web search."""
    temporal = temporal or {}
    context = do_web_search_legacy_func(query, timeline, tool_results, tl=tl)
    web_result = get_web_search_result_func(tool_results)
    found = int(web_result.get("found", 0) or 0)
    fetched_pages = int(web_result.get("fetched_pages", 0) or 0)
    news_count = int(web_result.get("news", 0) or 0)
    engines_used = set(web_result.get("engines", []) or [])
    current_evidence_engines = {"tavily", "duckduckgo", "ddg-news"}
    has_current_evidence = bool(engines_used & current_evidence_engines) or news_count > 0
    deeper_search = False

    if temporal.get("requires_web") and temporal.get("reasoning_depth") == "deep":
        weak_coverage = found < 4 or fetched_pages < 2 or (
            temporal.get("freshness_sensitive") and not has_current_evidence
        )
        if weak_coverage:
            deep_context = try_deep_research(
                query,
                temporal,
                timeline,
                clean_query_func=clean_query_func,
                tl=tl,
            )
            if deep_context:
                deeper_search = True
                context = (
                    context + "\n\nР”РѕРїРѕР»РЅРёС‚РµР»СЊРЅС‹Р№ СѓРіР»СѓР±Р»РµРЅРЅС‹Р№ РІРµР±-РїРѕРёСЃРє:\n" + deep_context
                    if context
                    else deep_context
                )

    freshness_state, freshness_note = compute_freshness(
        temporal=temporal,
        has_current_evidence=has_current_evidence,
        news_count=news_count,
        fetched_pages=fetched_pages,
        deeper_search=deeper_search,
    )

    if tool_results and tool_results[-1].get("tool") == "web_search":
        result = tool_results[-1].setdefault("result", {})
        if isinstance(result, dict):
            result["freshness_state"] = freshness_state
            result["deeper_search"] = deeper_search
            result["temporal_mode"] = temporal.get("mode", "none")
            result["has_current_evidence"] = has_current_evidence

    if context:
        context += "\n\n" + freshness_note
    return context


def do_temporal_web_search(
    query: str,
    timeline: list,
    tool_results: list,
    temporal: dict[str, Any] | None = None,
    web_plan: dict[str, Any] | None = None,
    *,
    do_web_search_func: SearchFunc,
    get_web_search_result_func: GetWebResultFunc,
    clean_query_func: CleanQueryFunc,
    tl: TimelineAppender | None = None,
) -> str:
    """Planner-driven multi-pass temporal web search."""
    temporal = temporal or {}
    context = do_web_search_func(query, timeline, tool_results, web_plan=web_plan, tl=tl)
    web_result = get_web_search_result_func(tool_results)
    found = int(web_result.get("found", 0) or 0)
    fetched_pages = int(web_result.get("fetched_pages", 0) or 0)
    news_count = int(web_result.get("news", 0) or 0)
    subquery_count = int(
        web_result.get("total_subqueries", len(web_result.get("subqueries", []) or [])) or 0
    )
    engines_used = set(web_result.get("engines", []) or [])
    current_evidence_engines = {"tavily", "duckduckgo", "ddg-news"}
    has_current_evidence = bool(engines_used & current_evidence_engines) or news_count > 0
    deeper_search = bool(web_result.get("deeper_search_used"))

    if temporal.get("requires_web") and temporal.get("reasoning_depth") == "deep":
        weak_coverage = (
            found < max(4, subquery_count * 2)
            or fetched_pages < max(2, subquery_count)
            or (temporal.get("freshness_sensitive") and not has_current_evidence)
        )
        if weak_coverage:
            deep_context = try_deep_research(
                query,
                temporal,
                timeline,
                clean_query_func=clean_query_func,
                tl=tl,
            )
            if deep_context:
                deeper_search = True
                context = (
                    context + "\n\nР”РѕРїРѕР»РЅРёС‚РµР»СЊРЅС‹Р№ СѓРіР»СѓР±Р»РµРЅРЅС‹Р№ РІРµР±-РїРѕРёСЃРє:\n" + deep_context
                    if context
                    else deep_context
                )

    freshness_state, freshness_note = compute_freshness(
        temporal=temporal,
        has_current_evidence=has_current_evidence,
        news_count=news_count,
        fetched_pages=fetched_pages,
        deeper_search=deeper_search,
    )

    if tool_results and tool_results[-1].get("tool") == "web_search":
        result = tool_results[-1].setdefault("result", {})
        if isinstance(result, dict):
            result["freshness_state"] = freshness_state
            result["deeper_search"] = deeper_search
            result["temporal_mode"] = temporal.get("mode", "none")
            result["has_current_evidence"] = has_current_evidence

    if context:
        context += "\n\n" + freshness_note
    return context
