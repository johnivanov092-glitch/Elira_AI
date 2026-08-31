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
  -> Domain Router + Capability Router + Evidence Router
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
- The UI exposes one profile: `Elira / Auto`. Former profiles are hidden domain
  policies for tone, evidence requirements and useful starter schemas; they do
  not lock or authorize tools. A request may combine multiple domains.
- The first model turn sees the compact built-in core plus deterministic routing:
  Domain Router classifies the task, Capability Router reveals relevant schema
  groups, and Evidence Router reveals Web for current/external facts, unknown
  technologies/errors, finance, medicine, science and security. An external
  first failure, repeated local failure, or an uncertain draft (`не знаю`, `нет
  данных`, `не удалось`) triggers one Web evidence pass before the final answer.
  Local state still comes from local tools; external contracts come from primary
  sources. The model may load further groups with `capability_load`.
- A requested download cannot finalize until an artifact receipt exists. The
  loop requires `resource_publish`, producing the existing clickable UI download
  card instead of printing a Windows path as if it were a link. Every distinct
  successful publication remains available as its own download chip and as an
  item in the preview panel, including repeated publications with the same
  visible filename.
- PDF/DOCX publication is fail-closed: the runtime binds structural checks,
  rendered page count and vision inspection to the exact published SHA-256.
  Failed or incomplete QA emits no download artifact. An exact page count is an
  optional task contract, and model prose cannot upgrade a missing external QA
  receipt to `passed`.
- File reads have a Qwen-specific deterministic recovery layer. A missing,
  truncated `read_file` path may be replaced only by one unique same-directory,
  same-extension prefix match from the immediately preceding `glob`; a third
  identical failed path is refused without another filesystem read. A missing
  project filename may also resolve to one exact attached ResourceRef name and
  is then read through the existing resource extractor without materializing a
  copy. Ambiguous matches fail closed.
- Local price-list assembly/BOM requests cannot finalize on model arithmetic.
  `bom_validate` reads the declared XLSX/CSV columns and deterministically
  validates exact codes, numeric stock, quantities and prices, then calculates
  markup, VAT, services and totals. A successful result is sealed with the
  catalog SHA-256 and an immutable receipt; any failed revalidation revokes it.
  BOM documents are generated from that snapshot rather than model arithmetic,
  and arbitrary prebuilt files are not publishable as the validated BOM. A
  failed validation has no canonical total.
  If the model still reports a mandatory component absent after Library search,
  the Evidence Router requires Web search for a sourced compatible alternative
  before allowing the BOM flow to continue.
- MCP/LSP remain per-run and appear only after a relevant `runtime_control`
  request. Infrastructure intent preloads typed IT Ops and SSH; no MCP is started
  merely because a domain policy matched.
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
  prompt. It is a default cwd/context, not a permission boundary: with no
  connected project, explicit absolute paths still work through the same file,
  map, glob and shell tools. No parallel project-awareness helper exists.
- Active Library files are request-time candidates. Upload/import extracts and
  stores reusable full text (physical cap: 1,000,000 characters); the request
  router uses an FTS5 index in the same `library.db` and injects only up to 10
  relevant excerpts of 2,500 characters. With no match, ordinary chat receives
  no Library text; freshest-file fallback exists only for an explicit
  Library/attachment/document request. `runtime_control` with
  `library_search → library_read` lets the
  agent consume the selected document in repeatable pages of at most 10,000
  characters. The topbar Library can upload directly; a durable chat resource
  is copied into Library only by the explicit save action. This is separate
  from current-chat resources, project RAG and run-scoped web Corpus.
- Project Corpus reuses `rag_memory.db`: `code_agent/indexing.py` discovers one
  repo, monorepo, or a parent containing multiple Git repos; Git provides native
  `.gitignore` semantics. `project_corpus_files` is the resumable file manifest,
  while source-backed `rag_items` rows own chunks, embeddings and
  repo/file/commit/language metadata. A repeated index call skips matching
  SHA-256 hashes, replaces changed source versions, removes stale files, and
  resumes after the per-pass 5000-chunk budget. This is not the run-scoped web
  Corpus and does not introduce a second vector store.
