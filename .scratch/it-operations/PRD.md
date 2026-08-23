# Elira IT Operations — исторический PRD

> **SUPERSEDED (2026-08-23):** этот план не является действующим контрактом.
> Отдельные `/api/itops/*`, operation scopes, feature flag, Assets Settings UI и
> change-executor удалены. IT Ops теперь вызывается агентом через единый
> `runtime_control`, подтверждения живут в Workflow UI, а секреты — в portable
> AES-GCM vault. Текущий контракт: `../universal-agent-workflow/PRD.md` и
> `../../docs/AGENT_ARCHITECTURE_GUIDE_RU.md`.

**Status:** superseded / historical reference · **Owner:** principal engineer · **Date:** 2026-07-10
**Current architecture:** [docs/AGENT_ARCHITECTURE_GUIDE_RU.md](../../docs/AGENT_ARCHITECTURE_GUIDE_RU.md)

## 1. Problem & goal

Elira is a strictly-local sysadmin/coding agent. Today it can run shell/SSH
through the code-agent, but it has **no first-class notion of a saved connection,
an asset, an operation scope, a snapshot, or a change lifecycle**, and **no secret
vault** — the only crypto is a single Fernet key at `data/elira_secret.key` used
by an `encrypt` tool. That is not a foundation for real IT operations.

**Goal:** a user can, from chat or Settings, save a new connection (endpoint +
user + auth), enroll an asset, and then run network / infrastructure /
configuration / database tasks **only inside an explicitly approved scope**, with
the runtime — not the model — owning credentials, scope, approval, snapshot,
verification, rollback, and the final honest status.

## 2. Non-negotiable principle

> **The LLM proposes an action. The runtime owns asset scope, credentials,
> approval, snapshot, verifier, rollback, and the final status.**

Corollaries (hard constraints on the whole program):

- **No second executor**, no second tool registry, no second DB layer, no huge
  base prompt. Reuse the existing `execute_tool` kernel, `ToolSpec` registry,
  `connect_sqlite` infrastructure stores, and the verifier/final-report layer.
- **New ops-tools are deferred** and activated only for an *allowed asset type*
  in the current run — never in BASE_TOOLS (compaction canary), bit-identical
  when the feature flag is off.
- **The model cannot** expand scope, save an asset, or replace a secret. Only
  user intent + runtime approval.

## 3. Security contract (the spine of the program)

1. **Secrets never touch** LLM context, chat transcript, memory, run journal,
   tool output, approval text, telemetry, exports, or markdown docs. A secret =
   password, private key, token, or connection string.
2. **Redaction-after-write is insufficient.** (Confirmed in code: `core/redaction.py`
   redacts *display* surfaces, but `args_sha256` and dispatch see the *raw* args.)
   We need **secure intake BEFORE persistence and BEFORE the LLM ever runs**.
3. **Natural-language "save a connection"** opens a secure connection card in the
   UI. Secret fields are POSTed to a **separate hardened endpoint**; the model is
   handed only a `secret_ref` (opaque handle), never the value.
4. **Primary vault = Windows Credential Manager** (per-user, OS-bound). DPAPI is
   how Credential Manager protects secrets under the hood — an OS-internal detail;
   the **application never invokes DPAPI or persists a ciphertext blob**. There is
   **no** separate second vault (a future backend would be its own ADR).
   The existing Fernet key in `data/` is **not** the ops vault (it may remain for
   the unrelated `encrypt` note tool). **No Fernet fallback for the Linux
   AI-server:** the AI-server is inference-only and must never store or receive
   ops credentials. If Elira's backend is ever run on a separate Linux host, that
   is a separate decision with its own threat model — not a fallback of this
   Foundation. This program is a **Windows-host** design.
5. **Password is temporary by default:** bootstrap only → install a selected
   public key → verify key login → **delete the temporary password**. A
   persistent password is allowed **only as an explicit opt-in fallback**.
6. **No `sshpass`, no password in argv, no private key in tool output, no second
   SSH provider.** Standard operations use the existing OS OpenSSH /
   `~/.ssh/config` / current SSH path. (Confirmed: SSH today is OS OpenSSH via the
   shell tools, no Paramiko.)
