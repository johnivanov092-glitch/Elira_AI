# Laya: CPU, 6 физических ядер, контекст 8192

Проверено 2026-09-26. Эксперимент отклонён по результатам измерений. По явному решению пользователя сервер Laya и модель удалены; исходники и сырые измерения сохранены в истории Git. Production preflight на Laya не включался.

## Вывод

Laya не рекомендуется как маршрутизатор Elira: финальная компактная схема дала **9/16 правильных решений (56,25%)**, включая ошибки на исправлении кода, создании навыка, цитатах и смешанных задачах. Оценка на маленьком наборе не является общей точностью модели; она уже выявляет неприемлемые для этой интеграции случаи.

Laya — модель типизированных решений, не обычный embedding endpoint и не замена вычислению KV/prefill основной LLM. Для русского нужен `multilingual`; корневой checkpoint ориентирован на английский и имеет контекст 512. Мультиязычный checkpoint — mmBERT-base, 322M параметров. [Карточка модели](https://huggingface.co/convaiinnovations/laya).

## Проверенный контракт

- `laya.load(local_snapshot, subfolder="multilingual", device="cpu")` загружает только нужный checkpoint. `agent.predict(state, questions, max_len=8192)` возвращает `answers` и `usage`; `choice` содержит выбранный ключ, `probabilities`, `confidence`, `answer_confidence`, `action.act_probability`. [SDK](https://github.com/NandhaKishorM/laya/blob/4066d5d5fbf08b66c6757ddeedbd797bd7655bc0/laya/agent.py).
- В конфиге checkpoint по умолчанию `max_len=1024`, `head_max_len=256`; encoder содержит `max_position_embeddings=8192`. Это общий бюджет вопроса, вариантов и текста, а не 8192 токена только текста. [Конфиг checkpoint](https://huggingface.co/convaiinnovations/laya/blob/55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851/multilingual/rl_agent_config.json), [конфиг encoder](https://huggingface.co/convaiinnovations/laya/blob/55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851/multilingual/encoder/config.json).
- Обычный `predict` обрезает слишком длинный текст. Адаптеру следует проверять реальный token budget и явно сообщать переполнение. [Построение последовательности](https://github.com/NandhaKishorM/laya/blob/4066d5d5fbf08b66c6757ddeedbd797bd7655bc0/laya/common.py).
- `LAYA_THREADS=6` в официальном HTTP server означает `torch.set_num_threads(6)`, не affinity. Кроме того, HTTP `/v1/systemone` не передаёт `max_len` в SDK: одного поля в JSON недостаточно для 8k. Требуется собственный минимальный адаптер SDK либо явная настройка загруженного checkpoint. [HTTP server](https://github.com/NandhaKishorM/laya/blob/4066d5d5fbf08b66c6757ddeedbd797bd7655bc0/laya/serve.py).

## Версии и воспроизводимость

GitHub commit: `4066d5d5fbf08b66c6757ddeedbd797bd7655bc0`. HF snapshot: `55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851`.

GitHub main и опубликованный wheel обозначены `0.3.20`, но API различается. Wheel проверен как ZIP в памяти: `laya.load` не принимает `revision`/`expected_sha256`; в main они есть. Для wheel следует сначала вызвать `huggingface_hub.snapshot_download` с конкретным revision, затем `laya.load` с локальным путём. Wheel SHA-256: `6039e802fa5effb8dd492061cd7ad39a43087beadc4a4fa4a649614e77eb83d4`. [PyPI 0.3.20](https://pypi.org/project/laya/0.3.20/), [SDK main](https://github.com/NandhaKishorM/laya/blob/4066d5d5fbf08b66c6757ddeedbd797bd7655bc0/laya/agent.py).

Заявленные минимумы пакета: Python 3.10, torch 2.0, transformers 4.48, safetensors 0.4, huggingface_hub 0.20, numpy 1.20. Для эксперимента нужны фиксированные реально проверенные версии, отдельное окружение и CPU wheel PyTorch. [pyproject.toml](https://github.com/NandhaKishorM/laya/blob/4066d5d5fbf08b66c6757ddeedbd797bd7655bc0/pyproject.toml).

## Исходная точка интеграции в Elira

Подтверждено чтением кода до эксперимента; ниже карта исходной реализации, а не утверждение о включённой Laya:

| Файл | Назначение / точка изменения |
|---|---|
| `backend/app/application/code_agent/capabilities.py`, `route_request_capabilities` | Текущая детерминированная классификация доменов, групп, признаков web/download. Возможная граница адаптера Laya. |
| `backend/app/application/chat/local_chat.py`, `classify_domain_policies` | Regex определения доменов. При замене сохранить единственную точку выбора стратегии. |
| `backend/app/application/code_agent/agent_loop.py`, вызов `route_request_capabilities` | Передаёт raw `memory_query`, без содержимого вложений. Возвращаемый `RequestCapabilityRoute` участвует в журнале и выборе guidance. |
| Тот же файл, `Structured planning preflight` | Отдельный генеративный план основной LLM. Laya не создаёт такой план. |
| `backend/app/infrastructure/llm/openai_compatible.py`, embedding client | Другая задача: векторный поиск. Замена Laya здесь нарушила бы контракт векторов. |

Проверявшийся вариант: один CPU сервис модели и тонкий клиент в существующей точке маршрутизации с сохранением `RequestCapabilityRoute`, executor, registry, workflow и основной LLM. Эксперимент показал, что дополнительная классификация не даёт подтверждённой пользы. Значения вероятности не являются пользовательским разрешением, доказательством факта или основанием менять permission mode.

## Ограничения качества

Официальный long-context benchmark содержит 20 обращений в поддержку на восьми языках, без русского. Его выводы и latency Apple GPU нельзя переносить на CPU Elira. [Benchmark](https://github.com/NandhaKishorM/laya/blob/4066d5d5fbf08b66c6757ddeedbd797bd7655bc0/research/scripts/bench_long_context.py).

Авторы указывают нестабильность качества после примерно 4k, избыточную уверенность базовых checkpoint и отсутствие полезного сигнала у `action.act_probability`. Поэтому требуется собственная проверка русских запросов, отрицаний, продолжений и смешанных задач; запуск 8192 сам по себе качество не подтверждает. [Honest Limits](https://huggingface.co/convaiinnovations/laya#honest-limits).

## Реальные измерения

Изолированный Docker `elira-laya` на сервере `192.168.88.15`, Ryzen 9 5900X. По `lscpu` CPU 0–5 относятся к шести разным физическим ядрам; Docker affinity `[0,1,2,3,4,5]`, torch intra-op 6 / inter-op 1. GPU devices отсутствовали, `torch==2.6.0+cpu`, `cuda_available=false`. SDK 0.3.20, transformers 4.57.6, только pinned multilingual snapshot. Существующие модели не перезапускались. Основной backend Elira не получал Laya/torch.

| Проверка | Результат |
|---|---|
| Загрузка модели с диска | 4,21 с; RSS после загрузки около 1688 MiB |
| Исходные 5 классов, 10 коротких RU запросов | 9/10; 62–89 мс. Цитата с «не выполнять» ошибочно дала code |
| 14 одинаково устроенных yes/no/uncertain вопросов | Непригодно: для исправления FastAPI все ответы absent; для GPU-аудио resources absent, memory present. 569–666 мс |
| 11 конкретных классов, расширенный набор 16 | 7/16; медиана 132 мс |
| Финальные 5 классов, тот же расширенный набор 16 | 9/16; финальный повтор медиана 72,02 мс, диапазон 66,86–87,34 мс. Предыдущий прогон той же схемы: 70,09 мс |
| Длинный текст с задачей в конце | 7971 токен текста, **8044 полных токена** на вопрос; 20,88 с; ошибочно other вместо transcription с answer_confidence 0,9391 |
| Пиковая память на длинном тексте | **7750 MiB RSS**, при выделенном контейнеру лимите 8 GiB |
| Переполнение | 13074 полных токена: HTTP 422 `context_window_exceeded`, без скрытой обрезки |

Ожидаемые классы фиксировались до предсказания; для двух смешанных запросов допускался любой из двух релевантных основных классов. Последний прогон на тех же данных не является независимой валидацией. Дальнейшая настройка на этом маленьком наборе остановлена.

Ошибки финальной схемы:

| Запрос | Ожидалось | Получено |
|---|---|---|
| Исправить ошибку backend FastAPI | code | other |
| Написать Python-навык для GPU-аудио | code | transcription |
| Объяснить цитату «удали файлы», ничего не выполнять | other | code |
| Настроить DHCP MikroTik по SSH | other | code |
| Открыть Блокнот и нажать кнопку | other | code |
| Исправить код SSH и проверить web-документацию | code / web | other |
| Расшифровать запись и создать DOCX-отчёт | transcription / document | code, answer_confidence 0,9366 |

В компактной схеме `other` намеренно означал передачу специализированных доменов основной LLM без дополнительной подсказки. Даже если считать настройку сети и управление UI допустимыми code-hints, остаются пять явных ошибок из 16.

Добавленная проверка отключения HTTP-клиента между последовательными вопросами не прошла до конца эксплуатационной проверки: пользователь выбрал удаление Laya, контейнер был остановлен, финальный health poll получил connection refused. Нельзя считать эту проверку успешной. Уже завершившиеся 8k/overflow измерения выше сохранены.

## Удаление эксперимента

Удалены только объекты этого эксперимента: контейнер `elira-laya`, образ `elira/laya:0.3.20-cpu`, volume `elira-laya-models` со скачанным checkpoint и HF-кэшем, сеть `laya_default`, `/home/aiadmin/elira-ai-server/services/laya` и `D:/AIWork/Elira_AI_Server/services/laya`. Перед удалением проверены Docker labels, mount `/models`, абсолютные пути и принадлежность source-папок корню `services/`. Общие базовые образы, существующие модели и глобальные кэши Docker не очищались; `docker prune` не выполнялся.

После удаления: Docker inspect подтвердил отсутствие всех четырёх объектов; обе source-папки отсутствуют; порт 8008 закрыт. Существующие девять контейнеров остались активными, LLM/embedding/vision `/health` на 8000/8001/8004 вернули HTTP 200 `status=ok`.

История `D:/AIWork/Elira_AI_Server`, ветка `codex/rocm10-runtime`:

- `373afa7` — исходники сервиса, pinned зависимости, воспроизводимый evaluator и сырые измерения.
- `c1d968a` — удаление ровно тех же 14 файлов из рабочей копии.

Оба коммита затрагивают только `services/laya/`; посторонние незакоммиченные изменения не включены. Push не выполнялся. Например, полные финальные измерения доступны через `git show 373afa7:services/laya/final-evaluation-2026-09-26.jsonl`.

Исходники клиентского эксперимента в основном Elira_AI сохранены родительской задачей отдельно в `e71bae1`; серверная очистка не меняла этот репозиторий, кроме настоящего отчёта.
