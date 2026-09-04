# Структура агента Elira

Это корневая оперативная карта runtime агента. Канонические архитектурные
решения остаются в `docs/ARCHITECTURE.md`, а карта модулей — в
`docs/PROJECT_MAP.md`. Отдельного «каталога навыков» нет: навыки пользователя
являются представлением существующих capability-групп и инструментов, а не
вторым реестром исполнения.

## Поток выполнения

```text
Запрос пользователя
  -> детерминированный preflight (route_request_capabilities)
  -> core tools + выбранные capability groups
  -> OpenAI-compatible tool schemas
  -> ToolRegistry: единственный владелец dispatch
  -> ToolExecutor: permissions, result contract, audit
  -> встроенный / SSH / LSP / MCP handler
  -> явный ToolResult {ok: bool, text: str, ...}
  -> journal, evidence и ответ модели
```

## Источники истины

- Состав групп: `backend/app/application/code_agent/capabilities.py`.
- Описание аргументов модели: `backend/app/application/code_agent/tool_schemas.py`.
- Владение встроенными вызовами: `backend/app/application/code_agent/tools/_dispatch.py`.
- ToolSpec для inventory, аудита, лимитов и базового mutation-профиля:
  `backend/app/application/tool_registry/builtins.py` и provider registries.
- Единое исполнение: `backend/app/application/agent_kernel/executor.py`.
- Классификация конкретного вызова read/change:
  `backend/app/application/agent_kernel/impact_policy.py`.

## Инварианты

1. `capability_load` управляет только видимостью schemas в prompt. Он не выдаёт
   разрешение и не создаёт новый executor.
2. Каждый исполняемый tool имеет ровно одного dispatch owner. Новый registry,
   executor или параллельный DB-слой не добавляется.
3. Permission определяется для конкретного вызова. Смешанные инструменты
   классифицируются по аргументам: например, screenshot является чтением, а
   click/type/fill — изменением внешнего состояния.
4. Каждый handler возвращает явный boolean `ok`. Отсутствующий `ok` является
   ошибкой контракта; текст с префиксом `ERROR:` сам по себе не классифицируется,
   потому что может быть легитимным содержимым файла.
5. Journal и evidence получают статус только из структурированного результата,
   а не угадывают успех по тексту модели или инструмента.

## Запуск и попытки Resume

`code_agent_run_id` стабилен на всём протяжении задачи: по нему живут journal,
контекст продолжения и frontend-reader. Workflow представляет каждую попытку
исполнения отдельной записью:

- первая попытка получает корневой `workflow_run_id`;
- Resume сохраняет `code_agent_run_id`, но создаёт новый `workflow_run_id`;
- `workflow_root_run_id` связывает попытки, а `attempt_number` задаёт их порядок;
- `cancelled` остаётся terminal и никогда не реактивируется;
- отказ конкурентному reader с `delivery_session_already_active` не меняет
  состояние попытки, которой уже принадлежит запуск;
- новый `ask_user`/Workflow request принадлежит текущей попытке Resume.

Так Stop не может быть перезаписан поздним worker, а продолжение остаётся
наблюдаемым как новая попытка той же пользовательской задачи.

## Личность

Каждый основной запуск строит bounded persona prompt из неизменяемого ядра
Elira, автоматически выбранного рабочего режима, текущего настроения, активной
версии развившихся черт и калибровки модели. Личность не меняет доступность
tools, permission mode или память пользовательских фактов.

После успешного пользовательского ответа публичный `/stream` сохраняет
наблюдение через background-задачу уже после SSE. Источником может быть только
существующая server-owned chat session; произвольный или draft `session_id`
отбрасывается. Идентификатор проходит через journal и Resume, поэтому несколько
запусков одного чата не считаются независимыми сессиями. Внутренние Workflow- и
subagent-вызовы личность не обучают. Кандидаты остаются в карантине до выполнения
порогов независимых диалогов, сессий, уверенности и непротиворечивости; после
promotion по одной bounded-черте каждого поддерживаемого слоя входит в следующий
persona prompt. Общий persona prompt имеет жёсткий символьный бюджет.

