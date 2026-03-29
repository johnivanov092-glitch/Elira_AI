"""Dashboard API — статистика использования Elira AI."""
from __future__ import annotations
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

_HISTORY_FILE = Path("data/run_history.json")


def _load_history() -> list[dict]:
    try:
        if _HISTORY_FILE.exists():
            data = json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else list(data.values()) if isinstance(data, dict) else []
    except Exception:
        pass
    return []


@router.get("/stats")
def dashboard_stats():
    """Основная статистика для дашборда."""
    runs = _load_history()
    now = datetime.utcnow()

    # Общее
    total = len(runs)
    success = sum(1 for r in runs if r.get("ok"))
    fail = total - success

    # За последние 24ч / 7 дней
    today_count = 0
    week_count = 0
    for r in runs:
        try:
            t = datetime.fromisoformat(r.get("finished_at", ""))
            if (now - t).total_seconds() < 86400:
                today_count += 1
            if (now - t).total_seconds() < 604800:
                week_count += 1
        except Exception:
            pass

    # Модели — частота использования
    model_counter = Counter(r.get("model", "unknown") for r in runs if r.get("model"))
    top_models = model_counter.most_common(10)

    # Роуты — типы задач
    route_counter = Counter(r.get("route", "unknown") for r in runs if r.get("route"))
    top_routes = route_counter.most_common(10)

    # Средняя длина ответа
    lengths = [r.get("answer_len", 0) for r in runs if r.get("answer_len")]
    avg_len = round(sum(lengths) / len(lengths)) if lengths else 0

    # Активность по дням (последние 14 дней)
    daily = Counter()
    for r in runs:
        try:
            t = datetime.fromisoformat(r.get("finished_at", ""))
            day = t.strftime("%d.%m")
            daily[day] += 1
        except Exception:
            pass
    # Последние 14 дней
    days_list = []
    for i in range(13, -1, -1):
        d = (now - timedelta(days=i)).strftime("%d.%m")
        days_list.append({"date": d, "count": daily.get(d, 0)})

    # Память
    memory_stats = {"total": 0, "categories": {}}
    try:
        from app.services.smart_memory import get_stats
        memory_stats = get_stats()
    except Exception:
        pass

    # Чаты
    chat_count = 0
    message_count = 0
    try:
        from app.services.elira_memory_sqlite import list_chats
        chats = list_chats()
        chat_count = len(chats)
        # Считаем общее кол-во сообщений
        from app.services.elira_memory_sqlite import get_messages
        for c in chats[:50]:  # Лимитируем для скорости
            msgs = get_messages(c["id"])
            message_count += len(msgs) if msgs else 0
    except Exception:
        pass

    # Плагины
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
        "top_models": [{"model": m, "count": c} for m, c in top_models],
        "top_routes": [{"route": r, "count": c} for r, c in top_routes],
        "daily_activity": days_list,
        "chats": chat_count,
        "messages": message_count,
        "memory": memory_stats,
        "plugins": plugin_count,
    }
