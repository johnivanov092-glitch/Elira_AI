# Elira AI — план зачистки и стабилизации

> Этот файл — единый источник задач для Sonnet.
> Каждая задача делается в новом чате, в отдельной ветке, с пушем в origin.
> После завершения задачи Opus (в другой сессии) ревьюит работу и сливает в main.

---

## Правила для исполнителя (Sonnet)

1. **Одна задача = одна сессия = одна ветка**. Имя ветки указано в брифе.
2. **Ветвись от свежего `main`** (`git checkout main && git pull && git checkout -b <branch>`).
3. **Не сливай в main сам**. Пушни ветку, проставь статус DONE в этом файле, остановись.
4. **Перед DONE — запусти acceptance-проверку из брифа и приложи реальный вывод**, не описание. Если падает — статус BLOCKED + причина.
5. **Не отключай `persona_evolution`**. Это явное требование пользователя. Если кажется что задача требует его трогать — STOP и спроси.
6. **Не лезь в задачи которые тебе не назначены** (даже если видишь "очевидную" соседнюю проблему). Логируй в раздел "Замечено по пути".
7. **Коммитить часто**, осмысленными чанками. Финальный коммит должен оставлять репо в зелёном состоянии.

---

## Контекст проекта (read-only)

- Локальный AI-воркспейс: FastAPI backend + React/Tauri frontend + Ollama (2B модель на RTX 4060).
- Цель: лёгкий быстрый агент, не enterprise-monolith.
- Сейчас: 439 .py файлов, ~40k LOC, 231 роут, 11 SQLite БД, старт ~25 сек.
- Корень проекта: `D:\AIWork\Elira_AI`.
- Backend venv: `backend\.venv\Scripts\python.exe`.

Архитектурные слои (post-refactor, всё ещё все живые):
- `backend/app/api/routes/` — FastAPI роуты
- `backend/app/application/` — бизнес-логика (~25k LOC)
- `backend/app/domain/` — domain-объекты (~4k LOC)
- `backend/app/infrastructure/` — I/O, БД, интеграции (~3k LOC)
- `backend/app/core/` — старый "общий" слой, частично жив
- `backend/app/services/` — **compat-шимы**, должны умереть (см. Task 3)

---

## Статус задач

| # | Задача | Ветка | Статус | Исполнитель | Ревью Opus |
|---|--------|-------|--------|-------------|------------|
| 1 | Lazy-load heavy ML imports | `cleanup/full-pass` | **DONE** | Opus 4.7 | self |
| 2 | Удалить мёртвые `elira_phase*` модули и роуты | `cleanup/full-pass` | **DONE** | Opus 4.7 | self |
| 3 | Удалить `app/services/` compat-слой | `cleanup/full-pass` | **DONE** | Opus 4.7 | self |
| 4 | Тяжёлые зависимости → optional | `cleanup/full-pass` | **DONE** | Opus 4.7 | self |
| 5 | Урезать persona prompt, сохранить evolution | `cleanup/full-pass` | **DONE** | Opus 4.7 | self |
| 6 | Реальный code-агент через Ollama function calling | `cleanup/full-pass` | **DONE** | Opus 4.7 | self |
| 7 | Удалить frontend `.js` дубликаты | `cleanup/full-pass` | **DONE** | Opus 4.7 | self |

**Итоги (2026-05-24):**
- Старт `from app.main import app`: **25 сек → 1.68 сек**
- Тесты: 2419 проходят, 1 pre-existing failure (`tavily` vs `duckduckgo`, не связан)
- Routes: 231 → 203 (−29 мёртвых + 1 новый `/api/code-agent/run`)
- `app/services/` удалён полностью (−49 файлов)
- `requirements.txt` урезан до ~50 MB core; тяжёлое ушло в три профиля `requirements-optional.txt`
- Persona prompt: ~2000 → 469 символов; `persona_evolution` нетронут
- Frontend: 11 `.js`/`.jsx` дубликатов удалены, `tsc --noEmit` + `vite build` зелёные
- Новый code-агент: 6 sandboxed-инструментов + agent loop через Ollama function calling, 13 unit-тестов

Зависимости:
- 1, 2, 3, 4, 5, 7 — независимы, можно параллелить.
- 6 — лучше после 3 (чище код), но не блокировано.

---

## Task 1 — Lazy-load heavy ML imports

**Ветка:** `cleanup/lazy-ml-imports`
**Goal:** Старт `from app.main import app` должен занимать **< 5 секунд** (сейчас 25).

