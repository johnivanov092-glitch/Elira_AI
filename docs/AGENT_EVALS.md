# Agent Routing Live Evals

Persona, streaming and structured-source diagnostics are documented separately
in [ANSWER_CONTRACT.md](ANSWER_CONTRACT.md). URL overlap in routing evals proves
neither excerpt delivery nor semantic support; the reporter exposes runtime
citations and `claim_support=not_assessed` on separate fields.

The routing eval suite verifies the real user path, not an isolated model call:

```text
CLI case
  -> POST /api/code-agent/stream
  -> profile router
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
  connected project, memory round-trip, Workflow `needs_input/secret/elevation`,
  Stop → Resume, and grounded action claims;
- `integration`: typed Telegram/vault, Library, Project RAG/Corpus, web Corpus,
  MCP and Python/TypeScript/Rust LSP;
- `itops`: typed TCP inventory, SSH discovery/Linux/Windows, and Workflow Stop
  of a command that does not return.

There is no MikroTik-specific Harness. Router targets use the same generic SSH
tools when an ordinary SSH case is appropriate.

Harness prompts state the user outcome, not exact JSON arguments or a scripted
tool recipe. The evaluator keeps the exact evidence contract hidden: required
successful operations, forbidden mutations, lifecycle order and grounded final
claims. When the model repeatedly emits a coherent alternative argument shape,
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

`backend/tests/smokes/routing_cases.json` covers:

- Auto routing to Personal, Balanced, Engineering, Business, Infrastructure,
  Science, and Medicine profiles;
- starter capability groups for each routed profile;
- project-file reading and exact answer grounding;
- one-off work on an explicit absolute path with no project connected;
- durable finite-job start plus polling to `completed` through `run_server`;
- downloadable document generation;
- typed TCP inventory without a shell fallback;
- web research for scientific and medical requests;
- on-demand MCP discovery, start, tool use, and stop;
- the negative MCP case: an ordinary chat must not start an integration.
- scripted Workflow UI resolutions for input/secret/elevation/approval;
- typed Telegram status/start/users/send/messages with a vault-backed token;
- Library add/search/paged-read/delete;
- Project Corpus status/index/recall through `runtime_control`;
- Workflow Stop followed by durable Resume of the same run;
- Python/TypeScript/Rust LSP diagnostics, definition, references and stop;
- Microsoft Docs HTTP MCP and Serena stdio MCP dynamic tools;
- opt-in read-only GitHub, Hugging Face, Playwright, Paper Search and Home
  Assistant MCP cycles, plus transport-only Unity/Blender checks;
- portable-vault backup/restore on an isolated eval data directory.
- Linux and Windows SSH commands through configured shortcuts.

Each case can assert the effective profile, initial/final runtime activation,
successful ordered tool sequences, forbidden operation families, MCP servers,
answer fragments, action claims grounded by successful tool results, citations returned by source tools, network states grounded
in typed inventory output, background-job action/kind/status, and the maximum
number of tool calls.

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

The 2026-08-24 provider acceptance used the primary Qwen model and an isolated
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

Current external blockers are configuration, not agent routing: the user's
DBHub DSN still has literal placeholders, and typed Telegram live acceptance
requires the user to unlock the portable vault in Workflow UI. The existing
offline/provider tests do not bypass either condition.

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
