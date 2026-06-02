# Elira Runtime Intelligence Roadmap

## 1. Назначение

Это рабочий план следующего этапа развития Elira после завершения P0-P8 Agent Kernel.

Цель: повысить качество автономной работы локальных моделей 7B-30B за счет надежного runtime:

- закрыть подтвержденные долги безопасности;
- уменьшить объем схем и контекста, который получает модель;
- подключить реальный model routing;
- расширять автономность только через policy, audit и durable state;
- не создавать второй executor, отдельный реестр инструментов или новую общую БД.

Работы выполнять поэтапно. Каждый следующий этап начинается только после тестов и review
предыдущего.

---

## 2. Исходное Состояние

Базовая точка перед началом работ: `19b6962`.

P0-P8 уже реализовали:

- единый `application/agent_kernel/executor.py`;
- ToolSpec в `application/tool_registry/`;
- approvals в `agent_monitor.db`;
- policy tiers `auto`, `require_approval`, `forbidden`;
- `project_scope_id`;
- ограничения agent loop;
- shell allowlist;
- SSRF guard;
- instruction loader;
- context compaction;
- MemoryCandidate;
- durable task retry;
- skill catalog;
- plugin manifest и запуск `run(args)` в subprocess;
- Telegram approval inbox;
- model profile registry.

### Подтвержденные Долги

1. `backend/app/infrastructure/plugins/plugin_system.py` все еще импортирует plugin-код и
   выполняет lifecycle hooks внутри backend-процесса.
2. `application/agent_kernel/executor.py` реально применяет только часть ToolSpec:
   `permission` и `max_output_chars`.
3. `model_profiles` доступны через CRUD, но runtime routing их не использует.
4. `task_planner` хранит durable-поля и retry-состояние, но startup-resumer для продолжения
   работы после перезапуска не подтвержден.
5. `application/context/compaction.py` должен перестать накапливать несколько старых
   summary-блоков.
6. `application/instructions/loader.py` должен поддержать цепочку инструкций от корня проекта
   до `working_dir`.

### Документационный Долг Перед P9

Выполнить отдельным docs-only изменением:

1. обновить `docs/ARCHITECTURE.md` и `docs/PROJECT_MAP.md`;
2. удалить завершенный `docs/AGENT_KERNEL_PLAN.md`;
3. удалить устаревший корневой roadmap;
4. убедиться, что старый Stop-hook отключился штатно.

Не смешивать docs-cleanup с реализацией P9-P12.

---

## 3. Правила Исполнения

Для каждого шага:

1. сначала сверить фактический код;
2. менять минимальный набор файлов;
3. переиспользовать существующие `tool_registry.db`, `agent_monitor.db`, `event_bus.db`,
   `task_planner` и единый executor;
4. не создавать новый контур инструментов или новую общую БД;
5. добавлять focused-тесты;
6. прогонять backend и frontend gates;
7. делать отдельный commit;
8. проводить review до начала следующего шага;
9. обновлять канонические документы при изменении runtime-контракта.

Все изменения схем БД должны быть additive и idempotent. Миграции проводить через
существующие init/migration paths соответствующих store-модулей. Повторный запуск миграции
не должен ломать существующую БД.

Общие gates:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests -q

