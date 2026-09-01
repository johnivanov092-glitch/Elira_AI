from __future__ import annotations

from typing import Any


def _build_memory_search_tools(search_memory_tool) -> list[dict[str, Any]]:
    return [
        {
            "name": "search_memory",
            "handler": lambda a: search_memory_tool(str(a.get("profile", "default")), str(a.get("query", "")), int(a.get("limit", 5))),
            "display_name": "Search Memory",
            "display_name_ru": "Поиск в памяти",
            "category": "memory",
            "description": "Search semantic memory for relevant facts",
            "parameters_schema": {"type": "object", "properties": {"query": {"type": "string"}, "profile": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]},
            "permission": "auto",
            "side_effect": False,
            "idempotent": True,
            "timeout_seconds": 15,
            "max_output_chars": 20000,
        },
    ]


def _build_web_tools(search_web, research_web, BrowserAgent, WebMultiSearchService) -> list[dict[str, Any]]:
    return [
        {
            "name": "search_web",
            "handler": lambda a: {"ok": True, "query": str(a.get("query", "")), "results": search_web(str(a.get("query", "")), max_results=int(a.get("max_results", 5)))},
            "display_name": "Search Web",
            "display_name_ru": "Поиск в интернете",
            "category": "web",
            "description": "Search the web for current information",
            "parameters_schema": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]},
            "permission": "auto",
            "side_effect": False,
            "idempotent": True,
            "timeout_seconds": 30,
            "max_output_chars": 50000,
        },
        {
            "name": "research_web",
            "handler": lambda a: {"ok": True, "query": str(a.get("query", "")), "results": (r := research_web(query=str(a.get("query", "")), max_results=int(a.get("max_results", 5)))), "count": len(r) if isinstance(r, list) else 0},
            "display_name": "Deep Research",
            "display_name_ru": "Глубокое исследование",
            "category": "web",
            "description": "Fetch and parse web pages for deep research",
            "parameters_schema": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]},
            "permission": "auto",
            "side_effect": False,
            "idempotent": True,
            "timeout_seconds": 60,
            "max_output_chars": 50000,
        },
        {
            "name": "browser_search",
            "handler": lambda a: BrowserAgent().search(str(a.get("query", "")), max_results=int(a.get("max_results", 5))),
            "display_name": "Browser Search",
            "display_name_ru": "Поиск через браузер",
            "category": "web",
            "description": "Search using headless browser",
            "permission": "auto",
            "side_effect": False,
            "idempotent": True,
            "timeout_seconds": 60,
            "max_output_chars": 50000,
        },
        {
            "name": "browser_run",
            "handler": lambda a: BrowserAgent().run(start_url=str(a.get("start_url", "")), steps=a.get("steps", []) if isinstance(a.get("steps", []), list) else [], headless=bool(a.get("headless", True))),
            "display_name": "Browser Run",
            "display_name_ru": "Запуск браузера",
            "category": "web",
            "description": "Run browser automation steps",
            "permission": "require_approval",
            "side_effect": True,
            "idempotent": False,
            "timeout_seconds": 120,
            "max_output_chars": 50000,
        },
        {
            "name": "multi_web_search",
            "handler": lambda a: WebMultiSearchService().search(str(a.get("query", "")), max_results=int(a.get("max_results", 5))),
            "display_name": "Multi Web Search",
            "display_name_ru": "Мульти-поиск",
            "category": "web",
            "description": "Search across multiple web engines",
            "permission": "auto",
            "side_effect": False,
            "idempotent": True,
            "timeout_seconds": 30,
            "max_output_chars": 50000,
        },
    ]


def _build_code_tools(execute_python) -> list[dict[str, Any]]:
    return [
        {
            "name": "python_execute",
            "handler": lambda a: execute_python(str(a.get("code", ""))),
            "display_name": "Python Execute",
            "display_name_ru": "Выполнить Python",
            "category": "code",
            "description": "Execute Python code in a sandboxed subprocess",
            "parameters_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
            "permission": "require_approval",
            "side_effect": True,
            "idempotent": False,
            "timeout_seconds": 60,
            "max_output_chars": 20000,
        },
    ]


