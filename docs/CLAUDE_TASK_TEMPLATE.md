# Claude Task Template

Use this header for implementation tasks that must stay small, reviewable, and
low on token/limit usage.

```text
STRICT TOKEN/LIMIT MODE:
- Maximize implementation, minimize narration.
- Do not restate obvious context.
- Inspect only files needed for this commit.
- No broad grep unless needed; prefer exact files from the preflight/task.
- No full-suite pytest until focused tests pass.
- Do not run full pytest more than once.
- Do not run tsc more than once.
- Do not spawn Opus until code + focused tests are final.
- Final report max 20 lines.
- If blocked, stop after the first real blocker; do not explore alternatives for hours.
- One commit only. Push. STOP.

Use strict bounded mode. This is one small commit, not the whole phase.

Task:
- <exact objective>

Base:
- Branch: <branch>
- Start SHA: <sha>
- Main must remain untouched unless explicitly told to merge.

Scope:
- Change only: <files/modules>
- Do not touch: <excluded areas>
- No unrelated refactors.
- No docs unless this is explicitly a docs task.
- Do not mention external idea sources.

Architecture constraints:
- No second executor.
- No second router.
- No new registry.
- No new database unless explicitly required.
- Reuse existing project modules and policy gates.

Verification:
- Run focused tests first.
- Run full pytest once only after focused tests pass.
- Run tsc once only.
- Get Opus PASS only after implementation and tests are final.
- Push branch.
- STOP.

Final report format, max 20 lines:
- SHA:
- Files changed:
- What changed:
- Tests:
- Opus verdict:
- Deviations/blockers:
- Next step:
```
