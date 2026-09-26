# Инвентаризация моделей Elira — 2026-09-26

Снимок получен 26 сентября 2026 года около 16:51–17:05 UTC+05:00. Проверка читала действующие HTTP endpoints, Docker, systemd, процессы и известные каталоги моделей. Инференс, загрузка моделей, остановка сервисов и изменения конфигурации не выполнялись.

Область проверки: клиент Elira, сервер `192.168.88.15`, известные HF-кэши и каталоги моделей Elira/игровых инструментов. Это не полный поиск по всем дискам пользователя. «Установлена», «настроена», «сервис запущен» и «веса загружены» ниже разделены.

## Оборудование и текущая нагрузка

| Узел | CPU | GPU | Состояние при проверке |
|---|---|---|---|
| Клиент Windows | Ryzen 7 5700X, 8 физических ядер / 16 потоков | GeForce RTX 4060 Ti, 8188 MiB | GPU: 975 MiB занято, 7%; локальных процессов Whisper/llama/Ollama или Elira backend не обнаружено |
| Сервер Ubuntu | Ryzen 9 5900X, 12 физических ядер / 24 потока | Radeon AI Pro R9700, 32 GB | VRAM: 29 698 707 456 / 34 208 743 424 байт; оперативная память: около 25 GiB available из 31 GiB |

На серверном GPU `rocm-smi` показал только два KFD-процесса: основной Qwen, PID 2949, 28 623 876 096 байт VRAM; MiniCPM, PID 2943, 1 007 697 920 байт. Одно измерение GPU use показало 100%, последующий Docker stats показал 0% CPU у обеих служб; устойчивую загрузку GPU этим снимком установить нельзя. Vulkan-процессы могут не отражаться в списке KFD.

## Сервер: настроенные сервисы и установленные веса

| Назначение | Модель | Состояние | Вычисления / ограничения | Endpoint |
|---|---|---|---|---|
| Основной LLM, включая fast/code/strong | Qwen3.8-27B-Q6_K, alias `local-model` | Запущен, веса подтверждены `/props` и GPU PID | GPU, все слои; context 131072; MTP3; один слот; Docker CPU/RAM quota не задана | `http://192.168.88.15:8000/v1` |
| Embeddings / RAG | Qwen3-Embedding-0.6B-Q8_0, alias `local-embed` | Запущен, healthy, `/props` подтверждает модель | CPU; context 4096; 8 threads + 8 batch threads; Docker quota 8 CPU, 4 GiB RAM; GPU devices отсутствуют | `http://192.168.88.15:8001/v1` |
| Vision | MiniCPM-V-4.6-Q5_K_M + F16 projector | Запущен, healthy, модель и GPU PID подтверждены | Языковая часть GPU; projector CPU (`--no-mmproj-offload`); context 8192; один слот | `http://192.168.88.15:8004/v1` |
| STT | Faster Whisper large-v3 / int8 | Сервис запущен; веса установлены; `/health` подтверждает конфигурацию. Текущая загрузка весов в RAM не подтверждена | CPU, faster-whisper 1.2.1; Docker CPU/RAM quota не задана; загрузка модели ленивая | `http://192.168.88.15:8006/stt` |
| TTS | Silero v5_5_ru; голос kseniya | `silero-tts` active/running, PID 1243810; `/health` подтверждает engine и голоса | CPU; по исходнику deployment 4 Torch threads, 24 kHz; systemd CPU/RAM quota отсутствует | `http://192.168.88.15:8005/tts` |
| OCR | PP-OCRv5_server_det + PP-OCRv5_server_rec + cyrillic_PP-OCRv5_mobile_rec | Сервис healthy; журналы подтверждают загрузку всех трёх моделей | CPU; Docker quota 8 CPU / 12 GiB; OMP/MKL threads 8; concurrency 1 | `http://192.168.88.15:8002/ocr` |
| OCR PDF fallback | Tesseract: eng, rus, osd | Файлы traineddata установлены, fallback заявлен `/health` | CPU, вызывается по необходимости | Тот же OCR endpoint |
| Генерация 3D | TRELLIS.cpp v0.6.0 и комплект моделей | `elira-3d` запущен, healthy; веса на диске. Активная генерация и резидентные веса не обнаружены | Vulkan GPU 0; `--require-gpu`; threads 12; Docker quota 16 CPU / 24 GiB; resolution 1024 | Только серверный loopback `127.0.0.1:8007` |
| Генерация движений | Kimodo SOMA RP v1.1 F32 | Веса установлены; `game-kimodo` stopped, exit 143, около 13 дней | При запуске Vulkan; Docker quota 12 CPU / 24 GiB | Активного endpoint нет |

