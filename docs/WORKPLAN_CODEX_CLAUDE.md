# Elira AI Agent Core Stabilization — live workplan

Status: **stabilized; ready for a new bounded task**
Updated: **2026-06-21**
Coordination owner: **Codex**
Implementation agents: **Codex or Claude, one bounded slice at a time**

This is the only live coordination document for the agent-core/context work.
`AGENT_CORE_AUDIT.md` and `CONTEXT_SYSTEM_AUDIT.md` are evidence snapshots,
not competing plans.

## Goal

Stabilize the existing agent runtime, context handling, compression and memory
without rebuilding working subsystems and without visually redesigning the new
workspace UI.

## 2026-06-21 stabilization closeout

Claude's `Task_Agent` output was reviewed against code and runtime behavior,
then repaired in one bounded pass. This section supersedes stale evidence below.

- The approved unified-workspace UI was preserved. The unimplemented context
  drawer/status-bar patch and fake pause/resume endpoints were removed; the
  existing compact context chip remains.
- The context package now has compatible profile/budget APIs, policy thresholds,
  bounded packing, rolling summary, pins, ledger, compression audit and session
  persistence. Context read/manual-compress/unpin routes reuse the existing code
  session database.
- The code-agent uses the existing provider/tool registry/executor. It now emits
  heartbeat events during blocking model waits, streams guarded content deltas,
  assembles fragmented OpenAI tool calls and stops repeated identical calls.
- Provider requests are normalized and rejected before HTTP when estimated input,
  output and margin exceed the effective context limit.
- Live server truth on 2026-06-21 is `local-model n_ctx=32768`, not the stale
  `131072` recorded earlier. Requested 128K caps are now clamped to the smaller
  live server limit; local/example configuration was corrected to 32768.
- Runtime scratch (`.agent`, `Task_Agent.txt`, `data/agent_workspace`, local SSH
  ACL) is excluded from Git. `start-claude-full.ps1` remains local and ignored.
- Recovery bundle before history cleanup:
  `D:\AIWork\Elira_AI-pre-cleanup-2026-06-21.bundle`.

Verified evidence:

- Frontend rendered at 1442x992 with no document overflow and no console errors.
- Live main, embedding, OCR and one-token chat probes returned HTTP 200.
- Live code-agent called `read_file`, streamed `# Elira AI`, and completed in two
  steps with `done ok=true`.

## Non-negotiable boundaries

- Preserve the UI baseline in `UI_BASELINE.md`. Functional UI additions must be
  minimal and visually reviewed against that baseline.
- Do not change server models, llama.cpp flags, GPU power limits, KV cache,
  OCR/embedding placement, vision service, or stress profiles.
- Reuse the existing tool registry, provider layer, agent loop, context package,
  memory stores and SQLite infrastructure. Do not add parallel implementations.
- Keep public API/event contracts compatible unless a slice explicitly records
  and tests a required contract change.
- Do not touch pre-existing dirty files unless the current slice explicitly
  owns them.
- Commit or push only when the user asks.

## Historical checkout before stabilization

Recorded at the start of this work; retained only as audit history:

- Branch: `claude/agent-ux-toolcalls`
- HEAD: `f4617facec0c382de47f5bed99f04d9d827007c8`
- UI baseline commit: `f4617fa`
- Pre-existing modified files, owner not assigned to this work:
  - `backend/app/application/code_agent/tool_schemas.py`
  - `backend/app/application/code_agent/tools.py`
  - `backend/app/application/rag_memory/runtime.py`
- Pre-existing untracked paths:
  - `.codex/`
  - `data/agent_workspace/`
  - `data/ssh_acl.json`

The clean handoff branch is `codex/task-agent-cleanup`, one commit above `main`.
Every agent must run `git status --short` before editing.

## Historical baseline

- Main LLM `/v1/models` reported `local-model`, `n_ctx=131072` during the
  original audit. The 2026-06-21 live value is `32768`; the closeout above is
  authoritative.
- Embedding `/v1/models` currently reports `local-embed`, `n_ctx=4096`.
- OCR `/health` currently reports `status=ok`, `device=cpu`.
- At audit start, local client configuration and several code defaults still
  used `16384`; local configuration and examples used a 120-second timeout.
- Existing runtime already has tool registries, provider gating, context
  compaction, rolling summaries, tool-output truncation, session persistence,
  pinning, max-step wrap-up and regression tests. The attached plans are a
  requirements backlog, not a clean-room architecture specification.

## Execution order

### S0 — Audit and coordination baseline

Status: **complete**

- [x] Inspect both supplied plans against current code.
- [x] Record live server profile without changing the server.
- [x] Record the protected UI baseline.
- [x] Map current agent-core and context implementations.
- [x] Create the single Codex/Claude handoff document.

Evidence:

- `docs/UI_BASELINE.md`
- `docs/AGENT_CORE_AUDIT.md`
- `docs/CONTEXT_SYSTEM_AUDIT.md`

### S1 — Active context profile and timeout truth

Status: **complete**

Objective: remove the confirmed 16K/120s client drift while preserving existing
routing and monitoring guardrails.

Implemented:

- Added `application/context/profile.py` using existing provider discovery and
  configuration, with explicit caps remaining authoritative.
- Aligned stale chat/code-agent/API defaults to `131072` and bounded local
  provider/profile timeouts to 300/600 seconds.
