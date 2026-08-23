# Workflow-first universal agent control plane

Status: completed

## Progress

Завершено. Ниже сохранён первоначальный план; фактический итоговый контракт и
карта файлов находятся в `docs/AGENT_ARCHITECTURE_GUIDE_RU.md`.

Дополнительно к первым срезам реализованы single-agent Workflow adapter,
единый executor без внутренних scope/path/asset/LAN gates, неограниченный по
product-time/steps code-agent loop, runtime control для скрытых интеграций,
native UAC bridge, переносимый vault и удаление устаревших Settings/API surfaces.

Implemented in the first two vertical slices:

- durable `input`, `secret`, `elevation`, and `approval` workflow requests;
- pending-request replay, side-effect-aware startup recovery, chained requests,
  and exact-step resume with duplicate-resolution protection;
- typed Workflow UI cards, including reference-only secret input and an explicit
  unavailable state for the not-yet-connected native UAC bridge;
- run-level `ask`, `accept_edits`, and local `bypass` propagation through the
  existing canonical tool executor;
- behavioral backend/frontend tests for request ordering, resume, secret
  isolation, elevation UI, and permission decisions.
- durable event-bus SSE with cursor replay, ready/heartbeat frames, bounded
  reconnect, finite catch-up mode, and degradation-only UI fallback refresh.

## Problem Statement

Пользователь хочет универсального локального агента, который умеет выполнять
разные типы задач, но не требует ручного управления множеством внутренних
панелей, API и сервисов. Сейчас permission policy распределена между несколькими
слоями, а workflow path не наследует выбранный в основном UI permission mode.
Интеграции, Telegram, IT Ops, pipelines и другие capabilities управляются через
отдельные Settings surfaces, хотя пользователь хочет запускать и настраивать их
естественным запросом агенту.

IT Ops secret values дополнительно привязаны к Windows Credential Manager.
Такая привязка не переживает чистую переустановку Windows без предварительной
миграции и противоречит требованию переносимого локального агента.

Цель рефакторинга — сделать workflow общей моделью каждого запуска и оставить в
UI один permission control с режимами `ask`, `accept_edits`, `bypass`. В режиме
`bypass` агент должен работать без approval interruptions в пределах
утверждённой runtime boundary.

## Solution

Ввести единый workflow run envelope, который переносит identity запроса,
project/session binding, origin, permission mode, capability set, correlation ID,
cancel state и structured events. Обычный single-agent запрос представлять как
одношаговый workflow без обязательного planner pass. Сложные запросы смогут
создавать несколько agent и typed runtime steps.

Все agent steps продолжат использовать существующее code-agent ядро, а все tool
и runtime actions — существующий kernel. Integration и service lifecycle будут
представлены typed runtime capabilities, доступными агенту. При отсутствии
секрета или обязательного человеческого значения runtime вернёт structured
input request, а UI покажет минимальную безопасную форму в текущем workflow.

`bypass` отменяет approval, risk, activation, path и asset restrictions для
зарегистрированных capabilities и разрешает catastrophic Windows operations.
Typed validation, secret isolation, timeout, cancellation, audit и privileged
process isolation остаются runtime invariants. Фактические административные
права предоставляет Windows service token после одноразового UAC bootstrap;
product workflow не может отменить Windows kernel security model.

Windows Credential Manager заменяется application-owned portable encrypted
vault. Workflow UI управляет create, unlock, lock, backup, restore, recovery и
legacy migration. После чистой установки Windows encrypted bundle + passphrase
или recovery key восстанавливают прежние `secret_ref`; privileged helper
регистрируется заново одним UAC flow из UI.

## Commits

1. Добавить behavioral characterization tests для трёх permission modes на
   обычном single-agent run без изменения production behavior.
2. Добавить characterization test, показывающий, что текущий multi-agent
   workflow не наследует permission mode.
3. Добавить characterization tests для runtime lifecycle текущих Telegram,
   integration и IT Ops control paths.
4. Зафиксировать machine-readable inventory постоянных Settings controls и
   связанных runtime operations.
5. Ввести тип canonical workflow run envelope без переключения callers.
6. Добавить в envelope permission mode, origin, project binding и correlation ID.
7. Добавить validation, запрещающую шагу повышать permission относительно run.
8. Добавить persistence и resume tests, подтверждающие сохранение permission
   mode на протяжении всего workflow.
9. Пропустить envelope через существующий workflow coordinator, сохранив старые
   API contracts адаптерами.
