# Elira AI Docs

This folder now keeps only the current working documentation at the top level.

## Current docs

- `ACTUAL_WORK.md`
  Live work log: what was repaired, what was upgraded, what was verified, and what is queued next.
  Also includes the current Tavily/DuckDuckGo/Wikipedia web stack, local key wiring, failover behavior, hidden internal time awareness, and current chat UX behavior such as draft-first chat creation.

- `ROADMAP_STABILIZATION_2026-03-29.md`
  Current stabilization roadmap: what is done, what is left, logging follow-up, and next priorities.

## Source of truth

- `README_Elira_AI.md`
  Setup, dependencies, startup order, launchers, and smoke checks.

- `docs/ROADMAP_STABILIZATION_2026-03-29.md`
  Current project status, completed work, remaining work, logging follow-up, and next priorities.

- `docs/ACTUAL_WORK.md`
  Actual execution log for concrete repair steps: started, completed, verified, and queued follow-up.

If you need to know how to install or run the project, use the root README.
If you need to know what was actually repaired and verified, use `docs/ACTUAL_WORK.md`.
If you need the broader status and next priorities, use the roadmap in `docs/`.

## Archive

Historical notes were moved out of the top level to keep `docs` readable:

- `archive/notes/`
  One-off patch notes, migration notes, and temporary checklists.

- `archive/stages/`
  Stage-by-stage historical implementation notes from earlier migration work.

These archived files are useful for context, but they are not the current source of truth.
