"""Living Persona step C — proactivity (initiative by approved triggers).

Elira stays fully reactive unless the user opts in. Two gates protect this:
  1. a global master switch `proactive_enabled` (default OFF) — nothing fires,
     nothing even asks, until the user turns it on once;
  2. per-trigger first-fire approval — the first time a trigger's condition is
     met, Elira asks ONCE (via the existing approval-gate) "включить триггер X?".
     Approve -> it may act going forward; deny -> it stays silent forever.

When a trigger is approved it produces a SUGGESTION (surfaced as text in Elira's
reply). Read-only context-gathering happens inside the turn; any side-effecting
action the user accepts still goes through the normal per-action approval-gate.
Forbidden actions are always blocked; mood/mode never loosen this. A rate-limit
keeps it from nagging.

`scheduled` is registered but NOT evaluated here — time-based proactivity needs
an out-of-band delivery channel (no open chat), which is a separate decision.
"""
from __future__ import annotations

import logging
import subprocess
import time
import uuid

from app.application.persona import store as persona_store

logger = logging.getLogger(__name__)

TRIGGERS: dict[str, dict[str, str]] = {
    "next_step": {
        "title": "Предлагать следующий шаг после задачи",
        "description": "После успешной проверенной правки предлагать следующий шаг (коммит и т.п.).",
    },
    "noticed_issue": {
        "title": "Отмечать замеченные проблемы",
        "description": "Если правил файлы, но не прогнал проверку — мягко напомнить прогнать тесты.",
    },
    "state_condition": {
        "title": "Подсказывать по состоянию проекта",
        "description": "Если в проекте есть незакоммиченные изменения — предложить коммит.",
    },
    "scheduled": {
        "title": "Действовать по расписанию",
        "description": "Периодические проактивные действия (дайджесты). Требует канала доставки.",
    },
}

# Triggers evaluated at end-of-turn, in priority order (at most ONE fires per
# turn — anti-spam). `scheduled` is intentionally excluded (needs a delivery
# channel decision).
_TURN_TRIGGER_ORDER = ("noticed_issue", "next_step", "state_condition")
_VALID_STATUS = ("unknown", "approved", "denied")
_RATE_LIMIT_SECONDS = 600.0
_ENABLE_ASK_TTL = 3600


def proactive_enabled() -> bool:
    """Master switch (default OFF). Until this is on, nothing fires or asks.
    Backed by the feature-flag mechanism (env ELIRA_PROACTIVE → file → False)."""
    try:
        from app.application.feature_flags import flag_enabled

        return flag_enabled("proactive")
    except Exception:
        return False


