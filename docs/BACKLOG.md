# Продолжение работ

Актуальный необязательный backlog после завершения Workflow/runtime-рефакторинга
и live-eval внутренней маршрутизации. Это не список незаконченных частей текущей архитектуры:
основной агентный контур уже реализован и проверен. Новую работу начинать только
по явной задаче и без создания второго agent loop, executor, registry или DB
abstraction.

## 2. Качество агента и eval

**Продолжено 2026-08-24 по двум реальным Workflow-прогонам:** добавлен
Harness-replay существующих `.agent/runs/*` без повторного обращения к модели.
Исправлены ложный `ok=true` у неуспешного `run_server(stop)`, разрастание
TaskSpec до 142 критериев на нумерованном заголовке «КРИТЕРИЙ ГОТОВНОСТИ» и
потеря cache telemetry в durable journal. Планировщик теперь может выбрать
capability-группы до первого execution-turn: это не запускает tools, но не даёт
поздней смене schemas сбросить prompt cache на уже большом контексте.

1. **Завершено 2026-08-24:** live Harness содержит постоянные opt-in сценарии
   Project RAG, web Corpus, Stop/cancel, background job, PDF/DOCX, SSH,
   Python/TypeScript/Rust LSP, Microsoft Docs/Serena MCP, vault restore и
   Workflow Resume. Scripted adapter отвечает через публичный Workflow API на
   `input`, `secret`, `elevation` и `approval`. Plaintext secret и поддельный
   UAC accept запрещены; elevation accept остаётся за native Tauri bridge.
2. Добавить больше неоднозначных Elira/Auto-запросов: сеть + код, медицина +
   документ, бизнес + web, короткие follow-up и смена темы внутри чата.
3. После каждой замены модели выполнять live-eval и сравнивать domain/tool/MCP
   accuracy, лишние tool calls, TTFT, duration, tokens/sec и prompt-cache hits.

## 3. Library, RAG и Corpus

4. **Завершено 2026-08-26:** Library принимает файлы прямо из topbar и явным
   сохранением durable-вложения чата, хранит полный извлечённый текст с
   физическим пределом 1 000 000 символов и показывает состояния индекса и
   использования. В prompt по-прежнему попадают только до 10 релевантных
   фрагментов по 2500 символов; весь извлечённый текст агент читает повторяемыми
   страницами через `library_search → library_read(next_offset)`. FTS5 находится
   в той же `library.db`; при отсутствии совпадений обычный чат не получает
   Library-контекст, а fallback на свежие файлы работает только при явном
   запросе к Library/вложению/документу.
5. **Завершено 2026-08-24:** Project Corpus индексирует один проект, monorepo
   или родительскую папку с несколькими Git-репозиториями в существующий
   `rag_memory.db`. Git-native `ls-files --exclude-standard` применяет вложенные
   `.gitignore`; manifest хранит SHA-256/mtime/status, поэтому повторный запуск
   пропускает неизменённое, продолжает неуспешные/непоместившиеся файлы и удаляет
   устаревшие chunks. Каждый chunk содержит repo/file/commit/language, а общий
   scoped-поиск получает bounded lexical shortlist по всему corpus сверх
   основного embedding candidate window. Лимит 5000 chunks относится к одному
   проходу: повторная команда продолжает с первого незавершённого файла.
6. **Завершено 2026-08-24:** детерминированный memory Harness проверяет
   remember/search/list/delete, дедупликацию, замену исправленного факта,
   классификацию и age-prune `volatile_fact`, зашифрованный backup/restore
   `smart_memory.db` + `rag_memory.db` и строгое разделение user memory,
   project-scoped RAG и run-scoped web Corpus. Harness не вызывает LLM/сеть и
   работает только во временном `ELIRA_DATA_DIR`.

**Live acceptance 2026-08-24 на основной Qwen и отдельном `ELIRA_DATA_DIR`:**
подтверждены `library_add`, автоматический релевантный Library context без tools,
Auto → `Личный` → `remember`, замена факта через `replaces_id`, последующий
поиск только актуального значения, полная индексация canary-проекта и Workflow
recall точного файла/chunk без `read_file`, shell и web. Полная трасса сохранена
в `.agent/evals/library-rag-memory-live/`.

6а. **Завершено 2026-08-24:** Project Corpus ingestion/status доступны через
    `runtime_control` (`project_index`, `project_status`) и по умолчанию
    используют подключённый проект. `memory_recall` приводит project path к
    тому же `scope:<sha256>`, что индексатор. Live Workflow acceptance прошёл
    status → index → recall точного canary-файла за три tool call.

Project Corpus и web Corpus — разные контуры. Текущий web Corpus работает как
run-scoped кэш:
`web_fetch(store=true) → web_corpus.sqlite3 → web_query`. Он не является
массовым индексатором локальных папок.

## 4. Background jobs

7. **Завершено 2026-08-24:** `run_server(kind="job")` сохраняет machine-local
   `starting`-запись до Popen, PID creation identity, log path и независимые
   launch/result sidecars. Production startup восстанавливает handles без
   второго executor; PID reuse не принимается за старый job. Raw-команда живёт
   только в коротком spec, journal/API получают redacted-вариант; повреждённый
   journal не перезаписывается, а карантинируется. После рестарта доступны
   прежние логи и явные `running/completed/failed/cancelled`, exit code и
   Workflow Stop/`stop` дерева. Terminal records ограничены числом и TTL; в
   portable backup не входят. Live acceptance на основной Qwen подтверждает
   Auto→Инженерный, `running→completed`, а также реальный backend restart при
   живом Windows job: тот же PID/log восстановлен с `recovered=true` и exit 0.
