"""
autopipeline_service.py — Autopipelines (cron-задачи) Elira AI.

Лёгкий планировщик на threading.Timer — без внешних зависимостей.
Задачи хранятся в SQLite, выполняются в фоне.

Типы задач:
  - prompt: отправить промпт в LLM и сохранить результат
  - web_search: выполнить веб-поиск по запросу
  - plugin: запустить плагин
  - http: вызвать URL (webhook/API)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any

from app.core.config import DATA_DIR

logger = logging.getLogger(__name__)

DB_PATH = DATA_DIR / "autopipelines.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_scheduler_thread: threading.Timer | None = None
_running = False
_TICK_INTERVAL = 30  # проверка каждые 30 сек


# ═══════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════

def _connect():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    conn = _connect()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pipelines (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                task_type TEXT NOT NULL DEFAULT 'prompt',
                task_data TEXT NOT NULL DEFAULT '{}',
                interval_minutes INTEGER NOT NULL DEFAULT 60,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_run TEXT,
                next_run TEXT,
                run_count INTEGER DEFAULT 0,
                last_result TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pipeline_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pipeline_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                ok INTEGER DEFAULT 0,
                result TEXT,
                error TEXT
            );
        """)
        conn.commit()
    finally:
        conn.close()


_init_db()


# ═══════════════════════════════════════════════════════════════
# CRUD
# ═══════════════════════════════════════════════════════════════

def create_pipeline(
    name: str,
    task_type: str = "prompt",
    task_data: dict = None,
    interval_minutes: int = 60,
    enabled: bool = True,
) -> dict:
    """Создаёт новый pipeline."""
    pid = str(uuid.uuid4())[:8]
    now = datetime.utcnow().isoformat()
    next_run = (datetime.utcnow() + timedelta(minutes=interval_minutes)).isoformat()
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO pipelines (id, name, task_type, task_data, interval_minutes, enabled, next_run, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (pid, name, task_type, json.dumps(task_data or {}), interval_minutes, int(enabled), next_run, now),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "id": pid, "name": name, "next_run": next_run}


def list_pipelines() -> dict:
    """Список всех pipelines."""
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM pipelines ORDER BY created_at DESC").fetchall()
        items = []
        for r in rows:
            d = dict(r)
            try:
                d["task_data"] = json.loads(d.get("task_data") or "{}")
            except Exception:
                d["task_data"] = {}
            d["enabled"] = bool(d.get("enabled"))
            items.append(d)
        return {"ok": True, "pipelines": items, "count": len(items)}
    finally:
        conn.close()


def get_pipeline(pid: str) -> dict:
    """Получить pipeline по id."""
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM pipelines WHERE id = ?", (pid,)).fetchone()
        if not row:
            return {"ok": False, "error": "Pipeline не найден"}
        d = dict(row)
        try:
            d["task_data"] = json.loads(d.get("task_data") or "{}")
        except Exception:
            d["task_data"] = {}
        d["enabled"] = bool(d.get("enabled"))
        return {"ok": True, **d}
    finally:
        conn.close()


def update_pipeline(pid: str, **kwargs) -> dict:
    """Обновляет поля pipeline."""
    allowed = {"name", "task_type", "task_data", "interval_minutes", "enabled"}
    updates = []
    values = []
    for k, v in kwargs.items():
        if k not in allowed:
            continue
        if k == "task_data":
            v = json.dumps(v) if isinstance(v, dict) else v
        if k == "enabled":
            v = int(v)
        updates.append(f"{k} = ?")
        values.append(v)

    if not updates:
        return {"ok": False, "error": "Нечего обновлять"}

    conn = _connect()
    try:
        values.append(pid)
        conn.execute(f"UPDATE pipelines SET {', '.join(updates)} WHERE id = ?", values)
        conn.commit()
        return {"ok": True, "id": pid, "updated": list(kwargs.keys())}
    finally:
        conn.close()


def delete_pipeline(pid: str) -> dict:
    """Удаляет pipeline и его логи."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM pipelines WHERE id = ?", (pid,))
        conn.execute("DELETE FROM pipeline_logs WHERE pipeline_id = ?", (pid,))
        conn.commit()
        return {"ok": True, "deleted": pid}
    finally:
        conn.close()


def get_pipeline_logs(pid: str, limit: int = 20) -> dict:
    """История выполнения pipeline."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM pipeline_logs WHERE pipeline_id = ? ORDER BY started_at DESC LIMIT ?",
            (pid, limit),
        ).fetchall()
        return {"ok": True, "logs": [dict(r) for r in rows], "count": len(rows)}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
# ВЫПОЛНЕНИЕ ЗАДАЧ
# ═══════════════════════════════════════════════════════════════