CPU quota `8` означает вычислительный бюджет восьми логических CPU, а не закрепление за восемью физическими ядрами: `CpusetCpus` у перечисленных контейнеров пуст. Основной и vision runtime: `elira/llama.cpp:server-rocm10.0.0-v0.4.1`; embeddings: `elira/llama.cpp:server-cpu-v0.4.1`.

STT после последнего старта показал около 60 MiB памяти, а код загружает `WhisperModel` только на первом `/stt`. Поэтому работающий `/health` не считается доказательством уже загруженной large-v3. Пробная транскрибация для этой инвентаризации не запускалась. TRELLIS показал около 7.3 MiB памяти и только HTTP server process; работающий контейнер тоже не означает загруженный генератор.

TTS занимает около 734 MiB в systemd. Его рабочий каталог `/home/claude` недоступен учётной записи `aiadmin`, поэтому прямое чтение действующего файла весов не выполнялось. Версия проверена через live API; путь и число threads ниже получены из локального deployment-кода.

## Сервер: пути и дополнительные установленные модели

| Модель / файл | Подтверждённый путь | Размер файла, байт |
|---|---|---:|
| Qwen3.8-27B-Q6_K | `/models/qwen3.8-27b-gguf/Qwen3.8-27B-Q6_K.gguf` | 22 884 408 288 |
| Qwen3 Embedding 0.6B Q8 | `/models/qwen3-embedding-0.6b-gguf/Qwen3-Embedding-0.6B-Q8_0.gguf` | 639 150 592 |
| MiniCPM-V 4.6 Q5 | `/models/minicpm-v-4.6-gguf/MiniCPM-V-4.6-Q5_K_M.gguf` | 577 802 944 |
| Vision projector | `/models/minicpm-v-4.6-gguf/mmproj-model-f16.gguf` | 1 108 746 944 |
| Whisper large-v3 | `/home/aiadmin/elira-ai-server/docker/stt/models/models--Systran--faster-whisper-large-v3/` | model blob: 3 087 284 237 |
| Whisper small, не выбран | `/home/aiadmin/elira-ai-server/docker/stt/models/models--Systran--faster-whisper-small/` | model blob: 483 546 902 |
| Kimodo SOMA RP v1.1 F32 | `/home/aiadmin/game-art/kimodo/sources/LocalAI-io__Kimodo-SOMA-RP-v1.1-GGML/models/kimodo-soma-rp-v1.1-f32.gguf` | 1 133 166 784 |

Silero deployment-код указывает `/home/claude/.cache/silero/v5_5_ru.pt`; прямой доступ к этому пути не подтверждён. PaddleOCR хранит модели в Docker volume `docker_ocr-model-cache`, внутри `/var/lib/elira-ocr/.paddlex/official_models/`. Файлы параметров: detector 87 932 887 байт, server recognizer 84 390 117, cyrillic recognizer 7 972 691.

Комплект TRELLIS находится в `/home/aiadmin/elira-ai-server/data/trellis/models/`:

