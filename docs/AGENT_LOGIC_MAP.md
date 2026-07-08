# Карта логики системы — code-agent runtime

Единая карта «правильной логики» прогона: целевые принципы → жизненный цикл → слои →
инварианты → дыры → план достройки → операционный контур. Verifier-слой детализирован в
[VERIFIER_COVERAGE_CATALOG.md](VERIFIER_COVERAGE_CATALOG.md) (каталог = контракт); эта карта —
зонтичный документ над всем прогоном. Все ссылки на код проверены по живому дереву
(инвентаризация 2026-07-08, HEAD `ab0933f`).

---

## 0. Принципы (почему логика именно такая)

1. **Runtime владеет истиной, не модель.** Completion решают verifier-вердикты
   (`CriteriaTracker`), финальный статус-блок генерирует runtime; слово модели не закрывает
   ничего и вычищается из финала, если противоречит статусу.
2. **Позитив-evidence, не денилисты.** Верификатор подтверждает только когда доказано именно
   то, что нужно (target + атрибуция: `interacted`, `_run_invokes`, width-bucket).
   Неизвестное по умолчанию НЕ проходит. Денилист «плохих случаев» незакрываем по построению —
   если ревью находит N-й edge-case одного класса, меняется дизайн (сужается контракт), а не
   пополняется список.
3. **Заземление перед утверждением.** Established-facts digest в system, grounding-nudge на
   неназванные файлы, `/props` как источник правды о контексте сервера. Модель не «врёт» —
   она отражает свой источник; чинится источник.
4. **Честная деградация.** `partial`/`unverified`/`failed` всегда лучше ложного ✅.
   Conditional-критерии скипаются (не failing), пассивные пробы нейтральны при несовпадении,
   blocked-вызов не пишет вердикт.
5. **Редирект вместо блока.** Гарды перенаправляют на правильное семейство действий
   (strategy-router, server→browser, cleanup-barrier), а не убивают ран. Стоп — только
   честный и с evidence.
6. **Детерминизм там, где runtime знает точно.** Closure-план вычисляется, а не угадывается;
   safe-конкретное подмножество runtime исполняет сам (auto-verifier pass) — подтверждение
   не зависит от того, догадается ли модель.
7. **Экономия.** Группировка closure-вызовов (одна команда закрывает N output-критов, один
   browser(actions) — всю форму), deferred-tools (база 15, остальное по активации),
   компактный readiness-UI.
8. **Всё ограничено.** Каждый гард/pass/nudge имеет cap (once-per-run, ≤N, timeout) и
   терминальное поведение. Ничто не может крутиться вечно.

---

## 1. Жизненный цикл прогона (state machine)

