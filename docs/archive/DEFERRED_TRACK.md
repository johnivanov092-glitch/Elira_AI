# Deferred Track — итоговый статус

Исторический deferred track закрыт. Активное продолжение работ ведётся в
[`BACKLOG.md`](../BACKLOG.md); runtime-инварианты — в
[`ARCHITECTURE.md`](../ARCHITECTURE.md).

## D1 — Remote MCP (streamable HTTP) — ✅ реализовано

`McpHttpClient` поддерживает HTTP transport через тот же MCP runtime/provider
contract, что и stdio. Конфигурация, lifecycle и secret references управляются
через `runtime_control`; произвольные LAN/private endpoints разрешены текущим
Workflow permission contract. URL всё равно обязан иметь корректный `http` или
`https` scheme и host.

## D2 — LSP context provider — ✅ реализовано

Реализованы:

- opt-in конфигурация и lifecycle в `lsp_runtime.py`;
- read-only `diagnostics`, `definition`, `references` через `lsp_provider.py`;
- bounded results, provenance и явный shutdown;
- очистка process tree на Windows;
- mock LSP server и contract tests;
- per-run активация через `runtime_control(lsp_list/lsp_start/lsp_stop)`.

## D3 — Structured action envelopes — superseded

Промежуточный `action_envelopes.py` и `ELIRA_ACTION_ENVELOPES` были удалены при
упрощении runtime. Production path использует native provider tool calls,
ограниченное восстановление inline tool JSON и существующую schema validation
без отдельного обязательного envelope-слоя. Решение зафиксировано в
[`ARCHITECTURE_SIMPLIFICATION_AUDIT_RU.md`](../ARCHITECTURE_SIMPLIFICATION_AUDIT_RU.md).

В этом документе больше нет активных implementation items.