def _build_project_file_tools(list_project_tree, read_project_file, write_project_file, search_project) -> list[dict[str, Any]]:
    return [
        {
            "name": "list_project_tree",
            "handler": lambda a: list_project_tree(int(a.get("max_depth", 3)), int(a.get("max_items", 400))),
            "display_name": "Project Tree",
            "display_name_ru": "Дерево проекта",
            "category": "project",
            "description": "List project file tree",
            "permission": "auto",
            "side_effect": False,
            "idempotent": True,
            "timeout_seconds": 15,
            "max_output_chars": 20000,
        },
        {
            "name": "read_project_file",
            "handler": lambda a: read_project_file(str(a.get("path", "")), int(a.get("max_chars", 12000))),
            "display_name": "Read File",
            "display_name_ru": "Прочитать файл",
            "category": "project",
            "description": "Read a project file",
            "parameters_schema": {"type": "object", "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer"}}, "required": ["path"]},
            "permission": "auto",
            "side_effect": False,
            "idempotent": True,
            "timeout_seconds": 15,
            "max_output_chars": 50000,
        },
        {
            "name": "write_project_file",
            "handler": lambda a: write_project_file(str(a.get("path", "")), str(a.get("content", ""))),
            "display_name": "Write File",
            "display_name_ru": "Записать файл",
            "category": "project",
            "description": "Write content to a project file",
            "parameters_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
            "permission": "require_approval",
            "side_effect": True,
            "idempotent": False,
            "timeout_seconds": 15,
            "max_output_chars": 5000,
        },
        {
            "name": "search_project",
            "handler": lambda a: search_project(str(a.get("query", "")), int(a.get("max_hits", 50))),
            "display_name": "Search Project",
            "display_name_ru": "Поиск в проекте",
            "category": "project",
            "description": "Search project files by content",
            "parameters_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            "permission": "auto",
            "side_effect": False,
            "idempotent": True,
            "timeout_seconds": 15,
            "max_output_chars": 20000,
        },
    ]


def _build_project_patch_tools(patch_service) -> list[dict[str, Any]]:
    return [
        {
            "name": "preview_project_patch",
            "handler": lambda a: patch_service.preview_patch(str(a.get("path", "")), str(a.get("new_content", "")), int(a.get("max_chars", 20000))),
            "display_name": "Preview Patch",
            "display_name_ru": "Предпросмотр патча",
            "category": "project",
            "description": "Preview file patch before applying",
            "permission": "auto",
            "side_effect": False,
            "idempotent": True,
            "timeout_seconds": 15,
            "max_output_chars": 20000,
        },
        {
            "name": "apply_project_patch",
            "handler": lambda a: patch_service.apply_patch(str(a.get("path", "")), str(a.get("new_content", ""))),
            "display_name": "Apply Patch",
            "display_name_ru": "Применить патч",
            "category": "project",
            "description": "Apply a file patch",
            "permission": "require_approval",
            "side_effect": True,
            "idempotent": False,
            "timeout_seconds": 15,
            "max_output_chars": 5000,
        },
        {
            "name": "replace_in_file",
            "handler": lambda a: patch_service.replace_in_file(str(a.get("path", "")), str(a.get("old_text", "")), str(a.get("new_text", "")), int(a.get("max_chars", 20000))),
            "display_name": "Replace in File",
            "display_name_ru": "Замена в файле",
            "category": "project",
            "description": "Replace text in a file",
            "permission": "auto",
            "side_effect": False,
            "idempotent": True,
            "timeout_seconds": 15,
            "max_output_chars": 20000,
        },
        {
            "name": "apply_replace_in_file",
            "handler": lambda a: patch_service.apply_replace_in_file(str(a.get("path", "")), str(a.get("old_text", "")), str(a.get("new_text", "")), int(a.get("max_chars", 20000))),
            "display_name": "Apply Replace",
            "display_name_ru": "Применить замену",
            "category": "project",
            "description": "Apply text replacement in a file",
            "permission": "require_approval",
            "side_effect": True,
            "idempotent": False,
            "timeout_seconds": 15,
            "max_output_chars": 5000,
        },
        {
            "name": "rollback_project_patch",
            "handler": lambda a: patch_service.rollback_patch(str(a.get("path", "")), str(a.get("backup_id", ""))),
            "display_name": "Rollback Patch",
            "display_name_ru": "Откатить патч",
            "category": "project",
            "description": "Rollback a file patch from backup",
            "permission": "require_approval",
            "side_effect": True,
            "idempotent": False,
            "timeout_seconds": 15,
            "max_output_chars": 5000,
        },
        {
            "name": "list_patch_backups",
            "handler": lambda a: patch_service.list_backups(path=str(a.get("path", "")).strip() or None, limit=int(a.get("limit", 20))),
            "display_name": "List Backups",
            "display_name_ru": "Список бэкапов",
            "category": "project",
            "description": "List available patch backups",
            "permission": "auto",
            "side_effect": False,
            "idempotent": True,
            "timeout_seconds": 15,
            "max_output_chars": 10000,
        },
    ]


