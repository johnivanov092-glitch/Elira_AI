# Проверки агента и журналы

Сверено с исполняемыми CLI и текущими контрактами 18.10.2026. Прежние таблицы
охвата, измерения и результаты находятся
[в архиве](archive/contracts-20261018/AGENT_EVALS.md). Этот документ не объявляет
новый live PASS, golden score или состояние установленного релиза.

## Уровни проверки

| Проверка | Что подтверждает |
| --- | --- |
| Typecheck/build/pytest | Поведение исходников в указанном окружении |
| Atlas check/impact | Архитектурные связи и проверки зоны влияния |
| Аудит журнала | События уже выполненного запуска без нового инференса |
| Live Harness | Конкретный запрос через HTTP/SSE и наблюдённые tools/results |
| Foundation verify | Проверенные байты конкретного кандидата и startup probe |
| Browser/native визуальная проверка | Указанное окно/viewport и пользовательский сценарий |
| Приёмка исходной задачи | Фактический результат и требования пользователя |

Один уровень не подменяет другой. Полный pytest запускается последним, когда
нет открытых вопросов по работе; правило и гейты — [AGENTS.md](../AGENTS.md).

## Аудит завершённых запусков

[routing_eval.py](../backend/tests/smokes/routing_eval.py) и
[journal_audit.py](../backend/tests/smokes/journal_audit.py) используют существующий
Harness/reporter. Replay журнала не вызывает модель повторно:

```powershell
backend\.venv\Scripts\python.exe -u backend\tests\smokes\journal_audit.py --last 2
```

CLI также принимает повторяемый `--run-id`, `--runs-root` и `--output-root`.
Аудит смотрит terminal/resumable state, tool errors, контекст и токены, TTFT и
события продолжения; он не создаёт отсутствующие tool receipts и не превращает
неудачный запуск в PASS.

## Живой путь

```text
routing_eval -> POST /api/code-agent/stream -> delivery_session -> agent core
  -> tools / Workflow requests -> SSE -> summarize_events -> report
```

Команды из корня репозитория:

```powershell
backend\.venv\Scripts\python.exe backend\tests\smokes\routing_eval.py --help
backend\.venv\Scripts\python.exe -u backend\tests\smokes\routing_eval.py --only balanced_chat
```

Последняя команда действительно обращается к настроенному backend/модели.
Нужен работающий стенд, соответствующий задаче и допустимым внешним действиям.
`--backend` выбирает его URL; `--cases` — явный JSON-набор;
`--reasoning-effort` принимает `none`, `low`, `medium`, `xhigh`.
`--timeout 0` не задаёт клиентский deadline, но не отменяет таймауты инструментов
и Web-бюджеты. Stateful/external cases остаются opt-in через `--only` либо
`--include-opt-in`; выбор `--suite core|integration|itops` — метка Harness,
а не действующая capability-группа или persona агента.

## Ограничения legacy Harness

[routing_cases.json](../backend/tests/smokes/routing_cases.json) содержит прежние
`expected_profile`, ожидаемые активации и имена операций. Reporter ещё сравнивает
часть этих полей. У приложения одна личность, legacy `profile_name` игнорируется,
реестр состоит из builtin + MCP, capability-групп сейчас четыре.
`Баланс` остаётся канонической меткой в `persona_defaults.DEFAULT_PROFILE`,
но не выбираемым доменным режимом. Ожидание пустого MCP-старта при настроенном
autostart либо удалённого typed tool может дать несовместимый assertion. Ни наличие case,
ни его старый PASS не доказывает покрытие текущей реализации.

Не переименовывать ожидания и не смягчать критерии только ради зелёного счёта.
В отчёте указывать исходный case, версию кода, конфигурацию стенда, фактические
вызовы и конкретную несовместимость. Полноценный новый live результат требует
реального исхода задачи с неизменёнными согласованными критериями.

## Память и локальные контракты

[memory_eval.py](../backend/tests/smokes/memory_eval.py) создаёт временный
`ELIRA_DATA_DIR`, не использует LLM/сеть и проверяет существующий memory facade
и encrypted portable backup:

```powershell
backend\.venv\Scripts\python.exe -u backend\tests\smokes\memory_eval.py
```

Это отдельный детерминированный контракт. Он не подтверждает правильный выбор
инструмента моделью. Тесты Workflow requests, отмены, ресурсов, доставки и UI
запускаются по зоне влияния через Atlas/pytest/vitest.

## Что сохранять в результате

Run ID, источник сценария, commit или fingerprint, исходные условия и версии
навыков, используемый model/provider/context, первый исход, retries, tool calls,
неудачные операции, итоговый артефакт и статус. Retry-only успех не считается
first-pass. Для файла проверяются скачанные байты/имя/размер/SHA, а не локальный
preview. Dated evidence остаётся в [архиве](archive/README.md) с исходными FAIL
и ограничениями; push исходников не меняет установленный релиз.
