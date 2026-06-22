# Frontend Rebuild Plan — clean React app on the v3 blueprint

Decision (2026-06-15): rebuild the **frontend only**, on the **same stack**
(React + Vite + TypeScript, Tauri), adding a real design system
(Tailwind CSS + shadcn/ui) and a clean component architecture, using the
`Elira_AI_mockups/unified_workspace_v3.html` mockup as the visual blueprint.
The Python backend and the agent runtime are **reused unchanged** — this is a
view-layer rebuild, not a full rewrite.

Supersedes the in-place approach in `UNIFIED_WORKSPACE_REFACTOR.md` for the
frontend. The backend track in `CODE_AGENT_REWRITE_PLAN.md` (streaming +
reliability) still applies and is a hard dependency for the live transcript.

## Why this, not a framework switch
- React + Vite + Tauri is already the modern, light agent-UI stack; the
  "heavy/not adaptive" feeling comes from 2000+ line monolith shells and the
  lack of a design system, not from React.
- Switching to Svelte/Solid/Next would discard the backend integration, the
  `api/` client layer, the streaming/tool-parsing logic, and the just-landed
  UX — with no real lightness win.
- A frontend-only rebuild keeps the risky part (the agent/backend) stable.

## Reuse vs rebuild
REUSE as-is:
- All of `backend/` (agent loop, tools, routes, streaming).
- `frontend/src/api/*` client layer (port with minimal changes; it already
  centralizes endpoints + auth via `withAuth`).
- Proven logic to lift into new components: SSE stream consumption, inline
  tool-call parsing, token-usage handling, stream registry.

REBUILD from the v3 blueprint:
- The shells (`EliraChatShell`, `CodeWorkspaceShell`, `CodeAgentChatShell`,
  `IdeWorkspaceShell`) -> one `WorkspaceShell` + small composable components.

## Stack / foundations
- React 18 + Vite + TS (keep). Tailwind CSS + shadcn/ui (Radix) + lucide-react.
- Design tokens = the v3 palette (one accent `#8b93f8`, neutral grays) as CSS
  vars / Tailwind theme. Light theme later.
- State: keep simple — React context + a small store (zustand) only if needed.
  No Redux/Next.
- Tauri 1 -> Tauri 2 as its own sub-track (lighter, responsive, mobile-ready;
  needs a full desktop rebuild to verify).

## Component inventory (from v4)
`AppShell` (grid: sidebar | main; preview slides in on the right) · `Sidebar`
(New chat + conversation list ONLY) · `Topbar` (project-path picker on the left,
top tabs `Чат | Пайплайны`, gear Settings, preview toggle after the gear; no
model/token) · `Transcript` (with inline file/image rendering + drag&drop) ·
`UserBubble` · `AgentTurn` · `ToolCallGroup` + `ToolCallRow` + `CodeBlock` ·
`Artifact` card · `Composer` (mode chips; "+" = pick project / attach / skills;
send) · `CommandPalette` (⌘K: skills/plugins/tools) · `PreviewPanel` (tabs:
Preview iframe / Code / Console / Diff) · `SettingsPanel` (Model, Memory,
Dashboard, Telegram, SSH/MCP, Theme) · `PipelinesShell` (separate, behind the
Пайплайны tab) · primitives (Button, Pill, Panel, Badge, Tabs, Tooltip).

## Approach: DIRECT rebuild (fast/simple) — agent may be offline during the work
The user is fine with the app/agent being unusable while we rebuild, so we skip
the parallel `VITE_NEW_UI` flag and the slow coexistence dance. Build the new
shell as the app's main view from Phase 0; keep old shells only as a reference
to port logic from, and delete them in Phase 5. This is the fastest path.
Speed levers: reuse ALL of `backend/` + `frontend/src/api/*` untouched; port
feature-panel LOGIC (not redesign each from zero) into v3-styled components;
defer the Tauri 1->2 upgrade to the end.

