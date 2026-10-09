"""OpenAI-compatible function-calling tool schemas for the code-agent.

Extracted verbatim from tools.py (no behaviour change) — pure static data,
no dependencies on the tool implementations. Re-exported from tools for
backward compatibility.
"""
from __future__ import annotations

from typing import Any

from app.application.code_agent.capabilities import (
    CAPABILITY_GROUPS,
    capability_catalog_text,
)


def build_tool_schemas() -> list[dict[str, Any]]:
    return _base_tool_schemas()


def _base_tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "capability_load",
                "description": (
                    "Load a tool group for this run; its tools appear on the next turn. "
                    "Not a permission. Groups:\n"
                    + capability_catalog_text()
                ),
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "group": {
                            "type": "string",
                            "enum": list(CAPABILITY_GROUPS),
                        },
                    },
                    "required": ["group"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "mcp",
                "description": (
                    "MCP servers from data/mcp_servers.json. First read the server's skill "
                    "(<id>-mcp in the skills catalog). action=list shows servers; start "
                    "(server_id, query) starts one and its tools appear on the next turn; tools "
                    "(server_id, query) reveals more of its tools; stop/restart; add (server_id, "
                    "config) adds or changes a server; remove deletes it. Start only the server "
                    "the task needs. Credentials go in config as env_secret_refs/"
                    "secret_header_refs with sref_ values, never plain text."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": [
                            "list", "start", "stop", "restart", "tools", "add", "remove",
                        ]},
                        "server_id": {"type": "string"},
                        "query": {"type": "string", "description": "start/tools: which tools are needed."},
                        "config": {"type": "object", "description": "add: description, command/args/env/cwd or url/headers."},
                    },
                    "required": ["action"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a plain text file. Returns lines with line numbers. "
                               "For PDF/Office or OCR, read the matching skill and run its script; "
                               "materialize attached ResourceRefs first.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative paths start at project root; any absolute filesystem path is accepted."},
                        "offset": {"type": "integer", "description": "Starting line (0-based). Default 0."},
                        "limit": {"type": "integer", "description": "Max lines to read. Default 2000."},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Create a new file or overwrite an existing one with the given content.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "Replace the single occurrence of `old_string` with `new_string` in `path`. Errors if old_string is not unique.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                    },
                    "required": ["path", "old_string", "new_string"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "glob",
                "description": "Find files matching a glob pattern (e.g. '**/*.py').",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "resource_process",
                "description": (
                    "Inspect metadata of an attached resource by resource_id (NOT a path). "
                    "The only operation is 'inspect'; execution_target accepts 'auto' or "
                    "'local_cpu'. For content, use resource_materialize and the matching "
                    "mutable document-read, ocr or audio-transcribe skill. Run its script "
                    "with ordinary tools and publish finished files with resource_publish."
                ),
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "resource_id": {"type": "string", "description": "Opaque durable resource id (never a filesystem path)."},
                        "operation": {"type": "string", "enum": ["inspect"], "description": "Inspect metadata only."},
                        "execution_target": {"type": "string", "enum": ["auto", "local_cpu"], "description": "Metadata inspection runs on local CPU; auto selects it."},
                    },
                    "required": ["resource_id", "operation"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "resource_materialize",
                "description": (
                    "Материализовать ПРИКРЕПЛЁННЫЙ файл (ресурс/вложение) в текущую папку "
                    "проекта, чтобы дальше обрабатывать его обычными инструментами "
                    "(run_bash/ffmpeg/python/конвертация/архивы). Copy an attached resource "
                    "(by resource_id) into THIS run's project workspace so the normal file / "
                    "run_bash tools can process it. Takes a resource_id (NOT a path) and an "
                    "optional destination_name (a RELATIVE name inside the workspace; default "
                    "= the resource's safe basename). Writes a NEW file — it never overwrites "
                    "an existing one. Returns a project-relative path only. Use this when the "
                    "user wants to convert/encode/run/unpack an attached file locally."
                ),
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "resource_id": {"type": "string", "description": "Opaque durable resource id (never a filesystem path)."},
                        "destination_name": {"type": "string", "description": "Optional relative filename inside the project workspace (no absolute path, no '..'). Default: the resource's safe basename."},
                    },
                    "required": ["resource_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "resource_publish",
                "description": (
                    "Опубликовать ГОТОВЫЙ файл из папки проекта пользователю для скачивания "
                    "(кнопка «Скачать» в интерфейсе). Publish an already-produced workspace "
                    "file to the user as a downloadable artifact. Use this AFTER you have "
                    "created/converted/encoded the file with the normal tools (run_bash / "
                    "ffmpeg / python / a skill script). Takes project_path (a RELATIVE path to an "
                    "existing file in the project workspace, NOT absolute) and an optional "
                    "download_name (a plain filename, no directories; default = the source's "
                    "safe basename). For PDF/DOCX the runtime performs external document QA "
                    "before publication. If the task promises an exact page count, pass the "
                    "optional expected_page_count; omit it otherwise. It copies the file to the download area and returns a "
                    "download_url — it never overwrites an existing download of the same name."
                ),
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "project_path": {"type": "string", "description": "Relative path to an existing file in the project workspace to deliver (never absolute, no '..')."},
                        "download_name": {"type": "string", "description": "Optional plain filename for the download (no path, no directories). Default: the source's safe basename."},
                        "expected_page_count": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Optional exact page-count contract for PDF/DOCX. Use only when the user/task explicitly requires it."},
                    },
                    "required": ["project_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "grep",
                "description": "Search file contents for a regex pattern. Returns 'file:line:match' lines.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string", "description": "Directory or file to search. Default: project root."},
                        "glob": {"type": "string", "description": "Glob filter for files. Default: '*'"},
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "project_map",
                "description": (
                    "One-call structural overview of a codebase: a pruned file "
                    "tree (ignores .git/node_modules/.venv/build caches), detected "
                    "manifests/config files, conventional entry points, and "
                    "top-level signatures (functions/classes) for the main "
                    "languages. Use this FIRST on an unfamiliar or non-trivial "
                    "project to understand its shape before reading files."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Subdirectory to map. Default: project root."},
                        "max_depth": {"type": "integer", "description": "Tree depth (1-8). Default 4."},
                        "max_files": {"type": "integer", "description": "Max files listed (20-2000). Default 400."},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "recall",
                "description": (
                    "Search the project index: code chunks and summaries of earlier runs "
                    "('where is X implemented', 'what did we do about Y'). action=index "
                    "builds/refreshes the index of the project (or path), action=status "
                    "reports it. Facts about the user are the memory tool."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["search", "index", "status"], "description": "Default search."},
                        "query": {"type": "string", "description": "What to find (search)."},
                        "path": {"type": "string", "description": "Folder to index/report; default the project."},
                        "top_k": {"type": "integer", "description": "Max results (default 5)."},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "memory",
                "description": (
                    "Long-term memory about the user: facts, preferences, corrections. "
                    "search before answering a personal question (list when search finds "
                    "nothing). add only when the user asks to remember or correct something; "
                    "keep the user's wording. Correction: search for the record, then add "
                    "with correction=true and id of the replaced record. delete by id."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["search", "list", "add", "delete"]},
                        "query": {"type": "string", "description": "search: what to find."},
                        "text": {"type": "string", "description": "add: the fact to save."},
                        "id": {"type": "integer", "description": "delete / correction: record id."},
                        "correction": {"type": "boolean", "description": "add: replaces record id."},
                        "limit": {"type": "integer", "description": "search/list: max records (default 10)."},
                    },
                    "required": ["action"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "library",
                "description": (
                    "The user's Library (documents curated in Settings): search by words "
                    "(empty query lists everything), then read a document by id page by "
                    "page with offset. Search with separate words (category, brand, model) "
                    "before concluding something is absent."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["search", "read"]},
                        "query": {"type": "string", "description": "search: words to find."},
                        "id": {"type": "integer", "description": "read: document id from search."},
                        "offset": {"type": "integer", "description": "read: continue from this character."},
                    },
                    "required": ["action"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "todo_update",
                "description": (
                    "Read or update the durable checklist for this run. "
                    "Use items to create checklist entries and updates to "
                    "change status/blocker. Valid statuses: pending, "
                    "in_progress, completed."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "New or replacement checklist items with text/status/position/blocker.",
                        },
                        "updates": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "Updates for existing checklist items; each update needs id.",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "delegate_task",
                "description": (
                    "Delegate a subtask to a child agent. "
                    "Roles: explore, plan, verify, review. The child gets its own "
                    "run_id, context, and inherits the workflow permission mode."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "role": {
                            "type": "string",
                            "description": "One of: explore, plan, verify, review.",
                        },
                        "task": {
                            "type": "string",
                            "description": "Concrete subtask to perform.",
                        },
                        "num_ctx": {
                            "type": "integer",
                            "description": "Optional child context request; 0 uses the server default.",
                        },
                    },
                    "required": ["task"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_bash",
                "description": (
                    "Run a platform-native shell command inside the project root. "
                    "On Windows this is cmd.exe (use dir/type/where or explicitly invoke "
                    "powershell.exe); on POSIX it is /bin/sh. Returns stdout, stderr, and exit code. "
                    "Runs until the command exits or the user presses Stop. For a long-lived process that "
                    "never returns on its own — a dev server, watcher, `npm run dev`, `uvicorn`, "
                    "`flask run` — use run_server so the runtime can track it."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_server",
                "description": (
                    "Background process that keeps running across turns: kind='server' for a "
                    "dev server/watcher, kind='job' for a long build, download or scan. start "
                    "returns the pid at once; logs(pid, wait_seconds up to 600) waits until the "
                    "job ends or new output appears - never wait with sleep; stop(pid) ends it."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["start", "list", "logs", "stop", "stop_all"],
                            "description": "Default 'start'.",
                        },
                        "command": {"type": "string", "description": "Shell command to launch (action='start')."},
                        "port": {"type": "integer", "description": "Optional port the server binds, for reporting."},
                        "pid": {"type": "integer", "description": "Target server pid (action='logs'|'stop')."},
                        "kind": {
                            "type": "string",
                            "enum": ["server", "job"],
                            "description": "Background process kind; default 'server'.",
                        },
                        "wait_seconds": {
                            "type": "integer",
                            "description": "action='logs': wait up to N seconds (max 600) until the job "
                                           "finishes or the server exits/prints new output.",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "unit_convert",
                "description": (
                    "Exact unit conversion: length, mass, volume, area, time, data (KB=1000, KiB=1024, "
                    "bit/byte), data rate, power, energy, frequency, speed, pressure, temperature. "
                    "RU or EN names (кВт, ГБ, Мбит/с, дюйм, °C)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string", "description": "Number, e.g. '2' or '1 500,5'."},
                        "from_unit": {"type": "string"},
                        "to_unit": {"type": "string"},
                    },
                    "required": ["value", "from_unit", "to_unit"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "calc",
                "description": (
                    "Exact calculator, no side effects. evaluate: arithmetic with exact decimals and "
                    "fractions, 16% = 16/100, sqrt/round/min/max/floor/ceil/log/sin...; simplify, "
                    "expand, factor, solve ('x**2 = 4'; systems separated by ';'), diff, integrate "
                    "(lower/upper for definite). date_info: exact Gregorian weekday for one ISO date "
                    "or up to 31 comma-separated YYYY-MM-DD dates; use it for calendar checks, not evaluate."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expression": {"type": "string", "description": "e.g. '125000 * 16%' or '2*x + y = 10; x - y = 2'"},
                        "operation": {"type": "string", "enum": ["evaluate", "simplify", "expand", "factor",
                                                                 "solve", "diff", "integrate", "date_info"]},
                        "variable": {"type": "string", "description": "Comma-separated for solve; default: free symbols."},
                        "lower": {"type": "string"}, "upper": {"type": "string"},
                        "order": {"type": "integer", "minimum": 1, "maximum": 10},
                        "places": {"type": "integer", "minimum": 0, "maximum": 20, "description": "Round result half up."},
                    },
                    "required": ["expression"],
                },
            },
        },
    ]