7. **First connect shows the host-key fingerprint** and requires explicit user
   confirmation before anything is saved permanently.
8. **Scope is user-granted, not model-granted.** Connecting to a host does **not**
   grant the right to scan its networks: the runtime reads routes/interfaces,
   *proposes* a CIDR, and the user confirms network scope **per run**.

### Secret intake data-flow (target design)

```
User → [Secure Connection Card] --secret fields--> POST /api/itops/secrets (hardened)
                                                      │  writes value to Windows
                                                      │  Credential Manager
                                                      ▼
                                              secret_ref (opaque)  ──► asset/profile record
                                              (it_ops.sqlite3 holds the ref + STATE only,
                                               never the value)
                                                      │
   LLM run  ──proposes ssh op on asset_id──►  runtime resolves secret_ref → transport
                                              (secret injected into OS OpenSSH out-of-band,
                                               NEVER into args, output, journal, or model msg)
```

The model's entire view is: `asset_id`, `alias`, `secret_ref` *state* (present /
temporary / rotated / revoked), never the value.

## 4. Domain model (terminology — fixed in docs, one runtime)

| Entity | Fields (essential) | Notes |
|---|---|---|
| **Asset** | id, label, kind, endpoint, tags, owner_scope, lifecycle_state | kind ∈ linux/windows/network_device/local_workspace/ssh_config_scope/database/… |
| **ConnectionProfile** | asset_id, transport, user, auth_ref (=secret_ref), ssh_alias, host_key_fingerprint, os_platform_meta, last_health | 1..n per asset |
| **OperationScope** | run_id, allowed_asset_ids, cidrs, local_roots, service_ids, config_roots, db_profiles/schemas, mode (read_only \| change) | **per-run**, runtime-owned |
| **Snapshot** | id, change_run_id, before_state, hash, captured_at, rollback_ref | before any change |
| **Evidence** | target_identity, scanner_vantage, timestamp, operation, result, exit/status, structured_facts | ties every finding to *who/where/when/what* |
| **ChangeRun** | lifecycle on its OWN axis `change_run_status`: planned → approved → snapshotted → applied → committed / applied_pending_verification / rollback_in_progress → rolled_back / rollback_failed | state machine over the existing verifier; `completion_status` (task axis) stored separately |

**Two axes, never mixed.** The **task axis** `completion_status` stays as-is
(`confirmed | partial | failed | unverified | n/a`, from CriteriaTracker). The
**lifecycle axis** `change_run_status` tracks the change itself. The `done` event
and UI/report show **both**. E.g. verifier red + auto-rollback ok →
`completion_status=failed`, `change_run_status=rolled_back`; verifier red +
rollback failed → `completion_status=failed`, `change_run_status=rollback_failed`;
partial/unverified after apply → `change_run_status=applied_pending_verification`.

**Rollback is not universal.** Each adapter declares `rollback_kind ∈ {automatic,
manual, none}`. The **automatic compensating rollback is a runtime-internal action,
NOT a model-callable tool and NOT in CRITICAL_TOOLS** — bound to the exact approved
ChangeRun+snapshot, fired only after a real failed targeted verifier, and only when
the snapshot is genuinely reversible AND `rollback_kind=automatic`; it does not
seek a second approval. A `partial`/`unverified` outcome after a normal final must
**never** silently roll back or commit — `change_run_status=applied_pending_verification`
with an explicit next step. **Public `apply_change` and a manual `rollback_change`
are critical/approval-gated tools.** The approval card states the rollback strategy
up front.

**Scope is never widened by a tool-call.** A scope miss is **blocked before
dispatch** — there is no "fall through to approval". Widening scope is a separate,
runtime-owned scope-request flow with explicit user approval; the model cannot
turn an arbitrary out-of-scope call into an approval for a new target.

**Do not build a second domain runtime.** These persist in one new infrastructure
store (`it_ops.sqlite3` — metadata/assets/secret_ref state; the secret *value*
lives in Windows Credential Manager) and drive behavior through the existing
executor + verifier. **No second SQLite DB** for secrets.

## 5. Dynamic scope (how assets enter)