def _execute_task(task_type: str, task_data: dict) -> dict:
    """Выполняет задачу по типу."""
    try:
        if task_type == "prompt":
            prompt = task_data.get("prompt", "")
            model = task_data.get("model", "")
            if not prompt:
                return {"ok": False, "error": "Нет промпта"}
            from app.services.agents_service import run_agent
            result = run_agent(
                model_name=model or "gemma3:4b",
                profile_name=task_data.get("profile", "Универсальный"),
                user_input=prompt,
                use_memory=False,
                use_web_search=task_data.get("web_search", False),
            )
            answer = result.get("answer", "")[:2000]
            return {"ok": True, "answer": answer, "length": len(answer)}

        elif task_type == "web_search":
            query = task_data.get("query", "")
            if not query:
                return {"ok": False, "error": "Нет запроса"}
            from app.services.web_multisearch_service import multi_search
            return multi_search(query, max_results=task_data.get("max_results", 5))

        elif task_type == "plugin":
            name = task_data.get("plugin_name", "")
            if not name:
                return {"ok": False, "error": "Нет имени плагина"}
            from app.services.plugin_system import run_plugin
            return run_plugin(name, task_data.get("args", {}))

        elif task_type == "http":
            import requests
            url = task_data.get("url", "")
            method = task_data.get("method", "GET").upper()
            if not url:
                return {"ok": False, "error": "Нет URL"}
            resp = requests.request(method, url, timeout=30, json=task_data.get("body"))
            return {"ok": True, "status": resp.status_code, "body": resp.text[:2000]}

        else:
            return {"ok": False, "error": f"Неизвестный тип: {task_type}"}

    except Exception as e:
        return {"ok": False, "error": str(e)}


def run_pipeline_now(pid: str) -> dict:
    """Запускает pipeline вручную прямо сейчас."""
    p = get_pipeline(pid)
    if not p.get("ok"):
        return p

    started = datetime.utcnow().isoformat()
    result = _execute_task(p["task_type"], p.get("task_data", {}))
    finished = datetime.utcnow().isoformat()

    # Сохраняем лог
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO pipeline_logs (pipeline_id, started_at, finished_at, ok, result, error) VALUES (?,?,?,?,?,?)",
            (pid, started, finished, int(result.get("ok", False)),
             json.dumps(result, ensure_ascii=False)[:5000], result.get("error", "")),
        )
        # Обновляем pipeline
        next_run = (datetime.utcnow() + timedelta(minutes=p.get("interval_minutes", 60))).isoformat()
        conn.execute(
            "UPDATE pipelines SET last_run = ?, next_run = ?, run_count = run_count + 1, last_result = ?, last_error = ? WHERE id = ?",
            (finished, next_run, json.dumps(result, ensure_ascii=False)[:2000], result.get("error", ""), pid),
        )
        conn.commit()
    finally:
        conn.close()

    return {"ok": True, "pipeline_id": pid, "result": result}


# ═══════════════════════════════════════════════════════════════
# ПЛАНИРОВЩИК (фоновый тик)
# ═══════════════════════════════════════════════════════════════

def _tick():
    """Проверяет и запускает просроченные pipelines."""
    global _scheduler_thread
    if not _running:
        return

    now = datetime.utcnow().isoformat()
    conn = _connect()
    try:
        due = conn.execute(
            "SELECT id FROM pipelines WHERE enabled = 1 AND next_run <= ?", (now,)
        ).fetchall()
    finally:
        conn.close()

    for row in due:
        try:
            run_pipeline_now(row["id"])
            logger.info(f"Autopipeline {row['id']} executed")
        except Exception as e:
            logger.error(f"Autopipeline {row['id']} error: {e}")

    # Перепланируем следующий тик
    _scheduler_thread = threading.Timer(_TICK_INTERVAL, _tick)
    _scheduler_thread.daemon = True
    _scheduler_thread.start()


def start_scheduler():
    """Запускает фоновый планировщик."""
    global _running, _scheduler_thread
    if _running:
        return {"ok": True, "status": "already_running"}
    _running = True
    _scheduler_thread = threading.Timer(_TICK_INTERVAL, _tick)
    _scheduler_thread.daemon = True
    _scheduler_thread.start()
    logger.info("Autopipeline scheduler started")
    return {"ok": True, "status": "started", "interval": _TICK_INTERVAL}


def stop_scheduler():
    """Останавливает планировщик."""
    global _running, _scheduler_thread
    _running = False
    if _scheduler_thread:
        _scheduler_thread.cancel()
        _scheduler_thread = None
    logger.info("Autopipeline scheduler stopped")
    return {"ok": True, "status": "stopped"}


def scheduler_status() -> dict:
    """Статус планировщика."""
    return {"ok": True, "running": _running, "tick_interval": _TICK_INTERVAL}


# Автозапуск при импорте
start_scheduler()
