# Agent Workflow Live Evals

Current routing belongs to the main Qwen agent: it chooses skills and capability
groups through the existing runtime. Laya and the old semantic prefill classifier
are not active. `agent_loop.py` uses the canonical `DEFAULT_PROFILE`; compatibility
domain hints are not independently selected personas or permissions.

The 2026-10-08 migration uses the canonical `Баланс` profile and an initially
empty capability/MCP/SSH/IT Ops activation state. The model loads each needed
group explicitly. The active inventory is 32 live-agent cases, three separate
controller round-trips and three Workflow UI contracts in pytest. These preserve
38 original capabilities, not 38 observed model successes. No fresh live
routing-suite PASS is claimed here. Dated results below preserve the earlier
architecture and their original scope.

Persona, streaming and structured-source diagnostics are documented separately
in [ANSWER_CONTRACT.md](ANSWER_CONTRACT.md). URL overlap in routing evals proves
neither excerpt delivery nor semantic support; the reporter exposes runtime
citations and `claim_support=not_assessed` on separate fields.

The routing eval suite verifies the real user path, not an isolated model call:

```text
CLI case
  -> POST /api/code-agent/stream
  -> request normalization / compatibility hints
  -> agent loop
  -> capability / builtin / IT Ops / MCP tools
  -> public SSE events
  -> contract checks + report
```

## Run

Start the normal backend, then run from the repository root:

```powershell
backend\.venv\Scripts\python.exe -u backend\tests\smokes\routing_eval.py
```

Run selected cases:

```powershell
backend\.venv\Scripts\python.exe -u backend\tests\smokes\routing_eval.py `
  --only engineering_project,mcp_context7
```

Run one named Harness (safe cases only by default):

```powershell
backend\.venv\Scripts\python.exe -u backend\tests\smokes\routing_eval.py --suite core
backend\.venv\Scripts\python.exe -u backend\tests\smokes\routing_eval.py --suite integration
backend\.venv\Scripts\python.exe -u backend\tests\smokes\routing_eval.py --suite itops
```

Add `--include-opt-in` for stateful/external acceptance. The suites are:

- `core`: absolute path with no connected project, relative paths in a
  connected project, memory round-trip, Workflow approval, Stop → Resume, and
  grounded action claims;
- `integration`: Project RAG/Corpus, web Corpus and MCP;
- `itops`: typed TCP inventory, SSH discovery/Linux/Windows, and Workflow Stop
  of a command that does not return.

There is no MikroTik-specific Harness. Router targets use the same generic SSH
tools when an ordinary SSH case is appropriate.

Vault restore, Library management and Telegram setup use a separate offline
controller entrypoint described below. They are not selected by these live
suite commands. Built-in LSP and its three cases were removed under the approved
architecture; the Serena MCP case exercises its own read-only scenario and does
not claim equivalent Python/TypeScript/Rust diagnostics coverage.

Harness prompts state the user outcome, not exact JSON arguments or a scripted
tool recipe. The exact evidence contract is omitted from the task prompt:
required successful operations, forbidden mutations, lifecycle order and grounded
final claims. Evaluator sources in this repository are accessible; omission is
not OS-level isolation or proof of a blind test. Record observed exposure in a
live acceptance run. When the model repeatedly emits a coherent alternative argument shape,
the canonical adapter normalizes that shape at its existing seam. The Harness
does not teach a production model test-specific calls and does not add a
repetition/step guard.

Stateful/external cases are opt-in. Select them explicitly with `--only` or
use `--include-opt-in`; they never enter the default suite accidentally.

Audit the last two already-finished Workflow runs without calling the model
again:

```powershell
backend\.venv\Scripts\python.exe -u backend\tests\smokes\journal_audit.py --last 2
```

Audit exact durable run IDs:

```powershell
backend\.venv\Scripts\python.exe -u backend\tests\smokes\journal_audit.py `
  --run-id <run-id-1> --run-id <run-id-2>
```