def _build_system_tools(_git_status_fn, _git_commit_fn) -> list[dict[str, Any]]:
    return [
        {
            "name": "git_status",
            "handler": lambda a: _git_status_fn(),
            "display_name": "Git Status",
            "display_name_ru": "Статус Git",
            "category": "system",
            "description": "Show git repository status",
            "permission": "auto",
            "side_effect": False,
            "idempotent": True,
            "timeout_seconds": 15,
            "max_output_chars": 10000,
        },
        {
            "name": "git_commit_push",
            "handler": lambda a: _git_commit_fn(message=str(a.get("message", "AI update")), add_all=True),
            "display_name": "Git Commit",
            "display_name_ru": "Git коммит",
            "category": "system",
            "description": "Commit and push changes",
            "permission": "require_approval",
            "side_effect": True,
            "idempotent": False,
            "timeout_seconds": 60,
            "max_output_chars": 5000,
        },
    ]


def _build_library_tools(list_library_files, build_library_context) -> list[dict[str, Any]]:
    return [
        {
            "name": "list_library",
            "handler": lambda a: list_library_files(),
            "display_name": "List Library",
            "display_name_ru": "Библиотека",
            "category": "memory",
            "description": "List indexed library files",
            "permission": "auto",
            "side_effect": False,
            "idempotent": True,
            "timeout_seconds": 15,
            "max_output_chars": 10000,
        },
        {
            "name": "build_library_context",
            "handler": lambda a: build_library_context(),
            "display_name": "Library Context",
            "display_name_ru": "Контекст библиотеки",
            "category": "memory",
            "description": "Build context from indexed library",
            "permission": "auto",
            "side_effect": False,
            "idempotent": True,
            "timeout_seconds": 15,
            "max_output_chars": 50000,
        },
    ]


def _build_project_brain_tools(map_service, brain_service) -> list[dict[str, Any]]:
    return [
        {
            "name": "project_map_scan",
            "handler": lambda a: map_service.build_map(max_depth=int(a.get("max_depth", 4)), max_items=int(a.get("max_items", 500))),
            "display_name": "Project Map",
            "display_name_ru": "Карта проекта",
            "category": "project",
            "description": "Structural map of the open project: file tree + entry points + top-level signatures",
            "permission": "auto",
            "side_effect": False,
            "idempotent": True,
            "timeout_seconds": 30,
            "max_output_chars": 20000,
        },
        {
            "name": "project_map_search",
            "handler": lambda a: map_service.search(str(a.get("query", "")), max_hits=int(a.get("max_hits", 30))),
            "display_name": "Map Search",
            "display_name_ru": "Поиск по карте",
            "category": "project",
            "description": "Semantic recall over the open project's indexed RAG memory",
            "permission": "auto",
            "side_effect": False,
            "idempotent": True,
            "timeout_seconds": 15,
            "max_output_chars": 20000,
        },
        {
            "name": "project_brain_analyze",
            "handler": lambda a: brain_service.analyze(focus=str(a.get("focus", "backend")), max_iterations=int(a.get("max_iterations", 3))),
            "display_name": "Brain Analyze",
            "display_name_ru": "Анализ проекта",
            "category": "project",
            "description": "Read-only structural analysis of the open project (focus area + map)",
            "permission": "auto",
            "side_effect": False,
            "idempotent": True,
            "timeout_seconds": 120,
            "max_output_chars": 50000,
        },
        {
            "name": "project_brain_loop",
            "handler": lambda a: brain_service.run_loop(path=str(a.get("path", "")), new_content=str(a.get("new_content", "")), message=str(a.get("message", "AI Project Brain patch")), max_iterations=int(a.get("max_iterations", 1)), auto_push=bool(a.get("auto_push", False))),
            "display_name": "Brain Loop",
            "display_name_ru": "Петля разработки",
            "category": "project",
            "description": "Iterative project development loop",
            "permission": "require_approval",
            "side_effect": True,
            "idempotent": False,
            "timeout_seconds": 300,
            "max_output_chars": 20000,
        },
    ]


