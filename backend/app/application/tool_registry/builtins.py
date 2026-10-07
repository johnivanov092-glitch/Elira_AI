from __future__ import annotations

from typing import Any


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
        ("grep",        "Grep",         "project", "Search files by content pattern",          15, 20000, True),
        ("project_map", "Project Map",  "project", "Structural overview: tree + entry points + signatures", 30, 30000, True),
        ("recall",      "Recall",       "project", "Search, index or report the project index", 60, 20000, True),
        ("library",     "Library",      "memory",  "Search and read the user's Library documents", 15, 20000, True),
        ("web_search",  "Web Search",   "web",     "Search the web",                          30, 50000, True),
        ("web_fetch",   "Web Fetch",    "web",     "Fetch and parse a web page",              30, 50000, True),
        ("web_query",   "Web Query",    "web",     "Search the run's saved web-evidence corpus for relevant excerpts (web_fetch store)", 30, 20000, True),
        ("browser",     "Browser",      "web",     "Open a URL in a real headless browser (renders JS) and return page text", 90, 50000, True),
        ("csv",         "CSV Analyze",  "data",    "Analyze a CSV file in the project",        30, 50000, True),
        ("calc",        "Calculator",   "math",    "Exact arithmetic, percentages and algebra (no code execution)", 30, 20000, True),
        ("unit_convert", "Unit Convert", "math",   "Exact unit conversion (data, power, length, temperature...)", 15, 5000, True),
        ("finance_calc", "Finance Calc", "math",   "Exact invoices, VAT, markup/margin, discounts, loans", 15, 30000, True),
        ("read_image",  "Read Image",   "vision",  "Describe an image file with the vision model", 120, 30000, True),
        ("resource_process", "Resource Process", "media", "Process a file attached to this run by resource_id on a chosen execution target (auto/local_gpu/server_cpu/local_cpu; legacy server_gpu alias accepted): inspect metadata, extract document text, or transcribe audio/video (mp4/ogg). Read-only, no path.", 3630, 20000, True),
    ]
    # ── Side-effect (require_approval) ─────────────────────────────────────
    approval_tools = [
        ("write_file",     "Write File",     "project", "Write content to a project file",       15,  5000, False),
        ("edit_file",      "Edit File",      "project", "Apply text replacement in a file",      15,  5000, False),
        ("run_bash",       "Run Bash",       "system",  "Execute a shell command in project",   120, 20000, False),
        ("run_server",     "Run Server",     "system",  "Start/manage a long-lived background server", 30, 20000, False),
        ("http_api",       "HTTP API",       "web",     "Send an outbound HTTP API request",      30, 30000, False),
        ("file_gen",       "File Gen",       "media",   "Generate and validate a Word/Excel/PDF file", 120, 10000, False),
        ("resource_materialize", "Materialize Resource", "media", "Copy a file attached to this run into the project workspace (new file, no overwrite) so file/run_bash tools can process it", 60, 5000, True),
        ("resource_publish", "Publish Resource", "media", "Validate and publish an already-produced project file as a downloadable artifact (streaming, hash-bound, no overwrite) via the existing download route", 120, 10000, True),
        ("computer",       "Computer Control", "system", "Control the desktop: screenshot + mouse/keyboard", 60, 20000, False),
        ("runtime_control", "MCP Control", "system", "List, start, stop and configure MCP servers", 900, 50000, False),
        ("telegram", "Telegram", "system", "Send a Telegram message or read the bot log", 60, 20000, False),
        ("itops_registry", "IT Ops Registry", "system", "List or change saved IT Ops assets, profiles and MikroTik routers", 60, 20000, False),
    ]
    auto_side_effect_tools = [
        ("todo_update", "Todo Update", "task", "Read or update the durable run checklist", 15, 10000, False),
        ("delegate_task", "Delegate Task", "task", "Run a child agent until completion or Workflow Stop", 60, 50000, False),
        ("memory", "Memory", "memory", "Search, list, add or delete long-term facts about the user", 15, 10000, False),
    ]

    # Legacy inventory labels retained only for DB compatibility/observability.
    # ToolExecutor never interprets them as authorization scopes.
    _legacy_scope_labels = {
        "read_file": ["fs.read"], "glob": ["fs.read"], "grep": ["fs.read"],
        "project_map": ["fs.read"],
        "recall": ["fs.read"],
        "web_search": ["net.outbound"], "web_fetch": ["net.outbound"], "browser": ["net.outbound"],
        "web_query": ["fs.read"],   # reads the local corpus, no network
        "csv": ["fs.read"],
        "calc": [], "unit_convert": [], "finance_calc": [],
        "todo_update": ["task.write"],
        "delegate_task": ["task.write", "fs.read"],
        "runtime_control": ["shell.exec", "net.outbound", "fs.read", "fs.write"],
        "telegram": ["net.outbound"], "itops_registry": ["fs.write"],
        "memory": ["task.write"], "library": ["fs.read"],
        "write_file": ["fs.write"], "edit_file": ["fs.write"],
        "run_bash": ["shell.exec"], "run_server": ["shell.exec"],
        "http_api": ["net.outbound"],
        "file_gen": ["fs.write"],
        "read_image": ["fs.read", "net.outbound"],
        # Reads the run-bound resource blob (fs.read) and may call remote STT (net.outbound).
        "resource_process": ["fs.read", "net.outbound"],
        # Reads the run-bound resource blob (fs.read) and writes a new workspace file (fs.write).
        "resource_materialize": ["fs.read", "fs.write"],
        # Reads a workspace file (fs.read) and writes a new download artifact (fs.write).
        "resource_publish": ["fs.read", "fs.write"],
        # Reads the run-bound resource blob (fs.read), sends it to the remote OCR
        # worker (net.outbound), and registers the recognized text as a new
        # durable resource — a blob + meta sidecar written under the data root.
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
