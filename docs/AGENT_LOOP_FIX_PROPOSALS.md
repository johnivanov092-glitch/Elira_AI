# Предложения по исправлениям — агентский цикл code-agent

**Дата:** 2026-06-10
**Статус: ✅ ВЫПОЛНЕНО 2026-06-11** (решение пользователя: миграцию на AI-server
он делает сам параллельно, agent-loop шаги — не ждать). Ветка
`claude/agent-loop-quality`, каждый пункт — отдельный коммит с focused-тестами:
F6 `5e3ce4d` · F5 `ceab923` · F2 `be47af2` · F3 `471507f` · F4 `5f3371c` ·
F1 `a5d8a8d`+`5af38a5` · F7 `0a4a112` · F8 `b81c51f`. Сводка статусов —
в [`POST_SERVER_BACKLOG.md`](POST_SERVER_BACKLOG.md). Документ ниже сохранён
как спецификация реализованного.
**Источник находок:** [`notes/2026-06-10_chat-review-agent-loop.md`](notes/2026-06-10_chat-review-agent-loop.md)
**База:** ветка `codex/deep-code-agent-compress`, HEAD `0d28ffa`.

**Post-server gate:** начинать выполнение только после того, как:
- AI-server стабильно отвечает через vLLM/OpenAI-compatible API;
- Elira подключена к серверному provider без регресса локального code-agent контура;
- выбран минимум один рабочий coder/general model profile;
- есть свежий focused smoke-test code-agent на новой модели.

**Гейты для каждого шага (стандарт проекта):**
`cd frontend && npx tsc --noEmit` → 0 ошибок; `backend\.venv\Scripts\python.exe -m pytest -q` → зелёно;
коммит → Opus-ревью → PASS.

**Правила:** не создавать второй executor / второй реестр / новую общую БД; семантику
policy/approval-гейтов executor не ослаблять; заглушек и мёртвого кода не оставлять.

---

## P1 — блокер и крупные дефекты качества

### F1. Approval-петля: пауза цикла + кнопки Approve/Reject в UI

**Проблема.** `write_file`/`edit_file`/не-readonly `run_bash` требуют approval, но цикл
не приостанавливается (модель получает текст и завершает ход), одобрение привязано к
`run_id` (следующий ход = новый run_id → не матчится), а во фронтенде нет UI approvals.
Базовый сценарий «создай/поправь файл» штатно не проходит.

**Решение — 3 части (2 коммита backend + 1 frontend):**

**F1.1 Backend — пауза/поллинг в `stream_code_agent`** (`application/code_agent/agent_loop.py`):
при `_exec_result.status == "waiting_approval"`:

1. `yield {"type": "approval_pending", "step", "tool", "arguments", "approval_id"}`
   (approval_id уже есть в `output`, в событие сейчас не попадает).
2. Wait-цикл с тиком ~1.5–2с: `monitoring.runtime.get_approval(approval_id)`.
   Выходы:
   - `approved` → повторный `_kernel_exec` с тем же `ToolExecutionRequest`
     (executor найдёт approved-запись, пометит `used`, задиспатчит) → дальше обычная
     обработка результата (yield `tool_call`, append tool-message);
   - `rejected` → `tool_call` с result «отклонено пользователем» + tool-message модели
     «user rejected this action; do not retry, adjust or finish»;
   - `cancel_event` → `done(cancelled)` (проверять каждый тик);
   - бюджет ожидания исчерпан (`approval_wait_seconds`, default = TTL 300с) →
     tool-message «approval not granted in time», цикл продолжается (модель завершит ход).
3. **Ожидание человека не тратит бюджет агента:** `deadline += фактически_прожданное`.
4. Каждые ~10с ожидания — `yield {"type": "approval_wait", "waited_s": n}` (SSE-keepalive;
   фронт неизвестные типы игнорирует — совместимо).

Параметр `approval_wait_seconds: int` прокинуть через `CodeAgentStreamRequest`
(default 300). Для легаси `POST /run` — default **0** (старое поведение, тесты не
зависают).

**F1.2 Frontend** (`CodeAgentChatShell.tsx`, `api/codeAgent.ts`): тип события
`approval_pending`; карточка «⏸ Требуется подтверждение: write_file(...)» с кнопками
**Разрешить** / **Отклонить** → `POST /api/agent-os/approvals/{id}/approve|reject`
(маршруты уже существуют в `agent_monitor_routes.py`). После клика карточка переходит в
«ожидание результата» — итог придёт обычным событием `tool_call`.

**F1.3 Текст для модели** при невыдаче approval (`agent_kernel/executor.py`): сейчас
«Approve at /api/agent-os/approvals/{id}/approve» — адресован человеку. Заменить на
модельно-ориентированный: «Действие ожидает подтверждения пользователя. Не повторяй
вызов; дождись итога или сообщи пользователю.» (после F1.1 этот текст достигает модели
только при wait-timeout/reject).