```
POST /api/code-agent/stream {message, project_root, run_id?, permission_mode}
        │
        ▼
┌─ SETUP ────────────────────────────────────────────────────────────────────┐
│ journal wrapper (events.jsonl+state.json, resume)  agent_loop.py:2288      │
│ route/model + num_ctx: правда с живого /props      agent_loop.py:586-595   │
│ sandbox preflight + deadline 600s (+человеческие паузы)   :597-629         │
│ tool registry (builtin+ssh+lsp+mcp) + deferred base=15    :643-686         │
│ ssh-интент → активация ssh_* с 1-го шага; persona posture :665-685         │
│ derive_task_spec → CriteriaTracker (или None = без TaskSpec-контракта)     │
│   continuation-сообщение восстанавливает TaskSpec из истории  :703-718     │
└────────────────────────────────────────────────────────────────────────────┘
        │  run_started
        ▼
┌─ ЦИКЛ ШАГА (≤200) ─────────────────────────────────────────────────────────┐
│ cancel? deadline? → честный терминал                                        │
│ compaction (num_ctx-бюджет) → LLM call (DRY-анти-повтор, thinking-канал,   │
│   heartbeat 10s) → reasoning-runaway budget (2/ран)                        │
│                                                                             │
│ НЕТ tool_calls (модель хочет финалить) ──────────────┐                     │
│ ЕСТЬ tool_calls:                                      │                     │
│   ├ cleanup-barrier: delete при открытых критах ПОД путём → редирект (1)   │
│   ├ server→browser redirect (2)   ├ exact-dup guard (6) / near-dup (5)     │
│   ├ ask_user (3+2 grace) / ssh_request_host (пауза, дедлайн возвращается)  │
│   ├ kernel exec: fail-closed гейты → permission/approval → hard-timeout    │
│   │   класс local 120s / shell 600s / network 900s → аудит                 │
│   ├ запись вердикта: _record_criterion_verdict (ЕДИНЫЙ для модели и auto)  │
│   └ strategy-router: прогресс? критерий флипнулся = сильнейший сигнал;     │
│       исчерпание семейства → редирект; всё исчерпано → честный стоп        │
└───────────────────────────────────────────────┬────────────────────────────┘
                                                │
        ┌───────────────────────────────────────▼─────────────────────────┐
        │ ФИНАЛИЗАЦИЯ-ПОПЫТКА (intent-gate, grounding-nudge, verify-gate) │
        │                                                                  │
        │ CLOSURE GATE (если криты открыты, ≤2 хода, 1/missing-set):       │
        │   1) AUTO-VERIFIER PASS (1/ран, ≤5 вызовов, safe-set):           │
        │      runtime САМ исполняет конкретные verifier-вызовы;           │
        │      всё green → сразу confirmed-финал БЕЗ closure-хода          │
        │   2) остаток → nudge с точными вызовами + report auto-pass'а     │
        │      (green «НЕ повторяй» / red «evidence…, исправь»)            │
        └───────────────────────────────────────┬─────────────────────────┘
                                                ▼
┌─ ФИНАЛИЗАЦИЯ (порядок жёсткий) ────────────────────────────────────────────┐
│ 1 strip markup → 2 finalize_conditionals → 3 completion_status             │
│ 4 gate_completion_claims + 5 scrub_success_marks  (только != confirmed)    │
│ 6 strip_model_status_sections → 7 scrub_manual_criteria_counts (всегда)    │
│ 8 + runtime_final_report («Готовность задачи — по verifier»)               │
│ 9 proactivity → 10 grounding digests → 11 mood → 12 auto-remember          │
│ 13 done{completion_status, criteria[], partial, stop_reason, facts…}       │
│ 14 журнал: run_completed, resumable-контракт                               │
└────────────────────────────────────────────────────────────────────────────┘
```

Полный перечень гардов (35 шт., в порядке срабатывания) с бюджетами — в инвентаризации;
ключевые capы: intent-gate 2, grounding 2, closure 2, auto-pass 1×5, ask_user 3+2,
exact-dup 6, near-dup 5 (Jaccard 0.6), reasoning-runaway 2, malformed-trace 3,
verify-gate 3×300s, server-redirect 2, cleanup-barrier 1, стратегия: 2 попытки/семейство,
2 семейства/таргет, 10 шагов без прогресса.

---

## 2. Карта слоёв

