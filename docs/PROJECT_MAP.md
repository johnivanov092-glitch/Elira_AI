# Project Map

Owners checked against the source tree on 2026-10-18. This maps the repository;
current installed release, mutable skill revision and external service state
need their own verification. Use Atlas passports/decisions before edits.

## Repository

```text
Elira_AI/
├─ backend/       FastAPI application, domain and infrastructure
├─ frontend/      React/Vite UI, workspace and companion
├─ src-tauri/     Native desktop shell, UAC bridge and companion window
├─ shared/        elira_common used by application and skill code
├─ skills/        Factory skill folders
├─ .agents/       Repository engineering skills, not Elira's skill catalog
├─ assets/        Branding and voice-sample documentation
├─ scripts/       Development, build, checks and release owners
├─ integrations/  Isolated portal utilities
├─ docs/          Current docs; dated history under archive/
├─ data/          Machine-local application/skill state (mostly ignored)
├─ .agent/        Local run/evaluation evidence
├─ .scratch/      Local tasks, notes and implementation evidence
└─ .runtime/      Dependencies, release state/candidates and logs
```

The sibling `Elira_AI_Server` owns serving/host operations. It does not own a
second copy of this application's agent, tools or user databases.

## Agent and execution owners

| Area | Canonical source |
| --- | --- |
| FastAPI lifecycle and mounted routers | [main.py](../backend/app/main.py), [registry.py](../backend/app/api/routes/registry.py) |
| Agent HTTP/SSE, cancel, resume and chat inputs | [code_agent_routes.py](../backend/app/api/routes/code_agent_routes.py) |
| Single coordinator and public adapters | [agent_loop.py](../backend/app/application/code_agent/agent_loop.py) |
| Delivery slices and automatic/manual continuation | [delivery_session.py](../backend/app/application/code_agent/delivery_session.py) |
| Live cancellation and Workflow rendezvous | [run_control.py](../backend/app/application/code_agent/run_control.py) |
| One model exchange | [model_turn.py](../backend/app/application/code_agent/model_turn.py) |
| Messages, skills, inputs and pinned packing | [turn_context.py](../backend/app/application/code_agent/turn_context.py) |
| Schema visibility and configured MCP autostart selection | [runtime_activation.py](../backend/app/application/code_agent/runtime_activation.py) |
| Base tools, groups and schemas | [tool_policy.py](../backend/app/application/code_agent/tool_policy.py), [capabilities.py](../backend/app/application/code_agent/capabilities.py), [tool_schemas.py](../backend/app/application/code_agent/tool_schemas.py) |
| One call and Workflow branches | [tool_execution.py](../backend/app/application/code_agent/tool_execution.py) |
| Sole executor and permission policy | [executor.py](../backend/app/application/agent_kernel/executor.py), [impact_policy.py](../backend/app/application/agent_kernel/impact_policy.py) |
| Builtin dispatch | [tools/_dispatch.py](../backend/app/application/code_agent/tools/_dispatch.py) |
| Builtin + MCP aggregation | [runtime_registry.py](../backend/app/application/tool_providers/runtime_registry.py) |
| Checklist and delegation tool | [tools/_meta.py](../backend/app/application/code_agent/tools/_meta.py) |
| Shell, persistent servers and finite jobs | [tools/_run.py](../backend/app/application/code_agent/tools/_run.py), [tools/_background_jobs.py](../backend/app/application/code_agent/tools/_background_jobs.py) |
| Durable run state/events and past-task hints | [run_journal.py](../backend/app/application/code_agent/run_journal.py) |
| Chat sessions and folders | [sessions.py](../backend/app/application/code_agent/sessions.py) |
| Terminal panel process/session owner | [terminal/sessions.py](../backend/app/application/terminal/sessions.py) |

The current registry has no separate SSH/IT Ops/Telegram provider path. Their
skills/API services are not additional capability groups. Full runtime boundaries
are in [ARCHITECTURE.md](ARCHITECTURE.md).

## Answers, skills and state