**Отвергнутая альтернатива:** привязка approval к сессии вместо `run_id` — ослабляет
модель безопасности P9 (`4264a27`); пауза/поллинг решает задачу, не трогая binding.

**Acceptance:** задача «создай файл X» → событие `approval_pending` в стриме; Approve в
UI → файл создан **в том же прогоне**, нормальный финальный ответ; Reject → агент
корректно завершает ход; семантика executor/policy не изменена; тесты: pause/resume с
fake-store и fake-`chat_fn`, продление deadline на время ожидания, wait-timeout, cancel
во время ожидания.

**Объём:** ~80–120 строк backend + тесты; ~100 строк frontend.
**Коммиты:** `feat(code-agent): pause loop on waiting_approval + approval_pending event`;
`feat(ui): inline approve/reject for code-agent tool calls`.

---

### F2. Финальный итог при обрыве по max_steps / deadline

**Проблема.** При `max_steps`/дедлайне прогон умирает `done(ok=false)` без
`final_response`: файлы на диске уже изменены, а «что успел / что осталось» пользователь
не получает.

**Решение** (`agent_loop.py`): перед терминальным `done` по этим двум причинам — один
**wrap-up LLM-вызов без tools**: system-добавка «Лимит шагов/времени исчерпан. Кратко:
что сделано (файлы, команды, результаты), что не доделано, следующий шаг.» →
`yield final_response` → `done(ok=False, stop_reason=...)`. При ошибке wrap-up-вызова —
детерминированный fallback из журнала вызовов цикла («Выполнено N шагов: write_file(a.py)
ok; run_bash(pytest) exit 1; ...» — цикл уже располагает tool/args/ok).

Заодно честная таксономия: `stop_reason="timeout"` для дедлайна вместо `"error"`
(фронт показывает stop_reason строкой — совместимо).

**Acceptance:** тест с fake-`chat_fn`: `max_steps=1` + tool_call → непустой
`final_response` и `done(stop_reason="max_steps")`; аналогично для deadline; happy-path
не делает лишний вызов; fallback срабатывает при падении wrap-up.

**Объём:** ~60 строк + тесты.
**Коммит:** `feat(code-agent): wrap-up summary on max_steps/timeout + honest stop_reason`.

---

### F3. Глубокая mid-run компакция + направление обрезки rolling summary

**Проблема.** (1) `maybe_compact` → `summarize_history` → `_coerce_history` выбрасывает
tool-сообщения и assistant с пустым content — mid-run summary строится почти из ничего,
агент забывает прочитанное; deterministic-fallback глубже основного пути. (2)
`_merge_summary`+`_cap_text` держат **первые** 4000 символов — rolling summary
«окаменевает» на старейшем контенте, новое отрезается.

**Решение** (`context/compaction.py` + `agent_loop.py`; `maybe_compact` вызывается
только из agent_loop — проверено):

- **F3.1** Новый `_flatten_for_summary(msgs)` в agent_loop: user → как есть;
  assistant с tool_calls → текст + `[tools called] name(краткие args); ...`;
  `role="tool"` → `[tool result <name>] <первые ~400 символов>`. В `maybe_compact`
  добавить опциональный `prepare_messages: Callable | None` (default None — поведение
  не меняется); loop передаёт flatten. Клиентский эндпоинт `/summarize-history`
  не затрагивается.
- **F3.2** `SUMMARIZE_SYSTEM_PROMPT`: пункт «Не пиши: результаты tool calls» заменить на
  «Сохрани кратко действия агента: какие файлы создал/правил, какие команды запускал и
  их итог (ok/exit-код). Не вставляй полные выводы инструментов и содержимое файлов.»
- **F3.3** `_merge_summary`: при превышении капа вытеснять **старейшие** части целыми
  блоками (накапливать с хвоста, маркер `[older summaries dropped]`); свежий блок не
  резать. `_cap_text` для одиночного summary оставить.

**Acceptance:** юнит: вход суммаризатора при mid-run компакции содержит имена
инструментов и фрагменты результатов; merge сверх капа сохраняет новейший блок целиком и
маркер; существующие тесты компакции зелёные.

**Объём:** ~70 строк + тесты.
**Коммит:** `fix(context): deep mid-run compaction input + evict oldest in rolling summary`.

---

### F4. Системный промпт ↔ фактический активный набор инструментов (P10.1)

**Проблема.** Промпт рекламирует `web_search`/`web_fetch`/`sandbox_run`/`sandbox_reset`
и правилами 9–10 предписывает их использовать, но базовый deferred-набор их не активирует
(вызов блокируется «activate via tool_search first»). Обратно: `todo_update`/`delegate_task`
активны, но в промпте не описаны.

