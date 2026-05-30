# Elira AI — Dev Conventions

Shared rules for anyone working on Elira (Claude, Codex, humans). Keep this in
sync with the code; if a rule changes, update here.

## Hard rules (from the project owner)
- **No stubs, no dead code.** Code = final logic. If a feature isn't finished,
  finish it or don't add it.
- **Dependencies are used-or-removed.** Never add an unused dependency; remove
  ones that nothing imports.

## Build & deploy (this trips people up constantly)
- The frontend is a **Vite bundle in `frontend/dist`** (gitignored). Tauri
  serves that bundle in production. **After any frontend change you MUST run
  `npm --prefix frontend run build` and restart the app** — editing source
  alone does nothing in the running app.
- Changes to **`src-tauri/tauri.conf.json`** (e.g. `fileDropEnabled`) are
  compiled into the binary → require a **full Tauri rebuild**
  (`run_tauri_dev.bat` / `build_exe.bat`), not just `npm run build`.
- The app launches the backend from the **`backend/.venv`** virtualenv. Verify
  with `backend/.venv/Scripts/python.exe`, not the system Python (different
  deps installed).

## Verify before committing
- Frontend: `cd frontend && npx tsc --noEmit` — must be 0 errors. `tsconfig`
  has **`noUnusedLocals: true`**, so unused imports/locals fail the typecheck.
- Backend: run the relevant `pytest` (and the full suite for cross-cutting
  changes) with the venv python.
- Don't commit runtime DBs (`data/*.db`) or `data/uploads/*` with code changes.
- PowerShell gotcha: here-strings break on Cyrillic/special chars — write the
  commit message to a temp file and `git commit -F`. `git push` prints stderr
  as a PS error but usually succeeds (check the `->` ref line).

## Architecture — two SEPARATE chat environments
- **Regular chat**: `EliraChatShell`; storage `elira_state.db` (chats +
  messages); streams via `executeStream` → `/api/chat/stream`; per-chat stream
  survival + sidebar indicator via the module-level `chatRuns` registry.
- **Code-agent**: `CodeWorkspaceShell` / `CodeAgentChatShell`; storage
  `code_agent_sessions.db` (`turns_json`); streams via `streamCodeAgent` →
  `/api/code-agent/stream`; survival via the module-level `LIVE_RUNS`. Its tools
  are `read_file/write_file/edit_file/glob/grep/run_bash` + SSH + MCP, wired
  through `ToolProvider` / `ToolRegistry` — **not** the chat skills.
- **Project state is independent**: the code-agent reads tree/files scoped to
  its `projectRoot` (advanced-project endpoints accept an optional `root`); the
  chat "Проекты" tab owns the global advanced-project. Only `ProjectPanel`
  calls `openAdvancedProject`.
- **Chat ids are strings on the frontend.** Backend ids are integers, but
  `normalizeChat` stringifies them; the whole frontend compares string ids
  (state, refs, registry keys). Do not reintroduce numeric-id comparisons — a
  `42 !== "42"` mismatch silently broke streamed answers once.
- **Stream stop**: aborting the fetch signal does NOT reliably reject an
  in-flight `reader.read()` in WebView2 — `executeStream` explicitly
  `reader.cancel()`s on abort and ignores late tokens.

## Models & skills
- Local 4–7B models are weak at function-calling. `agent_loop` recovers inline
  tool calls (JSON **and** `name(args)` call-expression). Reliability scales
  with model size; 14–20B is the target.
- Regular chat: only the skills in **`CHAT_WORKING_SKILLS`**
  (`frontend/src/chatConstants.ts`) actually work (planner-routed or
  injection-mode). The rest are shown disabled until bigger models — re-enable
  by adding ids to that set. The chat's skill router is `planner_v2` (its tool
  vocabulary is the source of truth for what auto-triggers).

## Cleanliness specifics
- Backend `ruff` F401 only legitimately flags **facade re-exports** (package
  `__init__.py`, `core/web.py`, `workflow_engine/runtime.py`) — those are public
  API, keep them. Anything else flagged is real dead code → remove.
- Core deps include `numpy` + `watchdog` (used). Heavy/optional deps live in
  `requirements-optional.txt`. OCR auto-detects the Tesseract binary
  (`pdf/runtime.py`) since it isn't always on PATH.