- User explicitly adds an asset in chat ("сохрани подключение …") or Settings.
- Example: Ubuntu w/ external IP + user/password → enrollment creates alias
  `ubuntu-client-01`, a ConnectionProfile, and an allowlist entry.
- `C:\Users\Root\Work` can be added as a **local workspace asset**.
- `%USERPROFILE%\.ssh` is a **narrow scope**: `config`, `known_hosts`, `*.pub`,
  and private-key *metadata* are allowed; **private keys cannot be read, copied,
  sent to the model, or shown**.
- Remote connection ≠ network-scan rights: runtime reads routes/interfaces →
  proposes CIDR → user confirms the run's network scope.

## 6. Capability matrix (supported / partial / planned / forbidden)

Legend: **S** shipped · **Par** partial today · **P** planned (this program) ·
**F** forbidden (out of scope, by policy).

### Foundation (Phase 0) — secrets, assets, scope, evidence, rollback
| Capability | State | Notes |
|---|---|---|
| Windows Credential Manager vault (DPAPI internal) + secret_ref | P | Windows-host; no Linux/Fernet ops-cred path |
| Secure intake endpoint (pre-persistence, pre-LLM) | P | secrets never reach model |
| Asset/ConnectionProfile/OperationScope/Snapshot/Evidence/ChangeRun schema | P | one infra store, migrated |
| Contract tests: no secret in persisted run/chat/event samples | P | DoD gate |
| Redaction of display surfaces | Par | exists (`core/redaction.py`) — necessary but insufficient |

### Core ops — network, hosts, services, configs, databases
| Track / Phase | State | Guardrail highlights |
|---|---|---|
| 1 Connection Enrollment | P | password→key bootstrap, temp secret delete, fingerprint approval |
| 2 Asset Scope & Policy | P | per-run enforcement in executor; model can't edit ACL |
| 3 Network Inventory (read-only) | P | explicit vantage; CIDR/port/rate caps; evidence-bound; CVE only w/ version evidence |
| 4 Infrastructure Ops | P | adapters (systemd/Win svc/IIS/Docker); inspect→snapshot→apply→health→rollback |
| 5 Configuration Ops | P | typed handlers; parse→diff→validate→snapshot→apply→reload→health→rollback |
| 6 Database Ops | P | secret_ref + allowed schemas; read-only first; backup+approval before write |
| Brute force / exploit / auth-scan / packet evasion / full-port-UDP / "scan everything" | **F** | never |
| `sshpass` / password-in-argv / key in output / second SSH provider | **F** | never |

### Platform ops (Tracks 10–14)
| Track | State | Depends on |
|---|---|---|
| 10 Identity & Access (local users/groups/sudo/SSH keys; AD/LDAP/Entra as named adapters) | P | Foundation, Scope; secret_ref lifecycle |
| 11 Storage/Backup/Restore (disks, SMB/NFS, NAS, object, backup systems) | P | Infra ops; "backup exists" ≠ success → restore/integrity verifier |
| 12 Virtualization/Compute (Hyper-V, Proxmox, VMware; cloud later) | P | Infra ops; snapshot+approval+post-health for power/resize/delete |
| 13 Containers/Orchestration (Docker/Compose first, K8s after) | P | Infra ops; inspect→validate→snapshot/tag→apply→health→rollback |
| 14 Monitoring/Logging/IR (Prom/Grafana, Zabbix, EventLog, journald) | P | Network+Infra; root cause never from one log line; hypothesis=advisory |
| Generic LDAP shell / generic provider shell adapter | **F** | named adapters only |

### Delivery ops (Tracks 15–17)
| Track | State | Depends on |
|---|---|---|
| 15 Patch/Package Lifecycle (apt/dnf/pacman, winget/choco, WU, lang PMs) | P | Infra+Config; reboot = explicit critical op; maintenance+rollback policy |
| 16 CI/CD, SCM, Deployments (git state, runners, artifacts, deploy targets) | P | Config+Infra; build→test→artifact hash→deploy→health→rollback; push/prod/secret-rotation critical |
| 17 Cloud & SaaS Adapters (AWS/Azure/GCP, DNS, mail, monitoring SaaS) | P | Scope; per-account/subscription scope + secret_ref; read-only inventory + audited change |
| Arbitrary cloud API endpoint/token tool | **F** | named adapters only |

