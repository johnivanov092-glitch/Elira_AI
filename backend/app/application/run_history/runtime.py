from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Callable


class RunHistoryService:
    def __init__(
        self,
        *,
        connect_func: Callable[[], Any],
        rotate_func: Callable[[Any], None],
        now_func: Callable[[], str] | None = None,
    ) -> None:
        self._connect = connect_func
        self._rotate = rotate_func
        self._now = now_func or (lambda: datetime.utcnow().isoformat())
        self._active_runs: dict[str, dict[str, Any]] = {}

    def start_run(self, user_input: str) -> dict[str, Any]:
        run = {
            "run_id": str(uuid.uuid4())[:8],
            "user_input": user_input or "",
            "started_at": self._now(),
            "events": [],
        }
        self._active_runs[run["run_id"]] = dict(run)
        return run

    def add_event(self, run_id: str, event_type: str, data: Any) -> None:
        active = self._active_runs.get(run_id)
        if active is not None:
            active.setdefault("events", []).append(
                {
                    "event_type": event_type,
                    "data": data,
                    "created_at": self._now(),
                }
            )

    def finish_run(self, run_id: str, result: dict[str, Any]) -> None:
        meta = result.get("meta", {}) if isinstance(result, dict) else {}
        active = self._active_runs.pop(run_id, {})
        now = self._now()
        entry = {
            "run_id": run_id,
            "user_input": str(result.get("user_input") or active.get("user_input", "")) if isinstance(result, dict) else str(active.get("user_input", "")),
            "started_at": str(result.get("started_at") or active.get("started_at") or now) if isinstance(result, dict) else str(active.get("started_at") or now),
            "finished_at": now,
            "ok": 1 if result.get("ok", False) else 0,
            "route": str(meta.get("route", "")),
            "model": str(meta.get("model_name", meta.get("model", ""))),
            "answer_len": len(str(result.get("answer", ""))),
            "error": str(result.get("error", "")),
        }

        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO run_history (
                    run_id,
                    user_input,
                    started_at,
                    finished_at,
                    ok,
                    route,
                    model,
                    answer_len,
                    error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    user_input = excluded.user_input,
                    started_at = excluded.started_at,
                    finished_at = excluded.finished_at,
                    ok = excluded.ok,
                    route = excluded.route,
                    model = excluded.model,
                    answer_len = excluded.answer_len,
                    error = excluded.error
                """,
                (
                    entry["run_id"],
                    entry["user_input"],
                    entry["started_at"],
                    entry["finished_at"],
                    entry["ok"],
                    entry["route"],
                    entry["model"],
                    entry["answer_len"],
                    entry["error"],
                ),
            )
            self._rotate(conn)
            conn.commit()
        finally:
            conn.close()

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, int(limit))
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT run_id, user_input, started_at, finished_at, ok, route, model, answer_len, error
                FROM run_history
                ORDER BY finished_at DESC, id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        finally:
            conn.close()
        items = [dict(row) for row in rows]
        items.reverse()
        return items