10. Пропустить permission mode в существующие agent steps без изменения текущей
    approval decision matrix.
11. Представить обычный code-agent request как одношаговый workflow fast path.
12. Добавить parity tests normal run до и после одношагового workflow adapter.
13. Переключить один внутренний normal-run caller на новый envelope за feature
    switch.
14. Переключить основной UI normal-run caller, сохранив rollback switch.
15. Заменить legacy workflow-to-chat вызов typed request существующего
    code-agent core для одного builtin workflow.
16. Перевести остальные builtin multi-agent steps на typed core request.
17. Удалить legacy tool booleans из workflow step contract после parity tests.
18. Ввести общий typed result runtime operation: completed, failed,
    needs_input, needs_secret, waiting_approval и cancelled.
19. Добавить generic workflow event и inline UI renderer для несекретного
    structured input.
20. Добавить отдельный write-only secure input renderer для secret values и
    доказать тестом, что значение не попадает в transcript, journal или model
    context.
20a. Добавить portable-vault contract и versioned authenticated envelope рядом с
     текущим backend без переключения callers.
20b. Добавить создание vault с random data key и passphrase-derived wrapping
     key.
20c. Добавить unlock/lock lifecycle; decrypted key существует только в памяти и
     очищается на lock, timeout и shutdown.
20d. Добавить atomic write, previous-version recovery и interruption tests.
20e. Добавить recovery-key wrapping и одноразовый secure export flow.
20f. Добавить encrypted backup bundle с manifest/checksums и необходимыми
     metadata stores без plaintext secrets или runtime logs.
20g. Добавить restore в пустой application data directory с сохранением
     `secret_ref` identity.
20h. Добавить workflow operations vault create/unlock/lock/status/backup/restore/
     rotate и inline secure cards.
20i. Добавить явную поэлементную WinCred-to-portable migration: portable write,
     decrypt round-trip, metadata switch, затем delete legacy credential.
20j. Сделать portable backend единственным backend для новых secrets и добавить
     telemetry оставшихся WinCred refs.
20k. Удалить Windows Credential Manager runtime после нулевой legacy telemetry и
     подтверждённого backup/restore drill.
20l. Добавить privileged-helper status/install/repair/re-register operations,
     которые могут вернуть structured `needs_elevation` event.
20m. Добавить inline UAC bootstrap flow для чистой Windows и проверить, что после
     установки helper административные workflow steps не требуют per-action UAC.
21. Представить Telegram status/start/stop/restart как runtime capabilities.
22. Добавить Telegram configuration capabilities, которые возвращают
    needs_secret или needs_input вместо отдельной обязательной панели.
23. Переключить agent workflow на Telegram capabilities и оставить старую панель
    compatibility fallback.
24. Представить MCP, LSP и plugin lifecycle как единообразные runtime
    capabilities.
25. Переключить integration workflows и оставить существующие endpoints как
    наблюдаемые aliases.
26. Представить IT Ops asset/status/diagnostic operations как runtime
    capabilities, не меняя privileged executor boundary.
27. Представить IT Ops changes как workflow runtime steps с inherited permission
    mode и существующим evidence/rollback lifecycle.
28. Добавить end-to-end tests всех трёх permission modes для local и IT Ops
    workflow actions согласно утверждённой boundary.
29. Представить pipeline schedule как workflow trigger management capability.
30. Переключить pipeline UI actions на тот же runtime contract.
31. Представить feature/capability status и enable/disable как runtime
    operations.
32. Перевести подходящие memory/library administration actions на runtime
    capabilities, сохранив отдельные data stores.
33. Добавить Settings usage telemetry и deprecation markers для панелей, которые
    достигли runtime parity.
34. Удалить Telegram panel после нулевой telemetry и успешного rollback window.
35. Удалить integrations panel после нулевой telemetry и успешного rollback
    window.
36. Сократить IT Ops panel до secure bootstrap/recovery либо удалить её после
    полной parity, сохранив inline secure forms.
37. Удалить отдельный pipelines shell после появления workflow trigger UI и
    parity tests.
38. Свести постоянный Settings UI к inference, project, permission default,
    secure connections/secrets и recovery diagnostics.
39. Сделать workflow path без feature switch единственным production caller
    после observability window.
40. Удалить compatibility adapters и public aliases по одному маленькому commit,
    каждый раз сохраняя зелёные contract и end-to-end tests.

## Decision Document