**Решение** (`agent_loop.py`): словарь `TOOL_PROMPT_LINES` (имя → строка описания на
русском) для всех builtin-инструментов; `_build_base_system_prompt(project_root,
active_tools)` собирает секцию «Твои инструменты» **только из активных** + явный блок:
«Если нужной возможности нет в списке (веб-поиск, sandbox, http, sql, ssh) — вызови
`tool_search("ключевые слова")`: подходящие инструменты активируются на следующем шаге.»
Правила 9–10 переформулировать через активацию («для свежих фактов: активируй веб через
tool_search → web_search → web_fetch»). Добавить описания `todo_update` (чеклист прогона)
и `delegate_task` (ограниченный read-only субагент). Compat-константу
`BASE_SYSTEM_PROMPT` собрать из дефолтного базового набора.

**Acceptance:** тест: промпт дефолтного прогона не упоминает `web_search` как прямо
доступный, содержит tool_search-инструкцию и описания todo_update/delegate_task; прогон
с кастомным `base_tools` отражает переданный набор.

**Объём:** ~60 строк + тест.
**Коммит:** `fix(code-agent): generate tool prompt from active deferred set`.

---

## P2 — устойчивость и память между ходами

### F5. Ограничить call-expression fallback в `_extract_inline_tool_calls`

**Проблема.** Regex `tool_name(...)` ловит «псевдо-вызовы» в обычном тексте: финальный
ответ «я запустил run_bash(command=\"pytest\") и всё зелёное» парсится как новый вызов →
повторное выполнение вместо завершения.

**Решение** (`agent_loop.py`): fallback применять построчно (после снятия код-фенсов) и
принимать только «чистые» строки-вызовы: `^\s*name\(args\)\s*;?\s*$`. Строка с прозой
вокруг вызова — не вызов. JSON-путь не менять.

**Acceptance:** юниты: проза с упоминанием вызова → 0; вызов отдельной строкой → 1;
вызов в ```bash-фенсе → 1; несколько чистых строк → все.

**Объём:** ~25 строк + тесты.
**Коммит:** `fix(code-agent): inline call fallback only for pure call lines`.

---

### F6. `[tools used]` в raw-историю между ходами (frontend)

**Проблема.** `buildHistoryPayload` шлёт только тексты; после хода с финалом «Готово.»
агент на следующем ходе не знает, что делал. Сжатая история (после `0d28ffa`)
информативнее несжатой — инверсия.

**Решение** (`CodeAgentChatShell.tsx`): общий helper `agentTurnHistoryContent(t)` —
`[tools used] <до 12 вызовов через summarizeToolCall, суммарно ≤600 символов>` +
`\n\n` + текст хода; использовать и в `compressHistory`, и в `buildHistoryPayload`.
Сервер примет (assistant, непустой content — `_coerce_history` пропускает).

**Acceptance:** tsc зелёный; payload второго хода содержит `[tools used]`
(фронтовых юнитов нет — канонический гейт tsc + ручная проверка).

**Объём:** ~30 строк.
**Коммит:** `feat(ui): include [tools used] in raw conversation history`.

---

## P3 — опционально (мелочи UX и роутер чата)

### F7. Мелочи UX
- `context_compacted`: счётчик в AgentTurn + бейдж «контекст сжат» (фронт сейчас
  молча игнорирует событие).
- Упомянуть маркер `[PRIOR SUMMARY]` в `SUMMARIZE_SYSTEM_PROMPT`
  («строки [PRIOR SUMMARY] — прежние сжатия, интегрируй их факты»).

### F8. Точечные правки planner_v2
- Image-гейт: `route=image` только при отсутствии code/python-сигналов:
  `if scores["image"] > 0 and scores["python"] == 0 and scores["code"] < 2`
  («нарисуй график в matplotlib» → code, «нарисуй кота» → image).
- `_CODE_WORDS` += `("найди баг", 3)`, `("найди ошибку", 3)`, `("найди и исправь", 3)`
  («найди баг в функции» перестаёт уходить в research/веб).

**Acceptance:** юниты роутера на оба примера.
**Коммит:** `fix(chat): planner image guard + code-bug keywords`.

---

## Порядок и зависимости

```
F1 (блокер) → F2 → F3 → F4 → F5 → F6 → [F7, F8]
```

Шаги независимы по коду, кроме: F1.3 (текст executor) логичнее делать после F1.1;
F3.2 (prompt суммаризатора) идёт в одном коммите с F3.1.

## Риски

- **F1:** дольше живущие SSE-прогоны (ожидание человека). Смягчение: тик проверяет
  `cancel_event`; `approval_wait_seconds=0` возвращает старое поведение; для `/run`
  default 0.
- **F2:** wrap-up-вызов после исчерпания дедлайна добавляет до одного LLM-вызова
  времени; он вне бюджета инструментов и единственный. При падении — детерминированный
  fallback.
- **F3:** рост объёма входа суммаризатора (tool-результаты) — капы per-message 4000 /
  transcript 30000 в `summarize_history` уже защищают.
- **F5:** модели, которые пишут вызов в прозе одной строкой, потеряют recovery —
  по наблюдениям (qwen2.5-coder) вызовы эмитятся отдельными строками/фенсами; покрыть
  юнитами.
