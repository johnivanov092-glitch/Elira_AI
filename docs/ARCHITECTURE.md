# Architecture

Elira runs as a local desktop/workspace application. The main PC owns UI,
backend state, tools, memory, approvals, and project files. The dedicated AI
server is an inference backend reached through OpenAI-compatible HTTP APIs.

## Topology

```text
Tauri desktop
  -> React frontend
  -> FastAPI backend on 127.0.0.1:8000
  -> local SQLite/runtime data in D:\AIWork\Elira_AI\data
  -> OpenAI-compatible LLM endpoint on the AI server
  -> OpenAI-compatible embedding endpoint on the AI server
```

Stage 1 does not move the Elira backend to the server. The server is
inference-only.

## Runtime Layers

- `backend/app/api` - HTTP route surface.
- `backend/app/application` - use cases, chat, code-agent, agent kernel,
  model routing, memory, task planning, media, project brain, tool providers.
- `backend/app/domain` - domain objects and tool definitions.
- `backend/app/infrastructure` - SQLite, search, LLM clients, local provider
  adapters, external IO.
- `frontend/src` - React UI and API clients.
- `src-tauri` - desktop shell and native capabilities.

## API Access (auth)

The FastAPI surface is gated by `backend/app/core/auth.py`. Loopback callers
(the Tauri shell and the dev browser on `127.0.0.1`, plus the in-process test
client) are trusted without a token; any
non-local caller (LAN/mobile) must present a bearer token. This closes
unauthenticated command execution when the backend is bound to `0.0.0.0`.

- Token: `ELIRA_API_TOKEN`, else auto-generated to `data/elira_api_token`.
- Enforcement toggle: `ELIRA_API_AUTH` (default on).
- Middleware is registered before CORS in `main.py` so CORS stays the outermost
  layer; `/health` and `OPTIONS` are open. Frontend sends the token via
  `withAuth()` when `VITE_ELIRA_API_TOKEN` is set.

## Local Model Contract

The local model path is OpenAI-compatible, not runtime-specific.

Canonical client:

- `backend/app/infrastructure/llm/openai_compatible.py`

Important environment variables:

- `LLAMA_SERVER_ENABLED`
- `LLAMA_SERVER_BASE_URL`
- `LLAMA_SERVER_MODEL`
- `LLAMA_SERVER_API_KEY`
- `LLAMA_SERVER_TIMEOUT_SECONDS`
- `LLAMA_SERVER_MAX_TOKENS`
- `LLAMA_SERVER_CONTEXT_WINDOW`
- `LOCAL_EMBED_ENABLED`
- `LOCAL_EMBED_BASE_URL`
- `LOCAL_EMBED_MODEL`
- `LOCAL_EMBED_API_KEY`
- `LOCAL_EMBED_TIMEOUT_SECONDS`

Default endpoints are:

- LLM: `http://192.168.88.15:8000/v1`
- Embeddings: `http://192.168.88.15:8001/v1`

Provider names used in app state and metrics:

- `llama_server`
- `local_embed_server`

## Model Routing

Model profiles live in `agent_monitor.db` table `model_profiles` and are seeded
from `backend/app/application/monitoring/store.py`.

Default local profiles:

- `00-local-llama-fast`
- `00-local-llama-code`
- `00-local-llama-strong`
- `local-embedding`

Cloud profiles are disabled by default and require explicit consent before use.

## Chat And Code-Agent

Chat entrypoints:

- `backend/app/application/chat/entrypoint_sync.py`
- `backend/app/application/chat/entrypoint_stream.py`
- `backend/app/application/chat/service.py`
- `backend/app/application/chat/local_chat.py`

Code-agent runtime (`backend/app/application/code_agent/`):

- `agent_loop.py` - the streaming run loop and orchestration.
- `tools.py` - sandboxed file/shell/web tool implementations.
- `tool_schemas.py` - static OpenAI function-calling schemas.
- `prompts.py` - system-prompt construction.
- `indexing.py` - project code indexing/RAG.
- `inline_tool_calls.py` - recovery of tool calls emitted as plain text/JSON.

The code-agent uses explicit tool registries and policy checks. Do not add a
second executor or parallel provider stack.

## Agent Kernel And Tools

Canonical owners:

- executor: `backend/app/application/agent_kernel/executor.py`
- deferred tool activation:
  `backend/app/application/agent_kernel/deferred_tools.py`
- tool registry: `backend/app/application/tool_registry/`
- tool providers: `backend/app/application/tool_providers/`
- policy preflight: `backend/app/application/agent_registry/sandbox.py`
- approvals/limits/metrics: `backend/app/application/monitoring/`
- audit events: `backend/app/application/event_bus/`
- MCP stdio runtime:
  `backend/app/application/tool_providers/mcp_client.py`,
  `mcp_provider.py`, `mcp_runtime.py`

Tool execution is fail-closed: unknown, unclassified, unactivated, forbidden, or
out-of-scope tools do not reach provider dispatch.

## Memory And Data

Active runtime root is root-level `data/`.

Gitignored runtime data:

- `data/*.db`
- `data/generated/`
- `data/uploads/`
- `data/system/`
- `data/run_history.json`
- `data/elira_secret.key` (Fernet key — auto-generated, must stay untracked)
- `data/elira_api_token` (per-machine API token — auto-generated)
- logs and caches

Intentionally tracked local bootstrap data:

- `data/plugins/`
- `data/plugins_config.json`

Secrets and machine-local config stay in `.env.local` files only.

## Frontend

Frontend source lives in `frontend/src`.

Key areas:

- `components/EliraChatShell.tsx` - primary chat shell.
- `components/CodeWorkspaceShell.tsx` and related components - code workspace.
- `components/ProjectPanel.tsx` - project selection.
- `api/` - typed API clients by domain.
- `streamRegistry.ts` - stream survival across UI switches.
- `pickFolder.ts` - shared Tauri folder picker.

The desktop app serves `frontend/dist` in packaged mode. Frontend changes need:

```powershell
npm --prefix frontend run build
```

Type changes need:

```powershell
npm --prefix frontend run typecheck
```

## Verification Policy

- Backend behavior: focused pytest first, full `pytest -q` for broad changes.
- Frontend behavior: `npm --prefix frontend run typecheck`; build when bundle
  output matters.
- Tauri/config changes: rebuild or run Tauri dev.
- Docs-only changes: `git diff --check` plus targeted grep for stale terms is
  enough.
