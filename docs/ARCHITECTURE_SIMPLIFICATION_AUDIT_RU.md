# Аудит упрощения архитектуры Elira

> Итог рефактора, 23 августа 2026 года. Полное описание текущей системы:
> [`AGENT_ARCHITECTURE_GUIDE_RU.md`](AGENT_ARCHITECTURE_GUIDE_RU.md).

## Итог

Цель достигнута: обычный и multi-agent Workflow используют один code-agent core,
один ToolExecutor, один provider registry и один UI permission selector.
Интеграции перенесены за `runtime_control`; Windows elevation проходит через
Workflow request и Tauri UAC bridge; активный vault не зависит от WinCred/DPAPI.

```text
Было
  много public execution routes
  + deferred tool activation
  + internal approvals/scopes/assets/feature gates
  + run/tool/SSE deadlines
  + legacy Python/change executors

Стало
  Workflow UI
    -> unified agent core
    -> unified ToolExecutor
    -> canonical runtime providers
    -> OS/LAN
```

## Что подтверждено кодом

| Проверка | Состояние | Владелец |
|---|---|---|
| Single-agent и chat compatibility используют `run_code_agent` | Готово | `application/chat/runtime.py` |
| Multi-agent step возвращается в тот же core | Готово | `domain/workflows/step_executor.py` |
| Все tool calls проходят через один executor | Готово | `agent_kernel/executor.py` |
| Providers собраны одним runtime registry | Готово | `tool_providers/runtime_registry.py` |
| Все подключённые schemas видимы сразу | Готово | `code_agent/agent_loop.py` |
| `tool_search` и deferred activation удалены | Готово | code-agent core/tools |
| Внутренняя approval DB не участвует | Готово | executor + Workflow request lifecycle |
| Operation/path/asset/LAN scopes не авторизуют вызов | Готово | executor/providers/media |
| Permission modes `ask/accept_edits/bypass` | Готово | Workflow schema/UI/impact policy |
| Run/tool/SSE/max-step/no-progress deadlines отсутствуют | Готово | loop/executor/frontend streams |
| Stop убивает зарегистрированное shell process tree | Готово | `tools/_shell.py`, cancel routes |
| MCP/LSP/Telegram/IT Ops/plugins скрыты за runtime | Готово | `tools/_runtime_control.py` |
| Portable AES-GCM vault | Готово | `infrastructure/secrets/vault.py` |
| Native Windows UAC Workflow bridge | Готово | `src-tauri/src/main.rs` |
| Reasoning chip имеет 4 режима | Готово | `Composer.tsx`, `agent_loop.py` |
| `cache_prompt=true` для chat requests | Готово | `openai_compatible.py` |

## Удалённые архитектурные кластеры

- Agent Registry sandbox/route layer;
- deferred tools and operation scopes;
- Autopipeline public/runtime path;
- legacy chat keyword planner modules;
- Elira Patch execution path;
- Progress/criterion/action-envelope hard gates;
- direct Task Planner/Telegram/IT Ops/Tool Registry/Terminal execution routers;
- standalone change executor service;
- duplicate Python execution/generation runtime;
- legacy approval route/store behavior;
- stale Settings integration/asset/Telegram/experimental panels.

## Что намеренно не объединено

SQLite-файлы не объединялись только ради меньшего числа файлов. Workflow requests,
tool inventory, metrics, memory, raw resources, IT Ops evidence и chat sessions
имеют разные транзакции, trust levels и retention. Их разделение — граница данных,
не раздутый agent runtime.

## Что не считается внутренним блокером

- текущий Windows token и UAC;
- API auth для удалённого LAN caller;
- JSON/schema/protocol validation;
- secret redaction;
- физическое context window и output truncation;
- transport/connect/provider/OS errors.

Эти механизмы не принимают продуктового решения «разрешить агенту работу»; они
проверяют действительность токена, формата или внешнего ресурса.

## Риск после упрощения

Главный риск — `bypass` действительно позволяет модели выполнять опасные действия
с правами текущего пользователя без дополнительной карточки. Это осознанный
контракт UI. `accept_edits` остаётся рекомендуемым режимом для повседневной работы:
обычные изменения не мешают, destructive/unknown действия требуют Workflow card.

## Финальная проверка

Единый gate выполняется после завершения всего рефактора:

```powershell
npm --prefix frontend run typecheck
npm --prefix frontend run build
backend\.venv\Scripts\python.exe -m pytest -q
```
