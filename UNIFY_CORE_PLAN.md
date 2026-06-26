# UNIFY_CORE_PLAN — одно универсальное ядро chat\code-агента

> **Статус:** пошаговый план реализации. Не начинать без явной команды.
> Опус (ревьюер) проверяет каждый gate. Исполнитель идёт строго по шагам.
> **Откат:** `git revert` соответствующего коммита (каждый этап — отдельный коммит).

---

## 0. Цель и принцип («железная логика»)

Сейчас в приложении **три параллельных пути чата**:

| Путь | Эндпоинт | Движок |
|------|----------|--------|
| **A — болтовня** | `/api/chat/stream` + `direct_llm=true` | `run_chat_stream` |
| **B — чат+планнер** | `/api/chat/stream` + `direct_llm=false` | `run_agent_stream` (PlannerV2 + ~20 `use_*`) |
| **C — code-agent** | `/api/code-agent/stream` | `stream_code_agent` (tool-loop, общий реестр) |

`/api/chat-agent/*` — **НЕ мозг**, а CRUD-хранилище (чаты/сообщения/сессии/модели/настройки/память/проекты). Остаётся как есть.

**Решение:** оставить ОДНО ядро = **Path C** (code-agent). Оно и болтает, и зовёт тулзы. Path A и Path B **удаляются полностью** (не оборачиваются, не проксируются). Выбор «текст vs тул» делает **модель по системному промпту**, без классификатора и без флагов.

**Что НЕ трогаем:**
- `/api/chat-agent/*` CRUD, `tool_registry` каркас, `agent_kernel.executor` (политика/scope/approval), `BuiltinToolProvider`.
- Серверный кап контекста, profile-предпочтение сервера, аппаратные капы (`get_safe_ctx`, `MODEL_SAFE_CTX`).
- Boundary-тесты (`test_chat_agent_boundary.py`), persona-тест (`test_persona_prompt_size.py` ≤700), фикстуры `:Nb`/`:latest`.
- `Elira_AI_Server` (отдельный репо), ротации ключей (TAVILY/Fernet).
- UI-вёрстка (заблокирована, `UI_BASELINE.md`) — только смена адресата запроса, не layout.
- `web_search`/`web_fetch` — уже нативные.

**Конвенции репо:** файлы UTF-8 **без BOM** (писать через Write tool / Python, НЕ `PS 5.1 -Encoding utf8`). `noUnusedLocals` enforced. APP-бэкенд: `backend\.venv\Scripts\python.exe -m pytest`, `PYTHONPATH=D:\AIWork\Elira_AI\backend`.

---

## 1. Факты, подтверждённые чтением кода (не догадки)

**Диспатч ядра:** `backend/app/application/code_agent/tools.py:959` — `build_tool_dispatch(project_root)` возвращает dict `{name: lambda **kw: tool_fn(project_root, **kw)}`.
**Схемы:** вынесены в `backend/app/application/code_agent/tool_schemas.py`, реэкспортятся из `tools.py:25` как `build_tool_schemas`.
**Обёртка исполнения:** `backend/app/application/tool_providers/builtin.py:50` `BuiltinToolProvider.dispatch` берёт `handler = self._dispatch_table.get(name); result = handler(**args)` и нормализует к `{"text": ...}`.
**Реестр (ToolSpec):** `backend/app/application/tool_registry/builtins.py:388-451` — табличные блоки `auto_tools`/`auto_side_effect_tools`/`approval_tools`, словарь `_native_scopes`, хендлер `_noop` (реальное исполнение — в провайдере). ToolSpec нужен, чтобы тулза была **видна `tool_search`**.
**tool_search:** `tools.py:622` зовёт `search_tool_specs`; `tools.py:633` авто-активирует только `activatable and not side_effect`; `activate_tools` — no-op, если run не в deferred-режиме ⇒ **болтовня НЕ форсит поиск тулзов** (модель сразу даёт `final_response`).

**Состояние 11 light-тулз (ВАЖНО — не все одинаковы):**

