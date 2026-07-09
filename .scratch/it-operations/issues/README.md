# IT Operations — issues index

One file per phase / track. Core phases 0–9 are the implementation order; tracks
10–20 are future domain tracks (planned, not now — captured for the capability
matrix, dependencies, and ordering).

**Program plan:** [docs/IT_OPERATIONS_PLAN.md](../../../docs/IT_OPERATIONS_PLAN.md) ·
**PRD:** [../PRD.md](../PRD.md) · **Foundation detail:** [../FOUNDATION_IMPL.md](../FOUNDATION_IMPL.md)

## Core ops (implementation order)
| # | Issue | Depends on |
|---|---|---|
| 0 | [Foundation / contract](PHASE-0-foundation.md) | — |
| 1 | [Connection Enrollment](PHASE-1-enrollment.md) | 0 |
| 2 | [Asset Scope and Policy](PHASE-2-asset-scope.md) | 1 |
| 3 | [Network Inventory (read-only)](PHASE-3-network-ro.md) | 2 |
| 4 | [Infrastructure Operations](PHASE-4-infra.md) | 3 |
| 5 | [Configuration Operations](PHASE-5-config.md) | 4 |
| 6 | [Database Operations](PHASE-6-database.md) | 5 |
| 7 | [Device Adapters](PHASE-7-device-adapters.md) | 6 |
| 8 | [Reporting, UI and Operations](PHASE-8-reporting-ui.md) | 7 |
| 9 | [Verification and Release Discipline](PHASE-9-discipline.md) | 0..8 |

## Future domain tracks (planned, not now)
| Track | Issue | Layer | Depends on |
|---|---|---|---|
| 10 | [Identity and Access](TRACK-10-identity.md) | Platform | 0, 2 |
| 11 | [Storage, Backup and Restore](TRACK-11-storage-backup.md) | Platform | 4 |
| 12 | [Virtualization and Compute](TRACK-12-virtualization.md) | Platform | 4 |
| 13 | [Containers and Orchestration](TRACK-13-containers.md) | Platform | 4 |
| 14 | [Monitoring, Logging and IR](TRACK-14-monitoring-ir.md) | Platform | 3, 4 |
| 15 | [Patch and Package Lifecycle](TRACK-15-patch.md) | Delivery | 4, 5 |
| 16 | [CI/CD, SCM and Deployments](TRACK-16-cicd.md) | Delivery | 5, 4 |
| 17 | [Cloud and SaaS Adapters](TRACK-17-cloud-saas.md) | Delivery | 2 |
| 18 | [Endpoint and Asset Management](TRACK-18-endpoint.md) | Fleet | 3, 10 |
| 19 | [Security Posture and Vuln Advisory](TRACK-19-security-posture.md) | Fleet | 3, 18 |
| 20 | [Automation and Scheduling](TRACK-20-automation.md) | Fleet | all read/change stable |

**Adapter admission rule:** a new tool/adapter ships only after three repeated
real task gaps.
