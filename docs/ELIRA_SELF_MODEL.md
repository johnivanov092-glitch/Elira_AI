# Elira Self-Model

Компактная самомодель для recall. Обновлять при архитектурных изменениях.

## Ядро

- **Точка входа**: `stream_code_agent` (SSE-генератор) / `run_code_agent` (sync legacy)
- **Worker**: `_chat_events` — поток + `queue.Queue`, heartbeat 10 c, cancel через `threading.Event`
- **Re-exports**: `indexing`, `inline_tool_calls`, `prompts`, `history`, `project_prompt`

## Ключевые инварианты

| Инвариант | Механизм |
|---|---|
| DONE решает верификатор, не слово модели | `CriteriaTracker` в agent_loop |
| Stop = OS-kill, не Python event | Popen + process group + `taskkill /F /T` / `killpg` |
| Не могу молча испортить файл | strict encoding detect + byte-exact round-trip + backup (sha256[:16].bak) |
| Авторизация — не моя | Workflow permission mode; runtime не добавляет allowlist'ов |
| Секреты — только secret_ref | `sref_*` из vault; plaintext не аргумент |
| DRY-дегенерация застрахована | Sequence breakers: цифры, точки, слэши, подчёркивания сбрасывают match |

## Анти-дегенерация (DRY)

- `dry_multiplier=0.8`, `dry_base=1.75`, `dry_allowed_length=12`, окно 1024 токена
- Breakers: `\n : " * . - / \ _ , ; = 0-9` — IP/MAC/пути/номера не мутируются

## Контекст и grounding

- `context_profile`: requested/server/effective window, reserved output/system, safety margin
- `established_facts` + `recent_tool_outputs` — передаются между ходами
- `RunEvidence` — run-local ledger; мутация продвигает project epoch

## Файлы (`_files.py`)

- **Encoding**: BOM авторитетен; cp1251/cp866/cp1252 через charset-normalizer; strict = chaos ≤ 0.10 + round-trip
- **Неопределимая кодировка** → отказ, файл не перезаписывается
- **`recover_read_path_from_glob`**: lookalike-маппинг (a→а, b→б…) для кириллических имён, ровно 1 кандидат
- **Backup**: `data/code_agent_backups/<sha256[:16]>.bak`, один на файл (latest)

## Shell и процессы

- **`run_bash`**: Popen (не subprocess.run), stdin=DEVNULL, drain в bg threads
- **Inline scripts**: `python -c "<multi-line>"` → argv (bypass shell), len==3
- **`run_server`**: kind=server|job; `CREATE_BREAKAWAY_FROM_JOB` (Windows) — worker переживает Job Object
- **Cancel**: `_LIVE_SHELL_PROCS[run_id]` → `_kill_proc_tree` (taskkill /T / killpg)
- **Raw SSH**: подсказка, не блокировка; `#!raw-ssh` подавляет

## Durable jobs (`_background_jobs.py`)

- Journal: `data/background_jobs/jobs.json`, atomic (mkstemp → fsync → os.replace)
- `process_identity(pid)`: Windows — creation time; POSIX — /proc starttime
- `reconcile_job_records`: на рестарте сверяет identity → running → failed если process disappeared
- Retention: max 128 terminal, 7 дней

## Sandbox

- `<DATA_DIR>/sandbox/<slug>/venv/` + `work/` — изолирован от project root
- `pip install` → только в venv; персистентность между turns
- `reset_sandbox` → `shutil.rmtree`

## Search и RAG

- **grep**: exclude .git/node_modules/.venv/…; max 200 matches, 5000 files, 2 MB/file
- **project_map**: manifests + entry points + top-level signatures (py/js/ts/rs/go)
- **recall**: curated facts (lexical) + RAG (vector, project-scoped + global)
- **remember**: `save_fact(text, correction, replaces_id)`; volatile_fact → требует live-проверку

## Web

- **search**: parallel fan-out (max 6), categories (it/science/images/…), time_range, page 1-5
- **fetch**: parallel; thin text (<200 chars) → headless browser fallback
- **browser**: Playwright/Chromium; fill/click; cancel → `task.cancel()`

## Runtime Control

- Тонкий адаптер: MCP, LSP, SSH, IT Ops, Telegram, Library, Memory, Plugins, Workflows, Vault
- Контракт: `completed` / `failed` / `requested` / `input_request` / `secret_request`

## Executor (`agent_kernel/executor.py`)

1. Resolve `ToolSpec` (presentation, не authorization)
2. Workflow permission: auto-approve → bypass; иначе `impact_policy.decide_approval`
3. Dispatch через injected `dispatch_fn`
4. Truncate → `max_output_chars`
5. Emit `tool.executed`
6. Return `ToolExecutionResult(ok | error | waiting_approval)`

Thread-local: `run_id`, `execution_channel`, `permission_mode`

## Capabilities

- 8 groups: project, runtime, web, desktop, resources, data, memory, operations
- CORE: `{capability_load}` — всегда доступен
- `route_request_capabilities` — детерминированный preflight (regex → evidence)
- Domain policies (hidden): Личный, Баланс, Инженерный, Деловой, Инфраструктура, Научный, Медицина

## Workflows

- `engine.py` → alias → `workflow_engine/runtime.py` (monolith, backward-compat)
- `store.py`: SQLite CRUD (templates + runs)
- `runtime.py`: start/resume/cancel
- `multi_agent.py`: мультиагентная оркестрация
- `triggers.py`: scheduler (cron-like)
- `execution.py`: `WorkflowExecutionState` dataclass + metrics

## Инференс

- Только OpenAI-compatible `llama-server` (192.168.88.15:8000/v1) + embeddings (8001/v1)
- Ollama — не активен в текущей архитектуре
- Инференс-сервер — отдельное репо `Elira_AI_Server`

## Стек

- Tauri 2.x (`src-tauri/`) + FastAPI (`backend/app`) + React 18/Vite/TS (`frontend/`)
- Слои: application / domain / infrastructure
- Секреты: `elira_secret.key`, `elira_api_token` — per-machine, вне git
