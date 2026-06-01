# Agent Kernel — план исполнения (Sonnet executor / Opus reviewer)

Рабочая спецификация для реализации `ELIRA_AGENT_KERNEL_ROADMAP.md`.
**Исполнитель:** Sonnet (этот чат). **Ревьюер:** Opus (субагент, по коммиту).

> Этот файл — переключатель режима. Пока он существует, Stop-hook
> `.claude/hooks/require_opus_review.ps1` не даёт завершить ход, если текущий
> коммит не получил `VERDICT: PASS` от Opus. Удали этот файл, когда работа
> закрыта — это снимет принудительное ревью.

## Как работать (Sonnet)

1. Иди по шагам сверху вниз. Один шаг = один коммит.
2. После каждого изменения прогоняй **гейты**:
   - `cd frontend && npx tsc --noEmit` → 0 ошибок;
   - `backend\.venv\Scripts\python.exe -m pytest -q` → зелёно.
3. Закоммить шаг (Co-Authored-By как обычно).
4. **Сразу запусти Opus-ревью** (протокол ниже). Получи `PASS`. Если `FAIL` —
   исправь, новый коммит, повтори ревью. Только потом следующий шаг.
5. Никаких заглушек и мёртвого кода. Зависимости — used-or-removed. Не плодить
   третий контур инструментов (см. P0A роадмапа).

## Протокол ревью (вызывается после каждого коммита)

Запусти субагента-ревьюера на Opus и дождись вердикта:

```
Agent(
  subagent_type="general-purpose",
  model="opus",
  description="Opus review of HEAD",
  prompt="Ты ревьюер. Проверь последний коммit (git show HEAD) против:
    - docs/AGENT_KERNEL_PLAN.md (текущий шаг),
    - docs/ARCHITECTURE.md (архитектура),
    - .claude/hooks/review_rubric.md (критерии).
   Прогони гейты: 'cd frontend && npx tsc --noEmit' и
   'backend\\.venv\\Scripts\\python.exe -m pytest -q'.
   Запиши вердикт в .claude/review/<полный_sha_HEAD>.md: первая строка
   'VERDICT: PASS' или 'VERDICT: FAIL', далее находки. Верни тот же вердикт."
)
```

Reviewer пишет `.claude/review/<sha>.md`. Stop-hook увидит `PASS` и пропустит ход;
при `FAIL` или отсутствии файла — заблокирует с инструкцией.

## Шаги

### Шаг 0 — Baseline: проверить и закоммитить P0B Codex  ✅ ВЫПОЛНЕНО
**Готово:** Opus отревьюил (PASS; gates: tsc 0 ошибок, pytest 2381 passed), закоммичено `753b273`, запушено. **Sonnet: начинай со Шага 1.**

(Исходно: Codex реализовал P0B в рабочем дереве — теперь это закоммиченный baseline.)
- Файлы: `backend/app/application/projects/scope.py` (new), `code_agent/{sandbox,agent_loop,tools}.py`, `api/routes/code_agent_routes.py`, `rag_memory/runtime.py`, `terminal/runtime.py`, `core/config.py`, `domain/tools/terminal_tool.py`, тесты (`test_project_scope.py` new + 4 обновлённых), `frontend/src/{api/codeAgent.ts,components/IdeWorkspaceShell.tsx}`.
- Проверь: `project_scope_id` используется везде, где была идентичность по basename; `legacy_project_key` применён для миграции старых RAG-строк; гейты зелёные.
- Commit: `feat(agent-kernel): P0B project scope isolation + loop limits + shell hardening`.
- Затем Opus-ревью.

### Шаг 1 — Убрать дубль реестра инструментов  ✅ ВЫПОЛНЕНО
**Готово:** Opus PASS (5372a85). Удалён монолит `application/tools/tool_registry.py` и все 4 шима-сироты (`builtin_tools`, `tool_service`, `code_analyzer`, `__init__`). `plugin_system.py` и тест переведены на `app.application.tool_registry.runtime`. Один реестр пишет в `tool_registry.db`. Gates: tsc 0 ошибок, pytest 2381 passed. **Sonnet: начинай со Шага 2.**