- Added narrowly scoped SQLite migration for known legacy default rows only.
- Updated checked-in examples and local machine configuration.
- Added discovery, explicit-cap and unavailable-server regression coverage.

### S2 — OpenAI-compatible message boundary

Status: **complete for confirmed failure**

- Extended the existing `_normalize_messages_for_request` only.
- Invalid roles and empty non-tool turns are removed; tool calls are preserved;
  adjacent trailing assistant text is merged before llama.cpp receives it.
- Added a regression test for the duplicate-assistant boundary.

### S3 — Agent-loop terminal reliability and tool-trace containment

Status: **partial; containment complete, streaming remains**

- Kept the existing max-step/deadline wrap-up path.
- Added known-tool recovery for Qwen XML-like calls and a final guard that
  prevents raw internal tool traces from reaching the user.
- Added known/unknown tool-trace regression tests.
- Token-by-token response streaming remains the next independent slice under
  `CODE_AGENT_REWRITE_PLAN.md`.

### S4 — Context budget observability and safe compaction

Status: **foundation complete; compression audit remains**

- Added structured usage, reserved output, safety margin and category breakdown
  to the existing context package and SSE usage event.
- Existing bounded compaction and deterministic fallback remain unchanged.
- Structured before/after compression metrics and replacement validation remain
  a separate follow-up; the additive persistence field is available.

### S5 — Persistent task state

Status: **bounded foundation complete**

- Extended existing code sessions with additive JSON columns for context state,
  task ledger, pinned items and compression events.
- The workspace keeps at most 200 ledger entries and stores tool-result size or
  bounded error text, not raw successful tool output.
- Session selection restores context usage and ledger state.
- Protected-item editing and populated compression audit history remain pending.

### S6 — Context indicator in the protected UI

Status: **complete for this slice**

- Added one compact read-only usage chip to the existing composer mode row.
- No top bar, sidebar, composer geometry, theme token or control order changes.
- Typecheck and production build pass. In-app browser verification at
  `1442 × 992` confirmed matching layout hierarchy and no page overflow.
- After screenshot: `docs/UI_AFTER_CONTEXT_2026-06-20.png`.

### S7 — Full regression and documentation closeout

Status: **complete for this bounded slice**

- Focused regressions cover context profile/usage, message normalization,
  tool-trace containment and session state.
- Frontend gates and the complete `backend/tests` suite pass.

## Verification ledger

Append results; do not rewrite history.

| Date | Slice | Command | Result |
| --- | --- | --- | --- |
| 2026-06-20 | S0 | live main `/v1/models` | `local-model`, `n_ctx=131072` |
| 2026-06-20 | S0 | live embed `/v1/models` | `local-embed`, `n_ctx=4096` |
| 2026-06-20 | S0 | live OCR `/health` | `ok`, CPU |
| 2026-06-20 | S0 | `npm --prefix frontend run typecheck` | PASS |
| 2026-06-20 | S0 | `npm --prefix frontend run build` | PASS, 1791 modules |
| 2026-06-20 | S0 | `backend\.venv\Scripts\python.exe -m pytest -q` | COLLECTION BLOCKED by pre-existing untracked `data/agent_workspace/ssh_test.py`: missing `paramiko` |
| 2026-06-20 | S0 | `backend\.venv\Scripts\python.exe -m pytest backend\tests -q` | PASS: 2937 tests, 11 subtests |
| 2026-06-20 | S1-S7 | `npm --prefix frontend run typecheck` | PASS |
| 2026-06-20 | S1-S7 | `npm --prefix frontend run build` | PASS, 1791 modules |
| 2026-06-20 | S1-S7 | `backend\.venv\Scripts\python.exe -m pytest backend\tests -q` | PASS: 2944 tests, 11 subtests |
| 2026-06-20 | S1-S7 | root `pytest -q` | COLLECTION BLOCKED by pre-existing untracked `data/agent_workspace/ssh_test.py`: missing `paramiko` |
| 2026-06-20 | S6 | in-app browser, `1442 × 992` | PASS: expected hierarchy, no horizontal/vertical overflow; capture `docs/UI_AFTER_CONTEXT_2026-06-20.png` |
| 2026-06-21 | Closeout | `npm --prefix frontend run typecheck` | PASS |
| 2026-06-21 | Closeout | `npm --prefix frontend run build` | PASS, 1791 modules |
| 2026-06-21 | Closeout | `backend\.venv\Scripts\python.exe -m pytest -q` | PASS: 2975 tests, 11 subtests |
| 2026-06-21 | Closeout | live main/embed/OCR/chat/code-agent probes | PASS; effective main context `32768` |
| 2026-06-21 | Closeout | in-app browser, `1442 × 992` | PASS: approved layout preserved, no overflow or console errors |

## Handoff protocol

Before starting a slice:

1. Read this file, `UI_BASELINE.md`, both audit docs, `ARCHITECTURE.md` and
   `PROJECT_MAP.md`.
2. Run `git status --short`, `git branch --show-current` and
   `git worktree list --porcelain`.
3. Claim exactly one slice here and list its files before editing.
4. Preserve unrelated dirty state and `.claude/`.

Before handing off:

1. Update slice status, files changed, unresolved risks and exact next step.
2. Append verification commands/results.
3. Record any new dirty files and their owner.
4. Do not mark a gate green unless it actually completed successfully.