| Слой | Что делает | Код | Статус |
|---|---|---|---|
| **Derivation** | текст задачи → TaskSpec (goal/criteria/constraints); «Функциональность» → details, не criteria; continuation восстанавливает spec | `taskspec.py:89-178` | ✅ |
| **Классификация** | критерий → intent (16 интентов: report/file/content/dom+interaction/viewport/server/page/command_output/command_check/generic + conditional) | `taskspec.py:768-826` | ✅ |
| **Вердикты** | tool call → verdict (интент от ТУЛА, таргет из structured evidence); атрибуция: `interacted`, `_run_invokes` (позитив-evidence), width-bucket | `taskspec.py:884-1065` | ✅ |
| **Closure-план** | детерминированный список недостающих вызовов; группировка interaction/command; nudge; redirect; barrier | `criterion_closure.py:36-431` | ✅ |
| **Auto-pass** | runtime сам исполняет safe-конкретное подмножество (см. §3 каталога) | `criterion_closure.py:131-245` + `agent_loop.py:1306-1442` | ✅ live-проверен |
| **Финальный отчёт** | runtime-owned статус-блок; scrub модельных ✅/счётчиков/клеймов при !=confirmed | `criterion_closure.py:434-548` | ✅ |
| **Tools** | база 15 + deferred (canary!); verifier-тулы с evidence/meta; активация: tool_search / closure-gate / allowlists | `tool_policy.py`, `_meta.py`, `builtins.py` | ✅ |
| **Exec/безопасность** | fail-closed kernel: spec-гейты → permission (ask/accept/bypass; critical всегда ask) → hard-timeout по классам → аудит; shell-гарды (metachar, raw-ssh redirect, killable trees, secret-strip, SSRF) | `executor.py`, `_shell.py`, `_run.py` | ✅ |
| **I/O** | SSE-стрим (heartbeat), cancel/resume, done-контракт, журнал (redaction, capы), Layer C consistency | `code_agent_routes.py`, `run_journal.py` | ✅ |
| **UI-честность** | readiness-панель (evidence по клику, dedup), approval/question cards, honest stop, ledger | `AgentTurn.tsx`, `backgroundRuns.ts` | ◐ (нет бейджа auto_verifier) |
| **Каталог** | контракт verifier-покрытия + негативные правила + validation-тесты | `verifier_catalog.yaml`, `test_verifier_catalog.py` | ✅ (runtime его НЕ читает — осознанно) |

---

## 3. Инварианты (нарушать нельзя, тесты пинуют)

1. grep/findstr/read_file/glob — НЕ вердикт; `ssh_read` доказывает только существование.
2. HTTP 200 — только `page_open`; видимый текст даёт только реальный render (boundary-anchored).
3. Слово модели не закрывает ничего; финальный статус — только от runtime.
4. Existence — confirm-only (phase-blind): пассивная проба никогда не hard-fail'ит.
5. `command_output` — confirm-only + атрибуция (named-команда, не композит/не eval/не dump).
6. Kind/intent-mismatch не кросс-закрывает (typecheck ≠ build; desktop ≠ mobile).
7. Conditional никогда не hard-fail (→ skipped, вне мандаторного знаменателя); generic
   никогда не auto-confirm.
8. Auto-pass не может сфабриковать red: blocked/error не пишет вердикт, cd-инференс только
   там, где red нейтрален, ssh только 1-хост+remote-путь, пробы — строго по пути критерия.
9. Гард редиректит, не блокирует; каждый гард ограничен и имеет терминал.
10. **Эвристика допустима для guard/redirect** (worst case = плохой хинт, ограниченный
    таймаутом/капом), **запрещена для verdict** (там только позитив-evidence) — это
    различение снимает противоречие «денилисты выпилили, а dev-server-детекция — список».
11. База промпта не растёт бездумно — компакшн-канарейка (num_ctx=8192) обязана жить.

---

## 4. Дыры и риски (честный список)

| # | Дыра | Класс | Митигация сейчас |
|---|---|---|---|
| G1 | Runtime не читает YAML-каталог (классификация задвоена: код+док) | drift-риск | validation-тесты каталога пинуют `code:`-указатели |
| G2 | Prose-критерии («запуск с CSV выводит…») → generic | покрытие | задокументированный размен; named-формулировка даёт 8/8 (live-доказано) |
| G3 | ~~run_server-churn~~ — ✅ закрыто R2 (FORM ×8→×2; lifecycle у runtime) | — | остаток: гонка Popen-окна при disconnect (принята) |
| G4 | ~~UI не различает auto_verifier-вызовы~~ — ✅ закрыто R4 (чипы «⚙ runtime» на карточке и в readiness) | — | — |
| G5 | ~~Model-path blocked-вердикт~~ — ✅ закрыто R3 (`b1361f1`: запись только при status=="ok") | — | — |
| G6 | `cli.output.not_contains` — нет верификатора | покрытие | каталог помечает missing; критерий честно unverified |
| G7 | ~~Live-smoke не автоматизированы~~ — ✅ закрыто R5 (`tests/smokes/`, 4/4 PASS) | — | — |
| G8 | Interactions не исполняются runtime'ом (selectors неизвестны) | по дизайну | closure-nudge даёт точный grouped-вызов модели |
| G9 | `command_check` red от среды (не от кода) — честный, но шумный fail | UX | red rescuable (зелёный прогон позже подтверждает) |