def _build_native_code_agent_tools() -> list[dict[str, Any]]:
    """Metadata-only ToolSpec records for code_agent native tools.

    These records provide inventory/presentation metadata. Legacy permission,
    scope and timeout columns remain schema-compatible but are not execution
    gates. Handlers are noops — actual dispatch goes through BuiltinToolProvider,
    not through tool_registry.execute_tool.
    """
    def _noop(a: dict) -> dict:
        return {"ok": False, "error": "native tool — execute via code-agent, not tool_registry"}

    # ── Read-only (auto) ────────────────────────────────────────────────────
    auto_tools = [
        ("capability_load", "Load Capability", "system", "Expose one optional built-in tool group for the current run", 15, 10000, True),
        ("read_file",   "Read File",    "project", "Read a file in the project root",         15, 50000, True),
        ("glob",        "Glob",         "project", "List files matching a glob pattern",       15, 20000, True),
        ("path_exists", "Path Exists",  "project", "Check whether a local file or directory exists (verifier for 'папка/файл создан')", 15, 5000, True),
        ("grep",        "Grep",         "project", "Search files by content pattern",          15, 20000, True),
        ("project_map", "Project Map",  "project", "Structural overview: tree + entry points + signatures", 30, 30000, True),
        ("recall",      "Recall",       "memory",  "Recall from project RAG memory",           15, 20000, True),
        ("web_search",  "Web Search",   "web",     "Search the web",                          30, 50000, True),
        ("web_fetch",   "Web Fetch",    "web",     "Fetch and parse a web page",              30, 50000, True),
        ("web_query",   "Web Query",    "web",     "Search the run's saved web-evidence corpus for relevant excerpts (web_fetch store)", 30, 20000, True),
        ("web_sitemap",  "Web Sitemap",  "web",     "Discover URLs from a site sitemap.xml (bounded, same-domain, robots-respected)", 30, 20000, True),
        ("browser",     "Browser",      "web",     "Open a URL in a real headless browser (renders JS) and return page text", 90, 50000, True),
        ("translator",  "Translator",   "text",    "Translate text with the local LLM",        60, 10000, True),
        ("regex",       "Regex",        "text",    "Test a regular expression against text",   15, 20000, True),
        ("csv",         "CSV Analyze",  "data",    "Analyze a CSV file in the project",        30, 50000, True),
        ("bom_validate", "BOM Validate", "data",    "Validate catalog codes, stock, prices, VAT and totals deterministically", 60, 50000, True),
        ("converter",   "Converter",    "media",   "Convert files between supported formats",  60, 10000, True),
        ("read_image",  "Read Image",   "vision",  "Describe an image file with the vision model", 120, 30000, True),
        ("ocr_file",    "OCR File",     "vision",  "Extract text from a scanned document/image",   120, 50000, True),
        ("resource_process", "Resource Process", "media", "Process a file attached to this run by resource_id on a chosen execution target (auto/local_gpu/server_cpu/local_cpu; legacy server_gpu alias accepted): inspect metadata, extract document text, or transcribe audio/video (mp4/ogg). Read-only, no path.", 3630, 20000, True),
    ]
    # ── Side-effect (require_approval) ─────────────────────────────────────
    approval_tools = [
        ("write_file",     "Write File",     "project", "Write content to a project file",       15,  5000, False),
        ("edit_file",      "Edit File",      "project", "Apply text replacement in a file",      15,  5000, False),
        ("run_bash",       "Run Bash",       "system",  "Execute a shell command in project",   120, 20000, False),
        ("run_server",     "Run Server",     "system",  "Start/manage a long-lived background server", 30, 20000, False),
        ("sandbox_run",    "Sandbox Run",    "code",    "Run code in the project sandbox",       60, 20000, False),
        ("sandbox_reset",  "Sandbox Reset",  "code",    "Reset the project sandbox",             30,  5000, False),
        ("http_api",       "HTTP API",       "web",     "Send an outbound HTTP API request",      30, 30000, False),
        ("sql",            "SQL",            "data",    "Query allowed local SQLite databases",   30, 50000, False),
        ("encrypt",        "Encrypt",        "security", "Encrypt or decrypt local text",         30, 10000, False),
        ("archiver",       "Archiver",       "media",   "Create or extract ZIP archives",        60, 20000, False),
        ("webhook",        "Webhook",        "web",     "Store, list, or clear webhook payloads", 15, 10000, False),
        ("screenshot",     "Screenshot",     "web",     "Capture a screenshot of a URL",        120, 10000, False),
        ("file_gen",       "File Gen",       "media",   "Generate and validate a Word/Excel/PDF file", 120, 10000, False),
        ("resource_materialize", "Materialize Resource", "media", "Copy a file attached to this run into the project workspace (new file, no overwrite) so file/run_bash tools can process it", 60, 5000, True),
        ("resource_publish", "Publish Resource", "media", "Validate and publish an already-produced project file as a downloadable artifact (streaming, hash-bound, no overwrite) via the existing download route", 120, 10000, True),
        ("resource_remote_process", "Remote OCR Process", "media", "Send a file attached to this run to the trusted remote OCR worker, verify the result, and attach the recognized text as a new resource (data egress; approval required)", 900, 5000, False),
        ("computer",       "Computer Control", "system", "Control the desktop: screenshot + mouse/keyboard", 60, 20000, False),
        ("runtime_control", "Runtime Control", "system", "Manage integration runtimes through Workflow UI", 900, 50000, False),
        ("reconcile_server_facts", "Reconcile Server Facts", "system", "Probe the live inference server, persist the observation and report configuration drift", 30, 10000, False),
    ]
    auto_side_effect_tools = [
        ("todo_update", "Todo Update", "task", "Read or update the durable run checklist", 15, 10000, False),
        ("delegate_task", "Delegate Task", "task", "Run a child agent until completion or Workflow Stop", 60, 50000, False),
        ("remember", "Remember", "memory", "Save a durable user fact / correction (source of truth)", 15, 5000, False),
    ]

    # Legacy inventory labels retained only for DB compatibility/observability.
    # ToolExecutor never interprets them as authorization scopes.
    _legacy_scope_labels = {
        "read_file": ["fs.read"], "glob": ["fs.read"], "grep": ["fs.read"],
        "path_exists": ["fs.read"],
        "project_map": ["fs.read"], "reconcile_server_facts": ["net.outbound"],
        "recall": ["fs.read"],
        "web_search": ["net.outbound"], "web_fetch": ["net.outbound"], "browser": ["net.outbound"],
        "web_query": ["fs.read"],   # reads the local corpus, no network
        "web_sitemap": ["net.outbound"],  # fetches sitemap.xml/robots.txt (SSRF-guarded)
        "csv": ["fs.read"], "converter": ["fs.read", "fs.write"],
        "todo_update": ["task.write"],
        "delegate_task": ["task.write", "fs.read"],
        "runtime_control": ["shell.exec", "net.outbound", "fs.read", "fs.write"],
        "remember": ["task.write"],
        "write_file": ["fs.write"], "edit_file": ["fs.write"],
        "run_bash": ["shell.exec"], "run_server": ["shell.exec"], "sandbox_run": ["shell.exec"], "sandbox_reset": ["fs.write"],
        "http_api": ["net.outbound"], "sql": ["fs.read", "fs.write"],
        "archiver": ["fs.read", "fs.write"], "screenshot": ["net.outbound", "fs.write"],
        "file_gen": ["fs.write"],
        "read_image": ["fs.read", "net.outbound"], "ocr_file": ["fs.read", "net.outbound"],
        # Reads the run-bound resource blob (fs.read) and may call remote STT (net.outbound).
        "resource_process": ["fs.read", "net.outbound"],
        # Reads the run-bound resource blob (fs.read) and writes a new workspace file (fs.write).
        "resource_materialize": ["fs.read", "fs.write"],
        # Reads a workspace file (fs.read) and writes a new download artifact (fs.write).
        "resource_publish": ["fs.read", "fs.write"],
        # Reads the run-bound resource blob (fs.read), sends it to the remote OCR
        # worker (net.outbound), and registers the recognized text as a new
        # durable resource — a blob + meta sidecar written under the data root.
        "resource_remote_process": ["fs.read", "fs.write", "net.outbound"],
        # Desktop control is shell-level power and is classified by Workflow impact.
        "computer": ["shell.exec", "net.outbound"],
    }

    result: list[dict[str, Any]] = []
    for name, display, cat, desc, timeout, max_chars, idempotent in auto_tools:
        result.append({
            "name": name, "handler": _noop,
            "display_name": display, "category": cat, "description": desc,
            "source": "code_agent",
            "permission": "auto", "side_effect": False,
            "scopes": _legacy_scope_labels.get(name, []),
            "idempotent": idempotent,
            "timeout_seconds": timeout, "max_output_chars": max_chars,
        })
    for name, display, cat, desc, timeout, max_chars, idempotent in auto_side_effect_tools:
        result.append({
            "name": name, "handler": _noop,
            "display_name": display, "category": cat, "description": desc,
            "source": "code_agent",
            "permission": "auto", "side_effect": True,
            "scopes": _legacy_scope_labels.get(name, []),
            "idempotent": idempotent,
            "timeout_seconds": timeout, "max_output_chars": max_chars,
        })
    for name, display, cat, desc, timeout, max_chars, idempotent in approval_tools:
        tool_def = {
            "name": name, "handler": _noop,
            "display_name": display, "category": cat, "description": desc,
            "source": "code_agent",
            "permission": "require_approval", "side_effect": True,
            "scopes": _legacy_scope_labels.get(name, []),
            "idempotent": idempotent,
            "timeout_seconds": timeout, "max_output_chars": max_chars,
        }
        if name == "resource_remote_process":
            # Persist the same strict boundary advertised to the model.  The
            # executor enforces this before creating an approval record.
            tool_def["parameters_schema"] = {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "resource_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
                    "operation": {"type": "string", "enum": ["ocr"]},
                },
                "required": ["resource_id", "operation"],
            }
        result.append(tool_def)

    # Russian descriptions keep provider metadata readable in diagnostics.
    _ru_search_terms = {
        "file_gen": (
            "Генерация файла Word/Excel/PDF",
            "Сгенерировать документ Word (.docx), таблицу Excel (.xlsx) или PDF (.pdf): "
            "ворд, word, docx, doc, эксель, excel, xlsx, таблица, документ, "
            "PDF, пдф, документ PDF, экспорт в PDF, "
            "отчёт, письмо, создать файл, сгенерировать файл",
        ),
        "resource_process": (
            "Обработка прикреплённого файла / ресурса (авто / локальный GPU / CPU / сервер)",
            "Прочитать прикреплённый файл, извлечь текст, расшифровать/транскрибировать "
            "аудио или видео, проанализировать вложение: ресурс, вложение, attachment, "
            "resource, прочитай файл, извлеки текст, расшифруй, транскрибируй, transcribe, "
            "extract text, inspect, аудио, видео, mp4, ogg, голосовое, документ, "
            "локально, локальное железо, локальная видеокарта, на GPU, local gpu, "
            "local cpu, server, вычислительная цель, execution target",
        ),
        "resource_materialize": (
            "Материализовать вложение/ресурс в папку проекта",
            "Скопировать прикреплённый файл (ресурс, вложение) в рабочую папку проекта, "
            "чтобы обработать его обычными инструментами: materialize, положи файл в проект, "
            "сохрани вложение в проект, конвертировать, ffmpeg, распаковать архив, "
            "прогнать через python, resource, attachment, materialize resource, "
            "copy attachment into project, workspace",
        ),
        "resource_publish": (
            "Опубликовать готовый файл пользователю для скачивания",
            "Отдать/опубликовать готовый файл из проекта пользователю, сделать кнопку "
            "Скачать, дать ссылку на скачивание результата: publish, download, скачать, "
            "отдай файл, пришли файл, ссылка на скачивание, готовый файл, результат, "
            "artifact, deliver file, download link, workspace file",
        ),
        "resource_remote_process": (
            "Удалённое распознавание текста (OCR) прикреплённого файла на воркере",
            "Распознать текст из прикреплённого скана PDF или изображения на удалённом "
            "OCR-воркере: удалённый OCR, распознать текст, распознавание, OCR, "
            "remote ocr, recognize text, скан, PDF, изображение, картинка, вложение, "
            "ресурс, attachment, resource, обработать удалённо, на воркере, remote worker",
        ),
    }
    for _spec in result:
        _ru = _ru_search_terms.get(_spec.get("name"))
        if _ru:
            _spec["display_name_ru"], _spec["description_ru"] = _ru
    return result


