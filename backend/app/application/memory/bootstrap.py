from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


TimestampFactory = Callable[[], str]

SETTINGS_DEFAULTS = {
    "active_mem_profile": "default",
    "model": "qwen3:8b",
}


def load_settings(*, settings_path: str | Path) -> dict[str, Any]:
    try:
        path = Path(settings_path)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return {**SETTINGS_DEFAULTS, **data}
    except Exception:
        pass
    return dict(SETTINGS_DEFAULTS)


def save_settings(*, settings_path: str | Path, settings: dict[str, Any]) -> None:
    try:
        Path(settings_path).write_text(
            json.dumps(settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def init_db(*, db_path: str | Path, now_iso_func: TimestampFactory) -> None:
    """No-op stub.

    Historical: this used to CREATE the 11-table legacy schema in
    data/memory.db (memories, mem_profiles, tool_usage, knowledge_chunks,
    task_runs, reflections, working_memory, self_improve_runs,
    v8_strategy_usage, web_learning_runs, memory_compaction_runs).

    Today: all eleven tables stayed at 0 rows in production. The
    write paths in domain/agents/orchestrator + planner + reflection
    are wrapped in `try/except: pass`, and the new memory system
    (smart_memory.db for TF-IDF facts, rag_memory.db for vectors)
    replaced this entirely.

    Kept as a no-op rather than removed so any orphan facade chain that
    still imports it doesn't crash. The orphan chain itself
    (app/core/memory.py, app/application/memory/profiles.py) has been
    deleted in the same commit.
    """
    return