### Шаг 2 — Расширить ToolSpec  ✅ ВЫПОЛНЕНО
**Готово:** Opus PASS (b4301c0). 6 новых колонок (permission, side_effect, scopes, timeout_seconds, max_output_chars, idempotent) + additive migration + builtins с tier-аннотациями + 6 тестов. Gates: tsc clean, pytest 2387 passed. Замечание Opus к Шагу 3: расширить Pydantic-схемы `ToolDefinition`/`ToolUpdate` (`api/schemas/tool_registry.py`) новыми полями. **Sonnet: начинай со Шага 3.**

### Шаг 3 — Единый ToolExecutor  ✅ ВЫПОЛНЕНО
**Готово:** Opus PASS (2d139a2). agent_kernel/executor.py + ChatBuiltinToolProvider + ToolRegistry.dispatch_raw + service.run_tool → executor + agent_loop → executor. Одинаковая policy-семантика из всех контуров. Gates: tsc clean, pytest 2387 passed.

### Шаг 3 — Единый ToolExecutor (детали)
- Новый `application/agent_kernel/executor.py`: resolve ToolSpec (`tool_registry`) → policy preflight (`agent_registry/sandbox`, на уровне tool-call) → approval-gate → dispatch (`tool_providers` router) → метрика (`monitoring`) + событие (`event_bus`) → truncate по `max_output_chars`.
- Обернуть встроенные чат-инструменты в `ChatBuiltinToolProvider`, чтобы один executor видел все.
- Перевести `tool_registry.service.run_tool` (чат/workflows) и `code_agent/agent_loop.py` на executor.
- Acceptance: один инструмент = одинаковая policy-семантика из chat/code-agent/workflow; второй путь исполнения не остаётся; гейты зелёные.

### Шаг 4 — Approvals  ✅ ВЫПОЛНЕНО
**Готово:** Opus PASS (1ebedc6). approvals в agent_monitor.db, TTL, одноразовость, маршруты /api/agent-os/approvals, 18 тестов. Gates: tsc clean, pytest 2405 passed. **P1 завершён. Следующий этап: P2 (Policy) по роадмапу.**

### Шаг 4 — Approvals (детали)
- Таблица `approvals` в `agent_monitor.db` (`monitoring/{store,runtime}.py`); маршруты `/api/agent-os/approvals` в `agent_monitor_routes.py`.
- Policy возвращает `waiting_approval` для write/delete/shell/install/etc.; модель не подтверждает своё действие; TTL; одноразовость.
- Acceptance: опасный tool-call ждёт подтверждения; approval истекает по TTL; гейты зелёные.

> tasks/schedules в этот kernel НЕ добавлять — это `autopipeline` + `task_planner` (P5).

---

## P2: Политика действий

### Шаг 5 — Нативные инструменты code-agent в ToolSpec + tier "forbidden"  ✅ ВЫПОЛНЕНО
**Готово:** Opus PASS (da1985a). 11 нативных инструментов зарегистрированы в ToolSpec с правильными тирами (auto/require_approval). Forbidden tier в executor. 19 тестов. Gates: tsc clean, pytest 2424 passed. **Sonnet: начинай со Шага 6.**

### Шаг 5 (детали)

**Проблема:** Нативные инструменты code-agent (`run_bash`, `write_file`, `edit_file`,
`sandbox_run` и др. из `code_agent/tools.py`) диспатчатся через `BuiltinToolProvider`
и обходят approval-gate: `get_tool("run_bash")` → None → `spec=None` → gate пропущен.

**Решение:**
- Добавить в `tool_registry/builtins.py` секцию `_build_native_code_agent_tools()`:
  metadata-записи с `source="code_agent"`, handler=noop (dispatch остаётся
  в `BuiltinToolProvider`). Правила:
  - `read_file`, `glob`, `grep`, `recall`, `web_search`, `web_fetch` → `permission="auto"`
  - `write_file`, `edit_file`, `run_bash`, `sandbox_run`, `sandbox_reset` → `permission="require_approval"`
- Добавить tier `"forbidden"` в executor: немедленный возврат
  `ToolExecutionResult(status="forbidden")` без approval, без dispatch.
  Пример: инструменты с `permission="forbidden"` блокируются полностью.

**Acceptance:**
- `get_tool("run_bash")` → `{permission: "require_approval"}`
- Первый вызов `run_bash` через executor → `status="waiting_approval"`
- Инструмент с `permission="forbidden"` → `status="forbidden"` без создания approval
- Чтение (`read_file`, `glob`) → `status="ok"` без approval
- Гейты зелёные

