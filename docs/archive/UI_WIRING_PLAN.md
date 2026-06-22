# UI wiring plan — connect the new workspace to all backend features

The new unified workspace (`frontend/src/workspace/`) renders a working chat
but leaves several backend capabilities unwired (the sidebar conversation list
is hardcoded placeholder, sessions aren't persisted, no UI for MCP/SSH/RAG-admin/
project-prompt/watcher). All endpoints already exist in `code_agent_routes.py`
(prefix `/api/code-agent`) + the `api/` layer — so this is **frontend-only
wiring**, no backend changes, agent may stay offline.

Scope: wire everything that fits the unified chat-centric UI. Keep the v4 cuts
(no Files tab, no standalone IDE editor / git panel / Tasks / planner-keywords) —
those were deliberate. Branch: `claude/agent-ux-toolcalls`.

## Audit — api modules vs new UI
Used by workspace/: chat, client, codeAgent, dashboard, library, pipelines,
smartMemory, telegram, terminal. Backend endpoints all present.

## Phases (each = its own commit + Opus review)
- [x] **P1 — Sessions / conversations.** Sidebar → real `listCodeSessions`;
      working "Новый чат" (`createCodeSession`, lazy on first send); click to
      load (`getCodeSession`); persist turns on run-done (`patchCodeSession`);
      delete. Serialize the new `Turn[]` as the session `turns` JSON.
- [x] **P2 — Memory / RAG admin** (Settings → Память): stats, list, add, delete,
      clear-category, index current project, recall test. (`getRagStats`,
      `listRagItems`, `addRagItem`, `deleteRagItem`, `clearRagCategory`,
      `indexProject`, `recallFromRag` + existing `listSmartMemory`.)
- [x] **P3 — MCP servers** (Settings): list + start/stop/restart with live status.
- [x] **P4 — SSH allowlist** (Settings): `getSshConfig`/`setSshConfig` (add/remove hosts).
- [x] **P5 — project-prompt** editor (Settings → Проект): get/set .elira/agent.md.
- [x] **P6 — watcher** (Settings → Проект): start/stop/status toggle.
- [x] **P7 — verify** pipelines / terminal wired + live-verified (list/run/toggle;
      cwd/exec). Plugins surfaced in Settings → Интеграции (list/reload/toggle).

## Agent tools/skills verification (done)
- Backend suite: **2937 passed** → all tool + skill logic green.
- **Found & fixed (d0de900):** mutating tools need Agent OS approval (F1); the
  new UI didn't handle `approval_pending` → agent could chat but not act. Wired
  the inline Разрешить/Отклонить prompt + resolveApproval. Verified live:
  approval_pending → approve → write_file executes → file written.
- Open question: opt-in auto-approve toggle (per-action approval = many clicks).

## Verification
Per phase: `tsc --noEmit` + `vite build` + encoding guard; probe the endpoint
with curl where useful. Full live behaviour needs the llama.cpp server + a real
project; flagged where it applies.