## Migration parity — nothing gets lost; ASK if not in the v3 mockup
Rule: every current feature must land in the new UI. If a feature is NOT in the
v3 mockup, STOP and confirm placement with the user before building that step.

Legend: [v3] = in the mockup; [ASK] = not in mockup, confirm placement.

Conversations/Chats ......... [v3] unified conversation list
Project root picker ......... [v3*] MOVED: composer "+" (not a right panel)
Files / uploads ............. REMOVED as a tab — drop into chat: renders inline
                             (image/doc) AND auto-indexes into memory
Code-agent transcript ....... main transcript
Preview / artifacts ......... right preview popup, toggle AFTER the gear
Skills / Plugins / MCP ...... command palette (⌘K) via composer "+" (no sidebar)
Spotlight search ............ folded into the command palette
Git / diff / patch .......... Diff tab in preview + git actions
Image generation ............ skill in palette; result in preview
Memory ...................... Settings item (drag-drop into chat still feeds it)
Dashboard (metrics) ......... Settings item
Telegram approvals .......... Settings item
Model / provider ............ Settings item ONLY (never shown in topbar)
SSH / MCP config ............ Settings item
IDE (file tree + editor) .... keep (panel) [confirm placement at step]
Terminal .................... run_bash console in preview (+ optional dock) [confirm]
Pipelines ................... SEPARATE ChatShell, opened from the top tab (Чат|Пайплайны)
Tasks (planner) ............. REMOVED for now
Planner keywords ............ [ASK] keep / where (confirm at step)
Profiles / persona .......... Settings (model area) [confirm at step]
Chat export ................. conversation menu [confirm at step]

## Layout & dedup rules (v4 — locked, apply throughout)
- Topbar: project-folder PATH on the left (where the model name used to be) +
  segmented top tabs `Чат | Пайплайны` + gear (Settings) on the right + preview
  toggle AFTER the gear. NO model name, NO token meter in the topbar.
- Model name is shown ONLY in Settings (Model/provider). Token meter removed
  from the UI (no top/in-chat duplication).
- Left sidebar = ONLY "Новый чат" + the conversation list. No workspace nav,
  no skills entry, no footer model name.
- Files: NO panel/tab. Drag&drop a file into the chat -> it renders inline
  (image thumbnail / document chip) AND is auto-indexed into memory.
- Skills/plugins/tools: ONE surface = the command palette (⌘K), reachable from
  the composer "+". No duplicate skills tab anywhere.
- Project root: chosen from the composer "+" (pick project / attach / skills);
  the chosen path appears in the topbar.
- Pipelines: a SEPARATE ChatShell behind the top "Пайплайны" tab (heavy feature,
  isolated from the normal chat).
- Memory / Dashboard / Telegram / SSH / MCP / Model / Theme live as Settings
  items (gear), not as sidebar tabs.
- Icons: reuse the project's existing lucide-react icons (do not add a new icon
  set). Tasks feature is dropped for now.

## Phases (each = reviewable commit; tsc + build per phase)

### Phase 0 — Foundation
Install Tailwind + shadcn/ui; define the v3 design tokens; build base primitives
(Button, Pill, Panel, Badge, Tabs); `AppShell` (Sidebar + Topbar + placeholder
transcript + Composer) wired to `/health` via the reused api layer. The new
shell IS the app's main view from here (direct rebuild, no flag).
- Accept: `npm run build` green; app opens on the new v3 shell.

### Phase 1 — Transcript + streaming (depends on CODE_AGENT_REWRITE_PLAN A)
Port code-agent SSE consumption into `Transcript`/`AgentTurn`/`ToolCallGroup`/
`CodeBlock` + token meter; live token streaming. Reuse stream/tool-parse logic.
- Accept: a real run renders tool-call cards + streamed text + token meter.

### Phase 2 — Preview panel
`PreviewPanel` with tabs Preview(iframe)/Code/Console/Diff; auto-open on
write_file/run artifacts; collapsible. No manual "run" button (agent runs via
tools; preview auto-refreshes).
- Accept: html artifact renders in iframe; console shows tool stdout; diff shows
  changes.