- Workflow exposes that owner through `runtime_control(project_status)` and
  `runtime_control(project_index)`. Both default to the connected project.
  `memory_recall` maps a project path to the same opaque scope ID, so ingestion
  and retrieval cannot silently address different namespaces.
- The memory facade is the application-level boundary over curated facts
  (`smart_memory.db`) and semantic/project records (`rag_memory.db`). A
  correction with an explicit `replaces_id` updates that profile's prior
  curated row in place, including when the wording changes completely.
  `runtime_control(memory_search)` exposes fact IDs so the agent can make that
  replacement deterministic; lexical matching remains only a compatibility
  fallback. Operational `volatile_fact` rows are non-authoritative and
  `memory_prune` removes only aged volatile rows in addition to the existing
  bounded semantic-memory prune.
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

`route_request_capabilities()` is the deterministic preflight. It may combine
code, network, resources, data and Web in one run. `classify_domain_policies()`
returns all matching hidden policies while `resolve_persona_mode()` retains one
dominant prompt overlay for compatibility. Neither function grants permission or
creates an executor/provider. TCP checks use `itops_network_inventory` with an
explicit per-connect timeout and concurrency, not sequential shell
`Test-NetConnection`.

MCP, LSP, SSH shortcuts, Telegram, IT Ops, plugins, Workflow scheduling,
memory/library administration and vault operations are behind the agent's
`runtime_control` tool. It returns a single structured capability envelope:
`completed`, `failed`, `needs_input`, `needs_secret`, `needs_elevation`,
`waiting_approval`, or `cancelled`. Existing domain runtimes remain owners;
`runtime_control` is an adapter, not a second executor or registry.
Telegram uses typed `status/start/users/send/messages` operations; the bot token
is resolved inside the Telegram runtime from the portable vault and is never an
agent argument. Successful outgoing messages are written to the durable
Telegram log, which is also the evidence source for `telegram_messages`.

Long finite commands use the existing `run_server(kind="job")` process runtime.
`start` returns a PID immediately; `list`/`logs` expose running or terminal state
and captured output; `stop` and Workflow Stop kill the owned process tree. Jobs
run through a small child wrapper inside this same runtime. Before `Popen`, the
runtime writes a redacted `starting` journal record; the worker then publishes a
launch sidecar with PID creation identity before executing the raw command and
writes an exit sidecar independently of the backend. The raw command exists only
in the short-lived spec deleted by the worker. On Windows the worker uses a new
process group plus console detachment and, where the host permits it, Job Object
breakaway; therefore a console/backend restart does not kill it, while explicit
Stop still terminates its tree. Production startup reconciles these files
(rejecting PID reuse), reattaches the existing log and restores
`running/completed/failed/cancelled`. Invalid journals are quarantined rather
than silently overwritten. Terminal records have bounded count/TTL and are
machine-local, not portable-vault content. There is no product wall-clock
deadline. Short typed tools stay synchronous; backgrounding does not replace
transport liveness timeouts such as TCP `connect_timeout`.

MCP servers do not auto-start with FastAPI or merely because of a persona. When
the user explicitly requests MCP, or the task requires a configured external
integration absent from active tools, the model uses `mcp_list`, selects one
relevant server, and calls `mcp_start(server_id)`; only that server's schemas are
considered for the next model turn. Small MCP servers remain intact. For a large
server, `McpToolProvider` ranks schemas against the current user task and exposes
only the positive, bounded subset; ambiguous intent deliberately keeps the full
set. The provider retains ownership of every advertised tool, so this is prompt
virtualization, not permission filtering. `mcp_tools(server_id, query)` changes
the visible subset without restarting the server when the model needs another
known operation. The selected query is run-scoped and survives Workflow Resume.
A successful `mcp_start`/`mcp_restart` also returns
the exact namespaced tool count and up to 50 tool names in server-advertised
order. It does not duplicate descriptions or JSON schemas, so the model can call
the right dynamic tool on the next turn without bloating the stable prompt
prefix; `available_tool_names_truncated=true` reports a longer roster. LSP
follows the same per-run rule. `ssh_hosts`
reveals the existing SSH provider and `itops_assets` reveals the IT Ops provider
without turning those discovery calls into authorization gates.