| Тулза | dispatch (`tools.py`) | schema (`tool_schemas.py`) | ToolSpec (`builtins.py`) | Реализация |
|-------|:---:|:---:|:---:|------------|
| `file_gen` | ✅ `tool_file_gen:919` | ✅ `:328` | ❌ | `skills` → `generate_word`/`generate_excel` |
| `translator` | ❌ | ❌ | ❌ | `skills_extra/runtime.py:translate_text:252` |
| `regex` | ❌ | ❌ | ❌ | `skills_extra/runtime.py:test_regex:216` |
| `csv` | ❌ | ❌ | ❌ | `skills_extra/runtime.py:analyze_csv:270` |
| `http_api` | ❌ | ❌ | ❌ | `skills/runtime.py:http_request:208` |
| `sql` | ❌ | ❌ | ❌ | `skills/runtime.py:run_sql:141` (+`list_databases`/`describe_db`) |
| `encrypt` | ❌ | ❌ | ❌ | `skills_extra/runtime.py:encrypt_text:50`/`decrypt_text:58` |
| `archiver` | ❌ | ❌ | ❌ | `skills_extra/runtime.py:create_zip:73`/`extract_zip:99` |
| `converter` | ❌ | ❌ | ❌ | `skills_extra/runtime.py:convert_file:118` |
| `webhook` | ❌ | ❌ | ❌ | `skills_extra/runtime.py:store_webhook:327`/`list_webhooks`/`clear_webhooks` |
| `screenshot` | ❌ | ❌ | ❌ | `skills/runtime.py:screenshot_url:249` |

⇒ **`image_gen`/`file_gen`: нужен ТОЛЬКО ToolSpec** (dispatch+schema уже есть, но они невидимы `tool_search`).
⇒ **Остальные 10: полный порт** (wrapper в `tools.py` + schema + dispatch + ToolSpec).

**Чистота реализаций (подтверждено grep):** `skills/runtime.py` и `skills_extra/runtime.py` НЕ импортируют `app.application.chat.*` ⇒ переиспользуются как есть; удаление chat-пути (этап 4) их не ломает.

**Шаблон wrapper'а** (копировать стиль `tool_image_gen`, `tools.py:880`):
```python
def tool_translator(project_root: Path, *, text: str, target_lang: str = "english") -> dict[str, Any]:
    from app.application.skills_extra.runtime import translate_text
    result = translate_text(text, target_lang=target_lang)
    if not result.get("ok"):
        return {"text": f"ERROR: {result.get('error') or 'unknown error'}"}
    return {"text": result.get("translation") or result.get("text") or str(result)}
```
(`project_root` принимать всегда для единообразия dispatch-сигнатуры, даже если не используется.)

**Фронт (Stage 3 seam):**
- `frontend/src/api/chat.ts:407` — `fetch("/api/chat/stream")`; `use_file_gen` (`:335`), `use_image_gen` (`:338`) и др. `use_*` в теле запроса.
- `frontend/src/api/codeAgent.ts:185` — `/api/code-agent/stream` (целевой эндпоинт).
- `frontend/src/workspace/backgroundRuns.ts:298` — «Чат»-режим cancel → переключить на code-agent cancel.

---

## 2. ЭТАП 1 — Light-тулзы → нативные тулзы ядра

**2.1. `image_gen` + `file_gen` — добавить только ToolSpec.**
В `tool_registry/builtins.py` (блок `388-451`):
- `image_gen` → в `approval_tools` (пишет файл): `("image_gen", "Image Gen", "media", "Generate an image from a text prompt", 120, 5000, False)`; в `_native_scopes`: `"image_gen": ["fs.write"]`.
- `file_gen` → в `approval_tools`: `("file_gen", "File Gen", "media", "Generate a Word/Excel file", 60, 5000, False)`; `_native_scopes`: `"file_gen": ["fs.write"]`.

**2.2. Остальные 10 — полный порт.** Для каждой:
1. **Wrapper** в `tools.py` (см. шаблон), реюз runtime-функции из таблицы §1.
2. **Schema** в `tool_schemas.py` (по образцу существующих `image_gen`/`file_gen` записей).
3. **dispatch** — строка в `build_tool_dispatch` (`tools.py:960`).
4. **ToolSpec** в `builtins.py` + scope.

Классификация по таблицам реестра:
- **`auto_tools`** (детерминированные, без записи/сети, `permission=auto`, scope без сетевых): `translator`, `regex`, `csv`, `converter`.
  - scopes: `translator`/`regex` → `[]`; `csv` → `["fs.read"]`; `converter` → `["fs.read","fs.write"]`.