## Retrieval долговременной памяти

Перед первым вызовом LLM harness отправляет каждый содержательный пользовательский
запрос в существующий `application.memory` facade. Для поиска передаётся отдельный
`memory_query` — исходный текст до добавления вложений и Library. Resume сохраняет
его из journal; если у старого journal или внутреннего caller исходный текст
не подтверждён, автоматическое чтение памяти отключено. Resolver не создаёт
новый memory DB и не полагается на решение модели:

```text
запрос пользователя
  -> lexical search в smart_memory.db
  -> user/work context cues + exact entity match
  -> authoritative policy: durable source, не volatile, не harness-rule
  -> ranking: exact person/company/client/project/server entity выше context match
  -> bounded блок «Личный контекст пользователя» в system prompt
  -> первый вызов LLM
```

Поиск не изменяет содержимое фактов (счётчик обращений остаётся telemetry), а в
prompt проходят только относящиеся к запросу записи. Имена из сохранённых фактов
сопоставляются без учёта регистра. Заглавная буква в начале предложения сама по
себе не считается именем; contextual match требует общей категории отношений
или объекта, а не совпадения произвольного глагола. Формы `клиенты/проекты/серверы`
получают канонический fallback в том же поиске. Старые записи от
`runtime_control(memory_add)` допускаются только этим relevance resolver;
новые получают серверный source `user_command` или `user_correction`.
Поведенческие правила агента не считаются пользовательскими фактами и остаются
в harness/prompt builder. Автоматической записи каждого сообщения нет: запись
остаётся явным действием `remember`/`memory_add` с validation и policy.
Записи включаются как JSON-строки с экранированием переносов и кавычек;
известные prompt-override конструкции отсекаются policy. Семейная категория
сама по себе не означает связь с пользователем: требуется явное «мой/наш»,
ссылка на память или имя. Telegram передаёт raw query только для разрешённых
чатов с включённой памятью; внутренний Workflow его не назначает.
Resolver эвристический: он не обещает полную морфологию имён или разрешение
местоимений между ходами. При недостаточном контексте доступны существующие
`memory_search` и `ask_user`; актуальное состояние объектов проверяется tools.

## Экран «Навыки»

Если экран понадобится, backend строит его read-only представление автоматически
из capability registry, tool schemas и ToolSpec. Экран не хранит отдельные
trigger words, prompt-тексты или имена инструментов и не участвует в dispatch.
Так UI не может рассинхронизироваться с реально доступным runtime.

Минимальная карточка навыка может содержать:

- стабильный id capability-группы;
- локализованное название и описание группы;
- доступность группы и её инструментов;
- read/change профиль из ToolSpec, уточнённый для конкретного вызова через
  `impact_policy`;
- runtime/provider status, если он применим.

## Выбор размещения вычислений

Размещение workload не является permission. Для операции с несколькими
доступными compute-targets агент использует уже существующий `ask_user` и
workflow-состояние `needs_input`, после ответа продолжает тот же запуск и
передаёт выбранный target в канонический tool:

```text
resource + операция
  -> capability_catalog (только доступные targets)
  -> ask_user: auto / local_gpu / server_cpu / local_cpu
  -> ответ пользователя
  -> resource_process(execution_target=<выбор>)
```

`auto` — явный вариант пользователя с порядком `local_gpu -> server_cpu ->
local_cpu`. Он не подставляется агентом вместо вопроса при нескольких доступных
targets. Старое входное имя `server_gpu` принимается как временный alias, но
результаты и телеметрия всегда используют `server_cpu`.

## Добавление возможности

1. Добавить handler или provider adapter к существующему dispatch seam.
2. Добавить schema и ToolSpec с тем же каноническим именем.
3. Включить имя в одну capability-группу либо в компактное core.
4. Вернуть явный `{ok, text}` во всех ветках handler.
5. Добавить контрактный тест `capability -> schema -> owner -> handler` и тест
   permission-классификации, если инструмент смешивает чтение и изменение.