MikroTik onboarding is SSH-only for RouterOS 6 and 7. The model uses
`itops_mikrotik_upsert/list/remove/sync`; router identity, version, SSH target and
key path live as a global `network_device` asset plus an `ssh` connection profile
in `it_ops.sqlite3`. `itops_mikrotik_inventory` executes one fixed read-only
RouterOS CLI plan through the canonical SSH provider and records evidence.
Arbitrary RouterOS CLI uses the same `ssh_run`; command calls use `ssh -n`, a
connect-only timeout and Workflow Stop for execution cancellation. Registered
RouterOS 6 targets receive only the required legacy MAC/RSA options without weakening
other SSH targets. The retired `mikrotik` MCP server and generated
`data/mikromcp/routers.yaml` are removed during migration/sync.
An opaque legacy password reference may be preserved during migration for
recoverability, but typed SSH never uses it; authentication is key/ssh-agent only.
Known blocking SSH waits (`Start-Process -Wait`, `WaitForExit`,
`WaitForStatus`, `Wait-Process`, service-control cmdlets and sleeps of at least
30 seconds) are intercepted before the synchronous SSH process starts. The
typed `ssh_run_ps` path wraps the original encoded PowerShell in a managed
remote child, transfers the SSH argv to canonical `run_server(kind="job")`
without local-shell reparsing, and records the local job PID plus the remote
Windows PID and process-start identity. The model receives structured poll and
cancel calls. `run_server` logs/list recover this metadata after a backend
restart; explicit stop verifies the same remote process identity, enumerates its
descendants, and confirms bounded process-tree cleanup through `taskkill /T /F`
before closing the local SSH job. Workflow Stop attempts the same bounded
cleanup before forcing local teardown and persists its status for later
logs/list inspection. Raw `ssh_run` PowerShell waits are
rejected with a structured `ssh_run_ps` recommendation so quoting and remote
cleanup are not lost; blocking POSIX commands retain argv-safe background
transfer. Low-level callers without project/job context receive
`needs_background`.

The read-only LSP stdio client answers server-side configuration/progress
requests, canonicalizes equivalent Windows file URI spellings and waits past an
empty warm-up diagnostics push. Repeated checks do not send an invalid second
`didOpen`; changed snapshots are close/open notifications, not edit operations.
Explicit stop and backend shutdown kill the complete child process tree.

Workflow Stop is race-safe across process creation. The per-run cancelled
marker and Popen registration share one lock; if Stop arrives after
`tool_started` but before registration, the new process tree is killed as soon
as it registers. MCP and LSP stdio children use the same run ownership, while
provider-level cancellation callbacks close active HTTP/JSON-RPC transports;
the UI does not acknowledge cancellation while a detached transport continues
working. Durable Resume reuses the persisted run ID only after this cleanup.

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
- The final llama.cpp SSE usage/timings event is preserved as
  `cached_prompt_tokens`, cache hit ratio, model TTFT, and separate prompt/output
  tokens per second; Workflow usage events expose the same values to UI/evals.
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
- `smart_memory.db` + `rag_memory.db`: facts, semantic memory and Project Corpus
  chunks/file manifest.
- `library.db`, `projects.db`, `web_corpus.sqlite3`, `elira_state.db`,
  `drift_facts.db`: domain-specific persistence.
- `web_corpus.sqlite3` is a run-scoped, seven-day web-evidence cache populated
  only by `web_fetch(store=true)` and queried by `web_query`; it is not durable
  agent memory.
- `.agent/runs/<run_id>`: code-agent journal.
- `data/resources`: durable raw resources.
- `data/background_jobs/`: machine-local finite-job journal, short-lived specs,
  launch identities and exit sidecars; logs remain under the owning project's
  `.elira/servers/`.
- `data/portable_vault.json`: AES-256-GCM portable vault. User-triggered backup
  includes `smart_memory.db` and `rag_memory.db` (therefore Project Corpus) but
  excludes the expiring `web_corpus.sqlite3` cache.

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
