# Зрелый runtime для code-agent — план

**Принцип:** LLM *думает и предлагает следующий шаг*. Runtime *владеет циклом*: решает,
был ли измеримый прогресс, можно ли продолжать этим способом, и достигнута ли цель.
35B-модель может ошибиться 1–2 раза — система не даёт ей ошибаться 100 раз одним способом.

Не «чат-агент с tools», а **локальный sysadmin runbook-agent с LLM внутри**.

```
User request → TaskSpec → Plan → Step attempt → Tool exec
             → ProgressEvaluator → Strategy decision → Verify → Final report
```

Центральный слой (то, чего сейчас нет как слоя):

```
цель → попытка → измеримый прогресс?
                    ├─ да  → продолжай
                    ├─ нет → запрети ЭТУ strategy_key, смени семейство
                    └─ снова нет (семейства исчерпаны) → честно остановись
```

Ключевой сдвиг относительно текущего: сейчас `no_progress` — это **плоский стрик**
(«8 бесполезных вызовов → стоп»). Зрелая версия — **маршрутизатор стратегий**: считаем
попытки на `strategy_key` (семейство+таргет), исчерпали семейство → редирект на другое,
исчерпали все → честный стоп. Гард не блокирует работу — он блокирует **тупиковый способ**.

---

## Фаза 0 — что УЖЕ легло (не переделывать)

Реализовано и покрыто тестами (289 зелёных, канарейки компактификации целы), НЕ закоммичено:

- `no_progress_streak` + `loop_helpers.step_made_progress` (touched_path / run_server-старт /
  новый investigation-факт) — **зерно** ProgressEvaluator.
- redirect@3 → honest-stop@8; семантический ok (exit=0-но-бесполезный ≠ прогресс);
  `_fact_shape` (цифры→N) схлопывает churn.
- `raw_ssh_redirect` (run_bash→ssh_* , `#!raw-ssh` override).
- `ssh_run_ps` (base64 EncodedCommand, ноль quoting) + интент-активация `ssh_*`.
- `_deterministic_stop_summary` — **зерно** runtime-report.
- UI: шапка «⛔ зациклилась/нет прогресса · задача не завершена» вместо «N ok»; ledger
  показывает вывод при exit≠0.

Ниже — как это дорастить до полноценного слоя. Каждая фаза самостоятельна и шиппится
отдельно (tracer-bullet), с тестами и проверкой канареек.

---

## Фаза 1 — Честный UX (доделать #1). ~0.5 дня

**Цель:** ни одного места, где провал выглядит как «я остановила себя» или «N ok».

- `AgentTurn.tsx`: убрать «Остановила себя …» для `loop_guard` И `no_progress`. Вместо
  «я»-формулировки — инженерный статус: **«Не завершено · остановлено: <причина> · <N> вызовов»**.
- Task-state панель (seed): под группой тулов показывать
  `Tool calls · Progress events · No-progress attempts · Exhausted strategy · Stage`.
  Данные приходят в `done`/`final_response` (см. ниже — бэкенд начинает их слать).
- `run_bash`: возвращать `exit_code` и `ok=(exit==0)` в meta (сейчас всегда ok=True). UI
  красит точку по семантике, а не «выполнилось без исключения». (Grep/findstr no-match
  = не ошибка → отдельный флаг `benign_nonzero`, чтобы «не найдено» не красилось как fail.)

**Файлы:** `frontend/src/workspace/AgentTurn.tsx`, `ToolCallGroup.tsx`, `backgroundRuns.ts`;
`backend/.../tools/_run.py` (exit_code в meta), `agent_loop.py` (счётчики прогресса в `done`).
**Тесты:** расширить UI-независимо — backend шлёт `progress_events`/`no_progress_attempts`
в `done`; `test_progress_controller.py` проверяет их наличие.
**Канарейки:** не затрагивает промпт → зелёные.

---

## Фаза 2 — `strategy_key` + ProgressEvaluator как МОДУЛЬ (ядро, #2). ~1.5 дня

**Цель:** заменить плоский стрик маршрутизатором стратегий.

Новый модуль `backend/app/application/code_agent/progress.py`:

```
@dataclass
class ProgressVerdict:
    status: Literal["progress","no_progress","blocked","complete"]
    strategy_key: str        # "remote_edit:inline_ps@home-srv01:C:\a.ps1"
    evidence: str            # чем доказано (touched_path / assert / new fact)
    exhausted: bool          # эта strategy_key исчерпала бюджет попыток

class ProgressEvaluator:
    def classify(self, *, name, args, tool_meta, fact, task_state) -> ProgressVerdict
    def strategy_key(self, name, args) -> str
```

