from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.application.code_agent.tools._files import (
    tool_edit_file,
    tool_glob,
    tool_read_file,
    tool_write_file,
)
from app.application.code_agent.tools._search import (
    tool_grep,
    tool_project_map,
    tool_recall,
)
from app.application.code_agent.tools._memory import tool_library, tool_memory
from app.application.code_agent.tools._meta import (
    tool_delegate_task,
    tool_todo_update,
)
from app.application.code_agent.tools._run import (
    tool_run_bash,
    tool_run_server,
)
from app.application.code_agent.tools._resources import (
    tool_resource_materialize,
    tool_resource_process,
    tool_resource_publish,
)
from app.application.code_agent.tools._mcp import tool_mcp
from app.application.code_agent.tools._capability import tool_capability_load


def build_tool_dispatch(project_root: Path) -> dict[str, Callable[..., dict[str, Any]]]:
    return {
        "capability_load": lambda **kw: tool_capability_load(**kw),
        "read_file": lambda **kw: tool_read_file(project_root, **kw),
        "write_file": lambda **kw: tool_write_file(project_root, **kw),
        "edit_file": lambda **kw: tool_edit_file(project_root, **kw),
        "glob": lambda **kw: tool_glob(project_root, **kw),
        "grep": lambda **kw: tool_grep(project_root, **kw),
        "project_map": lambda **kw: tool_project_map(project_root, **kw),
        "recall": lambda **kw: tool_recall(project_root, **kw),
        "memory": lambda **kw: tool_memory(**kw),
        "library": lambda **kw: tool_library(**kw),
        "todo_update": lambda **kw: tool_todo_update(**kw),
        "delegate_task": lambda **kw: tool_delegate_task(project_root, **kw),
        "run_bash": lambda **kw: tool_run_bash(project_root, **kw),
        "run_server": lambda **kw: tool_run_server(project_root, **kw),
        "mcp": lambda **kw: tool_mcp(project_root, **kw),
        # Reads a run-bound resource by opaque id (no project path involved).
        "resource_process": lambda **kw: tool_resource_process(**kw),
        # Writes a run-bound resource into the run's project workspace (needs root).
        "resource_materialize": lambda **kw: tool_resource_materialize(project_root, **kw),
        # Publishes a processed workspace file to the user as a download (needs root).
        "resource_publish": lambda **kw: tool_resource_publish(project_root, **kw),
    }