def _build_ssh_tools() -> list[dict[str, Any]]:
    """Metadata-only ToolSpec records for the SSH provider's tools (P9.2-FIXUP).

    These exist so the unified executor can describe SSH calls;
    actual dispatch runs through SshToolProvider, not the registry handler (the
    handler is a noop). Saved hosts are optional discovery shortcuts; explicit
    SSH targets are accepted directly by the provider.
    """
    def _noop(a: dict) -> dict:
        return {"ok": False, "error": "ssh tool — execute via code-agent SSH provider, not tool_registry"}

    return [
        {
            "name": "ssh_list_hosts", "handler": _noop,
            "display_name": "SSH List Hosts", "display_name_ru": "SSH хосты",
            "category": "ssh", "description": "List saved SSH host shortcuts",
            "source": "ssh",
            "permission": "auto", "side_effect": False, "idempotent": True,
            "scopes": ["net.outbound"],
            "timeout_seconds": 15, "max_output_chars": 10000,
        },
        {
            "name": "ssh_read", "handler": _noop,
            "display_name": "SSH Read", "display_name_ru": "SSH чтение",
            "category": "ssh", "description": "Read a file from a remote host via SSH",
            "source": "ssh",
            "permission": "auto", "side_effect": False, "idempotent": True,
            "scopes": ["net.outbound", "fs.read"],
            "timeout_seconds": 30, "max_output_chars": 50000,
        },
        {
            "name": "ssh_run", "handler": _noop,
            "display_name": "SSH Run", "display_name_ru": "SSH команда",
            "category": "ssh", "description": (
                "Run a shell command on a remote host via SSH; blocking commands "
                "use managed jobs, while raw PowerShell routes to ssh_run_ps"
            ),
            "source": "ssh",
            "permission": "require_approval", "side_effect": True, "idempotent": False,
            "scopes": ["net.outbound", "shell.exec"],
            "timeout_seconds": 120, "max_output_chars": 20000,
        },
        {
            "name": "ssh_write", "handler": _noop,
            "display_name": "SSH Write", "display_name_ru": "SSH запись",
            "category": "ssh", "description": "Write content to a remote file via SSH",
            "source": "ssh",
            "permission": "require_approval", "side_effect": True, "idempotent": False,
            "scopes": ["net.outbound", "fs.write"],
            "timeout_seconds": 60, "max_output_chars": 5000,
        },
        {
            "name": "ssh_run_ps", "handler": _noop,
            "display_name": "SSH PowerShell", "display_name_ru": "SSH PowerShell",
            "category": "ssh",
            "description": (
                "Run a PowerShell script on a remote Windows host via SSH "
                "(base64, no quoting); known blocking waits are moved to the "
                "managed background job runtime with remote PID cleanup"
            ),
            "source": "ssh",
            "permission": "require_approval", "side_effect": True, "idempotent": False,
            "scopes": ["net.outbound", "shell.exec"],
            "timeout_seconds": 120, "max_output_chars": 20000,
        },
        {
            "name": "ssh_replace", "handler": _noop,
            "display_name": "SSH Replace", "display_name_ru": "SSH замена",
            "category": "ssh",
            "description": "Replace a literal substring in a remote file via SSH",
            "source": "ssh",
            "permission": "require_approval", "side_effect": True, "idempotent": False,
            "scopes": ["net.outbound", "fs.write"],
            "timeout_seconds": 60, "max_output_chars": 5000,
        },
        {
            "name": "ssh_assert_contains", "handler": _noop,
            "display_name": "SSH Assert Contains", "display_name_ru": "SSH проверка (есть)",
            "category": "ssh",
            "description": "Assert a remote file contains a substring (verifier)",
            "source": "ssh",
            "permission": "auto", "side_effect": False, "idempotent": True,
            "scopes": ["net.outbound", "fs.read"],
            "timeout_seconds": 30, "max_output_chars": 5000,
        },
        {
            "name": "ssh_assert_not_contains", "handler": _noop,
            "display_name": "SSH Assert Not Contains", "display_name_ru": "SSH проверка (нет)",
            "category": "ssh",
            "description": "Assert a remote file does NOT contain a substring (verifier)",
            "source": "ssh",
            "permission": "auto", "side_effect": False, "idempotent": True,
            "scopes": ["net.outbound", "fs.read"],
            "timeout_seconds": 30, "max_output_chars": 5000,
        },
        {
            "name": "ssh_port_check", "handler": _noop,
            "display_name": "SSH Port Check", "display_name_ru": "SSH порт",
            "category": "ssh",
            "description": "Check whether a TCP port is LISTENING on a remote host (verifier)",
            "source": "ssh",
            "permission": "auto", "side_effect": False, "idempotent": True,
            "scopes": ["net.outbound"],
            "timeout_seconds": 30, "max_output_chars": 5000,
        },
        {
            "name": "ssh_exists", "handler": _noop,
            "display_name": "SSH Exists", "display_name_ru": "SSH существует",
            "category": "ssh",
            "description": "Check whether a file/directory EXISTS on a remote host (verifier)",
            "source": "ssh",
            "permission": "auto", "side_effect": False, "idempotent": True,
            "scopes": ["net.outbound", "fs.read"],
            "timeout_seconds": 30, "max_output_chars": 5000,
        },
        {
            "name": "ssh_not_exists", "handler": _noop,
            "display_name": "SSH Not Exists", "display_name_ru": "SSH удалён",
            "category": "ssh",
            "description": "Assert a file/directory is GONE on a remote host — cleanup verifier",
            "source": "ssh",
            "permission": "auto", "side_effect": False, "idempotent": True,
            "scopes": ["net.outbound", "fs.read"],
            "timeout_seconds": 30, "max_output_chars": 5000,
        },
    ]


