from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable


def load_legacy_runs(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [item for item in payload.values() if isinstance(item, dict)]
    return []


def rotate_runs(*, conn: Any, max_runs: int) -> None:
    total = conn.execute("SELECT COUNT(*) FROM run_history").fetchone()[0]
    overflow = total - max_runs
    if overflow <= 0:
        return
    conn.execute(
        """
        DELETE FROM run_history
        WHERE id IN (
            SELECT id
            FROM run_history
            ORDER BY finished_at ASC, id ASC
            LIMIT ?
        )
        """,
        (overflow,),
    )


def init_db(
    *,
    connect_func: Callable[[], Any],
    legacy_json_paths: Iterable[Path],
    max_runs: int,
    load_legacy_runs_func: Callable[[Path], list[dict[str, Any]]],
    rotate_func: Callable[[Any], None],
) -> None:
    conn = connect_func()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL UNIQUE,
                user_input TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                ok INTEGER NOT NULL DEFAULT 0,
                route TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                answer_len INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT ''
            )
            """
        )

        total = conn.execute("SELECT COUNT(*) FROM run_history").fetchone()[0]
        if total == 0:
            for path in legacy_json_paths:
                legacy_runs = load_legacy_runs_func(path)
                if not legacy_runs:
                    continue
                for index, item in enumerate(legacy_runs[-max_runs:]):
                    run_id = str(item.get("run_id") or f"legacy-{index}")
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO run_history (
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
                        """,
                        (
                            run_id,
                            str(item.get("user_input", "")),
                            str(item.get("started_at") or item.get("finished_at") or datetime.utcnow().isoformat()),
                            str(item.get("finished_at") or item.get("started_at") or datetime.utcnow().isoformat()),
                            1 if item.get("ok") else 0,
                            str(item.get("route", "")),
                            str(item.get("model", "")),
                            int(item.get("answer_len") or 0),
                            str(item.get("error", "")),
                        ),
                    )
                break

        rotate_func(conn)
        conn.commit()
    finally:
        conn.close()