**Why:** В [backend/app/application/memory/search.py:7](backend/app/application/memory/search.py) делается `from sentence_transformers import SentenceTransformer` на module-load. Это тянет цепочку `sentence_transformers → transformers → torch → sklearn → scipy → sympy` (~5.4 секунды + 3-4 GB RAM), **даже когда embedder ни разу не вызывается**. То же может быть в других местах.

**Файлы для проверки** (`grep` уже подтверждено что они импортят heavy):
- `backend/app/application/memory/search.py`
- `backend/app/infrastructure/db/memory.py`
- `backend/app/application/media/flux_schnell_runtime.py`
- `backend/app/application/media/image_generation.py`

**Что сделать:**
1. В каждом из этих файлов: все `import torch`, `from sentence_transformers ...`, `from diffusers ...`, `from transformers ...`, `import faiss` — перенести из module-scope **внутрь функций**, которые их реально используют.
2. Capability-check функции (типа `vector_memory_capability_status`) могут проверять наличие через `importlib.util.find_spec("sentence_transformers")` вместо реального импорта.
3. Прогнать `grep` ещё раз чтобы убедиться что других мест нет:
   ```
   grep -rn "^import torch\|^from torch\|^from sentence_transformers\|^from diffusers\|^from transformers" backend/app
   ```

**Что НЕ делать:**
- Не удаляй сами зависимости из requirements.txt (это Task 4).
- Не меняй публичные сигнатуры функций.

**Acceptance:**
```powershell
Push-Location backend
$t = Get-Date
& .\.venv\Scripts\python.exe -c "from app.main import app; print(len(app.routes))"
"Startup: $(((Get-Date)-$t).TotalSeconds) sec"
Pop-Location
```
Должно быть **< 5 секунд**. Приложи реальный вывод в комментарий PR.

Также:
```
backend\.venv\Scripts\python.exe -m unittest discover -s backend/tests -p "test_*.py" -v
```
Все тесты должны пройти.

**Branch & PR:**
```
git checkout main && git pull
git checkout -b cleanup/lazy-ml-imports
# ... work ...
git push -u origin cleanup/lazy-ml-imports
```
PR title: `perf: lazy-load heavy ML imports — startup 25s → <5s`

---

## Task 2 — Удалить мёртвые `elira_phase*` модули и роуты

**Ветка:** `cleanup/dead-phase-modules`
**Goal:** Убрать legacy-фазы которые висят в registry и application слое без реального использования.

**Why:** В коде остались модули с явно экспериментальными именами (`elira_phase19`, `elira_phase20_queue`, `elira_phase21`, `elira_supervisor`, `elira_task_runner`, `elira_devtools`, `elira_execute`, `elira_execution_controller/loop/state`, `elira_multi_file_loop`, `elira_preview_queue`, `elira_stabilization`). Все они активно регистрируются в [backend/app/api/routes/registry.py](backend/app/api/routes/registry.py). Это никогда не вычищенные итерации.

**Список для удаления** (модули):
```
backend/app/application/elira_phase19/
backend/app/application/elira_phase20/
backend/app/application/elira_phase20_queue/
backend/app/application/elira_phase20_state/
backend/app/application/elira_phase21/
backend/app/application/elira_supervisor/
backend/app/application/elira_task_runner/
backend/app/application/elira_devtools/
backend/app/application/elira_execute/
backend/app/application/elira_execution_controller/
backend/app/application/elira_execution_loop/
backend/app/application/elira_execution_state/
backend/app/application/elira_multi_file_loop/
backend/app/application/elira_preview_queue/
```

**Список роутов для удаления:**
```
backend/app/api/routes/elira_phase19.py
backend/app/api/routes/elira_phase20.py
backend/app/api/routes/elira_phase20_queue.py
backend/app/api/routes/elira_phase20_state.py
backend/app/api/routes/elira_phase21.py
backend/app/api/routes/elira_supervisor.py
backend/app/api/routes/elira_task_runner.py
backend/app/api/routes/elira_devtools.py
backend/app/api/routes/elira_execute.py
backend/app/api/routes/elira_execution_controller.py
backend/app/api/routes/elira_execution_loop.py
backend/app/api/routes/elira_execution_state.py
backend/app/api/routes/elira_multi_file_loop.py
backend/app/api/routes/elira_preview_queue.py
backend/app/api/routes/elira_stabilization.py
```