This replay path uses the same `summarize_events` reporter as live routing
evals. It adds terminal/resumable state, failed tools, suspicious `ERROR:`
results marked successful, context/tool-token growth and maximum model TTFT.
Reports are written under `.agent/evals/run-audit/<timestamp>/`.

The default client deadline is disabled. A run continues until the agent
finishes, the Workflow Stop path cancels it, or a lower-level transport/tool
failure occurs. `--timeout N` adds an explicit eval-client socket deadline when
one is required for CI.

## Coverage

The 32 cases in `backend/tests/smokes/routing_cases.json` cover:

- the canonical `Баланс` profile, without domain-selected permissions;
- initial and dynamically loaded capability groups;
- project-file reading and exact answer grounding;
- one-off work on an explicit absolute path with no project connected;
- durable finite-job start plus polling to `completed` through `run_server`;
- downloadable document generation;
- typed TCP inventory without a shell fallback;
- web research for scientific and medical requests;
- on-demand `capability_load(group=mcp)` and `mcp(action=list/start/restart/stop)`
  with actual server tools;
- the negative MCP case: an ordinary chat must not load or start an integration;
- scripted Workflow UI approval resolution;
- user-memory search/list and add/search/delete through `memory`;
- Project Corpus status/index/search through `recall`;
- Workflow Stop followed by durable Resume of the same run;
- Microsoft Docs HTTP MCP and Serena stdio MCP dynamic tools;
- opt-in read-only GitHub, Hugging Face, Playwright, Paper Search and Home
  Assistant MCP cycles, plus transport-only Unity/Blender checks;
- SSH discovery after `capability_load(group=ssh)`, and Linux/Windows commands
  through configured shortcuts.

Each case can assert the effective profile, initial/final runtime activation,
successful ordered tool sequences, forbidden calls, MCP servers,
answer fragments, action claims grounded by successful tool results, citations returned by source tools, network states grounded
in typed inventory output, background-job action/kind/status, and the maximum
number of tool calls.

`required_tool_sequence` is an ordered subsequence of successful results. Its
match fields include `tool`/`tool_prefix`, `group`, `action`, `kind` and `server_id`;
a failed result or an `ERROR:` result cannot prove an action. A
`forbidden_tool_calls` match rejects the attempt regardless of success. Legacy
`runtime_control` operation fields remain for reading older reports, not for
modern case expectations.

The 16 direct migrations retain their original tool budgets. The table counts
mandatory successful calls, including capability loading; skill reads and any
extra provider calls must fit the remaining allowance.

| Cases | Mandatory calls | Maximum calls |
|---|---:|---:|
| `mcp_context7` | 5 | 10 |
| `mcp_microsoft_docs`, `mcp_serena` | 5 | 8 |
| MCP restart cycles: GitHub, Hugging Face, Playwright, Paper Search, Home Assistant, DBHub, Unity transport, Blender transport | 7 | 9 |
| `mcp_unity_editor_readonly` | 4 | 7 |
| `personal_memory` | 3 | 4 |
| `memory_roundtrip` | 4 | 12 |
| `project_corpus_workflow` | 4 | 5 |
| `ssh_discovery` | 2 | 4 |

`personal_memory` explicitly requests two outcomes: search saved answer-style
preferences and separately list recent user facts. Thus search and list are
both required even when search finds a preference. The contract does not infer
an empty result from localized tool text or force a list against a conditional
task instruction.

The `background_job_durable` case seeds its command as a project file, so the
eval measures routing and process lifecycle instead of model-sensitive shell
quoting. A full restart acceptance additionally follows this public path:

```text
Workflow A → run_server(start, kind=job) → running/PID/log
backend stops while PID is alive
  → detached worker continues
backend starts → startup reconciliation → running + recovered=true
Workflow B → run_server(logs, same PID) → completed + exit=0 + final marker
```

The 2026-08-24 Qwen acceptance passed both Workflow runs and the standard
`background_job_durable` routing case. Local reports remain under the ignored
`.agent/evals/background-job-restart-live/` and `.agent/evals/agent-routing/`
trees.

