# Claude Task Template

Optional template for external coding-agent tasks. Adapt it to the actual request
and repository instructions; this document does not authorize a commit or push.

```text
FOCUSED IMPLEMENTATION:
- Maximize implementation, minimize narration.
- Do not restate obvious context.
- Inspect only files needed for this commit.
- No broad grep unless needed; prefer exact files from the preflight/task.
- Run checks appropriate to the change and required repository gates.
- Repeat checks when a fix or unresolved failure requires it, not without cause.
- Use an independent review when its scope justifies it; no particular model is required.
- Final report max 20 lines.
- Resolve routine blockers within scope; report a genuine external dependency precisely.
- Commit/push only when the user authorized it. Keep unrelated changes separate.

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
- Update affected documentation when behavior or usage changes.
- Preserve links, versions and attribution for external sources actually used.

Architecture constraints:
- No second executor.
- Reuse the current provider, tool registry and DB modules.
- Keep launch/recovery separate from replaceable application releases.
- Use current structured contracts; do not reintroduce removed semantic prefill routing.

Verification:
- State exact commands and actual results.
- For runtime changes, run the repository gates in AGENTS.md.
- Distinguish source tests, real-agent acceptance and deployed behavior.
- Before an authorized push, review the diff, encoding and staged scope.

Final report format, max 20 lines:
- SHA:
- Files changed:
- What changed:
- Tests:
- Review findings and remaining risk:
- Deviations/blockers:
- Next step:
```
