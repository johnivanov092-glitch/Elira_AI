# Post-Server Status

Last cleaned: 2026-08-24.

The local inference migration is implemented at the Elira client layer. Elira
uses OpenAI-compatible LLM and embedding endpoints exposed by the dedicated AI
server.

## Completed

- Local llama-server provider client:
  `backend/app/infrastructure/llm/openai_compatible.py`.
- Direct chat routing to the local provider:
  `backend/app/application/chat/local_chat.py`.
- Project-brain local provider bridge:
  `backend/app/application/project_brain/llm.py`.
- Local model listing facade:
  `backend/app/infrastructure/llm/local_models.py`.
- Model profile seeds for fast/code/strong/embedding local roles.
- Local embedding path for RAG/semantic memory.
- Legacy local-runtime references removed from code, requirements, tests, docs,
  and runtime SQLite state during the cleanup pass.

## Current Provider Contract

```env
LLAMA_SERVER_ENABLED=true
LLAMA_SERVER_BASE_URL=http://192.168.88.15:8000/v1
LLAMA_SERVER_MODEL=local-model
LLAMA_SERVER_API_KEY=local
LLAMA_SERVER_TIMEOUT_SECONDS=600
LLAMA_SERVER_CONTEXT_WINDOW=131072

LOCAL_EMBED_ENABLED=true
LOCAL_EMBED_BASE_URL=http://192.168.88.15:8001/v1
LOCAL_EMBED_MODEL=local-embed
LOCAL_EMBED_API_KEY=local
LOCAL_EMBED_TIMEOUT_SECONDS=30
```

`LLAMA_SERVER_TIMEOUT_SECONDS=600` задаёт конечный connect timeout. Chat HTTP
использует `(connect_timeout, None)`, поэтому после установления соединения у
генерации нет read/deadline timeout; её останавливает явный Workflow Stop либо
объективная provider/transport error. Эффективный context дополнительно
сверяется с live server properties и не может превышать доступное окно модели.

Server summary: `docs/SERVER.md`. Live access and smoke tests are documented in
the sibling repo at `../Elira_AI_Server/Server/ACCESS.md`.

## Remaining Work

Keep follow-up work narrow and evidence-based:

1. Run `backend/tests/smokes/routing_eval.py` after every server-model swap; see
   `docs/AGENT_EVALS.md` for cases and report locations.
2. Use its per-case latency, TTFT, tool/MCP trace, token usage, and failure data
   when comparing active models. Measure the maximum safe context separately
   with a dedicated long-context case before changing limits.
3. Tune model profile context limits only from measured server behavior.
4. A bearer-token auth gate now protects non-loopback access (`app/core/auth.py`,
   `ELIRA_API_TOKEN`/`ELIRA_API_AUTH`); add a reverse proxy/TLS only before any
   true non-LAN exposure or multi-user mode.
5. Keep cloud profiles disabled unless explicit user consent is recorded.

The consolidated continuation list lives in `docs/BACKLOG.md` (sections 2–9).

## Regression Checks

```powershell
cd D:\AIWork\Elira_AI
backend\.venv\Scripts\python.exe -m pytest -q
npm --prefix frontend run typecheck
npm --prefix frontend run build
git diff --check
```