| Area | Canonical source |
| --- | --- |
| Ordered answer checks/corrections | [answer_acceptance.py](../backend/app/application/code_agent/answer_acceptance.py) |
| Run observations and source/tool evidence | [run_observations.py](../backend/app/application/code_agent/run_observations.py), [run_evidence.py](../backend/app/application/code_agent/run_evidence.py) |
| User task requirements and observed publications | [task_outcomes.py](../backend/app/application/code_agent/task_outcomes.py) |
| Web URL/label ledger and CLI transport | [tools/_web.py](../backend/app/application/code_agent/tools/_web.py) |
| Web point/source checks | [web_points.py](../backend/app/application/code_agent/web_points.py), [web_answers.py](../backend/app/application/skill_services/web_answers.py) |
| Skill catalog, instruction snapshots and Git history | [task_skills.py](../backend/app/application/code_agent/task_skills.py) |
| Application-to-skill module resolution | [skill_modules.py](../backend/app/core/skill_modules.py), [skill_services/](../backend/app/application/skill_services/) |
| Factory domain implementations | [skills/](../skills/README.md) |
| Shared arithmetic, calendar, files and quote rules | [elira_common/](../shared/elira_common/) |
| Resource store/context and metadata inspection | [media/](../backend/app/application/media/) |
| Materialization, publication and document QA call | [tools/_resources.py](../backend/app/application/code_agent/tools/_resources.py) |
| Context profile, usage and compression | [context/](../backend/app/application/context/) |
| File working set | [working_set.py](../backend/app/application/code_agent/working_set.py) |
| Memory facade/provenance | [memory/facade.py](../backend/app/application/memory/facade.py), [memory/tool_provenance.py](../backend/app/application/memory/tool_provenance.py) |
| Curated facts | [smart_memory/store.py](../backend/app/application/smart_memory/store.py) |
| Library extraction/search/paging | [library/runtime.py](../backend/app/application/library/runtime.py) |
| Project Corpus | [indexing.py](../backend/app/application/code_agent/indexing.py), [rag_memory/](../backend/app/application/rag_memory/) |
| Persona/evolution and voice client | [persona/](../backend/app/application/persona/), [voice/runtime.py](../backend/app/application/voice/runtime.py) |
| Local model/embedding client | [openai_compatible.py](../backend/app/infrastructure/llm/openai_compatible.py) |
| Workflow state and requests | [workflows/](../backend/app/application/workflows/) |
| Vault | [secrets/vault.py](../backend/app/infrastructure/secrets/vault.py) |

## Integrations, UI and native shell

MCP lifecycle/configuration is
[tool_providers/mcp_runtime.py](../backend/app/application/tool_providers/mcp_runtime.py);
Settings uses [mcp_routes.py](../backend/app/api/routes/mcp_routes.py).
`data/mcp_servers.json` is its machine-local configuration source.
Telegram Settings/bot API remains in
[telegram_routes.py](../backend/app/api/routes/telegram_routes.py); the model's
Telegram actions use the [telegram skill](../skills/telegram/SKILL.md).

| UI/native owner | Responsibility |
| --- | --- |
| [WorkspaceShell.tsx](../frontend/src/workspace/WorkspaceShell.tsx) | Workspace composition |
| [Composer.tsx](../frontend/src/workspace/Composer.tsx) | User input, permission/reasoning controls |
| [backgroundRuns.ts](../frontend/src/workspace/backgroundRuns.ts) | Agent run stream ownership |
| [turnCodec.ts](../frontend/src/workspace/turnCodec.ts) | Persisted turn conversion and event state |
| [useWorkflowRequestEvents.ts](../frontend/src/workspace/useWorkflowRequestEvents.ts) | Durable Workflow request events |
| [companion/](../frontend/src/companion/) | Companion view/state/bridge |
| [src-tauri/src/main.rs](../src-tauri/src/main.rs) | Native command registration and UAC bridge |
| [companion.rs](../src-tauri/src/companion.rs) | Companion window/hotkey/capture plumbing |

Do not add a second SSE reader in a component or another tool executor in a UI
adapter. Respect [UI_BASELINE.md](UI_BASELINE.md) and later explicit decisions.

## Runtime data

`ELIRA_DATA_DIR` owns application DBs, user facts, Library, resources, skill
folders, MCP configuration and portable vault. `ELIRA_AGENT_RUNS_DIR` can redirect
run journals. `.agent/evals/` and `.scratch/` normally contain local evidence.
The native Web CLI has run-scoped workspace state; an old root
`data/web_corpus.sqlite3` must not be assumed to be its current cache owner.
Use the actual skill/context configuration to find that state.

Ignored caches, environments and DBs are not tracked source. Installed skills
may differ from factory copies, and a clean Git status says nothing about their
current versions. Do not delete these folders merely to get an empty file list.

## Releases, tests and docs

Release manager/service owners and deployment proof are in
[RELEASE_LIFECYCLE.md](RELEASE_LIFECYCLE.md). Current docs are indexed in
[README.md](README.md); historical plans, snapshots and measurements are under
[archive/](archive/README.md). Checks and their limits are in
[AGENT_EVALS.md](AGENT_EVALS.md); final repository gates are in
[AGENTS.md](../AGENTS.md).
