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
**Готово:** Opus PASS (2d139a2). agent_kernel/executor.py + ChatBuiltinToolProvider + ToolRegistry.dispatch_raw + service.run_tool → executor + agent_loop → executor. Одинаковая policy-семантика из всех контуров. Gates: tsc clean, pytest 2387 passed. **Sonnet: начинай со Шага 4.**

### Шаг 3 — Единый ToolExecutor
- Новый `application/agent_kernel/executor.py`: resolve ToolSpec (`tool_registry`) → policy preflight (`agent_registry/sandbox`, на уровне tool-call) → approval-gate → dispatch (`tool_providers` router) → метрика (`monitoring`) + событие (`event_bus`) → truncate по `max_output_chars`.
- Обернуть встроенные чат-инструменты в `ChatBuiltinToolProvider`, чтобы один executor видел все.
- Перевести `tool_registry.service.run_tool` (чат/workflows) и `code_agent/agent_loop.py` на executor.
- Acceptance: один инструмент = одинаковая policy-семантика из chat/code-agent/workflow; второй путь исполнения не остаётся; гейты зелёные.

### Шаг 4 — Approvals
- Таблица `approvals` в `agent_monitor.db` (`monitoring/{store,runtime}.py`); маршруты `/api/agent-os/approvals` в `agent_monitor_routes.py`.
- Policy возвращает `waiting_approval` для write/delete/shell/install/etc.; модель не подтверждает своё действие; TTL; одноразовость.
- Acceptance: опасный tool-call ждёт подтверждения; approval истекает по TTL; гейты зелёные.

> tasks/schedules в этот kernel НЕ добавлять — это `autopipeline` + `task_planner` (P5).
