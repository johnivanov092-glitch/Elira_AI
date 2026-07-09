# PHASE-9-discipline — Verification and Release Discipline

- **Layer:** Core ops
- **Status:** planned
- **Depends on:** PHASE-0..8

## Scope
- Every phase: focused tests -> full backend suite -> frontend typecheck/build (if UI) -> adversarial review -> live canary on a user-owned target.
- Ops smoke corpus: enrollment, key auth, scope denial, network read-only, service health, config rollback, DB rollback.
- Metrics: false-confirmed count, partial correctness, tool count, duration, orphan process/connection count, rollback success.
- Adapter admission rule: a new tool/adapter appears only after THREE repeated real task gaps.

## Definition of Done (behavior, not labels)
- Ops smoke corpus green; metrics tracked per phase.

## Forbidden in this issue
web_crawl, exploit tooling, generic 'scan everything', broad new base prompts.
