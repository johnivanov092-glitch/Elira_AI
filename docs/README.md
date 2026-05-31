# Elira AI — Documentation

Elira AI is a **fully local, private AI workspace**: a Tauri desktop app over a
FastAPI backend and local Ollama models, with everything — chats, memory, keys,
generated files — living on your own machine. This folder is the map of where
the project is and, more importantly, where it is going.

## Direction — where Elira is heading

The north star is a self-contained AI workspace that grows more capable as your
local hardware does:

- **Bigger local models, more autonomy.** The tool/skill system already works;
  reliability scales with model size, so the near-term target is 14–20B local
  models that can drive multi-step tool use and the code agent end to end.
- **Two first-class environments.** A planner-routed **chat** (memory, web
  search, document/image generation, autopipelines) and a **code agent** (file
  tools, SSH, MCP) — kept independent, each deepening over time.
- **Automation that compounds.** Autopipelines (scheduled tasks), the task
  planner, and the Agent OS layer (tool registry, plugins, event bus, workflows,
  monitoring) are the foundation for letting Elira run real work on a schedule,
  not just answer prompts.
- **Local-first, private by default.** No cloud dependency for the core loop;
  secrets stay in local env files, data stays on disk.

For the concrete next steps, start with the roadmap below.

## Where it is today

- **`ROADMAP_STABILIZATION_2026-03-29.md`** — the live roadmap: what is done,
  what is left, the logging follow-up, and the next priorities.
  **Start here for direction.**
- **`ACTUAL_WORK.md`** — the execution log: what was repaired, upgraded, and
  verified, plus the current web-search stack (Tavily / DuckDuckGo / Wikipedia
  with failover and local key wiring), internal time awareness, and current chat
  UX such as draft-first chat creation.

## Getting started

- **`README_Elira_AI.md`** (repo root) — setup, dependencies, startup order,
  launchers, and smoke checks.

## How to navigate

- To **install or run** the project → root `README_Elira_AI.md`.
- To see **what was actually repaired and verified** → `docs/ACTUAL_WORK.md`.
- To understand **status and where we go next** →
  `docs/ROADMAP_STABILIZATION_2026-03-29.md`.

## Archive

Historical notes live outside the top level so `docs/` stays focused on the
present and the road ahead:

- `archive/notes/` — one-off patch notes, migration notes, temporary checklists.
- `archive/stages/` — stage-by-stage implementation notes from earlier migration
  work.

Useful for context, but not the current source of truth.