---

## 5. План достройки (фазы, каждая отдельно шипуема)

**R1 — Каталог как источник классификации** *(отложенный «рискованный батч» — по решению
2026-07-07 делается ПОСЛЕ A-D; A-D закрыты → можно брать)*
- Runtime читает `verifier_catalog.yaml`: (а) sanity-check классификации (intent из кода ==
  ожидание каталога → метрика дрейфа), (б) label `unsupported/unverifiable` для критерия без
  верификатора — в readiness-панель вместо молчаливого unverified, (в) текст closure-хинтов
  из каталога.
- DoD: фиче-флаг; при выключенном флаге поведение бит-в-бит текущее; канарейки живы.

**R2 — Server Lifecycle (runtime владеет тем, что сам поднял) (G3) — ✅ DONE
(`3d464fe` + ревью-фиксы)**
Live-DoD выполнен: FORM 30→17 tools, 30→10 шагов, run_server ×8→×2, порт после прогона
закрыт. Ревью (20 находок, verify-фаза упала в лимит → ручной триаж, все реальные):
regex ужесточён (`vite build`/dev-build/--version/quoted-упоминания больше не редиректятся),
URL-очистка стала scoped (liveness-recheck — чужой фейл не стирает живой адрес), pre-bound
гейт (запрошенный порт, занятый ДО старта, не принимается — чужой listener не благословляется),
liveness пробит по хосту из URL (::1/LAN), stop не выкидывает незакиленный процесс из
трекинга, auto-pass не исполняет named dev-server команды, редиректнутый вызов не считается
верификацией, keep-флаг коммитится после доставки done. Остаточный риск: узкая гонка «регистрация
хэндла после finally» при client-disconnect ровно в окне Popen — принята и
задокументирована (единственный не закрытый кейс из 20 находок ревью).
Не «economy-хинты», а владение жизненным циклом: модель не владеет PID'ами.
- **Ownership:** run_server регистрирует pid/port/url с run_id; runtime знает live-список
  СВОИХ серверов в ране.
- **Liveness:** server→browser redirect и auto-pass browser-хинты только при реально живом
  сервере (PID жив + порт слушает); `_last_server_url` очищается на stop / stop_all /
  failed start / list-empty / dead-probe.
- **run_bash-редирект:** dev-server-команды (`npm run dev`/`vite`/`next dev`/`uvicorn`/
  `flask run`/`http.server`…) → редирект в run_server. Это ЭВРИСТИКА, и это допустимо:
  правило — эвристика разрешена для guard/redirect (worst case = плохой хинт, ограничен
  таймаутом), запрещена для verdict (false-confirm/fail).
- **Терминалы:** cancel/timeout/no_progress/error → runtime ВСЕГДА останавливает run-owned
  серверы. Answer-финал: с TaskSpec → останавливает (сервер был средством верификации;
  evidence уже записан); без TaskSpec (сервер = deliverable, «подними dev-сервер») →
  оставляет жить + ЯВНЫЙ отчёт (pid, url, как остановить). Комментарий "deliberately
  OUTLIVE" в _run.py заменяется этой политикой.
- DoD: FORM-smoke ≤ ~15 tool calls при 8/8; после smoke `run_server list` пуст и порт НЕ
  слушает; ран «подними сервер» без TaskSpec оставляет сервер жив с отчётом.

**R3 — Model-path parity для blocked (G5) — ✅ DONE (`b1361f1`)**
- Вердикт пишется только при `status=="ok"` (единая семантика с auto-pass через общий
  helper); rejected approval / rate-limit не может пометить критерий failed. Регрессия:
  model-called `npm test` blocked → unconfirmed, не failed. Бэкенд 3602.