def _ensure_table() -> None:
    conn = persona_store.connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS persona_triggers (
                trigger_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'unknown',
                last_fired_at REAL NOT NULL DEFAULT 0,
                decided_at TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def get_trigger(trigger_id: str) -> dict:
    _ensure_table()
    conn = persona_store.connect()
    try:
        row = conn.execute(
            "SELECT trigger_id, status, last_fired_at, decided_at FROM persona_triggers WHERE trigger_id = ?",
            (trigger_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"trigger_id": trigger_id, "status": "unknown", "last_fired_at": 0.0, "decided_at": None}
    return dict(row)


def set_trigger_status(trigger_id: str, status: str) -> dict:
    if trigger_id not in TRIGGERS:
        return {"ok": False, "error": "unknown_trigger"}
    if status not in _VALID_STATUS:
        return {"ok": False, "error": "invalid_status"}
    _ensure_table()
    conn = persona_store.connect()
    try:
        conn.execute(
            """
            INSERT INTO persona_triggers(trigger_id, status, last_fired_at, decided_at)
            VALUES (?, ?, 0, ?)
            ON CONFLICT(trigger_id) DO UPDATE SET
                status = excluded.status,
                decided_at = excluded.decided_at
            """,
            (trigger_id, status, persona_store.utc_now()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, **get_trigger(trigger_id)}


def _mark_fired(trigger_id: str) -> None:
    _ensure_table()
    conn = persona_store.connect()
    try:
        conn.execute(
            """
            INSERT INTO persona_triggers(trigger_id, status, last_fired_at)
            VALUES (?, 'unknown', ?)
            ON CONFLICT(trigger_id) DO UPDATE SET last_fired_at = excluded.last_fired_at
            """,
            (trigger_id, time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def list_triggers() -> dict:
    out = []
    for trigger_id, meta in TRIGGERS.items():
        tr = get_trigger(trigger_id)
        out.append({
            "trigger_id": trigger_id,
            "title": meta["title"],
            "description": meta["description"],
            "status": tr["status"],
            "last_fired_at": tr["last_fired_at"],
            "decided_at": tr["decided_at"],
        })
    return {"ok": True, "proactive_enabled": proactive_enabled(), "triggers": out}


def _has_uncommitted(project_root: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def _evaluate(trigger_id: str, ctx: dict) -> str | None:
    """Return a suggestion message if this trigger's condition holds, else None."""
    edited = bool(ctx.get("edited"))
    verified = bool(ctx.get("verified"))
    if trigger_id == "noticed_issue":
        if edited and not verified:
            return "Заметила: ты правил файлы, но проверку не прогонял — прогнать тесты/линтер?"
        return None
    if trigger_id == "next_step":
        if edited and verified:
            return "Готово и проверено. Хочешь, перейдём к коммиту или следующему шагу?"
        return None
    if trigger_id == "state_condition":
        if not edited and _has_uncommitted(str(ctx.get("project_root") or ".")):
            return "В проекте есть незакоммиченные изменения — сделать коммит?"
        return None
    return None


def _create_enable_ask(trigger_id: str, ctx: dict) -> str | None:
    """Reuse the existing approval-gate to ask, once, whether to enable a trigger."""
    try:
        from app.application.monitoring import runtime as mon

        approval = mon.create_approval(
            id=uuid.uuid4().hex,
            tool_name=f"proactive:{trigger_id}",
            agent_id="persona-proactive",
            source="proactive",
            run_id=str(ctx.get("run_id") or ""),
            project_scope_id=str(ctx.get("project_scope_id") or ""),
            args={
                "trigger_id": trigger_id,
                "title": TRIGGERS[trigger_id]["title"],
                "description": TRIGGERS[trigger_id]["description"],
            },
            ttl_seconds=_ENABLE_ASK_TTL,
        )
        return approval["id"]
    except Exception as exc:
        logger.warning("proactive enable-ask failed for %s: %s", trigger_id, exc)
        return None


def consider_proactive(ctx: dict) -> dict:
    """Evaluate end-of-turn triggers. Returns at most one item (anti-spam):
    a ready suggestion (approved trigger) or an enable-ask (unknown trigger).
    Master switch OFF -> empty. Fully fail-safe."""
    out: dict = {"suggestions": [], "enable_asks": []}
    try:
        if not proactive_enabled():
            return out
        for trigger_id in _TURN_TRIGGER_ORDER:
            message = _evaluate(trigger_id, ctx)
            if not message:
                continue
            tr = get_trigger(trigger_id)
            status = tr["status"]
            if status == "denied":
                continue
            if (time.time() - float(tr.get("last_fired_at") or 0)) < _RATE_LIMIT_SECONDS:
                continue
            if status == "approved":
                out["suggestions"].append(message)
                _mark_fired(trigger_id)
            elif status == "unknown":
                approval_id = _create_enable_ask(trigger_id, ctx)
                if approval_id:
                    out["enable_asks"].append({
                        "trigger_id": trigger_id,
                        "title": TRIGGERS[trigger_id]["title"],
                        "approval_id": approval_id,
                    })
                    _mark_fired(trigger_id)
            break  # at most one proactive item per turn
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("consider_proactive failed: %s", exc)
    return out


def decide_from_approval(tool_name: str, status: str) -> None:
    """Hook: when a `proactive:<id>` approval is approved/rejected, persist the
    trigger's decision so it is remembered (approve once, or deny forever)."""
    if not (tool_name or "").startswith("proactive:"):
        return
    trigger_id = tool_name.split(":", 1)[1]
    if trigger_id not in TRIGGERS:
        return
    set_trigger_status(trigger_id, "approved" if status == "approved" else "denied")


# ── Scheduled trigger (out-of-chat delivery via a pending-suggestions queue) ──
# The three in-stream triggers ride the approval-gate during an open SSE turn.
# `scheduled` has no open chat, so its first-fire ask AND its digest are written
# to a small queue that the frontend polls (in-app banner/toast) + surfaces as a
# desktop OS toast. Same double gate: master switch + per-trigger first-fire.
_CHECKIN_DEFAULT = "09:00"


def _ensure_aux_tables() -> None:
    conn = persona_store.connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS persona_pending_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger_id TEXT NOT NULL,
                kind TEXT NOT NULL,               -- 'enable_ask' | 'digest'
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                read_at TEXT,
                dismissed_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS persona_proactive_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                checkin_time TEXT NOT NULL,
                last_checkin_date TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def get_proactive_config() -> dict:
    _ensure_aux_tables()
    conn = persona_store.connect()
    try:
        row = conn.execute(
            "SELECT checkin_time, last_checkin_date FROM persona_proactive_config WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"checkin_time": _CHECKIN_DEFAULT, "last_checkin_date": None}
    return {"checkin_time": row["checkin_time"], "last_checkin_date": row["last_checkin_date"]}


def _valid_hhmm(value: str) -> bool:
    try:
        h, m = (value or "").split(":")
        return 0 <= int(h) <= 23 and 0 <= int(m) <= 59
    except Exception:
        return False


def set_checkin_time(checkin_time: str) -> dict:
    if not _valid_hhmm(checkin_time):
        return {"ok": False, "error": "invalid_time", **get_proactive_config()}
    _ensure_aux_tables()
    conn = persona_store.connect()
    try:
        conn.execute(
            """
            INSERT INTO persona_proactive_config(id, checkin_time, last_checkin_date)
            VALUES (1, ?, NULL)
            ON CONFLICT(id) DO UPDATE SET checkin_time = excluded.checkin_time
            """,
            (checkin_time,),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, **get_proactive_config()}


def _set_last_checkin_date(date_str: str) -> None:
    _ensure_aux_tables()
    conn = persona_store.connect()
    try:
        conn.execute(
            """
            INSERT INTO persona_proactive_config(id, checkin_time, last_checkin_date)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET last_checkin_date = excluded.last_checkin_date
            """,
            (_CHECKIN_DEFAULT, date_str),
        )
        conn.commit()
    finally:
        conn.close()


def queue_suggestion(trigger_id: str, kind: str, title: str, body: str) -> int:
    _ensure_aux_tables()
    conn = persona_store.connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO persona_pending_suggestions(trigger_id, kind, title, body, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (trigger_id, kind, title, body, persona_store.utc_now()),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_pending(only_unread: bool = True) -> list[dict]:
    _ensure_aux_tables()
    where = "dismissed_at IS NULL" + (" AND read_at IS NULL" if only_unread else "")
    conn = persona_store.connect()
    try:
        rows = conn.execute(
            f"SELECT id, trigger_id, kind, title, body, created_at, read_at, dismissed_at "
            f"FROM persona_pending_suggestions WHERE {where} ORDER BY id ASC"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def mark_read(suggestion_id: int) -> dict:
    return _stamp(suggestion_id, "read_at")


def dismiss_suggestion(suggestion_id: int) -> dict:
    return _stamp(suggestion_id, "dismissed_at")


def _stamp(suggestion_id: int, column: str) -> dict:
    _ensure_aux_tables()
    conn = persona_store.connect()
    try:
        conn.execute(
            f"UPDATE persona_pending_suggestions SET {column} = ? WHERE id = ?",
            (persona_store.utc_now(), int(suggestion_id)),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


def respond_suggestion(suggestion_id: int, decision: str) -> dict:
    """Resolve an enable_ask suggestion: set the trigger's status and dismiss it."""
    _ensure_aux_tables()
    conn = persona_store.connect()
    try:
        row = conn.execute(
            "SELECT trigger_id, kind FROM persona_pending_suggestions WHERE id = ?",
            (int(suggestion_id),),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"ok": False, "error": "not_found"}
    if row["kind"] == "enable_ask":
        set_trigger_status(row["trigger_id"], "approved" if decision == "approve" else "denied")
    dismiss_suggestion(suggestion_id)
    return {"ok": True, "trigger_id": row["trigger_id"], "decision": decision}


def build_checkin_digest() -> str:
    """A warm, time-based daily check-in. Code-built (no LLM call in the daemon),
    no project-git claims — the daemon has no active-project context, so concrete
    project nudges stay with the in-stream triggers."""
    import datetime

    hour = datetime.datetime.now().hour
    if 5 <= hour < 12:
        greet = "Доброе утро"
    elif 12 <= hour < 18:
        greet = "Добрый день"
    else:
        greet = "Добрый вечер"
    return (
        f"{greet}! Я на связи. Загляни, когда будет минутка — разберём, "
        "что в работе и что стоит сделать дальше."
    )


def run_scheduled_checkin(now=None) -> dict | None:
    """Daemon tick: once per day at/after the configured time, queue either the
    first-fire enable-ask (status unknown) or the check-in digest (approved).
    Master switch OFF or already-done-today -> nothing. Fully fail-safe."""
    try:
        if not proactive_enabled():
            return None
        import datetime

        now = now or datetime.datetime.now()
        today = now.strftime("%Y-%m-%d")
        cfg = get_proactive_config()
        if cfg.get("last_checkin_date") == today:
            return None
        if now.strftime("%H:%M") < (cfg.get("checkin_time") or _CHECKIN_DEFAULT):
            return None
        # Stamp the day FIRST so a minute-granularity daemon can't double-fire.
        _set_last_checkin_date(today)
        tr = get_trigger("scheduled")
        if tr["status"] == "denied":
            return None
        if tr["status"] == "unknown":
            qid = queue_suggestion(
                "scheduled",
                "enable_ask",
                "Ежедневный чек-ин Elira",
                f"Раз в день (в {cfg.get('checkin_time') or _CHECKIN_DEFAULT}) Elira может "
                "присылать короткий тёплый чек-ин. Включить?",
            )
            return {"kind": "enable_ask", "suggestion_id": qid}
        qid = queue_suggestion("scheduled", "digest", "Чек-ин Elira", build_checkin_digest())
        return {"kind": "digest", "suggestion_id": qid}
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("run_scheduled_checkin failed: %s", exc)
        return None


def start_proactive_scheduler(*, interval_seconds: float = 60.0, stop_event=None, tick_fn=None):
    """Daemon thread that periodically evaluates the scheduled check-in. Mirrors
    task_planner.start_task_recovery_scheduler: daemon thread, best-effort,
    stop_event for graceful shutdown/tests."""
    import threading
    import time as _time

    tick = tick_fn or run_scheduled_checkin

    def _loop() -> None:
        while True:
            if stop_event is not None:
                if stop_event.wait(interval_seconds):
                    return
            else:
                _time.sleep(interval_seconds)
            try:
                tick()
            except Exception as exc:
                logger.warning("proactive scheduler tick failed: %s", exc)

    thread = threading.Thread(target=_loop, name="persona-proactive", daemon=True)
    thread.start()
    return thread