| Файл | Размер, байт |
|---|---:|
| `ss_flow.gguf` | 2 586 488 480 |
| `ss_dec.gguf` | 147 379 392 |
| `shape_flow_512.gguf` | 2 586 636 032 |
| `shape_flow_1024.gguf` | 2 586 636 032 |
| `shape_dec.gguf` | 948 745 344 |
| `tex_flow_512.gguf` | 2 586 734 336 |
| `tex_flow_1024.gguf` | 2 586 734 336 |
| `tex_dec.gguf` | 948 713 888 |
| `dinov3.gguf` | 606 773 440 |
| `birefnet.gguf` | 882 749 024 |

В `/models/muse-glimmer-30b-gguf/` и `/models/gemma-4-26b-a4b-it-qat-gguf/` остались README, но весов в проверенном дереве `/models` нет. В `/home/aiadmin/.cache/huggingface/hub/` имеются каталоги Qwen3.6, Qwen3.6 MTP, Gemma4 31B и Muse Glimmer, однако файлов больше 10 MiB в проверенной глубине 4 не найдено. Названия этих каталогов не доказывают наличие установленных моделей.

## Клиент Windows

| Модель | Установка / расположение | Текущее использование |
|---|---|---|
| Faster Whisper large-v3 | `C:/Users/Root/.cache/huggingface/hub/models--Systran--faster-whisper-large-v3/`; model blob 3 087 284 237 байт; snapshot `edaa852ec7e145841d8ffdb056a99866b5f0a478` | Установлена; ссылка snapshot на веса цела; процессов inference нет |
| Та же Faster Whisper large-v3, второй кэш | `D:/AIWork/Elira_AI/models/huggingface/hub/models--Systran--faster-whisper-large-v3/`; тот же snapshot и размер | Установлена; ссылка цела; отдельный каталог. Физическое дублирование блоков диска не проверялось |
| Faster Whisper small | `C:/Users/Root/.cache/huggingface/hub/models--Systran--faster-whisper-small/`; 483 546 902 байт; snapshot `536b0662742c02347bc0e980a01041f333bce120` | Установлена, ссылка цела, не запущена |
| Piper ru_RU irina medium | `C:/Users/Root/.cache/huggingface/hub/datasets--rhasspy--piper-checkpoints/`; `epoch=4139-step=929464.ckpt`, blob 845 898 392 байт | Кэш training checkpoint; работающий TTS inference и готовый ONNX не подтверждены |
| Qwen3 Embedding 0.6B Q8 | `D:/AIWork/Elira_AI_Server/models/qwen3-embedding-0.6b-gguf/Qwen3-Embedding-0.6B-Q8_0.gguf`, 639 150 592 байт | Локальная копия; локальный inference не запущен |
| Qwen HF cache metadata | `C:/Users/Root/.cache/huggingface/hub/models--Qwen--Qwen3-Embedding-0.6B-GGUF/` | Найден refs/main; наличие весов в этом конкретном кэше не подтверждено |
| Laya | До начала работы отсутствовала; эксперимент установлен на сервере и затем удалён | Не используется; контейнер, веса и рабочие папки эксперимента удалены 26 сентября |

`backend/.env.local` направляет LLM, embeddings, vision, OCR, UI STT и UI TTS на сервер. `HF_HOME` указывает `D:/AIWork/Elira_AI/models/huggingface`; `ELIRA_LOCAL_STT_CACHE_DIR` отдельно указывает пользовательский кэш `C:/Users/Root/.cache/huggingface/hub`. Наличие весов Whisper на клиенте проверено; исправность CUDA/библиотек и реальная скорость транскрибации в этом исследовании не тестировались.

В прочитанной через SQLite `mode=ro` таблице `data/agent_monitor.db:model_profiles` активны три роли `fast/code/strong`, все с одной моделью `local-model`, и `embedding=local-embed`. Облачный профиль `claude-sonnet-4-5`, context 200000, отключён. Других model_profiles нет. Это профиль, не установленная локальная модель. Отдельных neural reranker и локального vision runtime в проверенных конфигурациях не обнаружено.

## Образы и вспомогательные службы

