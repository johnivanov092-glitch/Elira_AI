# Delivery Session — bounded multi-slice runs (S1)

Оркестрация НАД существующим `stream_code_agent` (не второй runtime): один
пользовательский запрос на структурную project-задачу может состоять из до
**4 bounded slices** одного и того же run_id. Модуль:
`backend/app/application/code_agent/delivery_session.py`.

## Контракт

- **Slice = обычный run.** Каждый slice — полный `stream_code_agent`
  (slice 2+ с `resume=True`): журнал, гарды, бюджеты (600s/slice), approval
  policy, TaskSpec/verifier-гейты работают без изменений.
- **Автопродолжение** только при выполнении всех условий сразу:
  - `stop_reason ∈ {timeout, context_limit, max_steps}` (бюджетные стопы);
  - задача не `confirmed` (partial/unverified);
  - в slice был **доказанный структурный прогресс**: реальные МУТАЦИИ этого
    slice (`progress.state_changes`; `state_changed` = ToolSpec.side_effect +
    успешное исполнение + непустой `touched_path` + не no-op: идентичные
    old/new_content не считаются; read_file/ssh_read не считаются; повторное
    реальное изменение уже тронутого файла — считается), критерий →
    `confirmed` (verifier), пункт durable чеклиста → `completed`
    (task_planner.db); `new_touched_paths` — только телеметрия;
  - не исчерпан лимит: максимум **3** автопродолжения после первого slice;
  - нет пользовательского Stop (`request_session_cancel` + `request_cancel`).
- **Никогда** после: `no_progress`, `loop_guard`, `error`, `cancelled`,
  rejected/expired approval, чистого `answer` — гарды уже сказали «стоп».
- **Ownership**: `_register_session` — атомарный claim; дубликат stream/resume
  того же run_id получает стабильную ошибку `delivery_session_already_active`
  и не трогает cancel-token оригинала. Stop в inter-slice gap перепроверяется
  сразу после `delivery_continuing`: следующий slice не запускается, терминал
  `cancelled` пишется в журнал lock-held API (`record_terminal_event`).
- **События**: промежуточный `done` НЕ доходит до клиента; вместо него
  информационный `delivery_continuing {slice, next_slice, auto_continuation,
  progress}`. Boundary-запись в журнал идёт через append-only
  `append_external_event` (только events.jsonl, state.json не переписывается —
  без run-lock нет гонки с ручным Resume). Терминальный `done` ровно один; на
  honest-partial он несёт `next_milestone` (первый открытый пункт чеклиста /
  неподтверждённый критерий) и `auto_continuations`; фронт показывает его
  отдельной строкой леджера «Следующий шаг: …» (только для не-solved).
- **Простые задачи** (нет TaskSpec по `derive_task_spec`) и **itops-diag-**
  прогоны: passthrough — один обычный run, поведение байт-в-байт как раньше.
- **Resume-контекст (server-owned)**: продолжение получает
  `[ПРОВЕРЕННЫЕ ФАКТЫ]`-блок из журнала + чеклиста (изменённые файлы,
  completed/open пункты, статус критериев) — `_coerce_history` перетегирует
  его в system. Ручной Resume (`POST /runs/{id}/resume`) использует тот же
  `build_continuation_kwargs`.
- **Запрет пере-планирования**: на resume-slice `todo_update`, замещающий
  существующие пункты другим текстом — через `items` ИЛИ через
  `updates=[{id, text}]` — получает редирект с реальным состоянием чеклиста
  (`resume_checklist_guard`); status/blocker-only updates, расширение и
  повторная отправка того же плана проходят.
- **Delivery-контракт**: на первом slice структурной задачи к user message
  добавляется нейтральный `[delivery-контракт]`: чеклист выводится из САМОЙ
  задачи (сначала обследование фактического состояния), без навязанных
  project-build вех; деплой — только по явному запросу; completed — только
  после фактической проверки. В system prompt не добавляется ничего.

## Reasoning recovery (B)

Первый `reasoning_runaway` в Thinking-run (`agent_loop.py`):
- run НЕ завершается; усечённая генерация не попадает в history;
- остальные model calls этого run переключаются на thinking-OFF (run-local,
  глобальный тумблер и журнальный request не меняются);
- в history добавляется `[internal correction]`-инструкция «прекрати
  рассуждать, проверь состояние, сделай конкретный шаг»;
- эмитится событие `reasoning_fallback` (журналируется) и в state.json
  ставится durable-флаг `thinking_fallback_applied` (сам `request.thinking` и
  глобальный тумблер не меняются);
- fallback строго one-shot: повторный runaway идёт в существующий бюджет
  `_REASONING_RUNAWAY_LIMIT=2` → `loop_guard`. `build_continuation_kwargs`
  принудительно ставит thinking=False по durable-флагу, поэтому НИ авто-slices,
  НИ ручной Resume этого run_id больше не включают Thinking.

## Тесты

`backend/tests/test_delivery_session.py` (поведение сессии, fallback, guard,
cancel, diag-исключение, approval-инвариантность),
`backend/tests/test_delivery_benchmarks.py` (два scripted-бенчмарка: desktop
CRUD/CRM и Telegram/service — реальные файлы + зелёная verification-команда;
SSH — только mock/provider-level).
