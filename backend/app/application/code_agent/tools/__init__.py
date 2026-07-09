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

from app.application.code_agent.tools._sandbox import (  # noqa: F401
    SandboxError,
    _resolve_safe,
    _truncate_middle,
)
from app.application.code_agent.tools._shell import (  # noqa: F401
    _kill_proc_tree,
    _new_process_group_kwargs,
    is_shell_critical,
    is_shell_safe,
    kill_run_processes,
    reset_current_run_id,
    set_current_run_id,
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
    tool_remember,
)
from app.application.code_agent.tools._web import (  # noqa: F401
    tool_browser,
    tool_web_fetch,
    tool_web_claim_add,
    tool_web_query,
    tool_web_search,
)
from app.application.code_agent.tools._sandbox_tools import (  # noqa: F401
    tool_sandbox_reset,
    tool_sandbox_run,
)
from app.application.code_agent.tools._run import (  # noqa: F401
    stop_all_servers,
    tool_run_bash,
    tool_run_server,
)
from app.application.code_agent.tools._meta import (  # noqa: F401
    TOOL_SEARCH_ACTIVATION_CAP,
    TOOL_SEARCH_RESULT_LIMIT,
    tool_delegate_task,
    tool_search,
    tool_todo_update,
)
from app.application.code_agent.tools._content import (  # noqa: F401
    tool_archiver,
    tool_converter,
    tool_csv,
    tool_encrypt,
    tool_file_gen,
    tool_http_api,
    tool_regex,
    tool_screenshot,
    tool_sql,
    tool_translator,
    tool_webhook,
)
from app.application.code_agent.tools._vision import (  # noqa: F401
    tool_ocr_file,
    tool_read_image,
)
from app.application.code_agent.tools._dispatch import build_tool_dispatch  # noqa: F401

__all__ = [
    "build_tool_schemas",
    "build_tool_dispatch",
    "SandboxError",
    "is_shell_safe",
    "is_shell_critical",
    "set_current_run_id",
    "reset_current_run_id",
    "kill_run_processes",
    "stop_all_servers",
    "tool_read_file",
    "tool_write_file",
    "tool_edit_file",
    "tool_glob",
    "tool_grep",
    "tool_project_map",
    "tool_recall",
    "tool_remember",
    "tool_todo_update",
    "tool_delegate_task",
    "tool_run_bash",
    "tool_run_server",
    "tool_web_search",
    "tool_web_fetch",
    "tool_web_query",
    "tool_web_claim_add",
    "tool_browser",
    "tool_sandbox_run",
    "tool_sandbox_reset",
    "tool_translator",
    "tool_regex",
    "tool_csv",
    "tool_converter",
    "tool_http_api",
    "tool_sql",
    "tool_encrypt",
    "tool_archiver",
    "tool_webhook",
    "tool_screenshot",
    "tool_file_gen",
    "tool_read_image",
    "tool_ocr_file",
    "tool_search",
    "TOOL_SEARCH_RESULT_LIMIT",
    "TOOL_SEARCH_ACTIVATION_CAP",
]