Scripted responses are resolved only through the public Workflow API. Secret
scripts may contain only an opaque `secret_ref`; the Harness rejects plaintext.
It cannot fake a successful UAC elevation, so unattended elevation cases use
decline/cancel. `resume_after_stop` records the first cancelled terminal event,
then calls the public Resume endpoint and evaluates the final stream.

The following dated results use pre-migration tool names and profiles. They do
not certify the current tool interface. The 2026-08-24 provider acceptance used
the primary Qwen model and an isolated
backend/data directory. Reports are under ignored
`.agent/evals/final-runtime-live/`. Project Corpus, Microsoft Docs/Serena MCP,
all three LSPs, vault restore and Stop→Resume passed. The live runs found and
fixed Windows diagnostics URI matching and a Stop-before-Popen-registration
race; final process-tree post-checks were clean.

The follow-up MCP certification on 2026-08-24 exercised each configured server
individually, never as a blanket startup. GitHub, Hugging Face, Playwright,
Paper Search and Home Assistant passed `start → read-only tool → restart →
repeat → stop`. Unity passed the same transport/request-context cycle with no
active editor instance. Blender transport/status passed and correctly reported
that Blender/addon was not connected, so scene operations remain an editor-open
acceptance. DBHub 1.2.1 passed the full cycle against its isolated demo SQLite
source (`SELECT 1` only); the user's configured `dbhub.toml` still contains two
literal `ХОСТ` placeholders and therefore cannot pass production connection
acceptance. Repeatable external cases are opt-in in `routing_cases.json`; the
DBHub case remains an expected preflight failure until real DSNs replace those
placeholders.

The 2026-08-27 editor-bound acceptance ran after the user opened both editors.
Unity read the real five-message Console tail and stopped its MCP with no editor
mutation. Blender remained connected before and after MCP restart, read the
unchanged `Scene` (`Cube`, `Light`, `Camera`) and stopped cleanly. Both routed
through `Elira / Auto → Инженерный` and used only model-selected tools. Large-MCP
schema routing reduced the Unity maximum tool context from 31,058 to 11,917
tokens, maximum total context from 36,923 to 17,881, worst model TTFT from
129.8 s to 54.3 s and end-to-end duration from 197.8 s to 127.7 s while keeping
the same `mcp_list → mcp_start → unity__read_console → mcp_stop` behavior.

At the dated acceptance above, external blockers included DBHub DSN placeholders
and a locked portable vault for Telegram. This documentation review did not
retest those external installations. Offline/provider tests do not establish
their current connection or vault state.

## Observability and reports

The driver derives results only from the public Workflow SSE contract. It
records the effective profile, initial/dynamic/final runtime activation,
successful and failed tools, ordered MCP lifecycle operations, answer/source
URLs, typed network observations, stop reason, Workflow/model TTFT, duration,
token usage, cached prompt tokens, cache hit ratio, and separate server
prompt/output throughput.

Durable journals now preserve numeric `cached_prompt_tokens` and
`prompt_tokens_per_second`. Journals created before 2026-08-24 may contain
`[REDACTED]` for those two values; replay reports fall back to the stored cache
ratio and explicitly mark the absolute cached-token count unavailable.

Every run writes ignored local artifacts under:

```text
.agent/evals/agent-routing/<suite-id>/
  report.md
  results.json
  events/<case>.jsonl
  projects/<case>/...
```

The command exits non-zero when any contract fails. Unit coverage for the
reporter and evaluator lives in `backend/tests/test_live_eval_driver.py`; live
model runs remain opt-in because they require the backend and LAN inference
server and can take several minutes.

## Isolated UI/controller contracts

Three former live cases keep their IDs in
`backend/tests/smokes/controller_eval.py`: `vault_restore`, `library_roundtrip`
and `telegram_typed_roundtrip`. Their management operations moved to Settings/UI
under the approved architecture, so they are not repaired by restoring deleted
agent tools. Run the controller separately in a fresh process:

```powershell
backend\.venv\Scripts\python.exe -X utf8 backend\tests\smokes\controller_eval.py `
  --output-dir .agent\evals\controllers\manual