### Fleet ops (Tracks 18–20)
| Track | State | Depends on |
|---|---|---|
| 18 Endpoint & Asset Management (workstation inventory, posture, EDR/Defender, certs) | P | Network+Identity; remote-control separate & always approval-gated |
| 19 Security Posture & Vuln Advisory (verified inventory + version/config evidence + sourced CVE) | P | Network+Endpoint; severity/confidence/advisory, never "proof of compromise" |
| 20 Automation & Scheduling (scheduled runs read-only by default) | P | ALL read/change workflows stable manually first; scheduled change = pre-approved named ChangeRun template w/ bounded scope+expiry+rollback |
| Exploit / brute-force / credential spraying / stealth-evasion | **F** | never |

## 7. Execution order & dependency graph

**Layers** (user's grouping):
- **Foundation:** secrets, assets, scope, evidence, rollback → *Phase 0*
- **Core ops:** network, hosts, services, configs, databases → *Phases 1–6*
- **Platform ops:** identity, storage, virtualization, containers, monitoring → *Tracks 10–14*
- **Delivery ops:** patches, CI/CD, cloud → *Tracks 15–17*
- **Fleet ops:** endpoints, security posture, scheduled automation → *Tracks 18–20*

**Strict order (do not skip):**
```
0 Foundation ──► 1 Enrollment ──► 2 Asset Scope ──► 3 Network(RO) ──► 4 Infra(inspect/health)
                                                          │
   ┌──────────────────────────────────────────────────────┘
   ▼
5 Config ──► 6 Database ──► 7 Device adapters ──► 8 Reporting/UI ──► 9 Verify/release discipline
```
Platform/Delivery/Fleet tracks (10–20) are **gated behind Core ops** and each
other as noted in §6; none start before Foundation + Scope + the relevant Core
phase are shipped and canary-verified.

**Adapter admission rule:** a new tool/adapter appears **only after three
repeated real task gaps** (same discipline that kept `web_crawl` out).

## 8. Definition of Done (per phase — enforced, behavior not labels)

- **0 Foundation:** contract tests prove a raw secret appears in **none** of:
  model messages, persisted chat, context summary, history, tool args,
  `args_sha256` input, approval display, SSE/events, journals, logs, error text,
  or API GET responses. The `itops` flag OFF disables new **endpoints, schemas,
  UI entrypoints AND deferred tools** (not just hides the UI). Schema migration is
  forward-only.
- **1 Enrollment:** raw secret absent from chat storage, journal, events, model
  messages, approval cards, logs; key-bootstrap canary; **failed enrollment
  leaves no enabled asset and no dangling secret**.
- **2 Scope:** out-of-scope target blocked *before dispatch*; explicitly-added
  target works; revocation blocks the *next* call immediately.
- **3 Network:** safe live canary on a user-owned lab subnet; wrong CIDR blocked;
  report distinguishes unknown/filtered/closed/open and does not overclaim.
- **4 Infra:** one Linux + one Windows live canary; abandoned runs leave no
  orphan process; failure → partial/failed, never confirmed.
- **5 Config:** valid apply; invalid-config rejected *before* reload;
  failed-health rollback; exact before/after evidence.
- **6 Database:** disposable DB canaries for read / failed migration / successful
  migration / restore-rollback; **no production DB mutation** in initial tests.
- **7 Device:** lab-only device canary; no production firewall changes in dev.
- **8 Reporting/UI:** compact UI; no-secret-leakage snapshot tests; revoked asset
  visibly fails.
- **9 Discipline:** ops smoke corpus (enrollment, key auth, scope denial, network
  RO, service health, config rollback, DB rollback); metrics tracked
  (false-confirmed count, partial correctness, tool count, duration, orphan
  process/connection count, rollback success).

## 9. Explicitly out of scope for this program

`web_crawl`, exploit tooling, generic "scan everything", brute-force,
credential spraying, stealth/evasion, generic LDAP/provider shell adapters,
arbitrary cloud-API token tools, and any broad new base-prompt additions.