- Workflow является общей моделью run, но не вторым agent runtime.
- Single-agent request использует одношаговый workflow fast path без
  обязательного planner вызова.
- Existing code-agent core остаётся единственным agent execution core.
- Existing tool kernel остаётся единственным tool execution и audit choke point.
- Permission mode задаётся один раз для workflow и наследуется всеми шагами.
- Поддерживаются только три пользовательских режима: `ask`, `accept_edits`,
  `bypass`.
- Workflow UI является единственным источником product-level human approval;
  backend не создаёт отдельный второй permission flow.
- `bypass` не создаёт approval interruptions для зарегистрированных
  capabilities, отключает product path/asset restrictions и разрешает
  catastrophic Windows operations.
- Выбор `bypass` в UI является blanket approval конкретного workflow run.
- Missing path/asset/credential оформляется structured input request в UI, а не
  скрытым policy denial.
- Модель и отдельный workflow step не могут повысить permission mode.
- Integration, Telegram, IT Ops и scheduler lifecycle представлены typed runtime
  operations, а не отдельными agent loops.
- Runtime запрашивает недостающий секрет или значение structured event, после
  чего UI показывает минимальную inline форму.
- Secret values никогда не передаются модели и не записываются в обычные stores.
- Secret vault и restore не зависят от Credential Manager, DPAPI, Windows account
  SID или другого Windows-bound secret state.
- Portable encrypted backup сохраняет stable `secret_ref` identities.
- Windows admin access выполняется через существующую privileged process boundary;
  workflow UI делает одноразовый UAC bootstrap/re-register после чистой установки.
- Settings panels сохраняются до behavioral parity и удаляются постепенно.
- Data stores не объединяются только ради уменьшения количества файлов.
- Privileged change execution остаётся отдельным process/failure boundary.

## Testing Decisions

- Тесты проверяют внешнее поведение run, permission inheritance, emitted events,
  lifecycle result и отсутствие утечки секретов; они не фиксируют внутреннее
  расположение функций.
- Для каждого permission mode проверяются local edits, shell, destructive calls,
  remote/integration actions, resume и multi-step inheritance.
- Для workflow fast path проверяются parity transcript, tool execution,
  approvals, cancellation, session persistence и final status.
- Для runtime capabilities проверяются status, idempotent start/stop, timeout,
  cancellation, retryability и structured errors.
- Для secure input проверяется отсутствие raw value в request logs, transcript,
  model messages, run journal, monitoring, event bus и exports.
- Для portable vault проверяются wrong-passphrase, tampered ciphertext/metadata,
  nonce uniqueness, atomic-write recovery, lock timeout, rotation и отсутствие
  plaintext key material на диске.
- Restore test выполняется в пустом data directory без Windows Credential Manager
  state и подтверждает resolution прежних `secret_ref`.
- Migration test подтверждает порядок portable write → decrypt round-trip →
  metadata switch → old credential delete и retry после сбоя каждого шага.
- Privileged-helper test подтверждает один elevation bootstrap и последующие
  machine-wide Windows actions в `bypass` без product approval/UAC на каждый шаг.
- Для каждого удаляемого Settings surface сначала требуется end-to-end
  natural-language workflow test с тем же результатом.
- Использовать существующие approval-flow, delivery-session, workflow,
  integration и IT Ops contract suites как prior art.
- После каждого commit выполняются затронутые targeted tests; перед завершением
  каждой миграционной фазы — frontend typecheck/build и полный backend suite.

## Out of Scope

- Создание второго agent loop, executor, registry или DB abstraction.
- Одномоментное удаление всех Settings panels и старых routes.
- Физическое объединение memory, session, workflow, monitoring и IT Ops stores.
- Передача credential values модели или сохранение их в transcript.
- Любая зависимость нового vault/backup/restore от Credential Manager, DPAPI,
  Windows account SID или machine-bound Windows key store.
- Попытка обойти Windows kernel security token/UAC вместо одноразовой корректной
  регистрации privileged helper.
- Автоматическое восстановление потерянных WinCred values без заранее выполненной
  portable migration/backup.
- Слияние privileged change executor с основным backend process.
- Обещание одинаковой cancel semantics там, где underlying process нельзя
  прервать до завершения текущего шага.

## Further Notes

Permission boundary подтверждена владельцем продукта: `bypass` — полный
machine-wide Windows scope без product-level approval/path/asset/catastrophic
blocks. Техническая невозможность отменить Windows security token решается
одноразовым UAC bootstrap существующей privileged process boundary.