```

The entrypoint creates a temporary `ELIRA_DATA_DIR` before importing the app,
blocks external network calls and uses the real local routes, stores, vault and
typed tool adapters. Each contract has its own stores; overrides are restored,
the Telegram receiver is stopped, and the temporary root is removed on exit.
Reports remain in the chosen output directory.

- `vault_restore`: UI create/status/secret/backup/restore/unlock, encrypted
  backup, rejection of a tampered backup, restoration of the original
  `secret_ref`, and rollback of a post-backup mutation.
- `library_roundtrip`: UI upload/list, typed search and two bounded read pages
  of a document larger than one page with the exact canary, UI deletion in
  cleanup, and absence of the row/file with a rejected read after deletion.
- `telegram_typed_roundtrip`: real vault-backed configuration and allowed-user
  setup through local UI routes/store, receiver start/stop, typed
  `telegram(action=send/messages)` and the actual out-log. Telegram transport and
  polling are stubbed; failed transport and a locked vault must not record a
  successful send.

Reports explicitly use `kind=controller_contract` and
`external_delivery_verified=false`. A controller PASS establishes the local
contract only. Model routing, UI rendering and physical Telegram delivery need
separate live integration acceptance. These results are never merged into the
32-case live-agent success percentage. Unit coverage lives in
`backend/tests/test_controller_eval_harness.py`.

The deleted direct `workflow_request` trigger is not part of the model tool
surface. Three former live specs (`workflow_input`, `workflow_secret`,
`workflow_elevation_cancel`) now map to these real UI/API contracts in
`backend/tests/test_workflow_requests.py::WorkflowRequestApiTest`:

- `test_pending_input_request_is_replayed_and_resolution_resumes_once` checks
  request replay and exactly one resumed dispatch after input resolution;
- `test_secret_request_rejects_plaintext_and_never_returns_resolution` checks
  opaque secret references and rejection of plaintext input;
- `test_elevation_cancellation_never_dispatches_a_tool` checks cancellation,
  repeated resolution, an empty pending queue, and no tool dispatch.

These pytest contracts establish the controller boundaries independently of
model tool selection. Live `workflow_resume` and `workflow_approval` remain in
the routing inventory. No Workflow coverage is counted as a model PASS by
moving it to a controller test.

## Deterministic memory eval

The memory lifecycle has a separate offline Harness:

```powershell
backend\.venv\Scripts\python.exe -u backend\tests\smokes\memory_eval.py
```

It boots the real memory facade, SQLite stores and portable vault in an
isolated temporary `ELIRA_DATA_DIR`. It does not call the model or network and
does not write to the user's databases. The six contracts cover:

- remember/search/list/delete and deduplication;
- explicitly targeted user corrections replacing a fully rephrased curated fact;
- non-authoritative `volatile_fact` classification and age pruning;
- isolation of global user facts, project-scoped RAG and run-scoped web Corpus;
- encrypted backup/restore of `smart_memory.db` plus a Project Corpus chunk,
  scope, metadata and file manifest in `rag_memory.db`, with the temporary
  `web_corpus.sqlite3` intentionally excluded.

Reports are written to `.agent/evals/memory/<suite-id>/report.md` and
`results.json`. Unit coverage for the entrypoint lives in
`backend/tests/test_memory_eval_harness.py`.

## Autonomous development and recovery

- `backend/tests/smokes/autonomy_ui.py`: isolated UI/backend release acceptance.
- The MCP creation and skill-advisor harnesses were removed with the skill
  publish pipeline (2026-10-07): skills are now a plain folder, `data/skills`.

These are opt-in harnesses, not evidence that all scenarios passed. Current
results, failed attempts, developer interventions and pending checks are in
[AUTONOMY_ADVISOR_ACCEPTANCE_RU.md](research/AUTONOMY_ADVISOR_ACCEPTANCE_RU.md).
Canonical release verification (backend tests, frontend checks, native build and
staged startup) is distinct from a real Qwen task and native visual acceptance.
The long task through actual context compaction, Stop, restart and Resume has a
separate local protocol; its preparation alone is not an observed PASS.
