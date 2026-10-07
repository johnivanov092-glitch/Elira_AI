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
    tool_remember,
)
from app.application.code_agent.tools._meta import (
    tool_delegate_task,
    tool_todo_update,
)
from app.application.code_agent.tools._run import (
    tool_run_bash,
    tool_run_server,
)
from app.application.code_agent.tools._web import (
    tool_browser,
    tool_web_fetch,
    tool_web_query,
    tool_web_search,
)
from app.application.code_agent.tools._content import (
    tool_csv,
    tool_file_gen,
    tool_http_api,
)
from app.application.code_agent.tools._math import tool_calc, tool_finance_calc, tool_unit_convert
from app.application.code_agent.tools._vision import (
    tool_read_image,
)
from app.application.code_agent.tools._computer import (
    tool_computer,
)
from app.application.code_agent.tools._resources import (
    tool_resource_materialize,
    tool_resource_process,
    tool_resource_publish,
)
from app.application.code_agent.tools._runtime_control import tool_runtime_control
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
        "remember": lambda **kw: tool_remember(project_root, **kw),
        "todo_update": lambda **kw: tool_todo_update(**kw),
        "delegate_task": lambda **kw: tool_delegate_task(project_root, **kw),
        "run_bash": lambda **kw: tool_run_bash(project_root, **kw),
        "run_server": lambda **kw: tool_run_server(project_root, **kw),
        "runtime_control": lambda **kw: tool_runtime_control(project_root, **kw),
        "web_search": lambda **kw: tool_web_search(**kw),
        "web_fetch": lambda **kw: tool_web_fetch(**kw),
        "web_query": lambda **kw: tool_web_query(**kw),
        "browser": lambda **kw: tool_browser(**kw),
        "csv": lambda **kw: tool_csv(project_root, **kw),
        "calc": lambda **kw: tool_calc(**kw),
        "unit_convert": lambda **kw: tool_unit_convert(**kw),
        "finance_calc": lambda **kw: tool_finance_calc(**kw),
        "http_api": lambda **kw: tool_http_api(project_root, **kw),
        "file_gen": lambda **kw: tool_file_gen(project_root, **kw),
        "read_image": lambda **kw: tool_read_image(project_root, **kw),
        "computer": lambda **kw: tool_computer(project_root, **kw),
        # Reads a run-bound resource by opaque id (no project path involved).
        "resource_process": lambda **kw: tool_resource_process(**kw),
        # Writes a run-bound resource into the run's project workspace (needs root).
        "resource_materialize": lambda **kw: tool_resource_materialize(project_root, **kw),
        # Publishes a processed workspace file to the user as a download (needs root).
        "resource_publish": lambda **kw: tool_resource_publish(project_root, **kw),
    }
