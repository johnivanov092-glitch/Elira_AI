# Architecture

Source contracts reviewed on 2026-10-18. This document describes the checked-in
application. Installed releases and mutable `data/skills/` may have different
versions; their current state needs separate evidence. The earlier accumulated
text is preserved in [the archive](archive/contracts-20261018/ARCHITECTURE.md).

## Canonical flow

```text
React / Tauri
  -> POST /api/code-agent/stream
  -> delivery_session
  -> agent_loop
  -> model_turn + runtime_activation + turn_context
  -> tool_execution
  -> agent_kernel.executor
  -> runtime_registry
  -> BuiltinToolProvider | MCP providers
  -> observed result -> answer_acceptance -> final_response / done
```

Workflow agent steps call the same agent core. `delegate_task` is a tool that
starts a child run through that core; the removed multi-agent UI/API mode is not
an additional runtime. `/api/advanced` still owns RAG/project endpoints; its
presence does not imply `/api/advanced/multi-agent` exists.

Owners: [HTTP adapter](../backend/app/api/routes/code_agent_routes.py),
[coordinator](../backend/app/application/code_agent/agent_loop.py),
[provider assembly](../backend/app/application/tool_providers/runtime_registry.py).

## Agent loop module boundaries

`agent_loop.py` remains the single coordinator. The following modules are leaves:
none imports `agent_loop`, owns another executor, or creates a parallel registry.

| Module in `backend/app/application/code_agent/` | Responsibility |
| --- | --- |
| `run_control.py` | Live registration, cancellation and Workflow rendezvous |
| `model_turn.py` | One model exchange, reasoning/content and heartbeat |
| `runtime_activation.py` | Run-local schema selection, MCP selections and rebuilds |
| `turn_context.py` | Messages, skills, user inputs, runtime blocks and packing |
| `tool_execution.py` | One tool call through the existing executor and Workflow branches |
| `run_observations.py` | Run-owned evidence, outcome, recovery and criteria owners |
| `answer_acceptance.py` | Ordered answer checks and bounded correction state |

State accounting before a yielded result and criterion/message updates after it
remain separate. Epochs and the journal preserve their existing meanings. Stream
adapters close owned iterators; a successful finish does not depend on a later Stop.

## Runtime invariants

### Runtime Guardrails

- One agent core: `backend/app/application/code_agent/agent_loop.py`.
- One executor: `backend/app/application/agent_kernel/executor.py`.
- One aggregation path: `backend/app/application/tool_providers/runtime_registry.py`.
- One durable human control plane: `backend/app/application/workflows/`.
- Extend the existing persistence owners; do not create another DB facade.
- Schema visibility is prompt composition. Execution and authorization remain
  with the canonical executor, ToolSpec and Workflow policy.
- Tool success comes from structured results, not an `ERROR:` substring or prose.
- User-owned text remains in the `user` role. Runtime blocks are projected into
  the leading system context by `history.project_runtime_roles`; project files,
  web text and attachments retain their untrusted labels.

## Tools and skills

[tool_policy.py](../backend/app/application/code_agent/tool_policy.py) defines the
default requested tools: `capability_load`, file read/write/edit, glob, grep,
`run_bash`, `run_server`, `todo_update`, `web_search`, `web_fetch`, `math`.
The HTTP adapter can supply another starting set. The actual schema set is built
by `RuntimeActivation.rebuild`, including the core tools and loaded MCP schemas.
Workflow questions such as `ask_user` are handled through the existing runtime.

The current groups in
[capabilities.py](../backend/app/application/code_agent/capabilities.py) are:

| Group | Contents |
| --- | --- |
| `project` | Files, shell, project map/index, checklist, delegation and math |
| `mcp` | MCP lifecycle/tool-discovery tool |
| `resources` | Inspect attachment metadata, materialize, publish |
| `memory` | User memory and document Library |

There are no native `ssh`, `itops`, `telegram`, `desktop`, `web` or `math`
capability groups. Their historical group names are not current instructions.
SSH/admin/Telegram/browser/document tasks use the corresponding skills and/or MCP
through the existing tools. The canonical builtin dispatch map is
[tools/_dispatch.py](../backend/app/application/code_agent/tools/_dispatch.py).

Skills are ordinary mutable folders under `data/skills/`. The factory `skills/`
is seeded only for missing folders; an update does not overwrite installed work.
`task_skills.py` builds the catalog, pins read instructions and records folder Git
history. `skill_modules.py` resolves application adapters to the installed skill,
with factory fallback only when the installed skill directory is absent.
See [TASK_SKILLS.md](TASK_SKILLS.md).

`math` is a thin call into the MATH skill. Document/OCR/desktop/domain algorithms
belong to their skills. `resource_process` currently supports metadata `inspect`
on `auto`/`local_cpu`; it is not a general document/GPU executor.

## MCP lifecycle

`data/mcp_servers.json` owns local server configuration. The `mcp` tool and the
Settings API use the same `mcp_runtime.py`. The registry combines builtin tools
and selected MCP providers; it does not add a second SSH or IT Ops provider path.