- **`approval_tools`** (side-effect, `require_approval`):
  - `http_api` → `["net.outbound"]`
  - `webhook` → `["net.outbound"]` (или `[]` если store локальный — см. `store_webhook`, скорее локальная запись → `["fs.write"]`; проверить при реализации)
  - `sql` → `["fs.read","fs.write"]`
  - `encrypt` → `[]` (Fernet локально; но side-effect-семантика → approval)
  - `archiver` → `["fs.read","fs.write"]`
  - `screenshot` → `["net.outbound","fs.write"]`

> Точные scope для `webhook`/`encrypt` уточнить по телу runtime-функции на реализации; правило: пишет файл → `fs.write`, ходит в сеть → `net.outbound`, иначе `[]`.

**2.3. Зависимость.** По умолчанию runtime-функции реюзаются как есть (импорты чистые). Если при реализации обнаружится импорт из `app.application.chat.*` — вынести функцию в нейтральный модуль (`skills_extra`), НЕ тащить chat в ядро.

**Файлы:** `code_agent/tools.py`, `code_agent/tool_schemas.py`, `tool_registry/builtins.py`. Реюз: `skills/runtime.py`, `skills_extra/runtime.py`, `skills/__init__.py`.

### Gate 1 (Опус проверяет)
- [ ] Все 11 тулз: `tool_search("<имя>")` находит и помечает `[eligible]`/`[side_effect]` (не `[blocked]`).
- [ ] `execute_tool(name, args, run_id=...)` возвращает осмысленный dict на безопасном входе (translator/regex/csv/converter — детерминированы; image/file — temp; http/webhook — локальный/моковый URL).
- [ ] `backend\.venv\Scripts\python.exe -m pytest` — зелёный (505+).
- [ ] Импорт-смоук: `python -c "import app.application.code_agent.agent_loop"` без ошибок.
- [ ] grep: нет нового импорта `app.application.chat` в `skills*`/`media`/`code_agent`.

---

## 3. ЭТАП 2 — Единый системный промпт ядра (модель решает текст vs тул)

**3.1.** Сейчас персона (`persona/service.py:build_persona_prompt:54`) применяется только в Path B. Ядро использует «человечный/планирующий» BASE_SYSTEM_PROMPT (`code_agent/prompts.py`, уже смягчён — НЕ переделывать).
**Задача:** слить в ОДИН промпт ядра:
- core-identity Elira + тёплый тон (короткая вставка из `build_persona_prompt`),
- tool-aware-инструкция: «простой разговор → отвечай текстом без тулзов; задача (читать/искать/создать/перевести/выполнить) → `tool_search` затем вызов нужной тулзы»,
- сохранить anti-injection rule 13 и no-refusal-ядро.
Расширить `_build_base_system_prompt` в `prompts.py` персона-вставкой, **уважая бюджет** (persona-тест ≤700 симв.).

**3.2. Никакого классификатора.** Решение «тул vs текст» — целиком на модели через промпт. Подтверждено (§1): deferred-режим не форсит `tool_search` для болтовни.

**3.3. Паритет фич.** Сверить, что ядро уже несёт: вложения (attachments), метрику usage, отмену (`request_cancel`). Чего нет — дотянуть в ядре (`code_agent_routes.py`), НЕ возвращать chat-путь.

**Файлы:** `code_agent/prompts.py`, `persona/service.py` (реюз), `code_agent/agent_loop.py` (conversational-ветка), `api/routes/code_agent_routes.py` (паритет attachments/usage).

### Gate 2 (Опус проверяет)
- [ ] `test_persona_prompt_size.py` — ≤700, core identity present.
- [ ] Промпт строится; маркеры персоны/«человечности»/rule13 на месте (юнит на наличие подстрок).
- [ ] Ручной прогон: «привет, как дела» → текст без тулзов; «прочитай файл X» / «переведи …» / «сгенерируй картинку …» → корректный tool-call.
- [ ] `pytest` зелёный.

---

## 4. ЭТАП 3 — Перенаправить чат-UI на ядро

