# Post-Server Backlog

**Статус:** будущие работы после переноса локального инференса на отдельный AI-server.

Этот файл фиксирует задачи, которые не нужно начинать до стабилизации серверного
инференса. Сначала сервер, ROCm/vLLM, OpenAI-compatible endpoint и подключение Elira.
Потом agent-loop улучшения.

## Gate: когда можно возвращаться к этому backlog

Начинать только после выполнения всех условий:

- AI-server на Ubuntu стабильно видит R9700 через ROCm.
- vLLM отвечает на `/v1/models` и `/v1/chat/completions`.
- Elira умеет использовать серверный provider.
- Есть рабочий coder/general model profile.
- Code-agent smoke test проходит на серверной модели.
- Старый Ollama path не используется как основной runtime.

## Agent Loop Quality Backlog

Источник: `docs/AGENT_LOOP_FIX_PROPOSALS.md` и
`docs/notes/2026-06-10_chat-review-agent-loop.md`.

Порядок после server migration:

1. **F6: raw history with `[tools used]`**  
   Маленький frontend patch. Несжатая история между ходами должна помнить tool calls.

2. **F5: strict inline call fallback**  
   Backend patch. Не выполнять `run_bash(...)` из обычной прозы финального ответа.

3. **F2: wrap-up summary on max_steps/timeout**  
   Backend UX patch. При обрыве дать пользователю итог: что сделано, что осталось.

4. **F3: deep mid-run compaction**  
   Backend quality patch. Серверная компакция должна учитывать tool calls/results и
   вытеснять старые rolling summaries, а не новые.

5. **F4: prompt from active deferred tools**  
   Backend prompt patch. System prompt должен описывать фактически активный tool set.

6. **F1: approval-loop pause + UI approve/reject**  
   Крупный backend+frontend patch. Делать отдельной фазой, потому что он меняет
   stream-семантику code-agent и approval UX.

7. **F7/F8: small UX/router fixes**  
   Только после основных agent-loop исправлений.

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
