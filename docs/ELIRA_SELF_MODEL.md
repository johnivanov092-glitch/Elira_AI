# Elira Self-Model

Компактная карта текущего кода для recall. Сверена 27 сентября 2026 года;
не подтверждает установленную версию или качество живой приёмки.

## Ядро

- **Точка входа**: `stream_code_agent` (SSE-генератор) / `run_code_agent` (sync legacy)
- **Worker**: `_chat_events` — поток + `queue.Queue`, heartbeat 10 c, cancel через `threading.Event`
- **Re-exports**: `indexing`, `inline_tool_calls`, `prompts`, `history`, `project_prompt`
- **Самообновление**: отдельный `scripts/elira_release.py` стабильной платформы
  проверяет и переключает backend/UI-кандидат после завершения работы.
  Agent loop заменяем; supervisor не заменяется кандидатом. Это не OS-изоляция
  от прямой записи с теми же правами. См. `RELEASE_LIFECYCLE.md`.

## Ключевые инварианты

| Инвариант | Механизм |
|---|---|
| Runtime `done.ok` не означает, что задача решена | `CriteriaTracker` наблюдает критерии; `TaskOutcome` проверяет свежие receipts и delivery; `answer_status` отдельно |
| Stop отменяет run и зарегистрированные процессы | cancel event + закрытие LLM response + `taskkill /F /T` / `killpg`; произвольные внешние процессы этим не охватываются |
| Запись сохраняет обнаруженную кодировку | strict encoding detect + byte-exact round-trip; backup best-effort, не гарантия восстановления любого изменения |
| Авторизация — не моя | Workflow permission mode; runtime не добавляет allowlist'ов |
| Секреты — только secret_ref | `sref_*` из vault; plaintext не аргумент |
| DRY уменьшает нежелательные повторы | Sequence breakers сбрасывают match; это не гарантия правильности вывода |

## Анти-дегенерация (DRY)

- `dry_multiplier=0.8`, `dry_base=1.75`, `dry_allowed_length=12`, окно 1024 токена
- Breakers: `\n : " * . - / \ _ , ; = 0-9` — снижают штраф за допустимые повторы IP/MAC/путей/номеров

## Контекст и grounding

- `context_profile`: requested/server/effective window, reserved output/system, safety margin
- `established_facts` + `recent_tool_outputs` — передаются между ходами
- `RunEvidence` — run-local ledger; мутация продвигает project epoch
- `TaskOutcome` — контракт пользователя (TaskSpec), публикации файлов с target/store
  hashes и проверка прямых условий пользователя; модель ничего не объявляет.
  Снимки переживают Resume, но не превращают устаревшую публикацию в текущую.

## Файлы (`_files.py`)

- **Encoding**: BOM авторитетен; cp1251/cp866/cp1252 через charset-normalizer; strict = chaos ≤ 0.10 + round-trip
- **Неопределимая кодировка** → отказ, файл не перезаписывается
- **`recover_read_path_from_glob`**: ровно 1 кандидат из последнего glob,
  та же папка, согласованное расширение и нормализованный префикс имени;
  lookalike-маппинг не разрешает произвольный fuzzy-поиск
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

## Search и RAG

- **grep**: exclude .git/node_modules/.venv/…; max 200 matches, 5000 files, 2 MB/file
- **project_map**: manifests + entry points + top-level signatures (py/js/ts/rs/go)
- **recall** (группа project): поиск по индексу проекта (код + прошлые прогоны);
  `action=index` строит индекс, `action=status` показывает его
- **memory** (группа memory): факты о пользователе — search/list/add/delete.
  `add` сохраняет `agent_note` + run ref по умолчанию; только буквальный
  текущий `memory_query` или остаток его команды «запомни» даёт user provenance.
  Поправка — `correction=true` + id; заметка агента не заменяет доверенный
  пользовательский факт. `volatile_fact` требует live-проверки.
- **library** (группа memory): поиск и чтение документов Библиотеки; добавление,
  удаление и закладки — только в UI
- **Project Corpus**: все chunks одного файла готовятся до публикации;
  manifest и chunks заменяются одной транзакцией после сверки source hash.
  Ошибка сохраняет полную старую версию; поиск не смешивает версии файла.
  Свежесть требует переиндексации; атомарность не распространяется на весь corpus.

## Web

- **search**: parallel fan-out (max 6), categories (it/science/images/…), time_range, page 1-5
- **fetch**: parallel; thin text (<200 chars) → headless browser fallback
- **browser**: Playwright/Chromium; fill/click; cancel → `task.cancel()`
- Успешное чтение DOM сохраняет web receipt с конечным URL; команды действий
  не входят в цитируемый текст. `matched` подтверждает происхождение, не смысл.

## Интеграции

- `mcp` (группа mcp) — MCP-серверы: list/start/stop/restart/tools/add/remove; у каждого
  сервера навык `<id>-mcp`, новый сервер — по навыку `mcp-install` (папка `data/mcp/<id>/`)
- SSH, IT Ops (`itops_registry` + typed `itops_*`), Telegram (`telegram` send/messages) —
  группы по требованию через `capability_load`
- Настройка Telegram-бота и хранилище секретов — только UI (Настройки)
- Статусы: `completed` / `failed` / `needs_input` / `needs_secret` /
  `needs_elevation` / `waiting_approval` / `cancelled`.
  `requested`, `input_request`, `secret_request` — имена helpers, не статусы.

## Executor (`agent_kernel/executor.py`)

1. Resolve `ToolSpec` (presentation, не authorization)
2. Workflow permission: auto-approve → bypass; иначе `impact_policy.decide_approval`
3. Dispatch через injected `dispatch_fn`
4. Truncate → `max_output_chars`
5. Emit `tool.executed`
6. Return `ToolExecutionResult(ok | error | waiting_approval)`

Thread-local: `run_id`, `execution_channel`, `permission_mode`

## Capabilities

- Группы: project, mcp, ssh, itops, telegram, web, desktop, resources, memory, math
- CORE: `{capability_load}` — всегда доступен
- `file_delivery_requested` — подсказка «пользователь просит файл для скачивания»; intent
  и выбор tools принадлежат Qwen. Download keyword не устанавливает delivery contract.
- База каждого запроса (`tool_policy.BASE_TOOLS`): read_file, write_file, edit_file,
  glob, grep, run_bash, run_server, web_search, web_fetch, todo_update, calc, capability_load
  (+ ask_user). Остальное модель загружает сама через `capability_load(group)`.

## Навыки и доставка

- Навыки — папки `data/skills/<имя>/` (SKILL.md, скрипты, своя .venv); модель
  читает, пишет и запускает их обычными инструментами. Прочитанный SKILL.md
  закрепляется на run; история папки — её git.
- Выдача файла: проверяются реальные попытки `resource_publish` и ссылки на
  скачивание в ответе; объявлений нет.
- Receipt связывает target и канонический download store с SHA-256; произвольный
  artifact не закрывает цель. Неподтверждённые ссылки исправляются или становятся
  некликабельными в degraded-ответе. См. `ANSWER_CONTRACT.md` и `TASK_SKILLS.md`.

## Workflows

- `engine.py` → alias → `workflow_engine/runtime.py` (monolith, backward-compat)
- `store.py`: SQLite CRUD (templates + runs)
- `runtime.py`: start/resume/cancel
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
