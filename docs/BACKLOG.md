# Продолжение работ

Актуальный необязательный backlog после завершения Workflow/runtime-рефакторинга
и live-eval профилей. Это не список незаконченных частей текущей архитектуры:
основной агентный контур уже реализован и проверен. Новую работу начинать только
по явной задаче и без создания второго agent loop, executor, registry или DB
abstraction.

## 2. Качество агента и eval

1. Расширить `backend/tests/smokes/routing_cases.json` сценариями подключённой
   папки проекта, project RAG, web Corpus, Stop/cancel, background job,
   PDF/DOCX download, SSH, LSP, elevation, vault restore и Workflow Resume.
2. Добавить больше неоднозначных Auto-запросов: сеть + код, медицина + документ,
   бизнес + web, короткие follow-up и смена темы внутри чата.
3. После каждой замены модели выполнять live-eval и сравнивать profile/tool/MCP
   accuracy, лишние tool calls, TTFT, duration, tokens/sec и prompt-cache hits.

## 3. Library, RAG и Corpus

4. Заменить отбор только по свежести на релевантный выбор Library-файлов и
   chunks. Сейчас в prompt попадают до 10 самых свежих активных файлов, до 2500
   символов каждый; UI честно показывает, какие из них находятся в контексте.
5. Добавить project-corpus ingestion для больших локальных наборов репозиториев:
   рекурсивный обход, `.gitignore`/исключения, incremental hashes, chunks,
   embeddings, метаданные repo/file/commit/language, resumable indexing,
   удаление устаревших chunks и единый поиск между несколькими проектами.
6. Расширить memory eval: remember/search/list/delete, исправление факта,
   дедупликация, устаревшие volatile facts, backup/restore и строгое разделение
   user memory, project RAG и временного web Corpus.

Текущий web Corpus уже работает как run-scoped кэш:
`web_fetch(store=true) → web_corpus.sqlite3 → web_query`. Он не является
массовым индексатором локальных папок.

## 4. Background jobs

7. При реальной необходимости сделать job journal долговечным между рестартами
   backend: reconciliation PID, повторное подключение к логам и явные состояния
   `running/completed/failed/cancelled` после восстановления.
8. Добавить компактное представление активных/завершённых jobs в Workflow UI,
   если transcript/artifacts окажется недостаточно.

Текущий `run_server(kind="job")` уже запускает конечные фоновые команды,
возвращает PID, logs/status/exit code и останавливает дерево через tool или
Workflow Stop.

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

12. Добавить live-eval настроенных MCP помимо Context7: GitHub, Hugging Face,
    Microsoft Docs, Playwright, DBHub, MikroTik, Home Assistant, Serena, Paper
    Search, Unity и Blender — только когда соответствующий сервер доступен.
13. Проверить failure recovery: ошибка start, аварийный exit, зависший request,
    restart, Stop во время вызова и гарантированный stop после Workflow.
14. Провести live-eval реальных Python/TypeScript/Rust language servers для
    diagnostics/definition/references и очистки дочернего process tree.

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

22. Обучить пользовательский Piper voice только на согласованном датасете.
23. Рассматривать XTTS/StyleTTS2 только при выделенном GPU-бюджете.
24. Cloud profiles оставлять выключенными до отдельного явного решения
    пользователя.

## Рекомендуемый порядок

```text
расширение eval
  → релевантный Library/project Corpus
  → IT Ops adapters по фактическим gaps
  → fresh-install/release drill
```
