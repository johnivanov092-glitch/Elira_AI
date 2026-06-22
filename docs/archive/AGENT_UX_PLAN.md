# Agent UX Plan — tool-call cards, icons, live status, token counter

Goal: bring the Code Agent transcript closer to the HF/llama.cpp "Chat with your
model" UX — per-tool icons, a collapsible "N tool calls" group, live in-progress
status with an elapsed timer, real token counter (`used / context`) + `tok/s`,
source chips, and composer mode chips. Reasoning / "Thought for Ns" is not in
scope for this pass.

Branch: `claude/agent-ux-toolcalls` (off `claude/security-critical-auth`).
Each phase = one small reviewable commit. Per repo rules: focused tests first,
then full `pytest -q` once, `tsc` once, Opus review per commit, push, stop.
This belongs in the **Code Agent** tab (CodeAgentChatShell) — the Chat Agent is
intentionally boundaried (no shell/sandbox); see `docs/AGENT_BOUNDARY_REVIEW.md`.

## Already works — DO NOT rebuild

- Streaming events from `stream_code_agent` (`backend/app/application/code_agent/
  agent_loop.py`): `run_started`, `step_started`, `tool_call` {step, tool,
  arguments, result, ...}, `final_response`, `context_compacted`, `done`
  {ok, steps, stop_reason, error?}.
- Tools: `web_search`, `web_fetch`, `sandbox_run` (Python in isolated venv),
  `run_bash`, file ops, `recall`, `tool_search` (`code_agent/tools.py`).
- Token/latency capture: `backend/app/application/monitoring/inference.py`
  `extract_llm_usage(response)` -> {prompt_tokens, completion_tokens,
  total_tokens, eval_count, tokens_per_second}, called per step at
  agent_loop.py (record_inference_telemetry). NOT yet emitted to the stream.
- Frontend transcript: `frontend/src/components/CodeAgentChatShell.tsx` —
  renders the events, has a `ToolBlock` component (collapsible per tool via
  ChevronDown/Right), lucide icons, `UiIcon`/`IconText` (StatusPanels), and a
  header context chip `{tokenEstimate} / {numCtx}` (lines ~1015/1032) that today
  uses the coarse client `estimateTokens` heuristic.
- Stream event types: `frontend/src/api/codeAgent.ts` `CodeAgentStreamEvent`
  union (done at ~line 83), `estimateTokens` (~line 586).

## Phases

### Phase 1 — Real token counter + tok/s  (backend + frontend; START HERE)
The header chip exists but shows a client estimate. Surface the real numbers.
- Backend: accumulate usage across steps (sum completion_tokens; keep the last
  step's prompt_tokens as "context used"; carry tokens_per_second of the last
  step). Add a `usage` object to the `done` event, and optionally emit a
  per-step `usage` event after each LLM call for live updates. Reuse
  `extract_llm_usage` — do not add a second telemetry path.
- Frontend: extend the `done` (and optional `usage`) event type in codeAgent.ts;
  in CodeAgentChatShell prefer real `total`/`prompt` tokens for the header chip
  when present, fall back to `estimateTokens`. Show `tok/s` near the chip.
- Files: `agent_loop.py` (done/usage emit), `api/codeAgent.ts` (types),
  `components/CodeAgentChatShell.tsx` (chip + tok/s).
- Accept: after a run, the header shows real `used / ctx`; a `tok/s` readout
  appears; with telemetry disabled it falls back to the estimate; tests green.

### Phase 2 — Per-tool icons by type  (frontend)
Map tool name -> icon instead of the generic Wrench: globe for `web_search`/
`web_fetch`, code/`</>` for `sandbox_run`/`run_bash`, file icons for read/write/
edit/glob/grep, memory icon for `recall`, search icon for `tool_search`.
- Files: `components/CodeAgentChatShell.tsx` (or a small `toolIcon.ts` helper).
- Accept: each tool row/`ToolBlock` shows a type-appropriate lucide icon;
  unknown tools fall back to Wrench.

### Phase 3 — "N tool calls" collapsible group  (frontend)
Wrap a turn's `ToolBlock`s under one collapsible header "N tool calls" (like the
GIF), collapsed by default once the turn is complete.
- Files: `components/CodeAgentChatShell.tsx`.
- Accept: a multi-tool turn renders one group header with a count + chevron;
  expanding shows the individual tool blocks unchanged.

### Phase 4 — Live status pill with elapsed timer  (frontend)
While a tool call is in flight, show "<verb> <short-arg>… Ns" with a running
seconds counter (Loader2 spinner already exists). Clear on tool result.
- Files: `components/CodeAgentChatShell.tsx`.
- Accept: an in-progress search shows "Searching <query>… Ns"; the timer stops
  and the row becomes a normal tool block when the result arrives.

### Phase 5 — Source citation chips  (frontend)
Render `web_search` results ({title, url, snippet}) as favicon chips under the
answer (favicon via the site origin). Read-only display of data already returned.
- Files: `components/CodeAgentChatShell.tsx` (+ maybe a `SourceChips` component).
- Accept: after a web_search, a row of source chips appears; clicking opens the
  URL via the existing Tauri shell-open path. Favicons load through the local
  backend proxy with SSRF guard so Tauri CSP does not allow arbitrary remote
  image loads.

### Phase 6 — "Thought for N seconds" reasoning  (NOT NEEDED)
Do not implement. We intentionally skip reasoning / "Thought for Ns" UX for
this product pass. The server stays with reasoning disabled; no frontend work
should depend on reasoning events.

### Phase 7 — Composer mode chips (Search / Code)  (DONE)
UI toggles over existing capabilities. Code keeps the default code-agent tool
set; Search starts the run with `web_search` / `web_fetch` already active.
No Think chip in this pass.

## Verification (every phase)
```powershell
cd D:\AIWork\Elira_AI
backend\.venv\Scripts\python.exe -m pytest -q            # full once, after focused
npm --prefix frontend run typecheck
npm --prefix frontend run build                          # when bundle output matters
git diff --check
```
Backend smoke: `from app.main import app` imports; routes/paths unchanged unless
a route is added.

## Status
- Current local implementation: Phase 2-5 done. Phase 4 adds additive
  `tool_started` stream event for a real elapsed timer. Phase 6 is explicitly
  skipped as not needed; Phase 7 adds Search / Code composer chips.
- [x] Phase 1 — real token counter + tok/s (done; backend `usage` stream event +
      header chip uses real `prompt_tokens / num_ctx` + tok/s, estimate fallback)
- [x] Phase 2 — per-tool icons
- [x] Phase 3 — "N tool calls" group
- [x] Phase 4 — live status pill (`tool_started` stream event + frontend timer)
- [x] Phase 5 — source chips
- [x] Phase 6 — reasoning ("Thought for Ns") skipped / not needed
- [x] Phase 7 — composer mode chips (Search / Code)
