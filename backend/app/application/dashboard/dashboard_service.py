"""Dashboard statistics aggregation service."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from app.infrastructure.db.run_history_service import RunHistoryService

_HISTORY = RunHistoryService()


def get_dashboard_stats() -> dict:
    runs = _HISTORY.list_runs(limit=500)
    now = datetime.utcnow()

    total = len(runs)
    success = sum(1 for run in runs if run.get("ok"))
    fail = total - success

    today_count = week_count = 0
    daily: Counter = Counter()

    for run in runs:
        try:
            finished_at = datetime.fromisoformat(run.get("finished_at", ""))
        except Exception:
            continue
        delta = (now - finished_at).total_seconds()
        if delta < 86400:
            today_count += 1
        if delta < 604800:
            week_count += 1
        daily[finished_at.strftime("%d.%m")] += 1

    model_counter = Counter(run.get("model", "unknown") for run in runs if run.get("model"))
    route_counter = Counter(run.get("route", "unknown") for run in runs if run.get("route"))
    lengths = [int(run.get("answer_len", 0)) for run in runs if run.get("answer_len")]
    avg_len = round(sum(lengths) / len(lengths)) if lengths else 0

    days_list = [
        {"date": (now - timedelta(days=i)).strftime("%d.%m"), "count": daily.get((now - timedelta(days=i)).strftime("%d.%m"), 0)}
        for i in range(13, -1, -1)
    ]

    memory_stats: dict = {"total": 0, "categories": {}}
    try:
        from app.application.memory.smart_memory import get_stats
        memory_stats = get_stats()
    except Exception:
        pass

    chat_count = message_count = 0
    try:
        from app.infrastructure.db.elira_memory_sqlite import get_messages, list_chats
        chats = list_chats()
        chat_count = len(chats)
        for chat in chats[:50]:
            message_count += len(get_messages(chat["id"]) or [])
    except Exception:
        pass

    plugin_count = 0
    try:
        from app.infrastructure.plugins.plugin_system import list_plugins
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
        "top_models": [{"model": m, "count": c} for m, c in model_counter.most_common(10)],
        "top_routes": [{"route": r, "count": c} for r, c in route_counter.most_common(10)],
        "daily_activity": days_list,
        "chats": chat_count,
        "messages": message_count,
        "memory": memory_stats,
        "plugins": plugin_count,
    }