- **strategy_key = (family, target).** Семейства (стартовый набор):
  `remote_edit:inline_ps`, `remote_edit:ssh_write`, `remote_edit:ssh_replace`,
  `local_edit`, `shell_check`, `ssh_health_check`, `http_check`, `read`.
  target = host / path / service / port из args.
- **Per-strategy бюджет:** `same strategy_key: 2 попытки` без прогресса → `exhausted=True`.
  Runtime кладёт `strategy_key` в `exhausted_strategies` и **редиректит** модель:
  «Стратегия `remote_edit:inline_ps` исчерпана — не повторяй. Выбери другое семейство:
  `ssh_write` (temp .ps1) или `ssh_replace`.» — НЕ стоп.
- **Стоп** только когда исчерпаны ≥2–3 семейства для одного target ИЛИ глобальные лимиты.
- `agent_loop.py` post-result: заменить inline `step_made_progress` вызовом Evaluator;
  вести `strategy_attempts: dict`, `exhausted_strategies: set`.
- `step_made_progress`/`_fact_shape` переезжают в `progress.py` (loop_helpers реэкспортит
  для обратной совместимости тестов).

**Пример (реальный Content-Length кейс):**
```
Попытка 1: strategy_key=remote_edit:inline_ps@…:agent-lab.ps1
           verifier=ssh_assert_not_contains(Content-Length) → no_progress → exhausted
Runtime: «inline_ps исчерпана, не повторяй; возьми ssh_write_temp / ssh_replace»
Попытка 2: strategy_key=remote_edit:ssh_replace@… → progress → complete
```

**Файлы:** `progress.py` (new), `agent_loop.py`, `loop_helpers.py`.
**Тесты:** `test_progress.py` (strategy_key деривация, per-strategy exhaustion, редирект vs
стоп, что РАЗНЫЕ-но-впустую вызовы одного семейства режутся в 2, а смена семейства
сбрасывает). Существующий `test_progress_controller.py` адаптировать.
**Канарейки:** редирект-текст идёт в тул-результат (не в базовый промпт) → зелёные.

---

## Фаза 3 — Высокоуровневые sysadmin primitives (#3/#4). ~1 день

**Цель:** модель не изобретает quoting — вызывает понятный примитив с аргументами.
Каждый примитив — ещё и **verifier** для ProgressEvaluator (чёткий ok/evidence).

Добавить в `ssh_provider.py` (Windows-aware, чтение/правка через base64 PS):
- `ssh_replace(host, path, old, new)` — атомарная замена; evidence = diff/кол-во замен.
- `ssh_assert_contains(host, path, pattern)` / `ssh_assert_not_contains(...)` — verifier,
  возвращает `ok` строго по факту.
- `ssh_port_check(host, port)` — LISTENING? evidence = pid/proto.
- `ssh_process_find(host, pattern)` — процессы по маске.
- `http_check(url, expected_status, expected_json?)` — тонкая обёртка над `tool_http_api`.

Активация: добавить в `_SSH_ACTIVATABLE_TOOLS`; метаданные в `builtins._build_ssh_tools`.

**Файлы:** `ssh_provider.py`, `tool_registry/builtins.py`, `agent_loop.py`,
`tools/_content.py` (http_check).
**Тесты:** расширить `test_ssh_provider.py` (subprocess замокан: argv/base64/stdin/assert-семантика).
**Канарейки:** тул-схемы грузятся только на SSH/http-прогонах → зелёные.

---

## Фаза 4 — Strategy budgets + verify-retry лимиты (#3 budgets). ~0.5 дня

**Цель:** нормальные лимиты вместо «200 шагов».

```
same strategy_key + target : 2 attempts   (Фаза 2)
same tool + host           : 5 attempts
remote-shell quoting        : 2 attempts   (raw_ssh_redirect + inline_ps семейство)
total tool calls / turn     : 25
verification retries        : 2
```

- Ввести `BudgetTracker` в `progress.py`; каждый лимит при исчерпании → редирект, а не kill;
  все лимиты сразу исчерпаны → honest stop с указанием, какой бюджет пробит.
- `DEFAULT_MAX_STEPS` 200 остаётся аварийным потолком, но реальный стоп наступает по
  strategy/budget гораздо раньше.