def _build_itops_tools() -> list[dict[str, Any]]:
    """Legacy metadata helper for the canonical IT Ops runtime provider."""
    def _noop(a: dict) -> dict:
        return {"ok": False, "error": "itops tool — execute via ItopsToolProvider"}

    return [
        {
            "name": "itops_ssh_healthcheck", "handler": _noop,
            "display_name": "IT-Ops SSH Health Check", "display_name_ru": "SSH диагностика",
            "category": "itops",
            "description": "Read-only SSH diagnostic (hostname, uname -a, uptime) on an explicit target or saved shortcut",
            "source": "itops",
            "permission": "auto", "side_effect": False, "idempotent": True,
            "scopes": ["net.outbound"],
            "timeout_seconds": 60, "max_output_chars": 20000,
        },
        {
            "name": "itops_linux_inventory", "handler": _noop,
            "display_name": "IT-Ops Linux Inventory", "display_name_ru": "Инвентарь Linux",
            "category": "itops",
            "description": "Read-only Linux inventory (os/cpu/mem/disk/net/uptime/blockdev) on a saved verified linux profile",
            "source": "itops",
            "permission": "auto", "side_effect": False, "idempotent": True,
            "scopes": ["net.outbound"],
            "timeout_seconds": 120, "max_output_chars": 14000,
        },
        {
            "name": "itops_windows_inventory", "handler": _noop,
            "display_name": "IT-Ops Windows Inventory", "display_name_ru": "Инвентарь Windows",
            "category": "itops",
            "description": "Read-only Windows inventory (os/version/hostname/uptime/disks/services/ip) on a saved verified windows profile",
            "source": "itops",
            "permission": "auto", "side_effect": False, "idempotent": True,
            "scopes": ["net.outbound"],
            "timeout_seconds": 120, "max_output_chars": 14000,
        },
        {
            "name": "itops_network_inventory", "handler": _noop,
            "display_name": "IT-Ops Network Inventory", "display_name_ru": "Инвентарь сети",
            "category": "itops",
            "description": "Read-only TCP-connect inventory of an explicit IPv4 CIDR and port set",
            "source": "itops",
            "permission": "auto", "side_effect": False, "idempotent": True,
            "scopes": ["net.outbound"],
            "timeout_seconds": 120, "max_output_chars": 14000,
        },
        {
            "name": "itops_systemd_service_inspect", "handler": _noop,
            "display_name": "IT-Ops systemd Inspect", "display_name_ru": "Инспекция systemd-службы",
            "category": "itops",
            "description": "Read-only systemd service inspect for an explicit target and unit",
            "source": "itops",
            "permission": "auto", "side_effect": False, "idempotent": True,
            "scopes": ["net.outbound"],
            "timeout_seconds": 120, "max_output_chars": 14000,
        },
        {
            "name": "itops_config_inspect", "handler": _noop,
            "display_name": "IT-Ops Config Inspect", "display_name_ru": "Инспекция конфигурации",
            "category": "itops",
            "description": "Read-only typed inspection of a server-owned config target (no args, no raw content)",
            "source": "itops",
            "permission": "auto", "side_effect": False, "idempotent": True,
            "scopes": ["net.outbound"],
            "timeout_seconds": 60, "max_output_chars": 6000,
        },
        {
            "name": "itops_database_inspect", "handler": _noop,
            "display_name": "IT-Ops Database Inspect", "display_name_ru": "Инспекция базы данных",
            "category": "itops",
            "description": "Read-only typed inspection of a server-owned SQLite target (no args, SQL, DSN or row data)",
            "source": "itops",
            "permission": "auto", "side_effect": False, "idempotent": True,
            "scopes": ["fs.read"],
            "timeout_seconds": 30, "max_output_chars": 10000,
        },
        {
            "name": "itops_mikrotik_inventory", "handler": _noop,
            "display_name": "IT-Ops MikroTik Inventory", "display_name_ru": "Инвентарь MikroTik",
            "category": "itops",
            "description": "Read-only MikroTik RouterOS 6/7 inventory via the configured typed SSH target",
            "source": "itops",
            "permission": "auto", "side_effect": False, "idempotent": True,
            "scopes": ["net.outbound"],
            "timeout_seconds": 120, "max_output_chars": 14000,
        },
    ]


def build_builtin_tools() -> list[dict[str, Any]]:
    """Seed metadata for tools owned by the canonical provider registry."""
    return [
        *_build_native_code_agent_tools(),
        *_build_ssh_tools(),
        *_build_itops_tools(),
    ]
