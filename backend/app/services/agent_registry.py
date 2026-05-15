"""Thin facade — all agent registry logic lives in application/agents/agent_registry.py.

Mutable module-level state (DB_PATH, _BUILTIN_AGENTS_SEEDED) lives in
application/agents/agent_registry.py.  Tests that need to redirect the
database path or reset the seeding flag must import that module directly:

    import app.application.agents.agent_registry as _ar
    _ar.DB_PATH = Path(tmpdir) / "agent_registry.db"
    _ar._BUILTIN_AGENTS_SEEDED = False
    _ar._init_db()
"""
from app.application.agents.agent_registry import (  # noqa: F401
    DB_PATH,
    _BUILTIN_AGENTS_SEEDED,
    _CREATE_SQL,
    _conn,
    _init_db,
    _now,
    _row_to_dict,
    delete_agent,
    get_agent,
    get_agent_runs,
    get_agent_state,
    list_agents,
    record_agent_run,
    register_agent,
    resolve_agent,
    seed_builtin_agents,
    set_agent_state,
    update_agent,
)