**R4 — UI: бейдж auto-verifier (G4) — ✅ DONE**
- Tool-карточка: чип «⚙ runtime» при `auto_verifier: true` (ToolCallGroup). Readiness-строка:
  «⚙ runtime» при `auto_verified` — новый флаг в `criteria.report()` (ставится в `record()`
  при auto=True, прокинут через `_record_criterion_verdict`). Модельные вызовы флага не несут
  (регрессии в обе стороны). Bundle пересобран.

**R5 — Автоматизированный live-smoke (G7) — ✅ DONE**
- `backend/tests/smokes/` (не собирается pytest'ом): `run.py` + `driver.py` + 4 таска +
  `baseline.json`. Одна команда: `python tests/smokes/run.py [--only cli,form] [--keep]`.
- Ассерты: stop_reason/completion/confirmed vs baseline, tool-budget, артефакты на диске,
  **порты закрыты после прогона**; SSH-canary скипается, если хост не в allowlist
  (прекондишн, не fail). Retry-политика: 1 повтор на любой фейл — детерминированная
  регрессия падает дважды (FAIL), стохастический флейк проходит как видимый FLAKY-PASS.
- **Первый же прогон окупил слой**: поймал live-баг классификации — «файл
  C:\AgentLab\smoke-canary.txt существует» уходил в command_check из-за «smoke» в ИМЕНИ
  файла (target-токен хайджекал intent; ssh_exists не мог подтвердить). Фикс: контекстные
  cue (cmd/server/open/viewport) читают прозу без path-токенов, кавычки сохранены
  («`npm test` проходит» живёт). Негативное правило добавлено в каталог + регрессии.
- Финальный прогон: **4/4 PASS attempt-1** — cli 8/8 (9 tools), csv_named 8/8 (19),
  form 8/8 (14, порт закрыт), ssh_canary 3/3 (7 tools, 34с; было 1/3 при 19 до фикса).

**R6 — `cli.output.not_contains` (G6)** *(маленький, по методу каталога)*
- Строка в каталоге → negative-вариант матчинга (token НЕ в выводе named-команды, exit-гейт)
  → тесты → support.

Порядок (утверждён 2026-07-08): **R3 → R2 → R5 → R4 → R6 → R1.** Главная боль по
live-прогонам — server lifecycle, поэтому R2 сразу после дешёвого correctness-фикса R3;
R4 (бейдж) не чинит поведение — позже; R1 самый рискованный — последним, под флагом.

---

## 6. Операционный контур (как этим пользоваться)

**Гигиена критериев (главный рычаг качества прогона):**
- команды — **named в бэктиках**: `` `node check.js inventory.csv` выводит `OK: 1` ``
  (prose «запуск с CSV выводит…» честно останется unverified);
- негативный кейс — отдельной named-командой с ожидаемым токеном и «завершается с ошибкой»;
- UI-результаты — «browser interaction: … показывает `X`» (+ поля/кнопки в бэктиках);
- layout — «нет горизонтального скролла на mobile/desktop» (device-слово, не px);
- пути — как они должны существовать: `log-summarizer/index.js`, не «файл index.js».

**Чтение результата:** статус даёт только блок «Готовность задачи — по verifier» и
readiness-панель (evidence по клику). `partial` + список недостающего = что осталось
недоказанным, а не «провал». `skipped` = conditional, не считается.

**Метод внесения нового verifier:** строка в каталоге (criterion→tool→evidence→
does_not_close→support) → код → регрессия → обновить support. Не whack-a-mole по live-багам.
Если ревью находит edge-cases одного класса — менять дизайн, не пополнять списки.

**Ревью-контур:** существенное изменение слоя = 1 адверсариальный Workflow-раунд
(find→refute), находки чинить före мержа. «0 confirmed» при упавшей verify-фазе =
недопроверка, не чистота. Моки в тестах обязаны повторять реальную схему вывода тула.

**Деплой:** правки backend → рестарт uvicorn (`python -m uvicorn app.main:app --host
127.0.0.1 --port 8000` из `backend/`); правки frontend → ребилд bundle; tauri.conf → полный
Tauri-ребилд. Live-smoke драйвер: см. R5 (пока — scratchpad сессии 8949…, `run_smoke.py`).
