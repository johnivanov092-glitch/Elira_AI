# Post-Server Backlog

**Статус (обновлено 2026-06-11):** agent-loop пункты F1–F8 **выполнены до миграции**
по решению пользователя (миграцию на AI-server он делает сам, параллельно).
Ветка `claude/agent-loop-quality`, шаги ниже помечены коммитами. Серверный gate
остаётся актуальным только для самой миграции инференса.

Этот файл фиксировал задачи, отложенные до стабилизации серверного инференса.
Сначала сервер, ROCm/vLLM, OpenAI-compatible endpoint и подключение Elira.

## Gate: когда можно возвращаться к этому backlog

Начинать только после выполнения всех условий:

- AI-server на Ubuntu стабильно видит R9700 через ROCm.
- vLLM отвечает на `/v1/models` и `/v1/chat/completions`.
- Elira умеет использовать серверный provider.
- Есть рабочий coder/general model profile.
- Code-agent smoke test проходит на серверной модели.
- Старый Ollama path не используется как основной runtime.

## Agent Loop Quality Backlog — ✅ ВЫПОЛНЕНО 2026-06-11

Источник: `docs/AGENT_LOOP_FIX_PROPOSALS.md` и
`docs/notes/2026-06-10_chat-review-agent-loop.md`.
Каждый пункт — отдельный коммит с focused-тестами (ветка `claude/agent-loop-quality`):

1. **F6: raw history with `[tools used]`** — ✅ `5e3ce4d`
   Несжатая история между ходами несёт дайджест tool calls (общий helper со сжатием).

2. **F5: strict inline call fallback** — ✅ `ceab923`
   Fallback срабатывает только на «чистых» строках-вызовах; упоминание вызова
   в прозе финального ответа больше не перезапускает инструмент.

3. **F2: wrap-up summary on max_steps/timeout** — ✅ `be47af2`
   При обрыве — один no-tools wrap-up вызов («что сделано / что осталось») с
   детерминированным fallback из журнала вызовов; честный `stop_reason="timeout"`.

4. **F3: deep mid-run compaction** — ✅ `471507f`
   `_flatten_for_summary`: tool-результаты доходят до суммаризатора;
   `_merge_summary` вытесняет старейшие блоки (`[older summaries dropped]`), а не новые.

5. **F4: prompt from active deferred tools** — ✅ `5f3371c`
   Секция «Твои инструменты» генерируется из фактического набора прогона +
   явная строка про `tool_search`; правила 9–10 корректны в обоих режимах.

6. **F1: approval-loop pause + UI approve/reject** — ✅ `a5d8a8d` (backend) + `5af38a5` (frontend)
   Цикл ставится на паузу при `waiting_approval` (поллинг 1.5с, keepalive,
   продление дедлайна на время ожидания человека), одобрение потребляется в том
   же прогоне; карточка Разрешить/Отклонить в чате код-агента.
   `approval_wait_seconds`: stream — 300 по умолчанию, легаси `/run` — 0.

7. **F7/F8: small UX/router fixes** — ✅ `0a4a112` / `b81c51f`
   Бейдж «контекст сжат ×N» + маркер PRIOR_SUMMARY в промпте суммаризатора;
   image-гейт планировщика при code/python-сигналах + «найди баг» в code-словарь.

## Rules

- Не создавать второй executor, второй registry, второй scheduler или новую общую БД.
- Не ослаблять policy, approval binding, scopes или fail-closed ToolSpec.
- Каждый пункт делать отдельным малым commit.
- Focused tests на каждый пункт, полный backend pytest + frontend typecheck перед merge.
- Перед реализацией заново сверить фактический код, потому что этот backlog может устареть
  после server/provider работ.

## Deferred Until Later

Не делать в рамках server migration:

- multi-user family profiles;
- public/LAN auth layer;
- mobile UI;
- full backend relocation to the server;
- autonomous desktop/home-operator extensions.
