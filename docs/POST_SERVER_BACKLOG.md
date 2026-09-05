# Post-Server Status

Last cleaned: 2026-09-06.

The local inference migration is implemented at the Elira client layer. Elira
uses OpenAI-compatible LLM and embedding endpoints exposed by the dedicated AI
server.

## Completed

- Local llama-server provider client:
  `backend/app/infrastructure/llm/openai_compatible.py`.
- Direct chat routing to the local provider:
  `backend/app/application/chat/local_chat.py`.
- The disconnected Project Brain provider/chat chain was retired; project tasks
  use `backend/app/application/code_agent/agent_loop.py`.
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

## Agent Review and Current Quality Status (2026-09-06)

The runtime review fixes cover Workflow approval classification, browser
cancellation, MCP HTTP retry/redirect handling, SSE attempt ownership and
premature EOF, evidence redaction, and context continuation. The unused V8
orchestrator and disconnected Project Brain modules have been removed. Poppler
is resolved from the project runtime rather than an obsolete drive-root install.

Elira has one stable identity and temperature. Mood, learned traits and relevant
memory remain separate from the stable system prefix. Ordinary work starts with
17 built-in tools and their verification instructions; discovery-only defaults
were reverted after a live current-events refusal. Capability loading and MCP
still use the existing registry/executor. Correct execution takes priority over
short-chat TTFT; the runtime has no TTFT cutoff.

Verified locally: 2185 backend tests and 65 subtests, frontend typecheck/build,
real UI search, file reading, personal-memory retrieval, and inference-server
HTTP health. Cold initial prompts around 6.7K tokens had model TTFT around
13–15 seconds. These runs do not establish a new p95 latency guarantee.

Full answer-quality readiness is **not** established. Follow-up work remains:

- News answers sometimes omit source links or cite pages that were searched
  but not read. Verify claims and source coverage together in live evaluations.
- One conversational follow-up used masculine self-reference despite the
  identity instruction. Check continuity across work/conversation and thinking.
- A stronger generic self-check instruction caused an 11-step search without
  an answer within the 180-second test budget and was reverted. Avoid replacing
  these quality failures with an unbounded verification loop.

Raw conversations, machine-specific traces and source snapshots stay local in
`.scratch/`; this section is the shared readiness record.

## Regression Checks

```powershell
cd D:\AIWork\Elira_AI
backend\.venv\Scripts\python.exe -m pytest -q
npm --prefix frontend run typecheck
npm --prefix frontend run build
git diff --check
```