Установленные дополнительные Docker-образы inference: `server-rocm10.0.0-v0.4.1-mtpgraph`, `server-rocm10.0.0-v0.4.1-fa64`, `build-rocm10.0.0-v0.4.1-optbase-20260920`, `rocm/dev-ubuntu-24.04:10.0.0-full`; старые STT `elira/stt:faster-whisper1.0.3-rollback-20260920` и `elira-stt-stt:latest`. Контейнеры на этих дополнительных образах не обнаружены. Образы runtime не равны отдельным моделям; Docker layers могут быть общими, их показанные размеры нельзя складывать как фактический расход диска.

Также запущены SearXNG, Home Assistant и hass-mcp. Это сервисы поиска/интеграций, собственные LLM-веса для них в проверке не обнаружены. Никакие модели, кэши или образы в рамках инвентаризации не удалялись.

## Laya и значение «внутренний prefill»

По [официальной карточке Laya](https://huggingface.co/convaiinnovations/laya), это модель классификации и типизированных решений, а не embeddings. Для русского языка и лимита 8192 нужен checkpoint `multilingual` (322M); корневой английский checkpoint имеет другое назначение и меньший контекст. CPU поддерживается. Заявленные результаты автора не заменяют измерения на запросах Elira; автор отдельно описывает слабость zero-shot typed decisions и калибровки.

В коде Elira нет самостоятельного модуля «prefill», который можно заменить embeddings-моделью. Найдены разные механизмы:

- До изменения `backend/app/application/code_agent/capabilities.py:route_request_capabilities` и `backend/app/application/chat/local_chat.py:classify_domain_policies` выполняли детерминированную предварительную классификацию задачи. Семантический классификатор удалён; основной Qwen теперь сам разбирает запрос и выбирает инструменты. Детерминированные контракты выдачи файлов и расчёта BOM сохранены.
- `backend/app/application/code_agent/agent_loop.py`, блок `Structured planning preflight` — отдельный генеративный вызов основного LLM для плана при `thinking` и `task_spec`. Laya не генерирует план и не заменяет эту функцию напрямую.
- Prefill/KV cache — стадия обработки prompt в llama.cpp, не отдельная модель в Elira.
- `backend/app/infrastructure/llm/openai_compatible.py:embed_text` — embeddings endpoint с контрактом 1024 измерения; `backend/app/application/rag_memory/service.py` и `runtime.py` — SQLite RAG и гибридное ранжирование. Laya не является совместимой заменой этого векторного пространства.

Эксперимент Laya проведён на шести физических ядрах сервера с CPU-only Torch и контекстом 8192. Итог — 9/16 маршрутов, а задача в конце длинного запроса пропущена. По решению пользователя Laya исключена и удалена; embeddings и основной Qwen не менялись. Измерения и подтверждение очистки — в [отчёте Laya](LAYA_CPU_EVALUATION_RU.md).

## Источники и расхождения документации

Текущие данные получены из SSH `ai-server-codex`: `docker ps -a`, выбранных полей `docker inspect`, `docker stats --no-stream`, `docker image ls`, `systemctl show silero-tts`, `lscpu`, `rocm-smi`, `find` в известных каталогах и журналов OCR/STT/TRELLIS. HTTP проверки: `/props` на 8000/8001/8004 и `/health` на 8002/8005/8006. Клиент: `nvidia-smi`, `Win32_Processor`, `Win32_Process`, проверка файлов/symlink и чтение SQLite в read-only режиме.

`docs/SERVER.md` устарел в части Silero v4_ru; фактически API сообщает v5_5_ru. `D:/AIWork/Elira_AI_Server/Server/ACCESS.md` содержит исторические runtime b10452 и GPU projector, тогда как текущий Docker показывает v0.4.1 и CPU projector. Более свежие `README.md` и `docs/CURRENT_SERVER_STATE.md` соседнего server-репозитория точнее, но их исторические секции тоже нельзя выдавать за сегодняшнее состояние. Эти файлы в рамках инвентаризации не изменялись.