**4.1.** `frontend/src/api/chat.ts`: вместо `fetch("/api/chat/stream")` (`:407`) → стримить с `/api/code-agent/stream` (реюз `codeAgent.ts:185`). Убрать `direct_llm` и все `use_*` (`:335`, `:338`, и пр.) из тела запроса.
**4.2.** SSE ядра (`run_started`/`step_started`/`tool_call`/`final_response`/`done`) маппить в текущую модель событий чат-UI (тонкий маппер, без правки вёрстки).
**4.3.** `backgroundRuns.ts:298` — cancel «Чат» → `/api/code-agent/cancel`.
**4.4.** CRUD без изменений — `/api/chat-agent/*`. UI-вёрстка без правок.

**Файлы:** `frontend/src/api/chat.ts`, `agent.ts`, `codeAgent.ts` (реюз), `workspace/backgroundRuns.ts`, тонкий SSE-маппер в чат-компоненте.

### Gate 3 (Опус проверяет)
- [ ] `cd frontend && npx tsc --noEmit` → EXIT 0.
- [ ] Ребилд bundle + рестарт (полный Tauri-ребилд НЕ нужен — `tauri.conf` не трогаем).
- [ ] E2E руками: (а) болтовня — тёплый тон, без тулзов; (б) «найди/прочитай/сделай» — реальный tool-call с апрувом; (в) Stop реально отменяет; (г) история/персона сохраняются; (д) light-тулза (перевод/картинка) работает из чата.

---

## 5. ЭТАП 4 — Полностью выпилить старый путь

**Только после зелёных Gate 1–3. Отдельный коммит.**

**5.1. Backend — удалить:**
- `run_agent_stream`(_impl), `run_chat_stream`, поле `direct_llm` и блок `use_*` в `ChatRequest`.
- PlannerV2-роутинг и `PlannerV2Service` (проверить `/classify`, freshness_gate; если нигде больше не используется — удалить целиком, иначе только роутинг).
- Осиротевшие чат-хелперы: `entrypoint_stream.py`, `entrypoint_sync.py`, `post_processing.py`, `auto_skills.py`, `planner_v2.py`.

**5.2. Роуты — удалить** (после этапа 3 фронт их не зовёт): `/api/chat/stream`, `/cancel`, `/classify`, `/keywords` в `api/routes/chat.py`.

**5.3. Мёртвый код:** убрать осиротевшие импорты/флаги/персона-инъекцию старого пути (логику персоны сохранили в §3, реализации тулз — в §2).

**5.4. Тесты:** удалить/переписать завязанные на `direct_llm`/PlannerV2; сохранить boundary/кап/persona.

**Файлы:** `api/routes/chat.py`, `application/chat/entrypoint_stream.py`/`entrypoint_sync.py`/`planner_v2.py`/`auto_skills.py`/`post_processing.py`, затронутые тесты.

### Gate 4 (финальный, Опус проверяет)
- [ ] `pytest` зелёный (кап/boundary/persona проходят).
- [ ] `npx tsc --noEmit` EXIT 0.
- [ ] grep: нет живых ссылок на `direct_llm` / `run_agent_stream` / `PlannerV2` в backend и frontend.
- [ ] Полный E2E ещё раз: один чат и болтает, и зовёт тулзы; ничего не сломано.
- [ ] Импорт-смоук всех затронутых модулей.

---

## 6. Порядок, коммиты, откат

1. Этап 1 → Gate 1 → коммит.
2. Этап 2 → Gate 2 → коммит.
3. Этап 3 → Gate 3 → коммит.
4. Этап 4 → Gate 4 → коммит.

Каждый этап — самостоятельный коммит; регрессия → `git revert` этого коммита. До этапа 4 старый путь физически на месте (мгновенный откат). После этапа 4 — одно ядро, без слоёв совместимости.

## 7. Out of scope
- Не пушим/не коммитим без явной команды (включая текущую несохранённую `prompts.py`-правку и `e981d45`).
- Не трогаем серверный/аппаратный кап, profile-предпочтение сервера, boundary-тесты, `Elira_AI_Server`, ротации ключей, фикстуры `:Nb`/`:latest`.
- Никаких прокси/слоёв совместимости на `/api/chat/*` — старое удаляется (этап 4).
- Визуальный редизайн UI (заблокирован) — только смена адресата запроса.