cd ..\frontend
npx tsc --noEmit
```

Зафиксированный baseline после P8:

- `pytest`: `2613 passed + 11 subtests`;
- `tsc`: `0` ошибок.

### Trust Boundary Для Контекста

Любой текст, полученный не из системного runtime, считать недоверенным контентом:

- project и local instruction-файлы;
- MCP resources и prompts;
- будущие LSP diagnostics;
- tool output;
- содержимое файлов проекта;
- результаты поиска и внешних запросов.

Недоверенный контент:

- не меняет policy tier;
- не расширяет scopes;
- не активирует side-effect tools автоматически;
- не отменяет approval;
- не увеличивает лимиты;
- не становится системной инструкцией без явной маркировки источника.

Для такого контента обязательны provenance, лимит размера, безопасное логирование без
секретов и явное отделение от системных инструкций.

---

## 4. P9: Обязательный Hardening

Новые возможности нельзя начинать до завершения P9.

### P9.1 Полная Изоляция Плагинов

#### Подтвержденный Долг

`backend/app/infrastructure/plugins/plugin_system.py` выносит основной `run(args)` в
subprocess, но при discovery все еще импортирует plugin-модуль через
`spec.loader.exec_module(mod)`. Lifecycle hooks также исполняются внутри backend-процесса.

#### Что Сделать

- Не импортировать пользовательский `.py` plugin внутри backend-процесса.
- Читать metadata только из manifest JSON.
- Расширить subprocess runner действиями `run`, `hook` и при необходимости `inspect`.
- Для каждого subprocess-вызова применять timeout, JSON input/output, лимиты
  stdout/stderr, явный cwd, минимальное окружение и безопасное логирование ошибок.
- Оставить plugins выключенными по умолчанию.

#### Основные Файлы

- `backend/app/infrastructure/plugins/plugin_system.py`
- `backend/app/infrastructure/plugins/_subprocess_runner.py`
- `backend/app/application/plugins/runtime.py`
- `backend/tests/test_plugin_manifest.py`

#### Критерий Готовности

- Plugin с top-level исключением не ломает backend.
- Бесконечный hook завершается timeout-ом.
- Lifecycle hook выполняется только в дочернем процессе.
- Disabled plugin не импортируется и не запускается.

### P9.2 Довести ToolSpec До Реального Enforcement

#### Подтвержденный Долг

ToolSpec хранит:

- `permission`;
- `side_effect`;
- `scopes`;
- `timeout_seconds`;
- `max_output_chars`;
- `idempotent`.

Но executor фактически применяет только `permission` и `max_output_chars`.

#### Что Сделать

1. Провести инвентаризацию ToolSpec для builtins, code-agent, plugin и MCP tools.
2. Перейти на fail-closed правило: каждый зарегистрированный tool обязан иметь явный
   корректный `permission`. Отсутствующее или неизвестное значение блокирует регистрацию
   либо исполнение и никогда не превращается в `auto`.
3. Зафиксировать минимальную семантику `scopes` и применять ее до dispatch.
4. Применять `timeout_seconds`:
   - жесткий timeout и принудительное завершение для subprocess и внешних provider-вызовов;
   - cooperative deadline для in-process handlers;
   - не считать истечение deadline полноценной отменой Python thread.
5. Использовать `idempotent` в durable retry:
   - idempotent-вызовы повторять только в рамках bounded retry;
   - non-idempotent-вызовы не повторять автоматически без idempotency key или нового
     approval.
6. Эмитить audit-события для timeout, scope block и invalid ToolSpec.

#### Словарь Scopes

Минимальный фиксированный словарь:

- `fs.read`;
- `fs.write`;
- `shell.exec`;
- `net.outbound`;
- `secrets.read`;
- `desktop.control`;
- `home.control`.

Правила:

- неизвестный scope блокируется fail-closed;
- каждый tool получает явный набор scopes;
- MCP и plugins декларируют scopes, но не могут расширять их самостоятельно;
- `side_effect` остается отдельным полем и не заменяется scopes.

#### Основные Файлы

- `backend/app/application/agent_kernel/executor.py`
- `backend/app/application/tool_registry/store.py`
- `backend/app/application/tool_registry/builtins.py`
- `backend/app/application/tool_providers/`
- `backend/app/application/task_planner/runtime.py`
- `backend/app/application/event_bus/runtime.py`

#### Критерий Готовности

- Каждый зарегистрированный tool имеет явный permission tier.
- Tool без корректного permission не достигает provider.
- Scope mismatch блокируется до dispatch.
- Зависший внешний tool завершается timeout-ом.
- Non-idempotent tool не повторяется автоматически.
- В audit видны `tool.timeout`, scope block и invalid ToolSpec.

### P9.3 Подключить Model Profiles К Реальному Routing

#### Подтвержденный Долг

`model_profiles` и `/api/agent-os/models/*` реализованы, но runtime продолжает выбирать
модель через существующие пути, включая `app.core.config.pick_model_for_route`.

#### Что Сделать

- Не создавать второй router моделей.
- Расширить существующий routing-path.
- Добавить детерминированное сопоставление route к роли: `fast`, `code`, `strong`,
  `embedding`.
- Сохранить существующий `route_model_map` для обратной совместимости.
- Для cloud profile требовать явное согласие.
- Использовать profile timeout и context limit.
- Записывать `profile_id`, provider и фактическую модель в run metrics.
- Ограничить fallback, не допускать бесконечный retry.

Порядок выбора модели:

1. явный пользовательский выбор;
2. включенный profile для вычисленной роли;
3. существующий `route_model_map`;
4. `DEFAULT_MODEL`.

Эффективный лимит контекста:

- `min(profile.context_limit, monitoring.max_context_tokens, provider/model limit если известен)`;
- sandbox preflight использует итоговый лимит;
- явный выбор модели не позволяет обойти лимиты.

#### Основные Файлы

- `backend/app/core/config.py`
- `backend/app/application/monitoring/runtime.py`
- `backend/app/application/chat/`
- `backend/app/application/code_agent/agent_loop.py`
- `backend/app/domain/workflows/step_executor.py`
- `backend/app/application/telegram/runtime.py`

#### Критерий Готовности

- Chat, code-agent и workflow используют общий порядок routing.
- Явный выбор пользователя сохраняет приоритет.
- Существующие настройки `route_model_map` продолжают работать.
- Cloud profile не используется без consent.
- Недоступная локальная модель приводит к ограниченному fallback.

### P9.4 Проверка P9

После P9:

- обновить `docs/ARCHITECTURE.md`;
- обновить `docs/PROJECT_MAP.md`;
- прогнать полный backend gate;
- прогнать `tsc`;
- провести security-review plugin isolation, fail-closed ToolSpec и timeout semantics.

---

## 5. P10: Эффективность Локальной Модели

### P10.1 Deferred Tool Search

Этот шаг является обязательным prerequisite перед любым расширением набора tools.

#### Проблема

Локальная модель теряет качество, если на каждом ходу получает слишком много schemas.
Скрыть schemas в prompt недостаточно: executor обязан блокировать обходной вызов скрытого
tool по угаданному имени.

#### Что Сделать

Добавить read-only meta-tool `tool_search`.

Принцип:

1. В начале run модель получает только малый базовый набор: `read_file`, `glob`, `grep`,
   `recall`, `tool_search`.
2. Остальные tools остаются в существующем ToolSpec registry, но не добавляются в prompt.
3. `tool_search(query)` ищет по имени, описанию, категории и source.
4. Найденные tools активируются только для текущего run с ограничением количества.
5. Executor хранит или получает run-scoped allowlist активных tools и проверяет его до
   dispatch.
6. Неизвестный или неактивированный tool блокируется до provider dispatch, даже если модель
   угадала его имя.
7. Активация schema не обходит policy, scopes или approvals.
8. MCP и plugin tools участвуют в deferred discovery на общих правилах.
9. Недоверенный контент не может активировать side-effect tool автоматически.

Для явно инициированного пользователем direct-вызова сохранить отдельный контролируемый
путь, но policy, scopes и approval остаются обязательными.

#### Граница Rollout

- Первый rollout deferred tool search — только code-agent.
- Executor enforcement реализовать как общий run-scoped механизм, пригодный для
  последующего подключения chat и workflows без второго контура.
- Текущий chat-path на этом шаге не менять.

#### Основные Файлы

- `backend/app/application/agent_kernel/executor.py`
- `backend/app/application/tool_registry/runtime.py`
- `backend/app/application/tool_registry/store.py`
- `backend/app/application/tool_providers/registry.py`
- `backend/app/application/code_agent/tools.py`
- `backend/app/application/code_agent/agent_loop.py`
- `backend/app/application/monitoring/`
- `backend/tests/`

#### Критерий Готовности

- Code-agent стартует с малым набором schemas.
- `tool_search("web")` находит и активирует web tools для текущего run.
- Неактивированный tool нельзя вызвать обходным путем.
- Неизвестный tool не достигает provider.
- Policy и approvals едины для direct и deferred tools.

### P10.2 Иерархические Инструкции Для Monorepo

#### Текущее Состояние

`application/instructions/loader.py` уже поддерживает global, project и local
instruction-файлы, дедупликацию и лимиты.

#### Что Добавить

- Опциональный `working_dir` внутри `project_root`.
- Поиск `.elira/agent.md` по цепочке каталогов от project root до working dir.
- Более специфичные инструкции добавлять позже общих.
- Не выходить за пределы `project_root`.
- Сохранить SHA-256 дедупликацию и prompt budget.
- Добавить idempotent init-команду, которая создает `.elira/agent.md`, только если файл
  отсутствует.
- Маркировать инструкции как недоверенный контент с provenance.

#### Основные Файлы

- `backend/app/application/instructions/loader.py`
- `backend/app/application/code_agent/agent_loop.py`
- `backend/tests/test_instruction_loader.py`

#### Критерий Готовности

- Вложенный модуль получает root и local инструкции.
- Одинаковый текст не дублируется.
- Путь выше project root игнорируется.
- Существующий файл не перезаписывается автоматически.

### P10.3 Structured Compaction

#### Текущее Состояние

`application/context/compaction.py` умеет сжимать контекст, вызывать summarizer, сохранять
последние сообщения и использовать deterministic fallback.

#### Что Улучшить

- Распознавать предыдущий compacted summary.
- Сливать предыдущий summary с новым вместо накопления отдельных summary-блоков.
- Сохранять секции: текущая цель, решения, важные файлы, выполненные проверки, pending work,
  ошибки и блокеры.
- Ограничить размер summary.
- При падении модели строить deterministic summary из истории tool calls.
- Не включать секреты и чрезмерные tool outputs.

#### Основные Файлы

- `backend/app/application/context/compaction.py`
- `backend/app/application/code_agent/agent_loop.py`
- `backend/tests/test_context_compaction.py`

#### Критерий Готовности

- Повторная compaction не размножает summary-блоки.
- Pending work и ключевые файлы сохраняются.
- Fallback работает без LLM.
- Размер summary ограничен.

---

## 6. P11: MCP Stdio Context

P11 начинать только после P10.1. Основной путь P11 сохраняет локальный stdio transport.

### P11.1 MCP Resources И Prompts

#### Текущее Состояние

`application/tool_providers/mcp_client.py` поддерживает stdio transport, initialize,
`tools/list` и `tools/call`.

#### Что Добавить

- Negotiation версии протокола с graceful fallback.
- Capability checks.
- `resources/list`;
- `resources/read`;
- resource templates;
- `prompts/list`;
- `prompts/get`;
- лимит размера результата;
- audit;
- read-only policy по умолчанию;
- provenance и маркировку результата как недоверенного контента.

Официальная спецификация:

- `https://modelcontextprotocol.io/specification`

#### Основные Файлы

- `backend/app/application/tool_providers/mcp_client.py`
- `backend/app/application/tool_providers/mcp_runtime.py`
- `backend/app/application/tool_providers/mcp_provider.py`
- `backend/tests/test_mcp_client.py`
- `backend/tests/test_mcp_provider.py`

#### Критерий Готовности

- Клиент читает текстовый MCP resource.
- Клиент получает MCP prompt.
- Unsupported capability возвращает контролируемую ошибку.
- Большой resource обрезается по лимиту.
- Версия протокола согласуется с graceful fallback.

---

## 7. P12: Управляемая Автономность

### P12.0 Startup Recovery Для Task Planner

Durable state сам по себе не означает durable execution. Этот prerequisite выполнить до
checklist и делегирования.

#### Что Сделать

- Подтвердить текущие task states и retry-переходы по фактическому коду.
- При startup находить recoverable задачи ограниченным запросом.
- Не повторять автоматически non-idempotent действие.
- Для idempotent-задач применять bounded retry и idempotency key.
- Сохранять waiting-approval задачи в paused-состоянии.
- Для неоднозначных stale-задач использовать явный recoverable/blocked state и ручное
  продолжение.
- Эмитить audit-события восстановления.
- Не создавать второй scheduler.

Классификация при recovery:

- `waiting_approval` остается paused;
- stale `in_progress` определяется по истекшему lease/deadline;
- idempotent stale task возвращается в bounded retry;
- non-idempotent stale task переходит в `blocked` / manual resume;
- превышение `max_retries` приводит к terminal `failed` / dead-letter;
- startup recovery обрабатывает ограниченный batch.

#### Основные Файлы

- `backend/app/application/task_planner/runtime.py`
- `backend/app/application/task_planner/`
- `backend/app/application/event_bus/runtime.py`
- `backend/tests/`

#### Критерий Готовности

- Симуляция restart не теряет recoverable-задачу.
- Non-idempotent задача не исполняется повторно без явного разрешения.
- Waiting-approval задача остается на паузе.
- Recovery ограничен по числу попыток.

### P12.1 Run-Scoped Checklist

#### Что Сделать

Добавить durable checklist для длинного run:

- `pending`;
- `in_progress`;
- `completed`;
- `blocked`.

Canonical owner: существующий `task_planner`. Новую общую БД не создавать. Миграция схемы
должна быть additive и idempotent.

Каждый item содержит стабильный id, текст, статус, порядок, timestamp и optional blocker.
Добавить tool `todo_update`: чтение автоматически, изменение после policy-проверки, все
изменения аудируются.

#### Критерий Готовности

- Checklist переживает restart backend.
- Run можно продолжить после паузы.
- Изменения checklist видны в audit.

### P12.2 Ограниченные Subagents

#### Принцип

Делегирование строить поверх существующего durable task planner после P12.0.

#### Что Сделать

Добавить `delegate_task` с ролями `explore`, `plan`, `verify`.

Ограничения:

- `max_depth = 1`;
- bounded steps;
- wall-clock timeout;
- отдельный run id;
- отдельный context budget;
- allowlist tools по роли;
- нет записи файлов по умолчанию;
- нет рекурсивного делегирования;
- все tool calls проходят единый executor;
- результат сохраняется в durable state;
- ошибка не обрушает родительский run.

Write-capable subagent вводить только отдельным этапом после review.

#### Критерий Готовности

- Explore-agent не может вызвать write tool.
- Превышение depth блокируется.
- Результат доступен после restart.
- Failed subagent имеет понятный terminal state.

### P12.3 Inference Telemetry

#### Цель

Подготовить измеримую основу для выбора и настройки будущей локальной 30B модели.

#### Что Собирать

- `profile_id`;
- provider;
- фактическая модель;
- prompt и completion tokens;
- context utilization;
- latency;
- time-to-first-token и tokens/sec, если доступны;
- compaction count;
- tool round-trips;
- approval wait duration;
- fallback count;
- error category.

Использовать существующие `agent_monitor.db`, `resource_usage`, `agent_metrics` и dashboard.
Новую telemetry-БД не создавать.

#### Критерий Готовности

- Для run видны модель, токены, latency и tool round-trips.
- Можно сравнить `fast`, `code` и `strong` profiles.
- Отсутствующая provider-метрика не ломает run.

### P12.4 Lite Guard Для Orchestration

Не вводить полный слой action envelopes без данных telemetry.

#### Что Сделать

- Для planner-path возвращать детерминированный blocker при неизвестном tool.
- Для model-originated вызова возвращать blocker при tool, не активированном в текущем run.
- Переиспользовать executor enforcement из P10.1.
- Не добавлять repair retry в быстрый chat-path.

#### Критерий Готовности

- Неизвестный tool не достигает provider.
- Неактивированный tool не достигает provider.
- Ошибка возвращается модели в коротком структурированном виде.
- Обычный chat не получает лишнюю latency.

---

## 8. Отложенный Трек После P12

Эти возможности полезны, но не входят в основной критический путь. Начинать их только после
стабилизации P9-P12 и отдельного review.

### D1 Remote MCP Streamable HTTP

Добавлять только при реальной необходимости удаленных MCP servers. stdio остается default.

Обязательные условия:

- SSRF guard;
- блокировка private и metadata endpoints;
- HTTPS по умолчанию;
- отдельное хранение секретов;
- timeout и bounded retry;
- health status;
- disabled-by-default конфигурация.

### D2 LSP Context Provider

Добавлять отдельным этапом после стабилизации основного runtime.

Обязательные условия:

- disabled by default;
- только read-only tools: diagnostics, definition, references;
- лимиты результатов и provenance;
- явный shutdown;
- очистка дочерних процессов;
- корректное завершение дерева процессов на Windows;
- mock LSP server в тестах.

### D3 Full Structured Action Envelopes

Вводить только если telemetry показывает, что главным ограничением стали ошибки
структурирования действий локальной моделью.

Возможный scope:

- Pydantic schemas для plan, action, tool request, tool result, final result и blocker;
- максимум один repair retry;
- deterministic fallback;
- быстрый chat-path без обязательного JSON.

---

## 9. Что Не Делать

- Не создавать второй executor.
- Не создавать отдельный kernel DB.
- Не создавать новый общий tool registry.
- Не включать полный доступ к ОС по умолчанию.
- Не импортировать plugin-код в backend-процесс.
- Не считать Python thread timeout полноценной отменой side effect.
- Не добавлять бесконечные retries.
- Не включать cloud profile без явного consent.
- Не запускать рекурсивные subagents.
- Не считать durable state достаточным без startup recovery.
- Не позволять недоверенному контенту менять policy, scopes, approvals или tool activation.
- Не добавлять remote MCP transport в основной путь до стабилизации stdio.
- Не добавлять LSP child processes в основной путь до стабилизации P12.
- Не вводить полный action-envelope слой до подтверждения пользы telemetry.
- Не перегружать prompt локальной модели десятками schemas.

---

## 10. Рекомендуемый Порядок Коммитов

1. `docs: refresh canonical kernel architecture and remove completed plan` — pre-P9 docs-only gate; выполняется отдельным docs-only commit'ом, не смешивать с реализацией P9-P12
2. `fix(p9): isolate plugin discovery and hooks from backend process`
3. `fix(p9): enforce fail-closed ToolSpec scopes and external timeouts`
4. `fix(p9): apply idempotency policy to durable retries`
5. `feat(p9): route runtime calls through enabled model profiles`
6. `docs(p9): document hardening contracts`
7. `feat(p10): add run-scoped deferred tool activation and executor allowlist`
8. `feat(p10): load hierarchical project instructions`
9. `feat(p10): merge structured compacted summaries`
10. `feat(p11): add stdio MCP resources prompts and capability negotiation`
11. `feat(p12): recover durable task planner work after restart`
12. `feat(p12): add durable run checklist in task planner`
13. `feat(p12): add bounded read-only task delegation`
14. `feat(p12): record inference telemetry by model profile`
15. `feat(p12): add lite orchestration blocker for unknown tools`
16. `docs(p12): update canonical runtime documentation`

После каждого коммита:

- focused tests;
- полный backend gate;
- frontend `tsc`;
- review;
- только затем следующий шаг.

Отложенный трек не включать в этот commit-план. Для каждого пункта D1-D3 создать отдельную
спецификацию после стабилизации P12.

---

## 11. Главный Риск

Главный риск: превратить Elira в набор функций, который локальная модель не может надежно
использовать.

Критерий качества каждой новой возможности:

1. модель видит минимум необходимого контекста;
2. неизвестный или неактивированный tool блокируется до dispatch;
3. side effect проходит policy и approval;
4. недоверенный контент не повышает привилегии;
5. ошибка ограничена и наблюдаема;
6. durable run можно безопасно восстановить после restart;
7. слабая модель получает deterministic fallback.
