# Architecture

Elira is a local Tauri + React + FastAPI agent. The desktop owns UI, durable
state, project files and tools; a LAN llama.cpp server owns inference only.

The detailed Russian guide is
[`AGENT_ARCHITECTURE_GUIDE_RU.md`](AGENT_ARCHITECTURE_GUIDE_RU.md).

## Canonical flow

```text
Composer
  -> POST /api/code-agent/stream
  -> delivery_session
  -> agent_loop
  -> agent_kernel.executor
  -> runtime_registry
  -> Builtin | SSH | IT Ops | MCP | LSP provider
  -> OS / LAN / files / subprocesses

Workflow request
  <- input | secret | elevation | approval
  <- workflow_engine.db + /api/agent-os/events/stream
```

Multi-agent uses `application/workflows` as a coordinator but every agent step
returns to the same `run_code_agent`, executor and provider registry.

## Runtime invariants

- One agent core: `application/code_agent/agent_loop.py`.
- One tool executor: `application/agent_kernel/executor.py`.
- One provider aggregation path: `application/tool_providers/runtime_registry.py`.
- One durable human control plane: `application/workflows` +
  `workflow_engine.db`.
- The first model turn sees only the compact built-in core, including
  `capability_load`, `runtime_control`, and managed background process control
  through `run_server`. Auto first resolves the task to one effective persona,
  then that profile receives its narrow starter groups: Personal=`memory`,
  Business=`web+resources+data`, Infrastructure=typed IT Ops,
  Science=`web+data`, Medicine=`web+resources`; Balance and Engineering use the
  core (which already contains project/code tools). The model can load any
  additional group through `capability_load`. MCP/LSP/SSH remain per-run and
  appear only after a relevant `runtime_control` request.
- A new-chat plain greeting has no tool schemas at all; any task, continuation,
  path, command, MCP/SSH request, or existing history uses the normal agent loop.
- No tool/path/asset/LAN authorization scope, internal ApprovalStore,
  feature dispatch gate, max steps, run deadline or no-progress self-stop.
- Healthy runs end through a natural answer or Workflow Stop. Provider, OS,
  protocol and physical context-window failures remain real errors.
- Command-only SSH calls use OpenSSH `-n`: accidental remote stdin reads receive
  EOF instead of hanging the workflow. IT Ops reuses the same SSH argument
  builder; SSH file writes explicitly forward stdin.
- Selecting a project folder persists `project_root` in the session and injects
  its absolute path plus an explicit connected-project block into every new run
  prompt. No parallel project-awareness helper exists.
- Legacy ToolSpec policy columns are inventory compatibility only.

## Permission selector

The Workflow run owns one mode:

- `ask`: read-only calls run; mutations ask in Workflow UI.
- `accept_edits`: ordinary local mutations run; high/unknown-impact calls ask.
- `bypass`: no product-level approval request.

`impact_policy.py` classifies calls only for this UI decision. It never blocks a
call independently. `bypass` does not create a Windows administrator token.

## Workflow requests

Public kinds are `input`, `secret`, `elevation`, and `approval`. Requests are
durable and resume the same workflow run and step. Plaintext secrets are never a
valid resolution; the payload contains only an opaque `secret_ref`.

Windows elevation is executed by the Tauri native bridge in
`src-tauri/src/main.rs`, which opens UAC and binds the result to the Workflow
request. The backend does not require permanent administrator rights.

## Integration boundary

Built-in schema composition is owned by
`application/code_agent/capabilities.py`. `capability_load(group)` validates a
model-selected group, then the existing runtime registry is rebuilt for the
next model turn. Loaded groups are journalled for Resume/automatic continuation;
a new run starts with the compact core again. This is prompt composition, not
authorization: the Workflow permission selector remains the only product-level
permission decision. A hidden built-in schema does not remove its canonical
dispatch owner; if a valid native/inline call reaches the runtime, it still uses
the same ToolExecutor and handler.

`PROFILE_CAPABILITY_GROUPS` and `PROFILE_ITOPS_DEFAULTS` are the deterministic
profile-to-starter-tool map. They receive the already-resolved effective mode,
so `Auto + network` becomes `Infrastructure + IT Ops`, while `Auto + code`
becomes `Engineering + project/code core`. IT Ops is not injected into unrelated
profiles. This map grants no permission and creates no executor/provider. TCP
checks in Infrastructure should use `itops_network_inventory` with explicit
per-connect timeout and concurrency, not sequential shell `Test-NetConnection`.

