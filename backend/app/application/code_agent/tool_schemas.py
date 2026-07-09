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
                "name": "reconcile_server_facts",
                "description": (
                    "Verify the live LLM server's authoritative facts and report any drift. "
                    "Use when the conversation is about the SERVER, the ACTIVE MODEL, the MODEL "
                    "FILE, the CONTEXT WINDOW (n_ctx), or a config/doc that might be stale: it "
                    "probes the running llama-server (/props) and returns the current model_path "
                    "and n_ctx plus any values that changed since the last check. Read-only; "
                    "prefer it over trusting a doc when the question is what is running now."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
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
                "name": "path_exists",
                "description": (
                    "Check whether a LOCAL file or directory exists. Deterministic "
                    "verifier for 'создана папка X' / 'файл X существует' / 'проект "
                    "внутри X' criteria — the local counterpart of ssh_exists. Use this "
                    "(not grep/read) to prove a path was created."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File or directory path, relative to the project root."},
                    },
                    "required": ["path"],
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
                "name": "remember",
                "description": (
                    "Save a durable USER fact / correction into curated memory "
                    "(the source of truth). Use when the user states a lasting fact "
                    "to remember or CORRECTS you (e.g. 'на самом деле…', 'это "
                    "неверно, правильно…', 'запомни, что…'). Such facts are "
                    "auto-injected into future prompts and trusted above web/memory."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fact": {"type": "string", "description": "The fact/correction to remember, as a clear standalone statement."},
                        "correction": {"type": "boolean", "description": "True if this fixes something you got wrong (highest trust). Default false."},
                    },
                    "required": ["fact"],
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
                    "Roles: explore, plan, verify, review. The child gets its own "
                    "run_id, max steps/context/time, cannot write files, "
                    "and cannot delegate again."
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
                "description": (
                    "Run a platform-native shell command inside the project root. "
                    "On Windows this is cmd.exe (use dir/type/where or explicitly invoke "
                    "powershell.exe); on POSIX it is /bin/sh. Returns stdout, stderr, and exit code. "
                    "BLOCKS until the command finishes (max 120s). For a long-lived process that "
                    "never returns on its own — a dev server, watcher, `npm run dev`, `uvicorn`, "
                    "`flask run` — use run_server instead, or run_bash will hang and time out."
                ),
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
                "name": "run_server",
                "description": (
                    "Start and manage LONG-LIVED background processes (dev servers, watchers) "
                    "that never exit on their own. Unlike run_bash, this returns IMMEDIATELY and "
                    "the process keeps running across turns; output is captured to a log file you "
                    "can tail. Use this for `npm run dev`, `uvicorn`, `flask run`, `vite`, etc. "
                    "Actions: 'start' (launch `command`, optional `port`), 'list' (show running "
                    "servers), 'logs' (tail output of `pid`), 'stop' (terminate `pid`), 'stop_all'. "
                    "The runtime stops run-owned servers at the run's end; stop early with 'stop'."
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
                    },
                    "required": [],
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
                    "For academic / peer-reviewed papers (specific studies, "
                    "abstracts, citations) prefer tool_search -> paper_search "
                    "(arXiv, PubMed, bioRxiv, medRxiv, Semantic Scholar and more); "
                    "fall back to web_search only when paper_search is thin/empty. "
                    "Pass `queries` (a list) to run SEVERAL searches in PARALLEL "
                    "in one call (faster than one-by-one; merged + de-duped); "
                    "otherwise pass a single `query`. "
                    "Optionally target engine `categories` (e.g. 'it' for "
                    "github/stackoverflow/pypi, 'science' for arxiv/pubmed, "
                    "'news') and/or `time_range` for recency. "
                    "Call `web_fetch` after on URLs that look relevant. "
                    "Present each source in your answer as a [Title](url) markdown "
                    "link, never a bare URL on its own line."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Single search query."},
                        "queries": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Several queries to run in parallel in one call (up to 6). Prefer this over many sequential web_search calls.",
                        },
                        "top_k": {"type": "integer", "description": "Max results per query (default 5, max 10)."},
                        "categories": {
                            "type": "string",
                            "enum": ["general", "news", "it", "science", "images", "videos", "map", "music", "files"],
                            "description": "Focus engines: 'it'=github/stackoverflow/pypi/mdn, 'science'=arxiv/pubmed/scholar, 'news', 'map', etc. Omit for general web.",
                        },
                        "time_range": {
                            "type": "string",
                            "enum": ["day", "week", "month", "year"],
                            "description": "Bias toward recent results. Omit for no recency filter.",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_fetch",
                "description": (
                    "Fetch a web page and extract the main readable text "
                    "(navigation, ads, scripts stripped; JS-rendered pages are "
                    "auto-rendered). Use AFTER `web_search` to actually read "
                    "pages, not just snippets. Pass `urls` (a list) to fetch "
                    "SEVERAL pages in PARALLEL in one call (far faster than one "
                    "at a time); otherwise pass a single `url`. Plain text up to "
                    "max_chars per page."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Single full http(s) URL."},
                        "urls": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Several http(s) URLs to fetch in parallel in one call (up to 6). Prefer this over many sequential web_fetch calls.",
                        },
                        "max_chars": {"type": "integer", "description": "Truncate each page to this many chars (default 8000, max 50000)."},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser",
                "description": (
                    "Open a URL in a REAL headless browser (Chromium) that runs "
                    "JavaScript, optionally interact (fill/click), then return the "
                    "visible page text. Use when `web_fetch` is not enough: JS-rendered "
                    "pages / SPAs, to verify how a page looks, OR to verify an "
                    "INTERACTION criterion — pass `actions` to type into a field and "
                    "click a button, then the returned DOM reflects the result. This is "
                    "the ONLY honest verifier for 'after clicking X the page shows Y' — "
                    "a grep or node script does NOT count."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Full http(s) URL."},
                        "wait_selector": {"type": "string", "description": "Optional CSS selector to wait for before reading."},
                        "max_chars": {"type": "integer", "description": "Truncate body text to this many chars (default 8000, max 50000)."},
                        "actions": {
                            "type": "array",
                            "description": (
                                "Optional interaction steps performed in order before reading the DOM. Each: "
                                "{\"fill\": \"<label|placeholder|css>\", \"value\": \"...\"} to type (value \"\" clears it), "
                                "{\"select\": \"<label|css>\", \"value\": \"<option>\"} to pick a dropdown option, "
                                "{\"check\": \"<label|css>\"} / {\"uncheck\": ...} to toggle a checkbox, "
                                "{\"click\": \"<button text|css>\"} to click, or {\"wait\": <ms>}. Then the returned DOM "
                                "reflects the result. Example: [{\"fill\": \"Job name\", \"value\": \"nas-backup\"}, "
                                "{\"select\": \"Schedule\", \"value\": \"Daily\"}, {\"check\": \"Encryption\"}, {\"click\": \"Validate\"}]."
                            ),
                            "items": {"type": "object"},
                        },
                        "viewport": {
                            "description": (
                                "Optional. Size the page and MEASURE horizontal overflow to verify a "
                                "layout / responsive criterion (\"no horizontal scroll on mobile\"). "
                                "Pass a preset \"mobile\" (375px) / \"tablet\" (768px) / \"desktop\" (1280px), "
                                "or an explicit {\"width\": 375, \"height\": 812}. The result reports whether "
                                "the layout fits at that width — the honest verifier for a viewport criterion."
                            ),
                        },
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
        {
            "type": "function",
            "function": {
                "name": "translator",
                "description": "Translate text to another language using the local LLM.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text to translate."},
                        "target_lang": {"type": "string", "description": "Target language, e.g. 'english', 'russian', 'spanish'."},
                        "model": {"type": "string", "description": "Optional local model name. Default local-model."},
                    },
                    "required": ["text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "regex",
                "description": "Test a regular expression against text and return matches with offsets and groups.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Regular expression pattern."},
                        "text": {"type": "string", "description": "Text to test."},
                        "flags": {"type": "string", "description": "Optional flags: i, m, s."},
                    },
                    "required": ["pattern", "text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "csv",
                "description": "Analyze a CSV file inside the project and return shape, columns, sample rows, nulls and stats.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "CSV path relative to the project root, or absolute inside it."},
                        "query": {"type": "string", "description": "Optional analysis question."},
                    },
                    "required": ["file_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "converter",
                "description": "Convert a project file using built-in converters: CSV to XLSX, JSON to CSV, MD to DOCX, XLSX to CSV.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_path": {"type": "string", "description": "Source path relative to the project root, or absolute inside it."},
                        "target_format": {"type": "string", "description": "Target extension: xlsx, csv, or docx."},
                    },
                    "required": ["source_path", "target_format"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "http_api",
                "description": "Send an outbound HTTP request. Use only for user-requested API calls.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Absolute http(s) URL."},
                        "method": {"type": "string", "description": "GET, POST, PUT, or DELETE."},
                        "headers": {"type": "object", "description": "Optional request headers."},
                        "body": {"description": "Optional request body for POST/PUT."},
                        "timeout": {"type": "integer", "description": "Timeout seconds. Default 15."},
                    },
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "sql",
                "description": "List, describe, or query allowed local SQLite databases.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "One of: list, describe, query."},
                        "db_path": {"type": "string", "description": "SQLite DB path for describe/query."},
                        "query": {"type": "string", "description": "SQL query for action=query."},
                        "params": {"type": "array", "description": "Optional positional SQL parameters."},
                        "max_rows": {"type": "integer", "description": "Max returned rows for SELECT. Default 100."},
                    },
                    "required": ["action"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "encrypt",
                "description": "Encrypt or decrypt short text using the local Fernet key.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "encrypt or decrypt."},
                        "text": {"type": "string", "description": "Plain text for action=encrypt."},
                        "token": {"type": "string", "description": "Encrypted token for action=decrypt."},
                    },
                    "required": ["action"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "archiver",
                "description": "Create or extract ZIP archives for files inside the project.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "create or extract."},
                        "source_path": {"type": "string", "description": "File or directory path for action=create."},
                        "zip_path": {"type": "string", "description": "ZIP path for action=extract."},
                        "dest": {"type": "string", "description": "Optional destination directory inside the project for extraction."},
                        "output_name": {"type": "string", "description": "Optional output ZIP filename for action=create."},
                    },
                    "required": ["action"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "webhook",
                "description": "Store, list, or clear local webhook payloads in the in-memory webhook buffer.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "store, list, or clear."},
                        "data": {"type": "object", "description": "Payload for action=store."},
                        "source": {"type": "string", "description": "Optional source label for action=store."},
                        "limit": {"type": "integer", "description": "Max items for action=list. Default 20."},
                    },
                    "required": ["action"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "screenshot",
                "description": "Capture a screenshot of an http(s) URL and return view/download URLs.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Absolute http(s) URL to capture."},
                        "width": {"type": "integer", "description": "Viewport width. Default 1280."},
                        "height": {"type": "integer", "description": "Viewport height. Default 800."},
                        "full_page": {"type": "boolean", "description": "Capture full page instead of viewport."},
                    },
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "file_gen",
                "description": (
                    "Generate a Word (.docx) or Excel (.xlsx) document. Returns a "
                    "download URL and saves the file into the project's generated/ "
                    "folder. For Word, pass `content` as plain text; lines starting "
                    "with '## '/'### ' become headings, '- '/'* ' bullets, 'N. ' "
                    "numbered list items. For Excel, pass `headers` (column names) "
                    "and `data` (a list of row arrays)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "format": {"type": "string", "description": "Output format: 'word' or 'excel'."},
                        "title": {"type": "string", "description": "Document title (Word heading / Excel sheet name)."},
                        "content": {"type": "string", "description": "Word body text (markdown-lite). Required for format=word."},
                        "headers": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Excel column headers. Used when format=excel.",
                        },
                        "data": {
                            "type": "array",
                            "items": {"type": "array"},
                            "description": "Excel rows, each a list of cell values. Used when format=excel.",
                        },
                        "filename": {"type": "string", "description": "Optional output filename (extension appended if missing)."},
                    },
                    "required": ["format"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_image",
                "description": (
                    "Describe an image file from the project using the vision model "
                    "(screenshots, photos, diagrams, scanned pages). Returns a text "
                    "description that also transcribes any visible text. Use this to "
                    "'see' an image the project already contains. Requires the vision "
                    "service to be enabled; returns an error otherwise."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to the image, relative to project root or absolute inside it."},
                        "prompt": {"type": "string", "description": "Optional instruction for what to focus on. Defaults to a full description."},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ocr_file",
                "description": (
                    "Extract text from a scanned document or image file in the project "
                    "(PDF scans, photographed pages, screenshots of text) via the OCR "
                    "service. Returns the recognized text. Use this for documents where "
                    "read_file shows only binary/garbage. Requires the OCR service to be "
                    "enabled; returns an error otherwise."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to the document/image, relative to project root or absolute inside it."},
                        "language": {"type": "string", "description": "Optional OCR language hint (e.g. 'ru', 'en'). Defaults to auto-detect."},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "computer",
                "description": (
                    "Control the local desktop like a human: take a screenshot (interpreted "
                    "by the vision model so you can 'see' the screen) and drive the mouse and "
                    "keyboard. Always call action='screenshot' FIRST to read the screen and its "
                    "size before clicking — coordinates are absolute pixels and grounding is "
                    "approximate, so re-screenshot to verify the result of each action. "
                    "Actions: 'screenshot' (returns a description + screen size), 'left_click'/"
                    "'right_click'/'double_click'/'middle_click' (need x,y), 'move' (x,y), "
                    "'type' (text), 'key' (keys, e.g. [\"ctrl\",\"c\"] or [\"enter\"]), 'scroll' "
                    "(amount + direction, optional x,y). Requires the vision service for "
                    "screenshots; needs a desktop session for input. Gated by the approval policy."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "screenshot", "left_click", "right_click", "double_click",
                                "middle_click", "move", "type", "key", "scroll",
                            ],
                            "description": "What to do. Default 'screenshot'.",
                        },
                        "x": {"type": "integer", "description": "Absolute X pixel (click/move/scroll target)."},
                        "y": {"type": "integer", "description": "Absolute Y pixel (click/move/scroll target)."},
                        "text": {"type": "string", "description": "Text to type (action='type')."},
                        "keys": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Key or chord for action='key', e.g. [\"enter\"] or [\"ctrl\",\"c\"].",
                        },
                        "amount": {"type": "integer", "description": "Scroll steps (action='scroll'). Default 3."},
                        "direction": {"type": "string", "enum": ["up", "down"], "description": "Scroll direction. Default 'down'."},
                        "clicks": {"type": "integer", "description": "Click count for click actions. Default 1."},
                        "prompt": {"type": "string", "description": "Optional focus for the screenshot description."},
                    },
                    "required": ["action"],
                },
            },
        },
    ]
