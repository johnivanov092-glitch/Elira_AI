# Unified Workspace Refactor — one Claude Code / HF-"Chat with your model" UI

Goal: collapse the two separate agent experiences (Chat + Code) into ONE
coherent local-agent workspace in the Claude Code / HF "Chat with your model"
style. One root, one transcript, one sidebar, one composer with mode chips.
Consolidate features — do not lose them. Delete only redundant shells/code as
they are absorbed.

Status: PLAN ONLY. Not started. Execute in order; each stage = one reviewable
commit with `tsc` + `build` + `pytest -q` + Opus review, then push.

Related: `AGENT_UX_PLAN.md` (tool-call cards/icons/token counter — DONE),
`CODE_AGENT_REWRITE_PLAN.md` (streaming + reliability — the runtime track this
plan depends on).

## Current state (verified 2026-06-15)

```
App.tsx -> EliraChatShell (root) + ToastHost
EliraChatShell.tsx        2217 lines  ROOT. mainTab: "chat" | "code".
  sideTab nav: chats, project, library, memory, tasks, dashboard, pipelines,
               telegram, settings. Features: chat, library/files, memory,
               tasks, dashboard, pipelines, telegram, charts, image-gen,
               spotlight, export.
  topbar tabs: [Чат] [Код] [panel toggle]
  mainTab === "code"  -> EARLY-RETURN full-screen:
    CodeWorkspaceShell.tsx  1451 lines  own chrome: project bar + "Сессии"
      -> CodeAgentChatShell.tsx 1841 lines  Codex-style transcript (tool-call
         cards, icons, token counter, mode chips, source chips)  <-- target base
      -> IdeWorkspaceShell.tsx  888 lines   file tree / editor
```
~6400 lines across 4 shells. The Chat side and Code side have **different
sidebars** (chat: Chats/Files/Memory...; code: project Sessions) and different
chrome — this mismatch is the core of the refactor.

Backend: two runtimes/route groups — `/api/chat-agent/*`
(`application/chat_agent/`, boundaried read-only) and `/api/code-agent/*`
(`application/code_agent/`, full executor). See `AGENT_BOUNDARY_REVIEW.md`.

## Target design (decided)

- **One root shell** (Claude Code / HF style). The Codex-style transcript
  (CodeAgentChatShell) is the single conversation surface ("home").
- **One persistent layout**: shared topbar + a single left sidebar that stays
  across everything (no full-screen world-swap on "Код").
- **One composer** with mode chips (Chat / Code / Search; Think later) ->
  routes to the unified agent loop + tool policy. Mode replaces the Chat/Code tab.
- **Unified sidebar** = reconcile the two: one session list (chats AND code
  sessions are the same "conversations") + collapsible panels for Files/Memory/
  Project/Tasks/Dashboard/Pipelines/Telegram/Settings.
- **Features consolidated, not dropped**: library, memory, image-gen, etc.
  become panels/tools in the one workspace.
- **Safety kept**: unifying the runtime adds a mode/policy layer, it does NOT
  remove the read-only guardrails from AGENT_BOUNDARY_REVIEW (Chat mode = no
  shell/sandbox/file-write unless the user switches to Code mode).

## Decisions to confirm before/while executing (do not block the plan)
1. Does "Chat mode" become the code-agent in read-only mode, or stay a thin
   caller of `/api/chat-agent`? (Affects backend unification depth.)
2. Which feature panels survive in v1 vs. move to a "more" menu (telegram,
   pipelines, dashboard are heavy)?
3. Keep `IdeWorkspaceShell` (file tree/editor) as a panel, or drop for v1?
4. One conversation store, or keep chat-history and code-sessions separate
   underneath a unified list?

## Stages (each = one commit; do in order)

### Stage 1 — Unified shell skeleton (frontend)
Make one shell with a persistent topbar + single sidebar; render the Code/agent
transcript inside the shared `<main>` instead of the `mainTab==="code"`
full-screen early return. Replace the [Чат]/[Код] tabs with a single workspace
where mode is chosen in the composer. Reconcile the two sidebars into one
(sessions + collapsible panels). No feature removal yet; CodeWorkspaceShell's
unique chrome is folded into shared panels.
- Files: `App.tsx`, `EliraChatShell.tsx`, `CodeWorkspaceShell.tsx`,
  `CodeAgentChatShell.tsx`, `styles.css`.
- Risk: HIGH (two layouts/sidebars merge). Mitigate: land in sub-commits
  (a: shared layout wrapper; b: move code view into main; c: unify sidebar;
  d: composer mode replaces tabs). `tsc`+`build` after each.
- Accept: one persistent sidebar+topbar across chat and code; switching mode
  does not swap the whole layout; no feature lost; build/tsc green.

### Stage 2 — Streaming + reliability (depends on CODE_AGENT_REWRITE_PLAN A/B)
Token streaming (live output, no read-timeout) + honest deadline/results. This
is what makes the unified agent FEEL like Claude Code. Execute per
CODE_AGENT_REWRITE_PLAN Phases A & B.
- Accept: long tasks stream tokens; failures reported honestly (no ok=True lie).

### Stage 3 — Fold chat-only features into the workspace (frontend)
Move library/memory/image-gen/tasks/etc. into the unified shell as panels or
tools, per the "decisions to confirm". Each feature = its own small commit.
- Accept: every kept feature reachable from the one workspace; nothing orphaned.

### Stage 4 — Backend unification (optional depth)
One agent loop with a mode/policy layer, or keep both backends behind the one
UI. Respect AGENT_BOUNDARY_REVIEW (mode gates tool access). Reuse the existing
executor/tool-registry — no second runtime.
- Accept: chat mode cannot reach shell/sandbox/write; code mode can; tests cover
  the mode->tool gate.

### Stage 5 — Delete redundant shells/code
Once absorbed: remove `CodeWorkspaceShell`, the old chat/code tab split, and any
now-dead render paths / routes / runtime. Verify zero references (grep) before
each delete; run full suite after each batch.
- Accept: dead shells gone; suite green; routes/paths unchanged except intended.

### Stage 6 — Polish ("technological, not clunky")
Consistent theme/spacing/typography, empty/loading/error states, keyboard flow,
the HF-style header (model picker + token meter). Visual pass.

## Verification (every stage)
```powershell
cd D:\AIWork\Elira_AI
npm --prefix frontend run typecheck
npm --prefix frontend run build
backend\.venv\Scripts\python.exe -m pytest -q
```
Plus a live drive of `/api/code-agent/stream` (and chat) to confirm behaviour.

## Status
- [ ] Stage 1 — unified shell skeleton
- [ ] Stage 2 — streaming + reliability (see CODE_AGENT_REWRITE_PLAN)
- [ ] Stage 3 — fold chat features into the workspace
- [ ] Stage 4 — backend unification (optional)
- [ ] Stage 5 — delete redundant shells/code
- [ ] Stage 6 — polish