### Шаг 6 — Runs API + P2 acceptance tests  ✅ ВЫПОЛНЕНО
**Готово:** Opus PASS (4e793b7 + c9583f9). GET /api/agent-os/runs (agent_id, source, status, limit, offset фильтры). 7 тестов. Gates: tsc clean, pytest 2431 passed. P2 завершён.

---

## P3: Контекст и память

### Шаг 7 — Instruction loader  ✅ ВЫПОЛНЕНО
**Готово:** Opus PASS (056fe69). instructions/loader.py: global+project+local, 4k/12k limits, SHA-256 dedup. 12 тестов. Gates: tsc clean, pytest 2443 passed. **Sonnet: начинай со Шага 8.**

### Шаг 7 (детали)

**Что есть:** `.elira/agent.md` уже читается в `_read_project_prompt`. Нет глобальных инструкций, нет `.elira/agent.local.md`, нет лимитов и дедупа.

**Реализовать:**
- Новый `application/instructions/loader.py`: load_instructions(project_root) → str.
- Порядок загрузки: global `~/.elira/agent.md` → project `.elira/agent.md` → local `.elira/agent.local.md`.
- Лимит: 4 000 символов на файл, 12 000 суммарно — лишнее обрезается с предупреждением.
- Дедупликация секций по SHA-256 content hash (одинаковые блоки не включаются дважды).
- Обновить `_build_system_prompt` в `agent_loop.py` использовать loader.
- Acceptance: три файла объединяются; дубли выброшены; превышение лимита усекается; гейты зелёные.

### Шаг 8 — Context compaction  ✅ ВЫПОЛНЕНО
**Готово:** Opus PASS (9895031). context/compaction.py: maybe_compact с DI summarize_fn, threshold 70%, keep 4 пары, fallback. stream_code_agent эмитит context_compacted. 13 тестов. Gates: tsc clean, pytest 2456 passed. **Sonnet: начинай со Шага 9.**

### Шаг 8 (детали)

**Что есть:** В code-agent loop нет compaction. Длинные сессии обрезаются произвольно.

**Реализовать:**
- Новый `application/context/compaction.py`: compact_messages(messages, num_ctx, model, chat_fn) → messages.
- Порог срабатывания: когда примерная длина messages > 70% num_ctx (токены ≈ chars / 4).
- Compaction: запрос модели на rolling summary, сохранить system + summary + последние 4 пары assistant/user.
- Deterministic fallback: если модель недоступна или ошибка — оставить system + «[context compacted]» + последние 8 сообщений.
- Вызывать в начале каждого шага loop перед model call.
- Acceptance: сессия > 70% num_ctx → messages компактируются и summary сохраняется в начале; fallback работает при ошибке модели; гейты зелёные.

### Шаг 9 — MemoryCandidate store + API  ✅ ВЫПОЛНЕНО
**Готово:** Opus PASS (58f1a55). memory_candidates в agent_monitor.db, CRUD, /api/agent-os/memory/candidates, accepted→prompt. 19 тестов. Gates: tsc clean, pytest 2475 passed. **P3 завершён. Следующий этап: P4 Desktop Operator MVP.**

### Шаг 9 (детали)

**Что есть:** `smart_memory` добавляет записи напрямую в RAG. Нет staging-слоя для проверки пользователем.

**Реализовать:**
- Таблица `memory_candidates` в `agent_monitor.db`: id, namespace, content, source, confidence, status (pending|accepted|rejected|expired), created_at, expires_at.
- Additive migration через `monitoring/store.py`.
- CRUD в `monitoring/{store,runtime}.py`.
- Маршруты `/api/agent-os/memory/candidates`: GET (list, filter status), GET /{id}, POST /{id}/accept, POST /{id}/reject, DELETE /{id}.
- В prompt (система code-agent) добавлять только `accepted` кандидаты с namespace=project.
- Acceptance: кандидат создаётся → остаётся pending → при accept попадает в prompt → при reject не попадает; гейты зелёные.

### Шаг 6 (детали)

- `GET /api/agent-os/runs` — последние записи `tool.executed` из event_bus с
  фильтрами `agent_id`, `source`, `status` и `limit`.
- E2E-тест: code-agent loop + `run_bash` → первый вызов → `waiting_approval`.
- E2E-тест: code-agent loop + `glob` → выполняется автоматически (нет approval).
- Acceptance: эндпоинт возвращает список runs; гейты зелёные.