**Что сделать:**
1. **Сначала** `grep -rn` по каждому имени модуля во всём `backend/` и `frontend/` — если что-то их импортит снаружи списка выше, **STOP** и опиши находку в "Замечено по пути". Не удаляй вслепую.
2. Удалить файлы и директории из списков выше.
3. Убрать соответствующие импорты и записи из [backend/app/api/routes/registry.py](backend/app/api/routes/registry.py) `ALL_ROUTERS`.
4. Удалить соответствующие тесты в `backend/tests/` (если есть `test_elira_phase19.py` и т.п.).
5. Удалить ссылки во frontend если есть (`grep -rn "elira/phase\|elira-phase\|/phase19\|/phase20\|/phase21\|/supervisor\|/task-runner\|/devtools\|/execute" frontend/src`).
6. **Базы данных** (`data/*.db`) НЕ трогать — пользователь сам решит.

**Что НЕ делать:**
- Не удаляй `elira_execution_*` если grep покажет что они вызываются из живого кода (chat_service и т.п.).
- Не удаляй `elira_state.py` route — он используется фронтом.
- Не удаляй `elira_patch.py` route.

**Acceptance:**
```powershell
Push-Location backend
& .\.venv\Scripts\python.exe -c "from app.main import app; print('routes', len(app.routes))"
& .\.venv\Scripts\python.exe -m compileall app
& .\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
Pop-Location
```
- `len(app.routes)` должен упасть минимум на ~30.
- `compileall` без ошибок.
- Все оставшиеся тесты зелёные.
- Приложи `git diff --stat` в PR.

**Branch & PR:**
```
git checkout main && git pull
git checkout -b cleanup/dead-phase-modules
git push -u origin cleanup/dead-phase-modules
```
PR title: `chore: remove legacy elira_phase* modules and routes`

---

## Task 3 — Удалить `app/services/` compat-слой

**Ветка:** `cleanup/drop-services-shims`
**Goal:** Каталог `backend/app/services/` должен исчезнуть полностью.

**Why:** В [docs/AGENT_OS_WORKPLAN.md](docs/AGENT_OS_WORKPLAN.md) и в auto-memory утверждалось что `services/` удалён — это ложь. Он жив: 49 файлов, 852 строки. Внутри в основном compat-шимы вида `sys.modules[__name__] = _runtime`, но 4+ файла в проекте всё ещё импортят `from app.services.*` и заставляют слой жить.

**Что сделать:**
1. Найти всех живых потребителей:
   ```
   grep -rn "from app\.services\|import app\.services" backend frontend
   ```
   На момент аудита найдено 4 файла:
   - `backend/app/infrastructure/search/multisearch.py`
   - `backend/app/infrastructure/git/runtime.py`
   - `backend/app/application/media/flux_schnell_runtime.py`
   - `backend/app/application/chat/temporal_intent.py`

2. В каждом из них: открыть, посмотреть что именно импортится, найти реальное место в `app.application.*` или `app.infrastructure.*` (compat-шим сам говорит куда — там `from app.application.X import Y`), переписать импорт на прямой.

3. После того как `grep` выше возвращает 0 матчей — удалить весь каталог `backend/app/services/`.

4. Проверить тесты — некоторые могут импортить `from app.services.*` тоже:
   ```
   grep -rn "from app\.services\|import app\.services" backend/tests
   ```
   Переписать на прямые импорты.

**Что НЕ делать:**
- Не удаляй ничего из `app/application/` или `app/infrastructure/`. Только шимы в `app/services/`.

**Acceptance:**
```powershell
# Должно вернуть 0 строк:
grep -rn "from app\.services\|import app\.services" backend frontend

# Каталог не должен существовать:
Test-Path backend/app/services  # → False

# Тесты зелёные:
backend\.venv\Scripts\python.exe -m unittest discover -s backend/tests -p "test_*.py"
```

**Branch & PR:**
PR title: `refactor: remove app/services/ compat shim layer`

---

## Task 4 — Тяжёлые зависимости → optional

**Ветка:** `cleanup/deps-to-optional`
**Goal:** Базовый `pip install -r requirements.txt` должен ставить **< 200 MB**, не 4-6 GB.

**Why:** Сейчас [backend/requirements.txt](backend/requirements.txt) ставит по умолчанию: `torch>=2.5`, `diffusers`, `transformers`, `accelerate`, `sentencepiece`, `protobuf`, `pdfplumber`, `pandas`, `openpyxl`, `pytesseract`, `pdf2image`, `cryptography`. Большинство нужны только для image-gen и парсинга документов — это опциональные фичи.

