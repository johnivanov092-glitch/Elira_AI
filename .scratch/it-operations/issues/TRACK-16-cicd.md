# TRACK-16-cicd — CI/CD, Source Control and Deployments

- **Layer:** Delivery ops
- **Status:** planned (not now)
- **Depends on:** PHASE-5 (Config), PHASE-4 (Infra)

## Scope
- Git state, runner health, build/test artifacts, deployment targets.
- Release flow: build -> test -> artifact hash -> deploy -> health -> rollback to prior artifact.
- Push, production deploy, secret rotation, destructive git ops = explicit policy/approval.

## Definition of Done (behavior, not labels)
- Release flow hashes the artifact, deploys, health-verifies, and rolls back to the prior artifact; destructive ops are approval-gated.