**Файлы:** `progress.py`, `agent_loop.py`.
**Тесты:** `test_progress.py` — каждый бюджет режет в срок; смена стратегии/таргета сбрасывает.

---

## Фаза 5 — Runtime final report (доделать #6). ~0.5 дня

**Цель:** аварийный финал строит runtime из task-state, а не пересказывает зацикленная модель.

Шаблон (заменяет `_deterministic_stop_summary`):
```
Не завершено.
Подтверждено:   <success_criteria, что verifier подтвердил + evidence>
Не сделано:     <невыполненные критерии>
Почему стоп:    <strategy X исчерпана N раз / пробит бюджет Y>
Следующий шаг:  <безопасное продолжение>
```

- Источник — task-state (Фаза 6) + `exhausted_strategies` + результаты verifier'ов.
- LLM-проза (если нужна) — необязательный слой ПОСЛЕ, не источник фактов.

**Файлы:** `progress.py`/новый `report.py`, `agent_loop.py` (все стопы loop/no_progress/budget),
UI рендерит структуру.
**Тесты:** `test_progress.py`/`test_wrap_up_honesty.py` — отчёт строится из состояния, факты не выдуманы.

---

## Фаза 6 — TaskSpec / Plan слой (#1 сущность, самый большой). ~2 дня

**Цель:** у прогона есть измеримая цель и критерии готовности, а не только `user_message`.

```
TaskSpec {
  goal: str
  scope: str[]              # где можно работать (совместить с access_mode/project_root)
  constraints: str[]        # что нельзя
  success_criteria: str[]   # как доказать готовность
  verifiers: str[]          # команды/примитивы для доказательства
  stop_conditions: str[]
}
```

- Деривация: один **ограниченный** LLM-препасс на старте (goal + criteria + verifier-хинты),
  или эвристика для простых задач. Хранить в `run_journal.state` (там уже есть `task/todo/done/
  current_phase` — расширить схемой). Токен-бюджет: препасс отдельным вызовом, НЕ в базовом промпте.
- ProgressEvaluator получает `приблизились ли к success_criteria` как сигнал `complete`.
- **Завершение по verifier, а не по слову модели** (расширить существующий `.elira/verify`
  hard-gate до общего механизма: `complete` = все success_criteria подтверждены verifier'ами).
- Stage-модель: `inspect → write → start → verify → cleanup/final`; модель выбирает следующий
  этап, но не может бесконечно долбить один класс действий без перехода состояния.
- UI: показывать TaskSpec (goal + критерии + прогресс к ним), а не только поток тулов.

**Файлы:** `taskspec.py` (new), `agent_loop.py`, `run_journal.py` (схема состояния),
`prompts.py` (ограниченный препасс-промпт), UI (панель задачи).
**Тесты:** `test_taskspec.py` (деривация, verifier-gated complete, stage-переходы).
**Канарейки:** препасс — отдельный вызов; базовый промпт не растёт → следить за компактификацией
при любом добавлении в системный промпт (правило [project_base_prompt_capacity]).

---

## Порядок и принцип внедрения

Строго по возрастанию риска и по зависимостям:

1. **Фаза 1** (честный UX) — дёшево, сразу убирает ложную картинку.
2. **Фаза 2** (strategy_key + ProgressEvaluator) — ядро, ради него всё.
3. **Фаза 3** (primitives) — чтобы стратегиям было куда переключаться.
4. **Фаза 4** (budgets) — формализация лимитов поверх Фазы 2.
5. **Фаза 5** (runtime report) — честный финал из состояния.
6. **Фаза 6** (TaskSpec/Plan) — верхний слой цели/критериев.

**Инварианты (соблюдать в каждой фазе):**
- Гард **редиректит**, не блокирует; hard-стоп только когда все семейства/бюджеты исчерпаны
  или это безопасность (destructive/timeout).
- Каждая фаза: полный прогон backend-тестов + **канарейки компактификации** + typecheck/build.
- Никакого роста базового системного промпта без проверки канареек; новые правила — через
  интент-инъекцию/тул-результаты, не в базу.
- Строго локально (llama.cpp 35B); никакого облака.

**Definition of done для всего трека:** повторить живой сценарий `agent-lab` на `home-srv01` —
удаление Content-Length должно занять 1 `ssh_replace`/`ssh_run_ps`, а не 80 попыток; при любом
застревании — редирект на другое семейство в ≤2 попытки, а не 55 шагов; финал — структурный
runtime-отчёт, не «91 ok».
