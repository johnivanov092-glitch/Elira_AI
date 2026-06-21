"""OpenAI-compatible function-calling tool schemas for the code-agent.

Extracted verbatim from tools.py (no behaviour change) — pure static data,
no dependencies on the tool implementations. Re-exported from tools for
backward compatibility.
"""
from __future__ import annotations

from typing import Any


def build_tool_schemas() -> list[dict[str, Any]]:
    """OpenAI-compatible function-calling tool schemas."""
    return [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file from the project. Returns lines with line numbers.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path relative to project root, or absolute inside it."},
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
                "name": "recall",
                "description": (
                    "Semantic search over the agent's RAG memory. Returns "
                    "relevant code chunks (if the project was indexed) and "
                    "summaries of prior agent runs. Use this before grep when "
                    "looking for 'where is X implemented' or 'what did I do "
                    "last time about Y'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Natural-language search query."},
                        "top_k": {"type": "integer", "description": "Max results (default 5)."},
                        "min_score": {"type": "number", "description": "Cosine similarity threshold 0..1 (default 0.3)."},
                    },
                    "required": ["query"],
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
                    "in_progress, completed, blocked."
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
                    "Delegate a bounded read-only subtask to a child agent. "
                    "Roles: explore, plan, verify. The child gets its own "
                    "run_id, max steps/context/time, cannot write files, "
                    "and cannot delegate again."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "role": {
                            "type": "string",
                            "description": "One of: explore, plan, verify.",
                        },
                        "task": {
                            "type": "string",
                            "description": "Concrete read-only subtask to perform.",
                        },
                        "max_steps": {
                            "type": "integer",
                            "description": "Optional child step cap. Max 6.",
                        },
                        "num_ctx": {
                            "type": "integer",
                            "description": "Optional child context cap. Max 8192.",
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
                "description": "Run a shell command inside the project root. Returns stdout, stderr, and exit code.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout": {"type": "integer", "description": "Seconds. Default 60."},
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    "Search the web for current information. Returns ranked "
                    "list of {title, url, snippet}. Use this BEFORE answering "
                    "any question that depends on facts you don't already "
                    "know — current events, library versions, niche docs. "
                    "Call `web_fetch` after on URLs that look relevant."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query."},
                        "top_k": {"type": "integer", "description": "Max results (default 5, max 10)."},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_fetch",
                "description": (
                    "Fetch one URL and extract the main readable text "
                    "(navigation, ads, scripts stripped). Use AFTER "
                    "`web_search` to actually read a page, not just see "
                    "its snippet. Output is plain text up to max_chars."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Full http(s) URL."},
                        "max_chars": {"type": "integer", "description": "Truncate body to this many chars (default 8000, max 50000)."},
                    },
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser",
                "description": (
                    "Open a URL in a REAL headless browser (Chromium) that runs "
                    "JavaScript, then return the visible page text. Use when "
                    "`web_fetch` is not enough: JS-rendered pages / SPAs, or to "
                    "verify how a page actually looks and behaves."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Full http(s) URL."},
                        "wait_selector": {"type": "string", "description": "Optional CSS selector to wait for before reading."},
                        "max_chars": {"type": "integer", "description": "Truncate body text to this many chars (default 8000, max 50000)."},
                    },
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "sandbox_run",
                "description": (
                    "Execute Python code in an isolated per-project venv. "
                    "Use this (NOT run_bash) for: experimenting with a "
                    "library, prototyping a snippet, anything that needs "
                    "`pip install` of packages you don't want in the user's "
                    "main environment. The sandbox PERSISTS between calls — "
                    "installed packages and files in ./work/ stay. cwd is "
                    "the work/ directory; the user's project tree is NOT "
                    "touched."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Python source to execute."},
                        "install": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional list of pip package specs to install before running (e.g. ['requests', 'rich>=13']).",
                        },
                        "timeout": {"type": "integer", "description": "Seconds. Default 60."},
                    },
                    "required": ["code"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "sandbox_reset",
                "description": (
                    "Wipe the project's sandbox: removes the venv AND "
                    "everything in work/. Use when the sandbox has gotten "
                    "into a broken state or you want a clean slate. Next "
                    "sandbox_run rebuilds from scratch (~3-5s for the venv)."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]
