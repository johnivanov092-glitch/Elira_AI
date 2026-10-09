"""Code-agent tools package.

Refactored from the former single-file ``tools.py`` into function-grouped
submodules with no logic changes. This ``__init__`` re-exports the exact public
surface (plus a handful of privately-imported helpers other application modules
and tests depend on) so every existing ``from app.application.code_agent.tools
import ...`` keeps working unchanged.
"""
from __future__ import annotations

# Static tool schemas live next to the package; re-exported so importers keep
# importing build_tool_schemas from tools unchanged.
from app.application.code_agent.tool_schemas import build_tool_schemas  # noqa: F401

from app.application.code_agent.tools._sandbox import SandboxError  # noqa: F401
from app.application.code_agent.tools._shell import (  # noqa: F401
    get_current_run_id,
    cancel_run_callbacks,
    kill_run_processes,
    register_run_cancel_callback,
    register_run_process,
    run_was_stopped,
    reset_current_run_id,
    set_current_run_id,
    unregister_run_process,
    unregister_run_cancel_callback,
)
from app.application.code_agent.tools._files import (  # noqa: F401
    tool_edit_file,
    tool_glob,
    tool_read_file,
    tool_write_file,
)
from app.application.code_agent.tools._search import (  # noqa: F401
    tool_grep,
    tool_project_map,
    tool_recall,
)
from app.application.code_agent.tools._memory import (  # noqa: F401
    tool_library,
    tool_memory,
)
from app.application.code_agent.tools._run import (  # noqa: F401
    stop_all_servers,
    tool_run_bash,
    tool_run_server,
)
from app.application.code_agent.tools._meta import (  # noqa: F401
    tool_delegate_task,
    tool_todo_update,
)
from app.application.code_agent.tools._content import (  # noqa: F401
    tool_csv,
)
from app.application.code_agent.tools._vision import (  # noqa: F401
    tool_read_image,
)
from app.application.code_agent.tools._dispatch import build_tool_dispatch  # noqa: F401
from app.application.code_agent.tools._capability import tool_capability_load  # noqa: F401
from app.application.code_agent.tools._mcp import tool_mcp  # noqa: F401
from app.application.code_agent.tools._telegram import tool_telegram  # noqa: F401
from app.application.code_agent.tools._itops import tool_itops_registry  # noqa: F401

__all__ = [
    "build_tool_schemas",
    "build_tool_dispatch",
    "SandboxError",
    "set_current_run_id",
    "get_current_run_id",
    "reset_current_run_id",
    "kill_run_processes",
    "cancel_run_callbacks",
    "register_run_cancel_callback",
    "register_run_process",
    "run_was_stopped",
    "unregister_run_process",
    "unregister_run_cancel_callback",
    "stop_all_servers",
    "tool_read_file",
    "tool_write_file",
    "tool_edit_file",
    "tool_glob",
    "tool_grep",
    "tool_project_map",
    "tool_recall",
    "tool_memory",
    "tool_library",
    "tool_todo_update",
    "tool_delegate_task",
    "tool_capability_load",
    "tool_mcp",
    "tool_telegram",
    "tool_itops_registry",
    "tool_run_bash",
    "tool_run_server",
    "tool_csv",
    "tool_read_image",
]
