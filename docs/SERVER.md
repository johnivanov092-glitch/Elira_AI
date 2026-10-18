# Локальный inference server

Сверено с клиентским кодом 18.10.2026. Этот репозиторий владеет приложением,
инструментами, памятью и состоянием пользователя. Серверный репозиторий
`Elira_AI_Server` владеет моделями и эксплуатацией LAN-сервисов.

Полные инструкции находятся в соседнем checkout: `../Elira_AI_Server/README.md`,
`../Elira_AI_Server/Server/ACCESS.md`, `../Elira_AI_Server/docs/README.md`.
Они лежат вне данного Git-репозитория. Hardware/model/process state здесь
не дублируется как подтверждённая текущая конфигурация.

## Клиентский контракт

[openai_compatible.py](../backend/app/infrastructure/llm/openai_compatible.py)
реализует один локальный OpenAI-совместимый путь. Исторический provider ID
`llama_server` остаётся именем приложения; он не доказывает конкретный engine.
Совместимый LAN endpoint llama.cpp/vLLM определяется настройкой и сервером.
Облачный inference в проекте запрещён.

| Настройка | Назначение |
| --- | --- |
| `LLAMA_SERVER_ENABLED`, `LLAMA_SERVER_BASE_URL` | Включение и адрес основного локального provider |
| `LLAMA_SERVER_MODEL`, `LLAMA_SERVER_API_KEY` | Model ID и фактическая авторизация конкретного сервера |
| `LLAMA_SERVER_TIMEOUT_SECONDS` | Connection timeout клиента |
| `LLAMA_SERVER_CONTEXT_WINDOW` | Запрошенный/резервный профиль; эффективное окно сверяется с runtime/server |
| `LOCAL_EMBED_ENABLED`, `LOCAL_EMBED_BASE_URL`, `LOCAL_EMBED_MODEL`, `LOCAL_EMBED_API_KEY` | Отдельный локальный embedding endpoint |
| `LOCAL_EMBED_TIMEOUT_SECONDS` | Таймаут embedding-запросов |

Default main/embedding URLs в клиенте — `http://192.168.88.15:8000/v1` и
`http://192.168.88.15:8001/v1`. Это defaults исходников, а не результат проверки
работающего сервера. Chat generation после соединения не имеет обычного HTTP
read deadline; Stop закрывает cancellable response. Транспортные ошибки и
фактическое окно контекста остаются отдельными состояниями.

Значения рабочей машины хранятся в `backend/.env.local` или окружении процесса;
ключи не копируются в документацию. Пример `local-model` не подтверждает model
ID, а строка-заглушка не подтверждает авторизацию. При подключении проверяются
фактические endpoint, credential, model ID и минимальный запрос.

## Остальные LAN-клиенты

| Контур | Владелец и конфигурация |
| --- | --- |
| Web/SearXNG и reranker | `skills/web-research/`; `SEARXNG_URL`, `LOCAL_RERANK_*` читаются реализацией навыка |
| OCR | `backend/app/infrastructure/llm/vision_ocr.py` и OCR-навык; `OCR_URL` |
| Vision | [vision skill](../skills/vision/SKILL.md); смотреть параметры установленного клиента |
| TTS/STT интерфейса | [voice/runtime.py](../backend/app/application/voice/runtime.py); `ELIRA_TTS_URL`, `ELIRA_STT_URL` |

Поиск и Home Assistant на той же машине — отдельные интеграции, а не перенос
agent loop на сервер. Настройки модели и доступность инструментов не дают
разрешения на внешнее действие. Конфигурация изменяемого навыка может отличаться
от заводской версии; проверяется используемая `data/skills/`.

## Проверка подключения

[scripts/smoke_agent_endpoints.py](../scripts/smoke_agent_endpoints.py) читает
те же backend env-файлы, проверяет LLM/embedding HTTP-контракты и, если chat
probe не отключён его параметрами, действительно делает bounded completion:

```powershell
backend\.venv\Scripts\python.exe scripts\smoke_agent_endpoints.py --help
backend\.venv\Scripts\python.exe scripts\smoke_agent_endpoints.py
```

Это не проверка всех портов OCR/Voice/Web. Текущий smoke ожидает embedding
размерности 1024; несовпадение требует разбора клиентского контракта/модели,
а не объявления сервиса исправным по HTTP 200. Состояние других сервисов
проверяется их собственным клиентом и инструкциями серверного репозитория.

Текущую модель и режим подтверждают server properties/health и конфигурация
реального процесса. Успешный endpoint probe не подтверждает качество задач,
Web-relevance, полный UI-путь или установленный релиз Elira.

## История

Прежние таблицы оборудования, портов, моделей и SSH aliases сохранены
[в снимке](archive/contracts-20261018/SERVER.md). Измерения от 26.09.2026 находятся
[в инвентаризации](archive/research/MODEL_INVENTORY_2026-09-26_RU.md). Они сохраняют
свои даты и не объявляются текущим deployment proof. Секреты и key material
остаются вне обоих репозиториев.
