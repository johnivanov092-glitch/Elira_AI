# Agent Routing Live Evals

The routing eval suite verifies the real user path, not an isolated model call:

```text
CLI case
  -> POST /api/code-agent/stream
  -> profile router
  -> agent loop
  -> capability / builtin / IT Ops / MCP tools
  -> public SSE events
  -> contract checks + report
```

## Run

Start the normal backend, then run from the repository root:

```powershell
backend\.venv\Scripts\python.exe -u backend\tests\smokes\routing_eval.py
```

Run selected cases:

```powershell
backend\.venv\Scripts\python.exe -u backend\tests\smokes\routing_eval.py `
  --only engineering_project,mcp_context7
```

The default client deadline is disabled. A run continues until the agent
finishes, the Workflow Stop path cancels it, or a lower-level transport/tool
failure occurs. `--timeout N` adds an explicit eval-client socket deadline when
one is required for CI.

## Coverage

`backend/tests/smokes/routing_cases.json` covers:

- Auto routing to Personal, Balanced, Engineering, Business, Infrastructure,
  Science, and Medicine profiles;
- starter capability groups for each routed profile;
- project-file reading and exact answer grounding;
- downloadable document generation;
- typed TCP inventory without a shell fallback;
- web research for scientific and medical requests;
- on-demand MCP discovery, start, tool use, and stop;
- the negative MCP case: an ordinary chat must not start an integration.

Each case can assert the effective profile, initial/final runtime activation,
successful ordered tool sequences, forbidden operation families, MCP servers,
answer fragments, citations returned by source tools, network states grounded
in typed inventory output, and the maximum number of tool calls.

## Observability and reports

The driver derives results only from the public Workflow SSE contract. It
records the effective profile, initial/dynamic/final runtime activation,
successful and failed tools, ordered MCP lifecycle operations, answer/source
URLs, typed network observations, stop reason, TTFT, duration, token usage, and
generation speed.

Every run writes ignored local artifacts under:

```text
.agent/evals/agent-routing/<suite-id>/
  report.md
  results.json
  events/<case>.jsonl
  projects/<case>/...
```

The command exits non-zero when any contract fails. Unit coverage for the
reporter and evaluator lives in `backend/tests/test_live_eval_driver.py`; live
model runs remain opt-in because they require the backend and LAN inference
server and can take several minutes.