**Что сделать:**

1. Из [backend/requirements.txt](backend/requirements.txt) **убрать** (перенести в optional):
   - `torch>=2.5`
   - `diffusers>=0.30,<0.32`
   - `transformers>=4.44,<5.0`
   - `accelerate>=0.33`
   - `sentencepiece>=0.2`
   - `protobuf>=4.25`
   - `pytesseract>=0.3.13`
   - `pdf2image>=1.17`
   - `pdfplumber>=0.11`
   - `pandas>=2.2`
   - `openpyxl>=3.1`
   - `scikit-learn>=1.5`

2. **Оставить в core:** `python-dotenv`, `fastapi`, `uvicorn[standard]`, `pydantic`, `ollama`, `httpx`, `python-multipart`, `aiofiles`, `ddgs`, `requests`, `beautifulsoup4`, `pypdf`, `python-docx`, `cryptography` (если используется для генерации `elira_secret.key` — проверь).

3. В [backend/requirements-optional.txt](backend/requirements-optional.txt) добавить три секции:
   ```
   # === Vector memory (semantic search) ===
   sentence-transformers>=3.0
   faiss-cpu>=1.8
   scikit-learn>=1.5

   # === Image generation (Stable Diffusion / FLUX) ===
   torch>=2.5
   diffusers>=0.30,<0.32
   transformers>=4.44,<5.0
   accelerate>=0.33
   sentencepiece>=0.2
   protobuf>=4.25

   # === Document parsing (Excel, PDF tables, OCR) ===
   pdfplumber>=0.11
   pandas>=2.2
   openpyxl>=3.1
   pytesseract>=0.3.13
   pdf2image>=1.17

   # === Browser screenshots ===
   playwright>=1.45
   ```

4. Все места в коде что используют эти модули должны **gracefully degradeить** (capability-status), а не падать на импорте. Часть этого делает Task 1 (lazy imports). Проверь что:
   - `from app.main import app` работает в venv где из optional не установлено НИЧЕГО.
   - `/health` отдаёт 200.
   - `/api/project-brain/status` показывает `available: false` для отсутствующих фич.

5. Обновить [README_Elira_AI.md](README_Elira_AI.md) раздел про зависимости: явно сказать что optional разбит на три профиля.

**Что НЕ делать:**
- Не удаляй capability-check код (он наоборот критичен сейчас).
- Не удаляй сами фичи image-gen / OCR / vector memory — они должны просто говорить "недоступно, установи такой-то профиль".

**Acceptance:**
```powershell
# Создать чистый venv:
python -m venv backend\.venv_test
backend\.venv_test\Scripts\pip install -r backend\requirements.txt

# Проверить размер:
Get-ChildItem backend\.venv_test -Recurse | Measure-Object -Property Length -Sum

# Импорт работает:
Push-Location backend
& .\.venv_test\Scripts\python.exe -c "from app.main import app; print(len(app.routes))"
Pop-Location

# Удалить тестовый venv:
Remove-Item -Recurse -Force backend\.venv_test
```
- Размер venv < 500 MB (раньше было 4-6 GB).
- Импорт работает без ошибок.
- Приложи реальный вывод size в PR.

**Branch & PR:**
PR title: `chore(deps): split heavy deps into optional profiles`

---

## Task 5 — Урезать persona prompt, сохранить evolution

**Ветка:** `cleanup/slim-persona-prompt`
**Goal:** Системный промпт для 2B модели должен быть **< 150 токенов**, при этом `persona_evolution` остаётся включённым и продолжает обновлять БД.

**Why:** Сейчас [backend/app/application/persona/service.py:25](backend/app/application/persona/service.py) `build_persona_prompt` собирает промпт из 15 секций (identity, voice, values, behavior_rules, preferences, tool_style, boundaries, disallowed_drift, profile overlay, calibration, runtime constraints) — это ~500+ токенов. 2B-модель физически не удерживает столько инструкций в attention, отсюда "плавающая личность".

**Что НЕ делать (важно):**
- **НЕ отключай `persona_evolution`** — `observe_dialogue`, `rollback_persona`, evolution tables в БД остаются.
- **НЕ удаляй `identity_guard`** post-processor (он нужен как safety net для 2B).
- **НЕ удаляй** evolution-логику из [backend/app/application/persona/evolution.py](backend/app/application/persona/evolution.py).

