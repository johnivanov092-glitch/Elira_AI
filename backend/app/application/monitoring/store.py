from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.infrastructure.db.connection import connect_sqlite


DEFAULT_MAX_RUNS_PER_HOUR = 120
DEFAULT_MAX_EXECUTION_SECONDS = 600  # 10 min — big tasks on a slow local model
DEFAULT_MAX_CONTEXT_TOKENS = 131072
DEFAULT_WORKFLOW_ENGINE_AGENT_ID = "workflow-engine"

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS agent_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_type TEXT NOT NULL,
    agent_id TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    workflow_id TEXT NOT NULL DEFAULT '',
    step_id TEXT NOT NULL DEFAULT '',
    ok INTEGER,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_metrics_type ON agent_metrics(metric_type);
CREATE INDEX IF NOT EXISTS idx_agent_metrics_agent ON agent_metrics(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_metrics_created ON agent_metrics(created_at);

CREATE TABLE IF NOT EXISTS agent_limits (
    agent_id TEXT PRIMARY KEY,
    max_runs_per_hour INTEGER NOT NULL,
    max_execution_seconds INTEGER NOT NULL,
    max_context_tokens INTEGER NOT NULL,
    allowed_tools_json TEXT NOT NULL DEFAULT '[]',
    allowed_scopes_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    workflow_id TEXT NOT NULL DEFAULT '',
    step_id TEXT NOT NULL DEFAULT '',
    resource TEXT NOT NULL,
    amount REAL NOT NULL DEFAULT 0,
    unit TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_resource_usage_agent ON resource_usage(agent_id);
CREATE INDEX IF NOT EXISTS idx_resource_usage_resource ON resource_usage(resource);
CREATE INDEX IF NOT EXISTS idx_resource_usage_created ON resource_usage(created_at);
"""

_APPROVALS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    agent_id TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    project_scope_id TEXT NOT NULL DEFAULT '',
    args_json TEXT NOT NULL DEFAULT '{}',
    args_sha256 TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    ttl_seconds INTEGER NOT NULL DEFAULT 300,
    expires_at TEXT NOT NULL,
    decided_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
CREATE INDEX IF NOT EXISTS idx_approvals_run ON approvals(run_id);
CREATE INDEX IF NOT EXISTS idx_approvals_tool ON approvals(tool_name);
"""

_MODEL_PROFILES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS model_profiles (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT 'llama_server',
    model TEXT NOT NULL,
    role TEXT NOT NULL,
    context_limit INTEGER NOT NULL DEFAULT 16384,
    timeout_seconds INTEGER NOT NULL DEFAULT 120,
    enabled INTEGER NOT NULL DEFAULT 0,
    cloud_consent_required INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_model_profiles_role ON model_profiles(role);
CREATE INDEX IF NOT EXISTS idx_model_profiles_enabled ON model_profiles(enabled);
"""

_LOCAL_LLAMA_PROFILE_IDS = (
    "00-local-llama-fast",
    "00-local-llama-code",
    "00-local-llama-strong",
)

# Default profiles — cloud profiles are disabled by default (Section 11 of roadmap)
_DEFAULT_MODEL_PROFILES = [
    {
        "id": "00-local-llama-fast",
        "provider": "llama_server",
        "model": "local-model",
        "role": "fast",
        "context_limit": 131072,
        "timeout_seconds": 300,
        "enabled": True,
        "cloud_consent_required": False,
    },
    {
        "id": "00-local-llama-code",
        "provider": "llama_server",
        "model": "local-model",
        "role": "code",
        "context_limit": 131072,
        "timeout_seconds": 600,
        "enabled": True,
        "cloud_consent_required": False,
    },
    {
        "id": "00-local-llama-strong",
        "provider": "llama_server",
        "model": "local-model",
        "role": "strong",
        "context_limit": 131072,
        "timeout_seconds": 600,
        "enabled": True,
        "cloud_consent_required": False,
    },
    {
        "id": "local-embedding",
        "provider": "local_embed_server",
        "model": "local-embed",
        "role": "embedding",
        "context_limit": 4096,
        "timeout_seconds": 30,
        "enabled": True,
        "cloud_consent_required": False,
    },
    {
        "id": "cloud-claude-sonnet",
        "provider": "anthropic",
        "model": "claude-sonnet-4-5",
        "role": "strong",
        "context_limit": 200000,
        "timeout_seconds": 120,
        "enabled": False,  # Cloud disabled by default — requires explicit consent
        "cloud_consent_required": True,
    },
]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    return connect_sqlite(db_path)


def init_db(db_path: str | Path) -> None:
    with get_connection(db_path) as con:
        con.executescript(CREATE_SQL)
    migrate_approvals_table(db_path)


def migrate_model_profiles_table(db_path: str | Path) -> None:
    """Additive migration: create model_profiles table and seed defaults."""
    with get_connection(db_path) as con:
        con.executescript(_MODEL_PROFILES_TABLE_SQL)
        now = now_utc()
        for p in _DEFAULT_MODEL_PROFILES:
            con.execute(
                """INSERT OR IGNORE INTO model_profiles
                   (id, provider, model, role, context_limit, timeout_seconds,
                    enabled, cloud_consent_required, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (p["id"], p["provider"], p["model"], p["role"],
                 p["context_limit"], p["timeout_seconds"],
                 1 if p["enabled"] else 0,
                 1 if p["cloud_consent_required"] else 0,
                 now, now),
            )
        con.execute(
            f"""UPDATE model_profiles
                SET context_limit = 131072, updated_at = ?
                WHERE id IN ({",".join("?" for _ in _LOCAL_LLAMA_PROFILE_IDS)})
                  AND provider = 'llama_server'
                  AND model = 'local-model'
                  AND context_limit IN (8192, 16384)""",
            (now, *_LOCAL_LLAMA_PROFILE_IDS),
        )
        con.execute(
            """UPDATE model_profiles
               SET timeout_seconds = CASE role WHEN 'fast' THEN 300 ELSE 600 END,
                   updated_at = ?
               WHERE id IN (?,?,?) AND provider = 'llama_server'
                 AND model = 'local-model' AND timeout_seconds IN (120, 180)""",
            (now, *_LOCAL_LLAMA_PROFILE_IDS),
        )


# Built-in runtime rows that should never be capped below the production
# context window. The multi-agent templates run under these agent_ids
# (builtin-* + workflow-engine); the chat/code-agent rows were the original
# pair. Any of these still carrying the old 16384 cap silently shrinks the
# effective context window via effective_context_limit()'s min().
_BUILTIN_RUNTIME_AGENT_IDS = (
    "chat",
    "code-agent",
    "api-direct",
    "workflow-engine",
    "builtin-orchestrator",
    "builtin-reviewer",
    "builtin-researcher",
    "builtin-programmer",
    "builtin-analyst",
    "builtin-universal",
    "builtin-socrat",
)


def migrate_default_runtime_limits(db_path: str | Path) -> None:
    """Raise built-in runtime rows still carrying the old 16384 context cap."""
    with get_connection(db_path) as con:
        con.execute(
            f"""UPDATE agent_limits
                SET max_context_tokens = ?, updated_at = ?
                WHERE agent_id IN ({",".join("?" for _ in _BUILTIN_RUNTIME_AGENT_IDS)})
                  AND max_context_tokens = 16384""",
            (DEFAULT_MAX_CONTEXT_TOKENS, now_utc(), *_BUILTIN_RUNTIME_AGENT_IDS),
        )
        con.execute(
            f"""UPDATE agent_limits
                SET max_execution_seconds = ?, updated_at = ?
                WHERE agent_id IN ({",".join("?" for _ in _BUILTIN_RUNTIME_AGENT_IDS)})
                  AND max_execution_seconds = 180""",
            (DEFAULT_MAX_EXECUTION_SECONDS, now_utc(), *_BUILTIN_RUNTIME_AGENT_IDS),
        )


def canonical_args_digest(args: dict[str, Any] | None) -> str:
    """SHA-256 of canonically serialised args.

    Uses sort_keys=True and stable separators so identical args produce
    identical digests regardless of insertion order or Python version.
    Both create_approval and find_approved_approval call this function;
    they MUST both use it to guarantee the digests match.
    """
    payload = json.dumps(args or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def migrate_approvals_table(db_path: str | Path) -> None:
    """Additive migration: create approvals table if not present."""
    with get_connection(db_path) as con:
        con.executescript(_APPROVALS_TABLE_SQL)


def migrate_approval_args_sha256(db_path: str | Path) -> None:
    """Additive idempotent migration: add args_sha256 column to approvals.

    Safe to run on databases created before this column existed.
    New databases already have the column from _APPROVALS_TABLE_SQL.
    """
    with get_connection(db_path) as con:
        existing = {row[1] for row in con.execute("PRAGMA table_info(approvals)").fetchall()}
        if "args_sha256" not in existing:
            con.execute("ALTER TABLE approvals ADD COLUMN args_sha256 TEXT NOT NULL DEFAULT ''")


def dumps_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def loads_json(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def row_to_limit(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    data["allowed_tools"] = loads_json(data.pop("allowed_tools_json", "[]"), [])
    data["allowed_scopes"] = loads_json(data.pop("allowed_scopes_json", "[]"), [])
    return data


def row_to_metric(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    data["details"] = loads_json(data.pop("details_json", "{}"), {})
    if data.get("ok") is not None:
        data["ok"] = bool(data["ok"])
    return data


def row_to_usage(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    data["details"] = loads_json(data.pop("details_json", "{}"), {})
    return data


def planner_tool_aliases() -> list[str]:
    return [
        "web_search",
        "memory_search",
        "library_context",
        "project_mode",
        "project_context",
        "python_executor",
        "project_patch",
    ]


def all_known_tools() -> list[str]:
    tool_names: list[str] = []
    try:
        from app.application.tool_registry.service import list_tools

        payload = list_tools()
        for item in payload.get("tools", []):
            name = str((item or {}).get("name", "")).strip()
            if name:
                tool_names.append(name)
    except Exception:
        pass

    tool_names.extend(planner_tool_aliases())
    deduped: list[str] = []
    seen: set[str] = set()
    for name in tool_names:
        key = name.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return deduped


def migrate_agent_limits_columns(db_path: str | Path) -> None:
    """Additive migration: add the allowed_scopes column to agent_limits."""
    with get_connection(db_path) as con:
        existing = {row[1] for row in con.execute("PRAGMA table_info(agent_limits)").fetchall()}
        if "allowed_scopes_json" not in existing:
            con.execute(
                "ALTER TABLE agent_limits ADD COLUMN allowed_scopes_json TEXT NOT NULL DEFAULT '[]'"
            )


def migrate_normalize_full_tool_allowlists(db_path: str | Path) -> None:
    """P9.2-FIXUP: collapse legacy "allow every tool" snapshots to [] (unrestricted).

    The old default allowed_tools was an all_known_tools() snapshot, which ALWAYS
    contained the full planner_tool_aliases() set. With the kernel now enforcing
    allowed_tools per tool-call (selected_tools=[name]), that frozen snapshot would
    wrongly block any tool registered after the limit row was created. A row that
    allows the entire planner-alias set was a legacy default snapshot — collapse it
    to [] (semantically lossless: "allow all" == unrestricted). Narrow admin
    restrictions (a strict subset that does not cover every alias) are preserved.
    Idempotent: an already-empty list is skipped.
    """
    alias_set = set(planner_tool_aliases())
    if not alias_set:
        return
    with get_connection(db_path) as con:
        rows = con.execute("SELECT agent_id, allowed_tools_json FROM agent_limits").fetchall()
        for agent_id, allowed_json in rows:
            allowed = set(loads_json(allowed_json, []))
            if allowed and alias_set.issubset(allowed):
                con.execute(
                    "UPDATE agent_limits SET allowed_tools_json = '[]' WHERE agent_id = ?",
                    (agent_id,),
                )


def default_limit_payload(agent_id: str) -> dict[str, Any]:
    timestamp = now_utc()
    return {
        "agent_id": agent_id,
        "max_runs_per_hour": DEFAULT_MAX_RUNS_PER_HOUR,
        "max_execution_seconds": DEFAULT_MAX_EXECUTION_SECONDS,
        "max_context_tokens": DEFAULT_MAX_CONTEXT_TOKENS,
        # P9.2-FIXUP: empty allowed_tools == UNRESTRICTED (mirrors allowed_scopes).
        # The kernel now enforces allowed_tools per tool-call (selected_tools=[name]),
        # so a frozen all_known_tools() snapshot would wrongly block any tool
        # registered after the limit was created (new builtins, classified plugins,
        # MCP). A tool restriction is an explicit admin opt-in, never the default.
        "allowed_tools": [],
        "allowed_scopes": [],
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def list_agent_limits(db_path: str | Path) -> list[dict[str, Any]]:
    with get_connection(db_path) as con:
        rows = con.execute("SELECT * FROM agent_limits ORDER BY agent_id").fetchall()
    items = [row_to_limit(row) for row in rows]
    return [item for item in items if item]


def delete_unknown_builtin_limits(
    db_path: str | Path,
    valid_agent_ids: set[str],
) -> int:
    valid_ids = {str(agent_id).strip() for agent_id in valid_agent_ids if str(agent_id).strip()}
    if not valid_ids:
        return 0

    with get_connection(db_path) as con:
        rows = con.execute(
            "SELECT agent_id FROM agent_limits WHERE agent_id LIKE 'builtin-%'"
        ).fetchall()
        stale_ids = [
            str(row["agent_id"])
            for row in rows
            if str(row["agent_id"]) not in valid_ids
        ]
        if not stale_ids:
            return 0
        con.executemany(
            "DELETE FROM agent_limits WHERE agent_id = ?",
            [(agent_id,) for agent_id in stale_ids],
        )
    return len(stale_ids)


def get_agent_limit(db_path: str | Path, agent_id: str) -> dict[str, Any] | None:
    with get_connection(db_path) as con:
        row = con.execute(
            "SELECT * FROM agent_limits WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
    return row_to_limit(row)


def upsert_limit(db_path: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    agent_id = str(payload.get("agent_id", "")).strip()
    if not agent_id:
        raise ValueError("agent_id is required")

    timestamp = now_utc()
    existing = get_agent_limit(db_path, agent_id)
    created_at = existing["created_at"] if existing else payload.get("created_at", timestamp)
    with get_connection(db_path) as con:
        con.execute(
            """
            INSERT INTO agent_limits
                (agent_id, max_runs_per_hour, max_execution_seconds, max_context_tokens,
                 allowed_tools_json, allowed_scopes_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                max_runs_per_hour = excluded.max_runs_per_hour,
                max_execution_seconds = excluded.max_execution_seconds,
                max_context_tokens = excluded.max_context_tokens,
                allowed_tools_json = excluded.allowed_tools_json,
                allowed_scopes_json = excluded.allowed_scopes_json,
                updated_at = excluded.updated_at
            """,
            (
                agent_id,
                int(payload.get("max_runs_per_hour", DEFAULT_MAX_RUNS_PER_HOUR)),
                int(payload.get("max_execution_seconds", DEFAULT_MAX_EXECUTION_SECONDS)),
                int(payload.get("max_context_tokens", DEFAULT_MAX_CONTEXT_TOKENS)),
                dumps_json(payload.get("allowed_tools", [])),
                dumps_json(payload.get("allowed_scopes", [])),
                str(created_at),
                timestamp,
            ),
        )
    return get_agent_limit(db_path, agent_id) or {}


def record_metric(
    db_path: str | Path,
    *,
    metric_type: str,
    agent_id: str = "",
    run_id: str = "",
    workflow_id: str = "",
    step_id: str = "",
    ok: bool | None = None,
    duration_ms: int = 0,
    details: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    timestamp = created_at or now_utc()
    with get_connection(db_path) as con:
        cursor = con.execute(
            """
            INSERT INTO agent_metrics
                (metric_type, agent_id, run_id, workflow_id, step_id, ok, duration_ms, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(metric_type or ""),
                str(agent_id or ""),
                str(run_id or ""),
                str(workflow_id or ""),
                str(step_id or ""),
                None if ok is None else (1 if ok else 0),
                int(duration_ms or 0),
                dumps_json(details or {}),
                timestamp,
            ),
        )
        row = con.execute(
            "SELECT * FROM agent_metrics WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return row_to_metric(row) or {}


def record_resource_usage(
    db_path: str | Path,
    *,
    agent_id: str,
    resource: str,
    amount: float,
    unit: str = "",
    run_id: str = "",
    workflow_id: str = "",
    step_id: str = "",
    details: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    timestamp = created_at or now_utc()
    with get_connection(db_path) as con:
        cursor = con.execute(
            """
            INSERT INTO resource_usage
                (agent_id, run_id, workflow_id, step_id, resource, amount, unit, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(agent_id or ""),
                str(run_id or ""),
                str(workflow_id or ""),
                str(step_id or ""),
                str(resource or ""),
                float(amount or 0),
                str(unit or ""),
                dumps_json(details or {}),
                timestamp,
            ),
        )
        row = con.execute(
            "SELECT * FROM resource_usage WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return row_to_usage(row) or {}


def record_agent_run_metric(
    db_path: str | Path,
    *,
    agent_id: str,
    run_id: str,
    route: str,
    model_name: str,
    ok: bool,
    duration_ms: int,
    streaming: bool = False,
    num_ctx: int = 0,
    tools: list[str] | None = None,
) -> None:
    record_metric(
        db_path,
        metric_type="agent.run",
        agent_id=agent_id,
        run_id=run_id,
        ok=ok,
        duration_ms=duration_ms,
        details={
            "route": route,
            "model_name": model_name,
            "streaming": streaming,
            "tools": list(tools or []),
            "num_ctx": int(num_ctx or 0),
        },
    )
    if num_ctx:
        record_resource_usage(
            db_path,
            agent_id=agent_id,
            run_id=run_id,
            resource="context_tokens",
            amount=int(num_ctx),
            unit="tokens",
            details={"route": route},
        )
    record_resource_usage(
        db_path,
        agent_id=agent_id,
        run_id=run_id,
        resource="selected_tools",
        amount=len(list(tools or [])),
        unit="count",
        details={"route": route},
    )


def record_workflow_run_metric(
    db_path: str | Path,
    *,
    workflow_id: str,
    run_id: str,
    status: str,
    duration_ms: int = 0,
    details: dict[str, Any] | None = None,
    workflow_engine_agent_id: str = DEFAULT_WORKFLOW_ENGINE_AGENT_ID,
) -> None:
    record_metric(
        db_path,
        metric_type="workflow.run",
        agent_id=workflow_engine_agent_id,
        run_id=run_id,
        workflow_id=workflow_id,
        ok=status == "completed",
        duration_ms=duration_ms,
        details={"status": status, **(details or {})},
    )


def record_workflow_step_metric(
    db_path: str | Path,
    *,
    agent_id: str,
    workflow_id: str,
    run_id: str,
    step_id: str,
    step_type: str,
    ok: bool,
    duration_ms: int = 0,
    details: dict[str, Any] | None = None,
) -> None:
    record_metric(
        db_path,
        metric_type="workflow.step",
        agent_id=agent_id,
        run_id=run_id,
        workflow_id=workflow_id,
        step_id=step_id,
        ok=ok,
        duration_ms=duration_ms,
        details={"step_type": step_type, **(details or {})},
    )


def count_agent_runs_last_hour(db_path: str | Path, agent_id: str) -> int:
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with get_connection(db_path) as con:
        row = con.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM agent_metrics
            WHERE metric_type = 'agent.run' AND agent_id = ? AND created_at >= ?
            """,
            (agent_id, since),
        ).fetchone()
    return int(row["cnt"]) if row else 0


def get_recent_blocked_runs(
    db_path: str | Path,
    hours: int = 24,
    limit: int = 10,
) -> list[dict[str, Any]]:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with get_connection(db_path) as con:
        rows = con.execute(
            """
            SELECT * FROM agent_metrics
            WHERE metric_type = 'sandbox.blocked' AND created_at >= ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (since, max(1, int(limit))),
        ).fetchall()
    items = [row_to_metric(row) for row in rows]
    return [item for item in items if item]


# ── Approvals ────────────────────────────────────────────────────────────────

def row_to_approval(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    data["args"] = loads_json(data.pop("args_json", "{}"), {})
    return data


def create_approval(
    db_path: str | Path,
    *,
    id: str,
    tool_name: str,
    agent_id: str = "",
    source: str = "",
    run_id: str = "",
    project_scope_id: str = "",
    args: dict[str, Any] | None = None,
    ttl_seconds: int = 300,
) -> dict[str, Any]:
    now = now_utc()
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=max(1, ttl_seconds))).isoformat()
    args_digest = canonical_args_digest(args)
    with get_connection(db_path) as con:
        con.execute(
            """INSERT INTO approvals
               (id, tool_name, agent_id, source, run_id, project_scope_id,
                args_json, args_sha256, status, ttl_seconds, expires_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
            (id, tool_name, agent_id, source, run_id, project_scope_id,
             dumps_json(args or {}), args_digest, ttl_seconds, expires_at, now, now),
        )
    return get_approval(db_path, id) or {}


def get_approval(db_path: str | Path, approval_id: str) -> dict[str, Any] | None:
    with get_connection(db_path) as con:
        row = con.execute(
            "SELECT * FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
    return row_to_approval(row)


def list_approvals(
    db_path: str | Path,
    *,
    status: str | None = None,
    agent_id: str | None = None,
    tool_name: str | None = None,
    run_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if agent_id:
        clauses.append("agent_id = ?")
        params.append(agent_id)
    if tool_name:
        clauses.append("tool_name = ?")
        params.append(tool_name)
    if run_id:
        clauses.append("run_id = ?")
        params.append(run_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, int(limit)))
    with get_connection(db_path) as con:
        rows = con.execute(
            f"SELECT * FROM approvals {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [a for a in (row_to_approval(r) for r in rows) if a]


def update_approval_status(
    db_path: str | Path,
    approval_id: str,
    *,
    status: str,
) -> dict[str, Any] | None:
    now = now_utc()
    decided_at = now if status in ("approved", "rejected", "expired", "used") else None
    with get_connection(db_path) as con:
        con.execute(
            "UPDATE approvals SET status = ?, decided_at = ?, updated_at = ? WHERE id = ?",
            (status, decided_at, now, approval_id),
        )
    return get_approval(db_path, approval_id)


def find_approved_approval(
    db_path: str | Path,
    *,
    tool_name: str,
    agent_id: str,
    source: str,
    run_id: str,
    project_scope_id: str,
    args: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return the first approved, non-expired approval matching all 6 binding fields.

    Binding fields: tool_name, agent_id, source, run_id, project_scope_id, args_sha256.
    Legacy approvals with an empty digest (args_sha256 = '') are never accepted;
    an empty run_id never matches any stored approval.
    """
    if not run_id:
        return None
    digest = canonical_args_digest(args)
    now = now_utc()
    with get_connection(db_path) as con:
        row = con.execute(
            """SELECT * FROM approvals
               WHERE tool_name        = ?
                 AND agent_id         = ?
                 AND source           = ?
                 AND run_id           = ?
                 AND project_scope_id = ?
                 AND args_sha256      = ?
                 AND args_sha256     != ''
                 AND status           = 'approved'
                 AND expires_at       > ?
               ORDER BY created_at DESC LIMIT 1""",
            (tool_name, agent_id, source, run_id, project_scope_id, digest, now),
        ).fetchone()
    return row_to_approval(row)


def expire_old_approvals(db_path: str | Path) -> int:
    """Mark expired pending/approved approvals as 'expired'. Returns count updated."""
    now = now_utc()
    with get_connection(db_path) as con:
        cursor = con.execute(
            "UPDATE approvals SET status = 'expired', updated_at = ? WHERE status IN ('pending', 'approved') AND expires_at <= ?",
            (now, now),
        )
    return cursor.rowcount if cursor else 0


# ── MemoryCandidate ──────────────────────────────────────────────────────────

_MEMORY_CANDIDATES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS memory_candidates (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL DEFAULT 'project',
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'pending',
    project_scope_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_memcand_status ON memory_candidates(status);
CREATE INDEX IF NOT EXISTS idx_memcand_ns ON memory_candidates(namespace);
CREATE INDEX IF NOT EXISTS idx_memcand_scope ON memory_candidates(project_scope_id);
"""


def migrate_memory_candidates_table(db_path: str | Path) -> None:
    """Additive migration: create memory_candidates table if not present."""
    with get_connection(db_path) as con:
        con.executescript(_MEMORY_CANDIDATES_TABLE_SQL)


def row_to_candidate(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    return dict(row)


def create_candidate(
    db_path: str | Path,
    *,
    id: str,
    namespace: str = "project",
    content: str,
    source: str = "",
    confidence: float = 1.0,
    project_scope_id: str = "",
    expires_at: str | None = None,
) -> dict[str, Any]:
    now = now_utc()
    with get_connection(db_path) as con:
        con.execute(
            """INSERT INTO memory_candidates
               (id, namespace, content, source, confidence, status,
                project_scope_id, created_at, updated_at, expires_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
            (id, namespace, content, source, float(confidence),
             project_scope_id, now, now, expires_at),
        )
    return get_candidate(db_path, id) or {}


def get_candidate(db_path: str | Path, candidate_id: str) -> dict[str, Any] | None:
    with get_connection(db_path) as con:
        row = con.execute(
            "SELECT * FROM memory_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
    return row_to_candidate(row)


def list_candidates(
    db_path: str | Path,
    *,
    status: str | None = None,
    namespace: str | None = None,
    project_scope_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?"); params.append(status)
    if namespace:
        clauses.append("namespace = ?"); params.append(namespace)
    if project_scope_id:
        clauses.append("project_scope_id = ?"); params.append(project_scope_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, int(limit)))
    with get_connection(db_path) as con:
        rows = con.execute(
            f"SELECT * FROM memory_candidates {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [c for c in (row_to_candidate(r) for r in rows) if c]


def update_candidate_status(
    db_path: str | Path,
    candidate_id: str,
    *,
    status: str,
    content: str | None = None,
) -> dict[str, Any] | None:
    now = now_utc()
    if content is not None:
        with get_connection(db_path) as con:
            con.execute(
                "UPDATE memory_candidates SET status = ?, content = ?, updated_at = ? WHERE id = ?",
                (status, content, now, candidate_id),
            )
    else:
        with get_connection(db_path) as con:
            con.execute(
                "UPDATE memory_candidates SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, candidate_id),
            )
    return get_candidate(db_path, candidate_id)


def delete_candidate(db_path: str | Path, candidate_id: str) -> dict[str, Any]:
    with get_connection(db_path) as con:
        con.execute("DELETE FROM memory_candidates WHERE id = ?", (candidate_id,))
    return {"id": candidate_id, "deleted": True}


def list_accepted_candidates(
    db_path: str | Path,
    *,
    namespace: str = "project",
    project_scope_id: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return accepted candidates for prompt injection."""
    return list_candidates(
        db_path,
        status="accepted",
        namespace=namespace,
        project_scope_id=project_scope_id or None,
        limit=limit,
    )


# ── Model Profiles ─────────────────────────────────────────────────────────────

def row_to_profile(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    data["enabled"] = bool(data["enabled"])
    data["cloud_consent_required"] = bool(data["cloud_consent_required"])
    return data


def get_model_profile(db_path: str | Path, profile_id: str) -> dict[str, Any] | None:
    with get_connection(db_path) as con:
        row = con.execute(
            "SELECT * FROM model_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
    return row_to_profile(row)


def list_model_profiles(
    db_path: str | Path,
    *,
    role: str | None = None,
    enabled_only: bool = False,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if role:
        clauses.append("role = ?")
        params.append(role)
    if enabled_only:
        clauses.append("enabled = 1")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection(db_path) as con:
        rows = con.execute(
            f"SELECT * FROM model_profiles {where} ORDER BY role, id", params
        ).fetchall()
    return [p for p in (row_to_profile(r) for r in rows) if p]


def set_model_profile_enabled(
    db_path: str | Path,
    profile_id: str,
    *,
    enabled: bool,
) -> dict[str, Any] | None:
    now = now_utc()
    with get_connection(db_path) as con:
        con.execute(
            "UPDATE model_profiles SET enabled = ?, updated_at = ? WHERE id = ?",
            (1 if enabled else 0, now, profile_id),
        )
    return get_model_profile(db_path, profile_id)


def get_profile_for_role(
    db_path: str | Path, role: str
) -> dict[str, Any] | None:
    """Return the first enabled profile for *role*, or None."""
    with get_connection(db_path) as con:
        row = con.execute(
            "SELECT * FROM model_profiles WHERE role = ? AND enabled = 1 ORDER BY id LIMIT 1",
            (role,),
        ).fetchone()
    return row_to_profile(row)
