# Domain Docs

How the engineering skills should consume this repo's domain documentation when
exploring the codebase. Layout: **single-context**.

## Before exploring

Use Atlas passports and current decisions for code discovery; the files below
are the maintained domain vocabulary. Source review on 2026-10-18 replaced old
accumulated runtime descriptions. Historical material lives only in the archive.

## Canonical references

This repo has no `CONTEXT.md` yet — its domain language and architecture live in:

- **`docs/ARCHITECTURE.md`** — backend/frontend/runtime architecture + contracts.
- **`docs/PROJECT_MAP.md`** — repo structure, ownership, where to change things.
- **`docs/AGENT_ARCHITECTURE_GUIDE_RU.md`** — unified agent/runtime boundary.
- **`docs/SERVER.md`** — client contracts and the separate inference-server boundary.
- **`AGENTS.md`** (root) — working rules incl. the UTF-8/no-mojibake contract.

If a `CONTEXT.md` / `docs/adr/` is later added, read those too. This project
does not currently use ADRs; architectural decisions live as the `docs/*.md`
files above — the runtime guardrails in `docs/ARCHITECTURE.md` and the
task-specific work in `.scratch/<feature>/`. Historical continuation lists are
under `docs/archive/`; they are not the active task queue. The completed D1-D3
track is historical and lives in `docs/archive/DEFERRED_TRACK.md`.

If a canonical path is missing, locate its current owner through Atlas and Git.
Do not invent a replacement or treat a missing source as verified. Report the
specific uncertainty when it affects the task; do not create placeholder docs.

## File structure (single-context)

```
/
├── AGENTS.md
├── docs/
│   ├── ARCHITECTURE.md                      ← architecture + runtime guardrails
│   ├── archive/README.md                   ← dated evidence and past plans
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
