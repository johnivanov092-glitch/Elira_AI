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
  -> capability_load + task guidance + Evidence Router
  -> agent_kernel.executor
  -> runtime_registry
  -> Builtin | SSH | IT Ops | MCP provider
  -> OS / LAN / files / subprocesses

Workflow request
  <- input | secret | elevation | approval
  <- workflow_engine.db + /api/agent-os/events/stream
```

Workflow `agent` steps use `application/workflows` as a coordinator but every
step returns to the same `run_code_agent`, executor and provider registry. The
multi-agent mode (Composer «Планирование/Саморевью», `/api/advanced/multi-agent`)
was removed on 2026-10-07; a sub-agent is the `delegate_task` tool the main
agent calls itself.

Managed application updates use `scripts/elira_release.py` to prepare and verify
a backend/UI candidate, then switch after active work drains, with restart and
pre-admission recovery. The Windows Foundation adapter runs this same release
manager in a protected LocalService installation; application commands run with
the authenticated interactive user's limited token. Service code/state and
published releases are separate from editable candidates. The agent loop remains
part of the replaceable application. Legacy same-user supervision remains
available without a Foundation installation and does not provide this OS
boundary. Source implementation, isolated Windows proof and production deployment
are separate statuses; see `RELEASE_LIFECYCLE.md` and
`research/FOUNDATION_WINDOWS_ACCEPTANCE_RU.md`.

The disconnected `domain/agents` V8 graph runtime and `application/project_brain`
chat/LLM chain were removed after a caller audit.

## Agent loop module boundaries

`application/code_agent/agent_loop.py` remains the single coordinator and public
entry point. Its `stream_code_agent` journal adapter and `run_code_agent` sync
collector preserve the existing callers and event/result contracts. Seven leaf
modules own narrower parts of that run; none imports `agent_loop` or starts a
second agent, executor, provider registry or persistence layer.

| Module in `application/code_agent/` | Responsibility |
|---|---|
| `run_control.py` | Live-run registration, upstream cancellation, cleanup and Workflow response rendezvous |
| `model_turn.py` | One model exchange, stream/non-stream events, reasoning and heartbeat |
| `runtime_activation.py` | Per-run integration/group visibility and schemas from the existing registry |
| `turn_context.py` | Initial/history messages, loaded skills, live user inputs, pinned guidance and packing through the existing compactor |
| `tool_execution.py` | One selected call through the existing executor and its Workflow branches |
| `run_observations.py` | One instance each of the existing evidence, outcome, command-recovery and criterion owners |
| `answer_acceptance.py` | Ordered final-answer checks and one-time correction state; the coordinator schedules any next model turn |

The coordinator retains the observable boundaries. Verification captures the
pre-dispatch state and refreshes it after an approval wait before retrying the
executor. Canonical evidence, input epoch, outcome/recovery snapshots and criterion
invalidation precede result yields; the late criterion verdict and tool-message
append follow them. A consumer closing at that yield must not run the later
stage. `code_input_epoch`, `project_epoch` and `criteria_epoch` keep their distinct
meanings and journal keys. Runtime Workflow pauses retain their separate branch.

Workflow waits observe both a response and Stop. The `ask_user` branch preserves
an accepted `workflow_input` before reporting a simultaneous cancellation;
approval and other branches retain their cancellation-first behavior. Session
Stop between delivery slices stays with `delivery_session`; live cancellation
tries every cleanup stage and reports surviving resources before acknowledging
Stop. This split adds no run deadline, permissions or new stored format.

Stream adapters explicitly close their owned iterators: the journal adapter
closes the core before finishing its journal, structured delivery closes each
slice, and the Workflow SSE projection closes on terminal output as well as
interruption. Cleanup does not rely on garbage collection or request Stop for
a successfully completed run.

## Runtime invariants

- One agent core: `application/code_agent/agent_loop.py`.
- One tool executor: `application/agent_kernel/executor.py`.
- One provider aggregation path: `application/tool_providers/runtime_registry.py`.
- One durable human control plane: `application/workflows` +
  `workflow_engine.db`.
- The UI exposes one personality: `Elira / Auto`, with one sampling temperature.
  Legacy profiles remain readable but do not switch identity, tone or sampling.
  Their evidence/calculation requirements survive as relevant task instructions.
- Every normal first turn sees eleven work tools from `tool_policy.BASE_TOOLS`
  (read/write/edit_file, glob, grep, run_bash, run_server, web_search, web_fetch,
  todo_update, calc) plus `capability_load` and `ask_user` (track «Elira на
  диете», 2026-10-07; calc stays visible so calculations go through a tool).
  Workflow questions remain available through the runtime.
  Explicit search, attachments and resumed activations may add schemas. The model loads other
  groups through the existing registry; routing hints do not preload them. An external
  first failure, repeated local failure, or a false denial of available Web
  access triggers one evidence pass before acceptance. General uncertainty and
  quoted/code examples do not trigger this answer-based recovery.
  Local state still comes from local tools; external contracts come from primary
  sources. The model may load further groups with `capability_load`.
- Chat file delivery is a fact, not a declaration: actual `resource_publish`
  attempts (failed ones included) establish the targets to check, and a download
  link in the answer must point to published current bytes. Request keywords only
  suggest guidance; a download feature being implemented does not itself require
  a file in the current chat (`task_decide`/`result_verify` removed 2026-10-07).
  `TaskOutcome` binds successful publication to the exact local target (when
  mapped), current SHA-256 and canonical stored download. A generic artifact
  receipt is insufficient; Resume rechecks the files and preserves stale status.
  `resource_publish`, `file_gen` and published local-GPU transcription use this
  owner. Unmapped publications cannot satisfy an arbitrary local target.
  Missing delivery or an unbacked canonical Markdown/autolink gets one correction,
  then a degraded answer with a diagnostic; unsupported links are made non-clickable.
  Ordinary prose is preserved. External source/product links require no local
  artifact. See `ANSWER_CONTRACT.md`. Every distinct
  successful publication remains available as its own download chip and as an
  item in the preview panel, including repeated publications with the same
  visible filename.
  Local GPU transcription also emits a receipt after saving and publishing its
  complete TXT through the same Resource Store publication primitive.
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
  Approximate filename similarity alone never selects another file.
- Uploaded ResourceRef IDs persist on their original user turns through session
  save/reload and follow-up requests. Historical metadata is resolved from the
  Resource Store and framed as untrusted attachment data; file contents are not
  eagerly injected. Missing resources are explicit, without filename substitution.
- Price-list specifications/BOM are checked by the factory skill `bom-check`
  (script over the declared XLSX/CSV columns: exact codes, stock, prices, markup,
  VAT 16% by default, totals); the model takes numbers from its output.
- MCP is per-run: group `mcp` (tool `mcp`, actions list/start/stop/restart/tools/
  add/remove), and a server's schemas appear after `mcp(action=start)`. Each
  server has an instruction skill `<id>-mcp`; the skill catalog is the MCP catalog. SSH and IT Ops are groups
  `ssh`/`itops` loaded through `capability_load`; domain routing hints neither
  preload schemas nor start an MCP server. LSP and plugins were removed.
- All conversation and work use one compact stable base system prompt. There
  is no greeting/compliment regex or separate conversational path.
- Role contract (owner's decision 2026-10-06): the `user` role carries only the
  owner's own UI text, the `assistant` role only real model replies, `tool` only
  tool results. Runtime-authored text — request context (date, workspace,
  personal memory, resources), task contract and state, guidance, skills, plan,
  restored excerpts, corrections, Resume instructions, prior-turn facts and
  summaries — is marked `_runtime_block` in the internal list and
  `history.project_runtime_roles` moves it into a `[РАБОЧИЙ КОНТЕКСТ RUNTIME]`
  section at the end of the single leading system message right before each
  provider call. Untrusted text (project files, web excerpts) keeps its explicit
  untrusted label there and never enters the base prompt. A rejected answer is
  quoted inside its correction, never left as the model's last reply. Runtime
  blocks are replaced in place so an unchanged block keeps the system prefix.
- The base tools are visible from the first turn (`tool_policy.BASE_TOOLS`).
  Their work and source-verification instructions are runtime blocks in the
  system section.
- Math contour (owner's decision 2026-10-06): the rule "check calculations with
  a tool" is executed with read-only tools (`side_effect=False`, no approval
  card in "ask"): `calc` (exact decimal/rational arithmetic, percentages,
  algebra via SymPy built from an AST allowlist — input is never evaluated as
  code; always in the base), group `math` with `unit_convert`, `finance_calc`
  (invoices, VAT, markup vs margin, discounts, loans, splits) and `csv` filters/aggregates
  (its old `eval` of the question was removed). Shared Decimal parsing lives in
  `application/calculation/numbers.py`. Scripts
  remain for complex modelling and ask for approval in "ask" mode.
  Correct task execution takes priority over short-chat TTFT; the discovery-only
  default was reverted on 2026-09-06 after a live current-events refusal.
- Task guidance arrives with tools, including Web and external MCP/SSH/IT Ops
  providers. Common verification rules and project instructions apply regardless
  of transport. Generated guidance messages use the existing compactor's pinned
  IDs; both summarization and its fallback preserve them and count them against
  the context budget. They never become a second system prefix.
  Work/project guidance distinguishes immediately usable data, published durable
  skill/MCP packages, and application candidates. It is rebuilt before Resume
  inference: verify against copied data, request a durable installation proposal,
  then finish the run. Only the user's explicit confirmation makes it pending;
  deferral, idle and restart never confirm it. The agent cannot confirm its own
  proposal or simulate the user's click. Confirmed installation waits for drain,
  then activates on current shared DATA; startup rollback precedes admission and
  needs no additional confirmation.
  Build/verification alone never establishes installation, and active application
  environments are not dependency-install targets.
- Web fetching keeps HTTP status, final URL and truncation separate from page
  text. Short fields and requested anchor sections survive extraction; failed
  HTTP responses cannot become successful excerpts through browser fallback.
  Corpus queries with no lexical matches return `no_results`, not arbitrary
  chunks. Publication/modification metadata is separate from retrieval time.
  Web search uses only the configured SearXNG endpoint, including news and
  encyclopedia queries; SearXNG owns upstream engine selection. Missing
  configuration and transport failures are explicit errors. Partial query
  batches retain successful results and expose the failed queries.
  A bounded candidate pool is filtered by explicit site constraints and ranked
  before the requested result cap; category fallback uses the same ranking.
  Sparse or failing first-page news results get one general-category attempt
  through the same SearXNG endpoint with the same query and time range. Engine
  warnings survive both successful and failed batches through answer acceptance.
  Search mode starts with `capability_load`, `web_search`, and `web_fetch`;
  other capabilities load on demand. Work guidance follows actual project
  discovery, mutations, code sources or a publication, not visible schemas.
  Plain web/file/API answers declare nothing, including full-machine.
  Missing excerpts after compaction/Resume can be restored from the evidence
  ledger without another request or a recovery refusal. Another diagnostic can
  permit a bounded retry of a failed operation, not an already successful search
  or read. Distinct arguments/inputs remain available. No runtime cue follows a
  read: a trailing user-role "next step, do not output" block made Qwen3.8 treat
  its finished answer as reasoning and write it twice (removed 2026-10-06).
  File/action goals continue to delivery.
  Passive failed browser reads are guarded individually;
  interactive browser access remains visible.
  Source availability uses the existing web-corpus SQLite store, separately
  from training labels and answer evidence. A transient failure pauses for one
  minute; confirmed failures defer demand-driven probes for 1, 2, and 4 weeks,
  then suspend automatic retries. Success resets the history. No background
  crawler is scheduled. HTTP and browser histories are separate; missing paths
  do not block a domain, and cross-origin redirect failures remain path scoped.
  Rate limits respect Retry-After and never classify a site as permanently dead.
  An access wall (HTTP 200 with a login redirect, anti-bot check or JS stub)
  is a failure of that page only (`access_wall`) on the same schedule
  (decision 2026-10-06), so a walled thread is not re-read in every run.
  Per-source probe leases prevent concurrent escalation; explicit force_refresh
  permits a requested recheck. Stored metadata is bounded to 10,000 host/hash
  records, contains no page/query text, and respects task memory permissions.
  Repeated identical web results trigger local recovery for that operation.
  A short notice points to the previous result; other queries and sources stay
  available through the same tools. Recovery requires no intermediate model
  summary. JSON-encoded query/URL arrays are normalized before repeat checks
  and execution; invalid batch formats receive an explicit format error.
  Recovery notices count model decisions,
  not duplicate calls within one batch; independent later calls remain available.
  Four ignored notices for the identical operation leave the task resumable
  and explicitly incomplete, with the collected sources preserved. Repeat state
  survives Resume and model task-plan changes. There is no global step or time
  limit, and search recovery never marks an artifact task complete.
  Search/fetch batches accept up to 30 items with at most five workers; search
  allows up to 30 results per query. Fetch extraction defaults to 16000
  characters per page and caps at 80000. Search/fetch tool messages have a
  120000-character presentation ceiling in native tools, executor registry and
  model messages; the existing context budget can reduce oversized messages.
  Batch fetches divide space between pages,
  keeping exact excerpts and link targets. Complete stored pages remain
  available through the existing web corpus and `web_query` when task memory
  policy permits the seven-day cache. Verified snippets, not stored passports,
  prove reading. The source registry holds 1024 receipts for 30×30 search batches.
  Per-query source bindings and upstream engine warnings survive the facade,
  native tools and SSE journal. A failed engine does not discard usable results;
  no results with upstream failures is an explicit failure. Actual engine errors
  also produce a deterministic technical notice in the final answer before
  length/language/outcome verification and answer hashing, including failures
  with no results. It adds no discovery receipts or new correction budget.
- Exact source receipts already in tool history are not duplicated. Missing
  excerpts are appended within a 7000-character restoration budget; compaction
  rebuilds that snapshot before context-budget accounting. This preserves the
  ordinary request prefix and does not add model calls or semantic validation.
  Restored excerpts remain labelled untrusted data in the runtime section, never
  an assistant-answer prefill nor text in the owner's name.
  An explicit user quote-word limit is checked at answer acceptance; numeric
  word-count labels are computed from the unchanged quote. One format correction
  is allowed and persists across Resume; repeated overflow is degraded.
  Recognised numeric update periods also require a local citation and the same
  quantity/unit in its presented excerpt. One correction persists across Resume;
  repeated failure is degraded. This is not semantic entailment validation.
- Ordinary read-only Web answers also check Markdown links against presented
  excerpts. A factual paragraph cannot call a discovered or failed page verified;
  one correction persists across Resume and repeated failure removes that body.
  The production completion path adds no private model review: provenance checks
  do not prove topic relevance or factual entailment. Those are independently
  assessed against the actual read excerpts during acceptance; the same model's
  assessment cannot certify factual truth or replace a valid answer with a refusal.
- Skills are one plain folder, `data/skills/<name>/` (SKILL.md, scripts, optional
  .venv); the model reads, writes and runs them with its ordinary tools. The
  runtime lists the catalog (name, description, path) in the prompt, pins a
  SKILL.md the model read or edited (one pinned block survives compaction;
  active_skills restores it on Resume) and commits the folder's git history.
  Built-ins are seeded from `skills/` once and never overwritten. No skill changes
  persona or grants permissions. See TASK_SKILLS.md.
- File working set (`code_agent/working_set.py`): the run records files it read
  or changed. A repeated read_file of an unchanged file, while the earlier result
  is still in context, returns a short "not changed, see above" note. Compaction
  rebuilds one pinned block listing those files with the current text of the most
  recently changed ones (windows of 16K+, budget num_ctx/4 characters); it
  travels as tool data like restored excerpts. Observation only: no tool call is
  executed or blocked.
- The primary Qwen agent interprets requests and selects tools from the existing
  catalog. Compatibility heuristics supply
  guidance and specific evidence recovery, not an independent intent classifier.
  Exact download and BOM delivery contracts remain deterministic; they do not
  grant permissions. The rejected Laya experiment is retained only in Git and
  `research/LAYA_CPU_EVALUATION_RU.md`. Embeddings retain their existing model.
- Confirmed llama.cpp backends use DRY sampling with a bounded 1024-token lookback and permit 12-token repeats.
  Short replies can still overlap history; the old 2-token allowance caused
  repeated identity answers to mutate the name. Long prose repetition retains
  DRY protection without a conversation-specific sampler or phrase exceptions.
  vLLM receives only its supported explicitly configured sampling parameters;
  DRY and llama.cpp repeat settings are omitted and logged, never translated into
  a different penalty. Unknown backends do not receive backend-specific extras.
- There is no transient persona mood (removed 2026-10-06 by the owner's
  decision): tone comes only from the selected persona mode, and the runtime
  never appends a tone line to the user's message.
- No tool/path/asset/LAN authorization scope, internal ApprovalStore,
  feature dispatch gate, max steps or run deadline.
- Healthy runs end through a natural answer or Workflow Stop. Provider, OS,
  protocol and physical context-window failures remain real errors.
- Three identical terminal command results for unchanged inputs require new
  diagnostic evidence before another execution. Two unchanged recovery probes
  or two ignored recovery requests end the run as resumable `blocked`. The
  existing journal preserves this state through Resume. Polling an existing job
  is not another execution; changed inputs or results allow work to continue.
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
  Library/attachment/document request. The `library` tool
  (`search → read`) lets the
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
  All chunks of one source are prepared before publication; chunks and manifest
  are replaced in one SQLite transaction after rechecking the source hash.
  Failure preserves a complete previously indexed version, or records `failed`
  when none exists. Retrieval reads one database snapshot and excludes managed
  chunks whose hash/status disagree with the manifest. Atomicity is per source,
  not across the entire corpus; freshness requires another indexing pass.
- The agent reaches that owner through `recall(action=status|index|search)`
  (group `project`). All default to the connected project and map it to the
  same opaque scope ID, so ingestion and retrieval cannot address different
  namespaces.
- The memory facade is the application-level boundary over curated facts
  (`smart_memory.db`) and semantic/project records (`rag_memory.db`). A
  correction with an explicit `replaces_id` can update that profile's prior
  curated row in place, subject to provenance and compare-and-swap checks.
  `memory(action=search)` exposes fact IDs so the agent can make that
  replacement deterministic; lexical matching remains only a compatibility
  fallback. Before the first model call, the harness sends each substantive user
  request through `memory.resolve_relevant_facts`, using a separate raw
  `memory_query` before attachments/Library enrichment. Resume preserves this
  field; old journals and internal callers without it disable automatic recall.
  Capability guidance also uses this raw text when provided. Retrieved documents
  do not establish a delivery contract or activate SSH schemas.
  Legacy callers without the field keep routing from their explicit task text.
  The compatibility `run_agent` adapter requires this field explicitly; Telegram
  supplies it only after its existing allowlist and memory-setting checks.
  Prompt injection is then gated by user/work context or an exact stored entity
  (people, clients, companies, projects and
  servers). Exact entities rank above contextual matches and the injected block
  is bounded. Operational `volatile_fact` rows and stored harness-behaviour
  rules are non-authoritative. Legacy rows saved by the retired runtime_control remain
  subject to prompt-override filtering, and selected rows are JSON-encoded before
  inclusion in the prompt. Legacy rows are readable only through this relevance
  gate. Model-authored `memory(action=add)` defaults to `agent_note`, with a run
  reference when available. Only the literal current `memory_query`, or the exact
  remainder of its explicit remember command, receives server-owned
  `user_command`/`user_correction` provenance. Paraphrases and a correction id
  cannot elevate trust. Agent notes cannot replace trusted user
  rows; dedup keeps their origins separate. Manual user APIs retain their existing
  contract, and historical rows are not relabelled automatically.
  Automatic retrieval never implies automatic write. `memory_prune` removes only aged
  volatile rows in addition to the existing bounded semantic-memory prune.
- Legacy ToolSpec policy columns are inventory compatibility only.

## Permission selector

The Workflow run owns one mode:

- `ask`: read-only calls run; mutations ask in Workflow UI.
- `accept_edits`: ordinary local mutations run; high/unknown-impact calls ask.
- `bypass`: no Workflow approval request for tool execution.

`impact_policy.py` classifies calls only for this UI decision. It never blocks a
call independently. `bypass` does not create a Windows administrator token.
Application installation still requires the user's explicit confirmation of the
specific verified release proposal; Workflow mode does not supply that consent.

## Workflow requests

Public kinds are `input`, `secret`, `elevation`, and `approval`. Requests are
durable and resume the same workflow run and step. Plaintext secrets are never a
valid resolution; the payload contains only an opaque `secret_ref`.

Accepted `ask_user` clarifications emit a typed `workflow_input` on the tool
event and persist in the existing run journal and session turn. Follow-up turns
and Resume replay these question/answer pairs as user input, not verified tool
facts. Secret/elevation responses and declined inputs are excluded.

Windows elevation is executed by the Tauri native bridge in
`src-tauri/src/main.rs`, which opens UAC and binds the result to the Workflow
request. The backend does not require permanent administrator rights.

Compute placement is chosen by the agent from the task and observed client
hardware/runtime. An explicit user device is passed as a strict target; delegated
choice or `bypass` does not trigger a mandatory placement question. The loop no
longer rewrites `resource_process` into `ask_user` or extracts device intent with
regular expressions. Ordinary `ask_user` remains available for material ambiguity.
`resource_process` with `auto` considers only `local_gpu -> local_cpu`; it never
probes or falls back to the voice server. An explicit `server_cpu` call remains
supported, and legacy `server_gpu` input normalizes to that canonical target.
The `/api/voice` STT/TTS conversation path is unchanged. For user workloads the
agent first inspects local hardware, favors repairing/installing GPU support for
long audio, and may build and use a missing processor through the existing
materialize/file/shell/MCP tools. Server infrastructure is a separately considered
option when suitable alternatives are unavailable.

Local GPU transcription consumes all decoded segments without a character cap.
It saves the complete UTF-8 TXT as a ResourceRef and downloadable artifact before
returning an explicitly labelled preview of at most 8,000 characters. The result
includes the full character count, resource ID, download URL and SHA-256;
`resource_materialize` exposes the complete text for further processing. CPU and
server transcription retain their existing limits. GPU power settings are not
part of this processing contract.

## Integration boundary

Built-in schema composition is owned by
`application/code_agent/capabilities.py`. `capability_load(group)` validates a
model-selected group, then the existing runtime registry is rebuilt for the
next model turn. Loaded groups are journalled for Resume/automatic continuation;
a new run starts with `tool_policy.BASE_TOOLS` (ordinary work tools), unless a
caller explicitly supplies a different `base_tools` set. This is prompt composition, not
authorization: the Workflow permission selector remains the only product-level
permission decision. A hidden built-in schema does not remove its canonical
dispatch owner; if a valid native/inline call reaches the runtime, it still uses
the same ToolExecutor and handler.

`route_request_capabilities()` supplies task/evidence and file-delivery hints only;
`task_outcomes.py` owns model-declared delivery and current publication receipts.
Legacy domain metadata in `DOMAIN_CAPABILITY_GROUPS` remains a compatibility
hint, not a persona switch or schema-activation policy. The HTTP adapter's
`application/chat/local_chat.py:resolve_persona_mode()` returns `DEFAULT_PROFILE`,
which the agent loop also applies. No hint grants permission
or creates an executor/provider. TCP checks use `itops_network_inventory` with an
explicit per-connect timeout and concurrency, not sequential shell
`Test-NetConnection`.

Integrations are small on-demand groups (track «Elira на диете», 2026-10-07):
`mcp` (tool `mcp`), `ssh`, `itops` (`itops_registry`
plus typed `itops_*`), `telegram` (`telegram` send/messages) and `memory`
(`memory`, `library`). They share one structured envelope: `completed`, `failed`,
`needs_input`, `needs_secret`, `needs_elevation`, `waiting_approval`, or
`cancelled`; the agent loop turns a needs_* status of any tool into a Workflow
card. Existing domain runtimes remain owners; the tools are adapters, not a
second executor or registry. Telegram bot setup and the secrets vault are UI
settings (Settings → Telegram, Settings → Секреты); LSP, plugins and the
Workflow template/trigger operations were removed from the agent's tools.
The `telegram` tool sends and reads messages; the bot token
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
prefix; `available_tool_names_truncated=true` reports a longer roster.
`capability_load(ssh)` and `capability_load(itops)` reveal the SSH and IT Ops
providers without turning those loads into authorization gates.

`data/mcp_servers.json` is the single source of truth for MCP servers (optional
fields `description`, `cwd`, `skill`; secrets only as `sref_` references). It is
read on every call, so an edit by the model, the user or Settings applies without
a release; `start_server` restarts a live process whose entry changed, and a
server with `enabled: false` does not start. Settings → MCP shows status and
description, starts/stops, switches a server on/off, edits one entry as JSON
(secret values masked; a masked value keeps the stored one; a new plain-text
credential is refused), adds and deletes entries. `api/routes/mcp_routes.py`
uses the same `tool_mcp` and `mcp_runtime`. Starting a process in Settings does
not preload its schemas into every agent run; HTTP stop disconnects the local
MCP client rather than stopping the remote service. New servers are installed
into `data/mcp/<id>/` with their own environment (factory skill `mcp-install`).

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

Workflow Stop is race-safe across process creation. The per-run cancelled
marker and Popen registration share one lock; if Stop arrives after
`tool_started` but before registration, the new process tree is killed as soon
as it registers. MCP stdio children use the same run ownership, while
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
- Model payloads contain exactly one leading `system` message (strict Qwen
  templates reject late system messages). Summaries, verified facts and previous
  tool output are runtime blocks projected into its runtime section.
- Every chat payload sets `cache_prompt: true`.
- The final llama.cpp SSE usage/timings event is preserved as
  `cached_prompt_tokens`, cache hit ratio, model TTFT, and separate prompt/output
  tokens per second; Workflow usage events expose the same values to UI/evals.
- Reasoning modes are `none`, `low`, `medium`, `xhigh`.
- Qwen reads `enable_thinking` + `reasoning_effort`.
- Public `none` fully disables Qwen thinking; the other levels enable it.
- Qwen MTP is a server-side acceleration mechanism independent of reasoning.

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