MCP, LSP, SSH shortcuts, Telegram, IT Ops, plugins, Workflow scheduling,
memory/library administration and vault operations are behind the agent's
`runtime_control` tool. It returns a single structured capability envelope:
`completed`, `failed`, `needs_input`, `needs_secret`, `needs_elevation`,
`waiting_approval`, or `cancelled`. Existing domain runtimes remain owners;
`runtime_control` is an adapter, not a second executor or registry.

Long finite commands use the existing `run_server(kind="job")` process runtime.
`start` returns a PID immediately; `list`/`logs` expose running or terminal state
and captured output; `stop` and Workflow Stop kill the owned process tree. A
completed job result is retained in a bounded in-memory tail. There is no
product wall-clock deadline. Short typed tools stay synchronous; backgrounding
does not replace transport liveness timeouts such as TCP `connect_timeout`.

MCP servers do not auto-start with FastAPI or merely because of a persona. When
the user explicitly requests MCP, or the task requires a configured external
integration absent from active tools, the model uses `mcp_list`, selects one
relevant server, and calls `mcp_start(server_id)`; only that server's schemas are
added to the next model turn. LSP follows the same per-run rule. `ssh_hosts`
reveals the existing SSH provider and `itops_assets` reveals the IT Ops provider
without turning those discovery calls into authorization gates.

The former Pipelines control plane is not mounted. Interval schedules are
`workflow_triggers` in `workflow_engine.db`; they start existing Workflow
templates and inherit `ask`, `accept_edits`, or `bypass`.

## Inference contract

`infrastructure/llm/openai_compatible.py` sends OpenAI-compatible requests.

- Chat generation has a finite connect timeout and no read/generation deadline.
- Workflow Stop closes the active provider `requests.Response` before the cancel
  endpoint acknowledges. One run-scoped handle covers planning, context
  compaction and normal generation; llama.cpp prompt processing stops as soon
  as its streaming response is bound.
- Context usage and prompt telemetry include the exact activated tool schemas;
  integrations can no longer fill the server window while the UI reports only
  message text. `context_prepared` updates the UI before prompt prefill starts.
- Model payloads contain exactly one leading `system` message. Summaries,
  verified facts and previous tool output remain assistant-shaped runtime
  context because strict Qwen templates reject late system messages.
- Every chat payload sets `cache_prompt: true`.
- Reasoning modes are `none`, `low`, `medium`, `xhigh`.
- Qwen reads `enable_thinking` + `reasoning_effort`.
- Muse reads `reasoning_strength`; public `none` maps to Muse `low`.
- MTP/DFlash are server-side acceleration mechanisms independent of reasoning.

## Mounted HTTP surface

`api/routes/registry.py` is the sole router list. Active families:

- `/api/code-agent`
- `/api/agent-os`
- `/api/advanced`
- `/api/media`
- `/api/lib`
- `/api/chat-agent`
- `/api/models`, `/api/profiles`, `/api/persona`
- `/api/skills`, `/api/voice`, `/api/elira`, `/api/drift`

Direct public execution/administration routers for Telegram, IT Ops, Terminal,
Tool Registry, Task Planner, pipelines and the old change executor are not
mounted.

## Data ownership

The default root is `data/`, overridden by `ELIRA_DATA_DIR`.

- `workflow_engine.db`: workflow templates/runs/steps/requests/triggers.
- `tool_registry.db`: tool inventory metadata.
- `task_planner.db`: run checklist/subagent records.
- `code_agent_sessions.db`: workspace sessions and task ledger.
- `agent_monitor.db`: metrics/model profiles.
- `event_bus.db`: durable events/messages/subscriptions.
- `it_ops.sqlite3`: IT Ops assets/profiles/evidence.
- `integrations.db`: Telegram configuration/users/log.
- `smart_memory.db` + `rag_memory.db`: facts and semantic memory.
- `library.db`, `projects.db`, `web_corpus.sqlite3`, `elira_state.db`,
  `drift_facts.db`: domain-specific persistence.
- `web_corpus.sqlite3` is a run-scoped, seven-day web-evidence cache populated
  only by `web_fetch(store=true)` and queried by `web_query`; it is not durable
  agent memory.
- `.agent/runs/<run_id>`: code-agent journal.
- `data/resources`: durable raw resources.
- `data/portable_vault.json`: AES-256-GCM portable vault.

These stores are separate because their transaction, retention and trust
boundaries differ. Do not merge them to reduce file count.

## Security versus product blockers

API bearer auth for non-loopback callers, schema validation, secret redaction,
UAC result binding, transport/protocol validation, output truncation and context
compaction remain. They protect interfaces or physical limits; they are not
additional user approvals.

## Verification gate

After a complete refactor run:

```powershell
npm --prefix frontend run typecheck
npm --prefix frontend run build
backend\.venv\Scripts\python.exe -m pytest -q
```
