# Repository Instructions (Elira_AI)

Local AI agent: Tauri (Rust shell) + FastAPI/Python backend + React/Vite/TS
frontend, talking to a LAN llama.cpp server. Read `docs/README.md` for the map.

The UI rebuild, code-agent rewrite, and unified chat/code-agent core plans are
shipped and archived. For the repo map/guardrails read `docs/README.md`,
`docs/ARCHITECTURE.md`, `docs/PROJECT_MAP.md`, and `docs/UI_BASELINE.md`.

## Encoding & Russian text — CRITICAL (read before writing any file)

This repo is **UTF-8 WITHOUT BOM, LF line endings**. Russian strings must stay
readable Russian (e.g. `привет` stays `привет`) — never let them turn into
garbled Latin-1 sequences. That corruption ("mojibake") comes from cp1251/UTF-8
round-trips and from a BOM being added by Windows tooling.

Rules:
- Author/save every text file as **UTF-8, no BOM, LF**.
- **Do NOT use PowerShell 5.1 `Set-Content`/`Out-File -Encoding utf8`** — it
  writes a BOM. Use the editor's write, or `python` (`open(...,encoding="utf-8")`),
  or PS7 `-Encoding utf8NoBOM`, or `[System.Text.UTF8Encoding]::new($false)`.
- Keep priority Russian strings correct and written in proper Russian. The
  highest-value ones: `backend/app/core/persona_defaults.py` (persona prompts),
  `frontend/src/elira_ru_labels.ts`, and any UI labels / system prompts.
- Never "fix" Russian by transliterating or escaping it away — keep it Cyrillic.
- If you see mojibake, repair with `app.utils.text_encoding.repair_mojibake_text`
  / `repair_mojibake_payload`; do not hand-edit byte soup.

Enforcement (must stay green): `backend/tests/test_text_encoding_persona_mojibake.py`
scans `backend/app`, `frontend/src`, `src-tauri`, `docs/`, root `*.md`, and
`*.bat` for mojibake AND for a UTF-8 BOM. A red suite means you introduced bad
encoding — fix it, don't suppress the test.

## Working rules
- Reuse existing runtimes/modules — do NOT add a second executor, tool registry,
  provider, or DB layer (see `docs/ARCHITECTURE.md` → Runtime Guardrails).
- Reuse the project's existing `lucide-react` icons in the frontend; don't add a
  new icon set.
- Prefer minimal, focused changes; match surrounding style.

## Gates (run before finishing)
```powershell
cd D:\AIWork\Elira_AI
npm --prefix frontend run typecheck
npm --prefix frontend run build
backend\.venv\Scripts\python.exe -m pytest -q
```
Each commit on a named branch is reviewed by the Opus gate
(`.claude/hooks` writes `.claude/review/<sha>.md`, first line `VERDICT: PASS`).
Commit/push only when asked; create named branches so work is visible.

## Agent skills

### Issue tracker

Tasks live as markdown under `.scratch/<feature>/` (local; no `gh` required).
See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage` / `needs-info` / `ready-for-agent` /
`ready-for-human` / `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context; domain language lives in `docs/ARCHITECTURE.md` +
`docs/PROJECT_MAP.md` (no `CONTEXT.md` yet). See `docs/agents/domain.md`.