**Что сделать:**

1. В [backend/app/application/persona/service.py](backend/app/application/persona/service.py) функция `build_persona_prompt` должна выдавать **короткий** промпт:
   - Имя + 1-строчная mission
   - Топ-3 trait из persona_evolution snapshot (отсортированных по confidence из БД, не все подряд)
   - Активный профиль (одна строка типа `Профиль: Программист — точный, краткий, код-первым.`)
   - Калибровка как **3 короткие пометки** (`tone:compact, format:structured, lists:moderate`), не как разделы
   - 1-2 жёсткие constraint: "Ты — Elira", "Не называй себя моделью"
   - Итог: < 150 токенов.

2. Полную структуру персоны (`payload.values`, `behavior_rules`, и т.п.) **сохранить в БД** — она используется evolution-логикой. Просто **не выгружай всю в каждый промпт**.

3. `persona_evolution.observe_dialogue` должна продолжать работать на каждом ответе — это не трогаем.

4. Если есть существующие тесты `test_persona*.py` — они должны пройти. Если тест ожидает старый длинный формат — обнови его, но **только** assertions на конкретный текст, не саму логику.

5. Создать новый тест `backend/tests/test_persona_prompt_size.py`:
   ```python
   def test_persona_prompt_under_token_budget():
       from app.application.persona.service import build_persona_prompt
       prompt = build_persona_prompt("Программист", "qwen2.5:3b")
       # Грубая оценка: 1 токен ≈ 4 символа на русском
       assert len(prompt) < 600, f"Prompt too long: {len(prompt)} chars"
   ```

**Acceptance:**
- Новый тест зелёный.
- Все существующие тесты в `backend/tests/` зелёные.
- `persona_evolution` явно НЕ удалён — приложи `git log --stat` показывающий что [evolution.py](backend/app/application/persona/evolution.py) не тронут (кроме разве что cosmetic refactor).
- Ручная проверка через curl на `/api/chat`: персона отвечает как Elira, не дрейфит на первых 3 вопросах (приложи скриншот/transcript).

**Branch & PR:**
PR title: `feat(persona): slim per-call prompt for 2B models, keep evolution intact`

---

## Task 6 — Реальный code-агент через Ollama function calling

**Ветка:** `feat/real-code-agent`
**Goal:** Добавить настоящий agent loop с tool use, как у Claude Code/Codex (в локальном масштабе).

**Why:** Сейчас "code-агент" это [backend/app/application/code_agent/](backend/app/application/code_agent/) — `subprocess.run(["python", file.py])` с до 2-х повторов на ошибку. Нет Read/Edit/Write, нет agent loop, нет tool calling. Пользователь хочет уровень Codex/Claude Code.

**Что сделать:**

1. **Выбор модели.** 2B-модели часто не справляются с tool calling. Проверь и выбери одну из:
   - `qwen2.5-coder:3b` (поддерживает tools, ~2GB VRAM)
   - `llama3.2:3b` (поддерживает tools)
   - `qwen2.5:3b`
   Если 3B всё ещё не справляется на RTX 4060 — `qwen2.5-coder:7b` (~5GB VRAM, влезает в 8GB карту с запасом).
   Зафиксируй выбор в комментарии к PR с обоснованием.

2. Создать новый модуль `backend/app/application/code_agent/agent_loop.py`:
   - Функция `run_code_agent(user_message: str, project_root: Path, model: str, max_steps: int = 20) -> dict`
   - Использует Ollama 0.4+ tool calling API (`ollama.chat(..., tools=[...])`)
   - Tool registry — те самые инструменты, не registry-as-DB-CRUD:

3. Описать инструменты (JSON schema), реализация в `backend/app/application/code_agent/tools.py`:
   - `read_file(path: str, offset: int = 0, limit: int = 2000) -> str`
   - `write_file(path: str, content: str) -> str` (создаёт/перезаписывает)
   - `edit_file(path: str, old_string: str, new_string: str) -> str` (точная замена)
   - `glob(pattern: str) -> list[str]`
   - `grep(pattern: str, path: str = ".", glob: str = "*") -> str`
   - `run_bash(command: str, timeout: int = 60) -> str`
   - Все пути sandboxed внутри `project_root` (raise если выход за пределы).