Enabled servers with `autostart: true` can start automatically. Run activation
waits for configured autostart and includes their IDs. Other servers become
visible after explicit lifecycle actions. A large server may expose a selected
schema subset; its dispatch owner remains the same provider. Run-local selections
and schema queries survive Resume. A stale statement that MCP never autostarts
must not be used as a current contract.

## Context, state and continuation

`context/profile.py` resolves the effective window and budgets using the provider
and explicit request. `context/usage.py` estimates occupancy; `compaction.py`
compresses history while keeping pinned runtime/skill state. `working_set.py`
restores the relevant file working set. No fixed model window in this document
replaces the effective profile reported by the runtime.

`run_journal.py` owns durable run state and events. `delivery_session.py` may
continue a structured task after `context_limit`, retaining the code-agent run
identity. An answer, provider error and Stop keep their distinct terminal meanings.
Workflow resume attempts have their own Workflow attempt identity. Missing or
stale evidence does not become current just because the run was resumed.

## Answers, Web and delivery

`delta` is a draft; `final_response` accepts the answer; `done` reports terminal
run state. The UI stream owners are `backgroundRuns.ts` and `turnCodec.ts`.
The closing work report can be separated into activity by `split_status_report`;
a response consisting only of that report is retained as an answer.

`answer_acceptance.py` owns the implemented corrections and degraded status.
`TaskOutcome` records user requirements and observed publication attempts; it does
not require `task_decide` or `result_verify`, and its `verify_answer` currently
returns no independent semantic attestation. See [ANSWER_CONTRACT.md](ANSWER_CONTRACT.md).

`web_search` and `web_fetch` are native entry points into the mutable
`web-research` CLI. The application owns the run-bound URL/label ledger and
passes observed pages into existing evidence handling. The skill owns research
budgets, fetching, search engines, page reuse and its domain implementation.
Application adapters in `skill_services/` reuse that implementation.

Search mode and deep/quick research have actual bounded web budgets. A global
claim of unlimited web time/sites or the old fixed 5/10/30-page rules is not the
current implementation. The source of limits is
[research.py](../skills/web-research/webskill/research.py), not historical results.
The native transport also enforces its framed deadline and Stop cancellation.

The current answer path resolves read-page numbers/labels and may correct,
reattribute or remove unsupported source points according to `web_points.py` and
`answer_acceptance.py`. These checks are implementation behavior; they are not a
universal judgement that every statement is true. Legacy `[[source:id]]` support
remains for exact excerpt/quote provenance.

File delivery requires a successful `resource_publish` receipt for the current
bytes, with canonical download URL and SHA-256. A local path, script stdout or
preview alone does not deliver the file. PDF/DOCX publication invokes the shared
document QA adapter; unverifiable QA is not promoted by the model's prose.

## Permissions and Windows

The product selector is `ask`, `accept_edits` or `bypass`. The implementation is
[impact_policy.py](../backend/app/application/agent_kernel/impact_policy.py) and
[executor.py](../backend/app/application/agent_kernel/executor.py). Bypass does
not change the process's Windows token or approve release installation.
`needs_input`, `needs_secret`, `needs_elevation` and `waiting_approval` flow through
Workflow. Tauri owns the existing UAC bridge; secrets stay vault references.

The terminal panel has its own session owner in `application/terminal/`; it does
not replace the agent executor. Finite background jobs remain under the existing
`run_server` owner and its durable journal/process identity checks.

## Persona, inference and release

Elira has one live persona. Legacy `profile_name` input is ignored; dead modes,
`/api/profiles` and the mood module are removed. Persona evolution and calibration
remain under `application/persona/`; they do not own permissions or capabilities.
Voice UI uses `application/voice/runtime.py` and the LAN service contract.

The single OpenAI-compatible local client retains the `llama_server` provider ID.
A compatible local llama.cpp/vLLM endpoint is deployment configuration, not a
second provider path. Model identity, context size, sampling support and service
health require current server evidence. See [SERVER.md](SERVER.md).

Source merge/push, candidate verification, installed admission and visual
acceptance are separate facts. Windows Foundation is the release owner when
registered; the local supervisor is the legacy/isolated path. Neither the agent
nor a test result silently confirms installation. See
[RELEASE_LIFECYCLE.md](RELEASE_LIFECYCLE.md).

## Data ownership and API map

`ELIRA_DATA_DIR` selects machine-local data; run journals can be redirected with
`ELIRA_AGENT_RUNS_DIR`. Memory, Library, Project Corpus, resource store, Workflow
and skill state retain their existing owners. A skill's web cache is not a second
application memory store. Secrets, uploads, journals, DBs, dependencies and model
weights stay outside Git.

Mounted routers are enumerated by
[api/routes/registry.py](../backend/app/api/routes/registry.py). Verify a concrete
route against that router and the local FastAPI OpenAPI output; an old route list
or a count from an earlier release is not the mounted API contract.

The current file owners are in [PROJECT_MAP.md](PROJECT_MAP.md). Working rules,
Atlas checks, encoding and the final test gates are in [AGENTS.md](../AGENTS.md).
