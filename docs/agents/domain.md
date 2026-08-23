# Domain Docs

How the engineering skills should consume this repo's domain documentation when
exploring the codebase. Layout: **single-context**.

## Before exploring, read these

This repo has no `CONTEXT.md` yet — its domain language and architecture live in:

- **`docs/ARCHITECTURE.md`** — backend/frontend/runtime architecture + contracts.
- **`docs/PROJECT_MAP.md`** — repo structure, ownership, where to change things.
- **`docs/AGENT_ARCHITECTURE_GUIDE_RU.md`** — unified agent/runtime boundary.
- **`docs/SERVER.md`** — the AI inference server (host, endpoints, models).
- **`AGENTS.md`** (root) — working rules incl. the UTF-8/no-mojibake contract.

If a `CONTEXT.md` / `docs/adr/` is later added, read those too. This project
does not currently use ADRs; architectural decisions live as the `docs/*.md`
files above — the runtime guardrails in `docs/ARCHITECTURE.md` and the
forward-looking continuation work in `docs/BACKLOG.md`. The completed D1-D3
track is historical and lives in `docs/archive/DEFERRED_TRACK.md`.

If any referenced file doesn't exist, **proceed silently** — don't flag its
absence or suggest creating it upfront.

## File structure (single-context)

```
/
├── AGENTS.md
├── docs/
│   ├── ARCHITECTURE.md                      ← architecture + runtime guardrails
│   ├── BACKLOG.md                           ← continuation work
│   ├── archive/DEFERRED_TRACK.md            ← completed D1-D3 history
│   ├── PROJECT_MAP.md
│   ├── AGENT_ARCHITECTURE_GUIDE_RU.md
│   ├── SERVER.md
│   └── agents/                              ← this setup (issue-tracker/triage/domain)
├── backend/   (FastAPI + Python runtime)
├── frontend/  (React + Vite + TS)
└── src-tauri/ (Tauri desktop shell)
```

## Use the documented vocabulary

When your output names a domain concept (an issue title, a refactor proposal, a
hypothesis, a test name), use the term as defined in `docs/ARCHITECTURE.md` /
`docs/PROJECT_MAP.md`. Don't drift to synonyms. If the concept isn't documented
yet, that's a signal — either you're inventing language the project doesn't use
(reconsider) or there's a real gap (note it).

## Flag decision conflicts

If your output contradicts a documented decision (e.g. a guardrail in
`docs/ARCHITECTURE.md` or the agent boundary), surface it
explicitly rather than silently overriding.
