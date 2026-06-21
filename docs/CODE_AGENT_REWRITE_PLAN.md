# Code Agent → Claude Code / Codex-style runtime — consolidation plan

Goal: make the Code Agent behave and feel like Claude Code / Codex. The UI polish
(tool-call cards, icons, token counter) is done (see AGENT_UX_PLAN.md); this plan
fixes the **runtime behaviour**, which is the real problem.

Branch: `claude/agent-ux-toolcalls` (continue) or a fresh
`claude/code-agent-streaming`. Per repo rules: focused tests, full `pytest -q`
once, `tsc` + `build` once, Opus review per commit, push, stop. Code Agent only
(Chat Agent stays boundaried unless Phase E is approved). Self-contained so Codex
can pick up any phase.

## Grounded diagnosis (reproduced 2026-06-15, live runs vs the local 27B server)

Driving `/api/code-agent/stream` as a user:
- "echo hello_elira": OK in **5.0s** (run_bash → answer).
- "create calc.py with add(a,b) and run it": **~300s on a single LLM call** →
  the 180s run deadline (`DEFAULT_MAX_EXECUTION_SECONDS`) was blown mid-call →
  `write_file` refused → the model **invented** a failure ("система не
  подтвердила операцию в установленное время") → **no file created** → run still
  reported **`done ok=True`**. The screenshot's `Read timed out (120.0)` is the
  same root cause hitting `LLAMA_SERVER_TIMEOUT_SECONDS=120` instead.

Root cause chain:
1. The local 27B model is slow/verbose; one generation can take minutes.
2. LLM calls are **non-streaming** (`chat_completion` blocks for the full
   response) → UI freezes, and a single read hits `LLAMA_SERVER_TIMEOUT_SECONDS`
   (120s) or the run deadline (180s).
3. A blown deadline mid-call refuses the pending tool, the model invents an
   outcome, and the run is still marked `ok=True`. Trust-breaking.
4. Secondary: ~**2560-token** base prompt every call; `tok/s` always **0**
   (`extract_llm_usage` reads llama-native `eval_duration`, absent on the
   OpenAI-compatible endpoint).

The model speed itself is a server concern (`Elira_AI_Server` MODELS.md plans a
fast 7-8B for routine steps) — out of this repo, but the biggest external lever.

## Already works — DO NOT rebuild
- Event stream + tools + per-tool UI cards/icons/token chip (AGENT_UX_PLAN, done).
- `extract_llm_usage` / `record_inference_telemetry` (monitoring/inference.py).
- Provider client `backend/app/infrastructure/llm/openai_compatible.py`
  (`chat_completion`, non-streaming) — extend, don't replace.

## Phases

### Phase A — Token streaming (THE lever; fixes #1 + the "feel")
- Backend: add a streaming call in `openai_compatible.py` using the OpenAI
  `stream:true` SSE that llama-server supports; yield content deltas and
  assemble streamed `tool_calls` deltas into the final message. Keep
  `chat_completion` (non-stream) as a fallback path. Streaming makes the read
  timeout per-chunk, so long generations no longer time out.
- `stream_code_agent` (`code_agent/agent_loop.py`): consume the streamed deltas,
  emit new `token`/`delta` events (and still emit the existing `tool_call`/
  `usage`/`final_response`). `_local_chat` gets a streaming sibling.
- Frontend (`codeAgent.ts` + `CodeAgentChatShell.tsx`): append `delta` text live
  to the in-progress assistant turn (the Claude Code typing feel).
- Accept: a long task (landing page) streams visible tokens and does NOT hit a
  read timeout; tool calls still parse; tests green.
- Risk: streamed tool-call assembly. Document the OpenAI delta shape
  (`choices[].delta.tool_calls[].{index,function.{name,arguments}}`).

### Phase B — Reliability: never fake success
- A blown run deadline / timeout must produce an **honest** terminal result:
  `done ok=False` with `stop_reason="deadline"|"timeout"`, a truthful message,
  and the refused tool reported as refused — not a model-invented excuse, not
  `ok=True`. Do not dispatch tools after the deadline silently.
- Verify side effects: report the real `write_file`/`run_bash` result (e.g.,
  confirm the file exists) rather than trusting the model's narration.
- Re-tune `DEFAULT_MAX_EXECUTION_SECONDS` / `LLAMA_SERVER_TIMEOUT_SECONDS` for
  realistic local-model latency once streaming lands (deadline becomes a soft
  budget, not a hard mid-call cut).
- Files: `agent_loop.py` (wrap-up + deadline logic), `code_agent_routes.py`.
- Accept: a deadline run shows the truth and `ok` reflects reality; a refused
  tool is labelled refused; the calc.py repro either succeeds or fails honestly.

### Phase C — Trim base context
- Cut the ~2560-token base prompt + tool schemas sent each call. Ensure deferred
  tool search (P10.1) only ships **active** tools' schemas; shorten `prompts.py`.
- Files: `code_agent/prompts.py`, `tool_schemas.py`, schema-collection path.
- Accept: prompt_tokens on a trivial task drop materially; behaviour unchanged.

### Phase D — Fix tok/s + usage on the OpenAI endpoint
- `extract_llm_usage` should read OpenAI `usage` + llama-server `timings`
  (`predicted_per_second`/`predicted_ms`) or compute tok/s from measured elapsed,
  instead of the absent llama-native `eval_duration`.
- Files: `monitoring/inference.py` (+ a test).
- Accept: `tok/s` > 0 during/after a run; token count unchanged.

### Phase E — Unify Chat + Code into one Claude-Code-style agent (OPTIONAL, needs go)
- One agent loop + one transcript, tool access gated by a mode/policy layer
  (keep the safety from `AGENT_BOUNDARY_REVIEW.md` — unify the runtime, not
  remove the read-only guardrails). Biggest structural change; spec before code.
- Decide: does "Chat" become Code-Agent-in-readonly-mode, or stay a thin caller?

## Verification (every phase)
```powershell
cd D:\AIWork\Elira_AI
backend\.venv\Scripts\python.exe -m pytest -q
npm --prefix frontend run typecheck
npm --prefix frontend run build
```
Live check: drive `/api/code-agent/stream` with the calc.py repro; a long task
must stream tokens and finish honestly.

## Status
- [ ] Phase A — token streaming
- [ ] Phase B — reliability (no fake success)
- [ ] Phase C — trim base context
- [ ] Phase D — fix tok/s
- [ ] Phase E — unify chat+code (optional, needs approval)