8. Добавить компактное представление активных/завершённых jobs в Workflow UI,
   если transcript/artifacts окажется недостаточно.

`run_server(kind="job")` запускает конечные фоновые команды, возвращает PID,
восстанавливает logs/status/exit code после backend restart и останавливает
дерево через tool или Workflow Stop.

## 5. IT Ops и сеть

9. Добавлять typed adapters только после повторяющихся реальных задач:
   ICMP, UDP, IPv6, SNMP, PCAP, NetFlow/sFlow/IPFIX и полноценное DHCP-управление.
10. При необходимости интегрировать vulnerability scanner: Nmap/NSE плюс CVE
    correlation либо отдельный Greenbone/OpenVAS runtime с evidence/remediation.
11. Возможные adapters: managed switches, UniFi, pfSense/OPNsense, Cisco,
    Proxmox, Docker/Kubernetes и NAS/storage.

Старые IT Ops Phase 0–20 под `.scratch/it-operations/` — историческая
декомпозиция, не активный порядок реализации. Новый adapter принимается только
после трёх повторяющихся gaps в реальных задачах.

## 6. MCP, LSP и интеграции

12. **Завершено 2026-08-27, кроме внешней конфигурации DBHub:** кроме Context7 проверены Microsoft Docs,
    Serena, GitHub, Hugging Face, Playwright, Paper Search и Home Assistant.
    Последние пять прошли read-only `start → tool → restart → tool → stop`.
    Unity с открытым editor прочитал реальную Console без изменений проекта;
    Blender с открытым editor подтвердил одинаковые status/scene до и после restart.
    DBHub transport и SQL прошли на изолированной demo SQLite, но рабочий
    `dbhub.toml` пока содержит два literal placeholder `ХОСТ`. `mcp_start`
    теперь возвращает count и до 50 точных namespaced tool names без schemas.
    Осталось только повторить DBHub после реальных DSN.
    Большие MCP теперь раскрывают релевантный task-scoped поднабор schemas;
    `mcp_tools(server_id, query)` меняет его без рестарта и без permission-блока.
    Постоянные внешние сценарии opt-in. **MikroTik завершён 2026-08-26:**
    MikroMCP удалён; durable assets используют typed SSH для RouterOS 6/7,
    автоматический version/health probe, minimal per-target RouterOS 6 crypto
    compatibility и fixed read-only inventory с evidence. Live hEX S 6.49.19
    acceptance прошёл через зарегистрированный RSA key.
13. **Завершено 2026-08-24 для текущих transport contracts:** subprocess-live
    fake servers проверяют start error/hang, crash, request timeout/unblock,
    restart и process-tree stop. Workflow Stop/Resume live-eval закрыл race
    `tool_started → Stop → поздняя регистрация Popen`.
14. **Завершено 2026-08-24:** реальные Pyright, typescript-language-server и
    rust-analyzer прошли diagnostics/definition/references, TypeScript restart и
    очистку process tree. Исправлены server-side configuration requests,
    Windows file URI и пустой warm-up diagnostics push.

## 7. Сервер и производительность

15. **Завершено 2026-08-24:** Workflow telemetry/UI/eval сохраняют серверные
    `cached_tokens`, cache hit ratio, prompt/output tok/s и model TTFT. На основной
    Qwen3.8 cold/warm probe подтвердил `0% → 99,80%` cache hit и сокращение TTFT
    `5,30 с → 0,27 с`; `cache_prompt=true` отправляется каждым chat request.
16. **Закрыто решением 2026-08-24:** отдельный синтетический 128K agent-eval не
    продолжается. Основная Qwen3.8 остаётся на проверенном профиле 131072/MTP3;
    profile limits менять только при проблеме в реальной Workflow-нагрузке.
17. Сравнить `none/low/medium/xhigh` по качеству, reasoning tokens, TTFT и общей
    длительности.
18. Reverse proxy/TLS добавлять только перед выходом за доверенную LAN или
    multi-user режимом. Bearer auth для non-loopback API уже реализован.

## 8. Windows, переносимость и release

19. Провести живой fresh-install drill в чистой Windows/VM: restore portable
    vault, resolution прежних `secret_ref`, Workflow elevation/UAC и выполнение
    административной команды.
20. Проверить legacy WinCred migration на реальных записях, если они ещё есть.
21. Перед публичным release собрать Tauri installer и вручную проверить install,
    update, uninstall, восстановление данных, backend startup, системные ссылки,
    download/preview, UAC и Stop.

## 9. Необязательные возможности

22. Пользовательский клонируемый голос рассматривать как отдельную замену TTS-
    сервиса и обучать только на согласованном датасете; текущий Silero v4_ru не
    поддерживает добавление такого голоса через модельный файл.
23. Рассматривать XTTS/StyleTTS2 только при выделенном GPU-бюджете.
24. Cloud profiles оставлять выключенными до отдельного явного решения
    пользователя.

## Рекомендуемый порядок

```text
неоднозначные Auto eval после замены модели
  → DBHub после появления реальных credentials/DSN
  → IT Ops adapters по фактическим gaps
  → fresh-install/release drill
```