### Phase 3 — Command palette (skills/plugins/tools/features)
`CommandPalette` (⌘K) backed by the real skills/plugins/tools registries +
feature routes. Searchable, grouped.
- Accept: every capability reachable from ⌘K; launches the right action.

### Phase 4 — Features (v4 layout — per-feature commits)
Port features into the new shell reusing existing api clients (confirm exact
placement at each step):
- Chat drag&drop: drop file -> inline render (image/doc) + auto-index to memory
  (no Files tab).
- Settings panel: Model/provider, Memory, Dashboard, Telegram, SSH/MCP, Theme.
- Code tooling: IDE (tree+editor), Terminal (run_bash console + optional dock),
  Git (Diff tab + actions).
- Image generation (skill -> preview).
- Pipelines: its own `PipelinesShell` behind the top tab.
- Dropped: Files tab, Tasks. [ASK]: Planner keywords, Profiles, Chat export.
- Accept: every kept feature reachable; nothing orphaned; old shells unneeded.

### Phase 5 — Delete old shells + dead code
Once parity is reached, delete `EliraChatShell`/`CodeWorkspaceShell`/
`CodeAgentChatShell`/`IdeWorkspaceShell` and any now-dead components/routes
(grep zero-refs before each delete; full suite + build after).
- Accept: old shells gone; app runs on the new UI; backend suite green.

### Phase 6 — Tauri 2 + polish
Upgrade Tauri 1 -> 2; responsive layout; light theme; empty/loading/error
states; keyboard flow. Full desktop rebuild to verify.

## Decisions to confirm (do not block planning)
1. New tree in-place under `frontend/src/` (flag) vs a separate `frontend-next/`.
2. Tailwind + shadcn vs CSS-variables-only (lighter, no dep) — recommend
   Tailwind+shadcn for speed/consistency.
3. zustand vs React context for shared state.
4. Tauri 2 now (Phase 0) or after parity (Phase 6) — recommend after parity.
5. Which feature panels are v1 vs deferred.

## Verification (every phase)
```powershell
cd D:\AIWork\Elira_AI
npm --prefix frontend run typecheck
npm --prefix frontend run build
backend\.venv\Scripts\python.exe -m pytest -q   # backend unchanged; stays green
```
Plus a live drive of the agent against the new shell.

## Status
- [x] Phase 0 — foundation (Tailwind v4, tokens, AppShell skeleton) — a304416
- [x] Phase 1 — transcript wired to the code-agent SSE stream (tool-call cards,
      project picker, modes, stop). Token-by-token streaming still pending
      (CODE_AGENT_REWRITE_PLAN A).
- [x] Phase 2 — preview panel (iframe/Code/Console/Diff, auto-open on write_file)
- [x] Phase 3 — command palette (⌘K: tools/skills/plugins/features; prefill + nav)
- [x] Phase 4 — features: 4a model, 4b drag&drop→memory, 4c settings sections,
      4d pipelines shell, 4e terminal dock. Dropped per user decision: manual
      IDE-editor / git-panel / persona / planner-keywords UIs (agent does these
      via tools).
- [x] Phase 5 — deleted the old shells + dead code (EliraChatShell,
      CodeWorkspaceShell, CodeAgentChatShell, IdeWorkspaceShell + 9 panels + 4
      orphan helpers). components/ = MarkdownRenderer + ToastHost only.
- [~] Phase 6 — Tauri 2 + token streaming + polish
  - [x] 6b — light theme + Settings toggle (data-theme override of @theme tokens)
  - [x] 6c — Tauri 1 → 2: allowlist → capabilities, tauri-plugin-dialog,
        on_window_event two-arg signature, config schema v2, @tauri-apps/api ^2.
        Verified: cargo check (codegen validates config + capabilities), frontend
        typecheck + build. Live desktop run still pending (needs cargo build +
        webview2 on the target box).
  - [ ] 6a — token streaming (stream_code_agent + openai_compatible tool_calls
        assembly) — deferred; needs live llama.cpp verification.
