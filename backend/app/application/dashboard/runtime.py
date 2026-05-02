"""Application-layer runtime for dashboard statistics.

Aggregates run history, chat counts, memory stats, and plugin counts
into a single stats dict.  All functions return plain dicts (no HTTPException).
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from app.application.run_history_service.runtime import RunHistoryService

_HISTORY = RunHistoryService()


def compute_dashboard_stats() -> dict:
    runs = _HISTORY.list_runs(limit=500)
    now = datetime.utcnow()

    total = len(runs)
    success = sum(1 for run in runs if run.get("ok"))
    fail = total - success

    today_count = 0
    week_count = 0
    for run in runs:
        try:
            finished_at = datetime.fromisoformat(run.get("finished_at", ""))
        except Exception:
            continue
        delta_seconds = (now - finished_at).total_seconds()
        if delta_seconds < 86400:
            today_count += 1
        if delta_seconds < 604800:
            week_count += 1

    model_counter: Counter = Counter(
        run.get("model", "unknown") for run in runs if run.get("model")
    )
    route_counter: Counter = Counter(
        run.get("route", "unknown") for run in runs if run.get("route")
    )
    top_models = model_counter.most_common(10)
    top_routes = route_counter.most_common(10)

    lengths = [int(run.get("answer_len", 0)) for run in runs if run.get("answer_len")]
    avg_len = round(sum(lengths) / len(lengths)) if lengths else 0

    daily: Counter = Counter()
    for run in runs:
        try:
            finished_at = datetime.fromisoformat(run.get("finished_at", ""))
        except Exception:
            continue
        daily[finished_at.strftime("%d.%m")] += 1

    days_list = []
    for index in range(13, -1, -1):
        day = (now - timedelta(days=index)).strftime("%d.%m")
        days_list.append({"date": day, "count": daily.get(day, 0)})

    memory_stats: dict = {"total": 0, "categories": {}}
    try:
        from app.application.smart_memory import get_stats
        memory_stats = get_stats()
    except Exception:
        pass

    chat_count = 0
    message_count = 0
    try:
        from app.application.elira_memory_sqlite.runtime import get_messages, list_chats
        chats = list_chats()
        chat_count = len(chats)
        for chat in chats[:50]:
            message_count += len(get_messages(chat["id"]) or [])
    except Exception:
        pass

    plugin_count = 0
    try:
        from app.services.plugin_system import list_plugins
        plugin_count = list_plugins().get("count", 0)
    except Exception:
        pass

    return {
        "ok": True,
        "total_runs": total,
        "success": success,
        "errors": fail,
        "success_rate": round(success / total * 100, 1) if total else 0,
        "today": today_count,
        "this_week": week_count,
        "avg_answer_length": avg_len,
        "top_models": [{"model": model, "count": count} for model, count in top_models],
        "top_routes": [{"route": route, "count": count} for route, count in top_routes],
        "daily_activity": days_list,
        "chats": chat_count,
        "messages": message_count,
        "memory": memory_stats,
        "plugins": plugin_count,
    }