4. Agent loop:
   ```
   messages = [system_prompt, user_message]
   for step in range(max_steps):
       response = ollama.chat(model=model, messages=messages, tools=tool_schemas)
       if response has tool_calls:
           execute each tool, append tool_result to messages
       else:
           return response.content
   ```

5. Добавить роут `POST /api/code-agent/run` в `backend/app/api/routes/code_agent.py` (новый файл), зарегистрировать в [registry.py](backend/app/api/routes/registry.py).
   - Request: `{message: str, project_root: str, model: str}`
   - Response: streaming или sync с `{response: str, tool_calls: [...], steps: int}`

6. Тесты в `backend/tests/test_code_agent_loop.py`:
   - Mock Ollama: один tool_call → один tool_result → финальный ответ
   - Песочница: `read_file("../../etc/passwd")` должен raise
   - Реальный smoke (skipped по умолчанию, помечен `@pytest.mark.requires_ollama`): "создай файл foo.txt с текстом bar" → проверить что файл создался.

**Что НЕ делать:**
- Не трогай старый `/api/chat` — это отдельный путь.
- Не выкидывай существующий `code_agent/execution.py` и `generation.py` — они используются другими местами. Просто добавляем новый путь рядом.
- Не пихай 50 инструментов сразу. Шесть выше — минимум жизнеспособного агента.

**Acceptance:**
- `pytest backend/tests/test_code_agent_loop.py -v` зелёный.
- Ручной smoke с реальной Ollama (приложи transcript):
  ```
  curl -X POST http://127.0.0.1:8000/api/code-agent/run \
    -H "Content-Type: application/json" \
    -d '{"message": "В папке testroot создай python скрипт hello.py, выводящий Hello Elira, и запусти его", "project_root": "C:/tmp/testroot", "model": "qwen2.5-coder:3b"}'
  ```
  Должен: создать файл, запустить, вернуть stdout `Hello Elira`.

**Branch & PR:**
PR title: `feat(code-agent): real agent loop with Ollama tool calling`

---

## Task 7 — Удалить frontend `.js` дубликаты

**Ветка:** `cleanup/frontend-js-dupes`
**Goal:** Только `.ts/.tsx` в `frontend/src/`. Никаких `.js`+`.ts` пар одного и того же.

**Why:** Phase 6 (TypeScript migration) была DONE, но `.js` файлы не удалены. Сейчас в `frontend/src/` и `frontend/src/api/` живут пары:
- `chatConstants.js` + `chatConstants.ts`
- `chatUtils.js` + `chatUtils.ts`
- `StatusPanels.jsx` + `StatusPanels.tsx`
- `api/advanced.js` + `api/advanced.ts`
- `api/agent.js` + `api/agent.ts`
- `api/chats.js` + `api/chats.ts`
- `api/dashboard.js` + `api/dashboard.ts`
- `api/integrations.js` + `api/integrations.ts`
- `api/library.js` + `api/library.ts`
- `api/tasks.js` + `api/tasks.ts`
- `api/apiUtils.js` + `api/apiUtils.ts`

**Что сделать:**

1. Для каждой пары:
   - `diff` `.js` и `.ts` версии. Если `.ts` — это полный надкласс — удали `.js`.
   - Если в `.js` есть что-то чего нет в `.ts` — STOP, опиши в "Замечено по пути".
2. После удаления — найти импорты что ссылаются на `.js` явно:
   ```
   grep -rn "from '.*\.js'" frontend/src
   ```
   Поправить на без расширения или на `.ts`.
3. Запустить:
   ```
   npm --prefix frontend run typecheck
   npm --prefix frontend run build
   ```

**Что НЕ делать:**
- Не удаляй `.js` файлы которые НЕ имеют `.ts` пары (если такие есть).
- Не трогай `vite.config.js` — это конфиг, иногда оставляют js.

**Acceptance:**
- `npm --prefix frontend run typecheck` без ошибок.
- `npm --prefix frontend run build` собирается.
- `git diff --stat` показывает только deletions `.js` + минимальные правки импортов.

**Branch & PR:**
PR title: `chore(frontend): remove .js duplicates after TS migration`

---

## Замечено по пути

> Sonnet и Opus оба пишут сюда находки которые не входят в текущую задачу, но требуют внимания.

(пусто)

---

## Лог завершённых задач

| Дата | Задача | Ветка | Исполнитель | Ревьюер | Заметки |
|------|--------|-------|-------------|---------|---------|
| — | — | — | — | — | — |
