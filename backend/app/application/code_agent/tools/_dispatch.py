from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.application.code_agent.tools._files import (
    tool_edit_file,
    tool_glob,
    tool_path_exists,
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
    tool_web_search,
)
from app.application.code_agent.tools._sandbox_tools import (
    tool_sandbox_reset,
    tool_sandbox_run,
)
from app.application.code_agent.tools._content import (
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
from app.application.code_agent.tools._vision import (
    tool_ocr_file,
    tool_read_image,
)
from app.application.code_agent.tools._computer import (
    tool_computer,
)
from app.application.code_agent.tools._drift import (
    tool_reconcile_server_facts,
)


def build_tool_dispatch(project_root: Path) -> dict[str, Callable[..., dict[str, Any]]]:
    return {
        "read_file": lambda **kw: tool_read_file(project_root, **kw),
        "write_file": lambda **kw: tool_write_file(project_root, **kw),
        "edit_file": lambda **kw: tool_edit_file(project_root, **kw),
        "glob": lambda **kw: tool_glob(project_root, **kw),
        "path_exists": lambda **kw: tool_path_exists(project_root, **kw),
        "grep": lambda **kw: tool_grep(project_root, **kw),
        "project_map": lambda **kw: tool_project_map(project_root, **kw),
        "recall": lambda **kw: tool_recall(project_root, **kw),
        "remember": lambda **kw: tool_remember(project_root, **kw),
        "todo_update": lambda **kw: tool_todo_update(**kw),
        "delegate_task": lambda **kw: tool_delegate_task(project_root, **kw),
        "run_bash": lambda **kw: tool_run_bash(project_root, **kw),
        "run_server": lambda **kw: tool_run_server(project_root, **kw),
        "reconcile_server_facts": lambda **kw: tool_reconcile_server_facts(**kw),
        "web_search": lambda **kw: tool_web_search(**kw),
        "web_fetch": lambda **kw: tool_web_fetch(**kw),
        "browser": lambda **kw: tool_browser(**kw),
        "sandbox_run": lambda **kw: tool_sandbox_run(project_root, **kw),
        "sandbox_reset": lambda **kw: tool_sandbox_reset(project_root, **kw),
        "translator": lambda **kw: tool_translator(project_root, **kw),
        "regex": lambda **kw: tool_regex(project_root, **kw),
        "csv": lambda **kw: tool_csv(project_root, **kw),
        "converter": lambda **kw: tool_converter(project_root, **kw),
        "http_api": lambda **kw: tool_http_api(project_root, **kw),
        "sql": lambda **kw: tool_sql(project_root, **kw),
        "encrypt": lambda **kw: tool_encrypt(project_root, **kw),
        "archiver": lambda **kw: tool_archiver(project_root, **kw),
        "webhook": lambda **kw: tool_webhook(project_root, **kw),
        "screenshot": lambda **kw: tool_screenshot(project_root, **kw),
        "file_gen": lambda **kw: tool_file_gen(project_root, **kw),
        "read_image": lambda **kw: tool_read_image(project_root, **kw),
        "ocr_file": lambda **kw: tool_ocr_file(project_root, **kw),
        "computer": lambda **kw: tool_computer(project_root, **kw),
    }
