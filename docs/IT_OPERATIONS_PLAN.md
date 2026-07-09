# Elira IT Operations — Program Plan

**Status:** Foundation plan for approval · **Owner:** principal engineer · **Date:** 2026-07-10
**PRD:** [.scratch/it-operations/PRD.md](../.scratch/it-operations/PRD.md) · **Issues:** [.scratch/it-operations/issues/](../.scratch/it-operations/issues/)

This is a **program**, not a feature. It turns Elira from "an agent that can run
SSH/shell" into "a sysadmin agent that owns assets, scope, credentials, snapshots,
verification, rollback, and honest status". The plan is **code-grounded** — every
reuse decision below was verified against the live tree (not old reports).

---

## 0. The one principle

> **The LLM proposes an action. The runtime owns asset scope, credentials,
> approval, snapshot, verifier, rollback, and the final status.**

Enforced by four hard "no second X" rules, each satisfiable by REUSING what
already exists:

| "No second …" | Reuse instead | Evidence (live) |
|---|---|---|
| executor | `agent_kernel/executor.py:execute_tool` — one fail-closed gate ladder | statuses ok/error/blocked/forbidden/waiting_approval already exist |
| tool registry | one `tool_registry.db`, ops-tools = **deferred** ToolSpecs | `VALID_SCOPES` closed vocab incl. unused `secrets.read` |
| DB layer | one new infra store via `connect_sqlite` | `infrastructure/web_corpus/store.py` shape; `data/*.sqlite3` gitignored |
| SSH provider | `tool_providers/ssh_provider.py` (OS OpenSSH, key-only) | `BatchMode=yes`, TOFU host keys, 11 ssh_* tools incl. a verifier set |
| huge base prompt | **deferred activation per allowed asset type** | `_SSH_ACTIVATABLE_TOOLS`, compaction canary `num_ctx=8192` |

---

## 1. Terminology (domain model — fixed here, one runtime)

| Entity | Purpose | Persisted where |
|---|---|---|
| **Asset** | a thing we operate on: id, label, kind, endpoint, tags, owner_scope, lifecycle_state | `it_ops.sqlite3:assets` |
| **ConnectionProfile** | how to reach an asset: asset_id, transport, user, **auth_ref** (=secret_ref), ssh_alias, host_key_fingerprint, os_platform_meta, last_health | `it_ops.sqlite3:connection_profiles` |
| **OperationScope** | what a single run may touch: run_id, allowed_asset_ids, cidrs, local_roots, service_ids, config_roots, db_profiles/schemas, mode(read_only\|change) | **in-memory, run_id-keyed** (mirrors `deferred_tools.py`) + audit row |
| **Snapshot** | before-state for a change: id, change_run_id, before_state, hash, captured_at, rollback_ref | `it_ops.sqlite3:snapshots` + runtime-owned handle |
| **Evidence** | a fact tied to who/where/when/what: target_identity, scanner_vantage, timestamp, operation, result, exit/status, structured_facts | `it_ops.sqlite3:evidence` |
| **ChangeRun** | the mutation lifecycle, tracked on its OWN axis `change_run_status` (planned → approved → snapshotted → applied → committed / applied_pending_verification / rollback_in_progress → rolled_back / rollback_failed) | `it_ops.sqlite3:change_runs` (references `agent_monitor.db` approval by id; carries `rollback_kind`; stores `completion_status` separately) |

`kind ∈ {linux, windows, network_device, local_workspace, ssh_config_scope,
database, …}`. Adapters map a kind to an existing transport (linux/windows →
`ssh_provider`; network_device/MikroTik → existing `mikrotik` MCP; local_workspace
→ fs tools with `_resolve_safe`). Each **adapter declares `rollback_kind ∈
{automatic, manual, none}`** per operation — the runtime never assumes a change is
reversible. **Two axes never mix:** the task axis `completion_status`
(confirmed/partial/failed/unverified/n/a, unchanged) and the lifecycle axis
`change_run_status` above.

---

## 2. Reuse map (how each ownership area plugs into live code)

**Scope** → a new fail-closed gate INSIDE `execute_tool`, between the agent-scope
gate (`executor.py:~206`) and the approval gate (`~228`). Backed by a new
run_id-keyed in-memory module `agent_kernel/operation_scope.py`
(`bind_scope/get_scope/clear_run`, presence == scoped, absence == unaffected →
legacy back-compat). It classifies the tool by its **declared scopes** (reusing
`_resolve_tool_timeout` classification), extracts the target from `request.args`
(host / url→CIDR / path / service / db_profile), and checks membership. **A scope
miss ALWAYS returns `blocked` before dispatch — there is NO fall-through to
approval.** Widening scope is a **separate runtime-owned scope-request flow** with
explicit user approval (patterned on the inline `ssh_request_host` onboarding); the
model cannot turn an arbitrary out-of-scope call into an approval for a new target.

**When a scope IS bound, it covers the EXISTING transport tools too** — not just
new itops tools: `ssh_run`, `ssh_run_ps`, `ssh_write`, `ssh_read`, `ssh_replace`,
the host/assert tools, **and a raw-SSH redirect through `run_bash`** (fed by the
existing SSH-detection in the shell path) — each is checked against the bound
asset/host before dispatch. **New itops tools with no bound OperationScope fail
closed** (blocked). **An ordinary legacy code-agent run with NO bound
OperationScope keeps its current semantics** (the gate only bites when a scope is
bound). Scope violations also join the never-auto-approve set so bypass can't
defeat scope.

**Credentials** → a new vault module `infrastructure/secrets/` returning an opaque
`secret_ref`, resolved to the real value **only inside dispatch, after all gates**
— generalizing the proven `mcp_http_client._resolve_header_value` `${ref}`→value
pattern (value never in config/context/args/logs). **Primary vault = Windows
Credential Manager** (`CredWrite`/`CredRead`/`CredDelete`, per-user). DPAPI is how
Credential Manager protects secrets under the hood — an OS-internal detail; the
**application never invokes DPAPI or manages a ciphertext blob**. **No Fernet and
no cross-platform path** — the
Linux AI-server is inference-only and must never store or receive ops credentials;
this is a Windows-host design. The model only ever sees `asset_id` + `secret_ref` +
secret *state* (present/temporary/rotated/revoked). In Phase 0 the **only durable
store of a secret value is Windows Credential Manager**; `it_ops.sqlite3` holds
only the opaque `secret_ref` + metadata/lifecycle. DPAPI may be an internal detail
of the Windows credential stack, but the **application never creates or stores a
DPAPI blob** — nothing decryptable lives in the DB.

**Approval (public tools) vs automatic rollback (runtime-internal)** → REUSE
`monitoring/store.py` approvals in `agent_monitor.db` (`create_approval`,
`canonical_args_digest`, `find_approved_approval`, TTL). The RAW-args-digest vs
redacted-display split IS the secret contract's display half. `ChangeRun`
references the approval by `approval_id`. **Public, model-callable tools** —
`apply_change` and a **manual** `rollback_change` — are added to `CRITICAL_TOOLS`
(`tool_policy.py:63`, **currently empty**) so they are force-ask even in bypass,
and to the corpus-taint `SIDE_EFFECT_TOOLS` set. The **automatic compensating
rollback is NOT a model-callable tool and is NOT in `CRITICAL_TOOLS`**: it is a
runtime-internal action bound to the exact approved `ChangeRun` + snapshot +
`rollback_kind=automatic`, fired only after a real failed targeted verifier —
it compensates an already-approved apply, so it does not seek a second approval.
The approval card for `apply_change` states the rollback strategy up front.

**Snapshot + rollback** → CLONE the R2 server-lifecycle ownership: a run_id-keyed
snapshot registry mirroring `_LIVE_SERVERS`/`_ServerHandle` (`_run.py:326-446`),
running at the **same guaranteed terminals** as server-stop (answer path + the
`finally` at `agent_loop.py:~2505`). `_save_backup` (`_files.py:197`) is the
byte-snapshot storage precedent to generalize. **Rollback is NOT universal.** Each
adapter declares `rollback_kind ∈ {automatic, manual, none}`:
- **automatic compensating rollback** (runtime-internal, not a tool) fires ONLY on
  a failed targeted verifier AND a genuinely reversible snapshot AND
  `rollback_kind=automatic`, bound to the exact approved ChangeRun+snapshot. It
  drives `change_run_status`: `applied` → `rollback_in_progress` →
  `rolled_back` (or `rollback_failed` = honest `failed`). No second approval.
- **`partial`/`unverified` after a normal final** must NEVER silently roll back or
  silently commit → `change_run_status=applied_pending_verification` with an
  explicit next step surfaced to the user.
- **`rollback_kind=manual|none`** → the runtime does not auto-revert; it reports
  `applied_pending_verification` (+ the recorded manual procedure for `manual`). A
  **manual `rollback_change`** later requested by model/user is a public,
  approval-gated critical tool.
Decision key = **verify outcome**, never spec presence.

**Verifier + final status (TWO independent axes)** → REUSE `CriteriaTracker`: a
ChangeRun's intended post-state IS success criteria (`CriteriaTracker.from_spec`).
Post-apply, read-only checks flow through `_record_criterion_verdict` and the
bounded auto-verifier pass (`missing_verifier_actions`); **apply itself is EXCLUDED
from the auto pass** (side-effectful, like `run_server` today). The SSH verifier
tools (`ssh_assert_contains/ssh_exists/ssh_port_check`) ARE the remote read-only
verifier — do not rebuild.

The final report carries **two separate axes** — never merged:
- **`completion_status`** (task axis, UNCHANGED): `confirmed | partial | failed |
  unverified | n/a`, straight from `completion_status()` (`taskspec.py:1173`). Not
  extended.
- **`change_run_status`** (lifecycle axis, NEW): `planned | approved | snapshotted
  | applied | committed | applied_pending_verification | rollback_in_progress |
  rolled_back | rollback_failed`.

Both ship in the `done` event and the UI/report. Examples:
- verifier red + automatic rollback succeeded → `completion_status=failed`,
  `change_run_status=rolled_back`;
- verifier red + rollback failed → `completion_status=failed`,
  `change_run_status=rollback_failed`;
- normal final with partial/unverified after apply → (`completion_status=partial|
  unverified`) + `change_run_status=applied_pending_verification`.

**Transport** → `ssh_provider.py` for linux/windows (reuse `_ssh_args`,
`_read_remote_bytes`, `_write_remote_bytes`; inherit BatchMode/TOFU/atomic-write/
Windows-base64); existing `mikrotik`/`hass-mcp` MCP for devices. **No paramiko,
no sshpass, no second provider.**

**UI** → new "Активы / Подключения" Settings tab (3 edits in `Settings.tsx`, Lazy
pattern); secure connection card + scope picker in `IntegrationsSection.tsx`
(the `SshBlock` scope UI is the template). One deferred flag `itops`
(`feature_flags._ENV_VAR` + `elira_state` Literal + `ExperimentalSection`
FLAG_META — **two-file sync or PUT 422s silently**). Approval card renders
`JSON.stringify(args)` verbatim → **ops-tool args must carry only `secret_ref`**.

---

## 3. Threat model (must be answered by contract tests)

| Threat | Control | DoD gate |
|---|---|---|
| **Secret leakage** | secure intake before persistence & before LLM; value in Windows Credential Manager; secret_ref-in-args only; resolve inside dispatch; sink-level redaction on event_bus + run_journal; fix `GET /mcp/servers` verbatim leak; output canary masks any resolved value that reappears | raw secret in NONE of: model messages, persisted chat, context summary, history, tool args, `args_sha256` input, approval display, SSE/events, journals, logs, error text, API GET responses |
| **Prompt injection** | untrusted asset/web output never changes policy/scope/approval/activation; scope + approval are runtime-owned; model can't widen scope | injected "add host / widen CIDR" is ignored; enrollment needs a human click |
| **Wrong target** | fail-closed `_validate_host`; per-run OperationScope membership before dispatch; **scope miss = blocked (never auto-approved), widening only via a separate approved scope-request flow**; explicit scanner vantage | out-of-scope target blocked before dispatch; a tool-call cannot mint scope |
| **Stale state** | verifier re-checks CURRENT state at verify time (the W3 lesson: no cached verdict); host-key TOFU rejects a changed key | verify reflects live state, not record time |
| **Destructive rollback** | adapter-declared `rollback_kind`; automatic compensating revert (runtime-internal, not a tool) ONLY on failed verify + reversible snapshot + `rollback_kind=automatic`; `partial`/`unverified` → `change_run_status=applied_pending_verification` (never silent commit/revert); `rollback_failed` = honest `completion_status=failed`; each phase bounded | forced-fail canary auto-restores only where reversible; non-reversible surfaces `applied_pending_verification`; `rollback_failed` surfaces on both axes |
| **Disconnected remote host** | one fresh ssh/call, ConnectTimeout, heartbeat; a probe that can't run returns `ok=False` **without** `verifier:True` (never marks a criterion failed) | disconnected mid-run → partial/failed, no orphan, no false confirm |

---

## 4. Capability matrix

Legend: **S** shipped · **Par** partial today · **P** planned (this program) ·
**F** forbidden.

### Foundation (Phase 0) — secrets, assets, scope, evidence, rollback
| Capability | State |
|---|---|
| Windows Credential Manager vault (DPAPI internal) + secret_ref — no Linux/Fernet ops-cred path | P |
| Secure write-only intake (pre-persistence, pre-LLM) | P |
| Asset/ConnectionProfile/OperationScope/Snapshot/Evidence/ChangeRun store — ONE `it_ops.sqlite3`, forward-only migrations, no second secrets DB | P |
| Sink-level redaction (event_bus + run_journal) + fix `GET /mcp/servers` leak | P (closes live leaks) |
| `itops` flag OFF disables endpoints + schemas + UI + deferred tools (not just hides UI) | P |
| Contract tests: raw secret in none of the 12 surfaces (see §3) | P |
| Display redaction (`core/redaction.py`) | Par — necessary, insufficient |

### Core ops — network, hosts, services, configs, databases
| Phase | State | Guardrails |
|---|---|---|
| 1 Connection Enrollment | P | password→key bootstrap → verify → **delete temp secret**; persistent password opt-in only; fingerprint approval |
| 2 Asset Scope & Policy | P | per-run enforcement in executor; revocation blocks next call |
| 3 Network Inventory (read-only) | P | explicit vantage; CIDR/port/rate/timeout caps; evidence-bound; CVE only w/ version evidence |
| 4 Infrastructure Ops | P | adapters (systemd/Win svc/IIS/Docker); inspect→snapshot→apply→health→rollback |
| 5 Configuration Ops | P | typed handlers; parse→diff→validate→snapshot→apply→reload→health→rollback |
| 6 Database Ops | P | secret_ref + allowed schemas; read-only first; backup+approval before write |
| brute-force / exploit / auth-scan / packet-evasion / full-port-UDP / "scan everything" | **F** | |
| sshpass / password-in-argv / key-in-output / second SSH provider | **F** | |

### Platform ops (Tracks 10–14) — planned, not now
| Track | Depends on |
|---|---|
| 10 Identity & Access (local users/groups/sudo/keys; AD/LDAP/Entra as **named** adapters, never generic LDAP shell) | Foundation, Scope, secret_ref |
| 11 Storage/Backup/Restore ("backup exists" ≠ success → restore/integrity verifier; retention=critical) | Infra ops |
| 12 Virtualization/Compute (Hyper-V/Proxmox/VMware; power/resize/delete → snapshot+approval+post-health; no generic provider shell) | Infra ops |
| 13 Containers/Orchestration (Docker/Compose first, K8s after; inspect→validate→snapshot/tag→apply→health→rollback) | Infra ops |
| 14 Monitoring/Logging/IR (Prom/Grafana/Zabbix/EventLog/journald; root cause never from one line; hypothesis=advisory) | Network + Infra |

### Delivery ops (Tracks 15–17) — planned, not now
| Track | Depends on |
|---|---|
| 15 Patch/Package Lifecycle (apt/dnf/pacman, winget/choco, WU, lang PMs; reboot=explicit critical; maintenance+rollback policy) | Infra + Config |
| 16 CI/CD, SCM, Deployments (build→test→artifact hash→deploy→health→rollback; push/prod/secret-rotation critical) | Config + Infra |
| 17 Cloud & SaaS Adapters (AWS/Azure/GCP, DNS, mail; per-account scope + secret_ref; read-only inventory + audited change; no arbitrary API/token tool) | Scope, secret_ref |

### Fleet ops (Tracks 18–20) — planned, not now
| Track | Depends on |
|---|---|
| 18 Endpoint & Asset Management (posture, EDR/Defender, certs; remote-control separate & always approval-gated) | Network + Identity |
| 19 Security Posture & Vuln Advisory (verified inventory + version/config evidence + sourced CVE; severity/confidence/advisory, never "proof of compromise") | Network + Endpoint |
| 20 Automation & Scheduling (scheduled runs read-only by default; scheduled change = pre-approved named ChangeRun template, bounded scope+expiry+rollback) | ALL read/change workflows stable manually first |
| exploit / brute-force / credential-spraying / stealth-evasion | **F** |

---

## 5. Execution order & dependency graph

**Layers:** Foundation (secrets/assets/scope/evidence/rollback) → Core ops
(network/hosts/services/configs/databases) → Platform ops (identity/storage/
virtualization/containers/monitoring) → Delivery ops (patches/CI-CD/cloud) →
Fleet ops (endpoints/security-posture/scheduled-automation).

```
0 Foundation
  └► 1 Enrollment ─► 2 Asset Scope ─► 3 Network(RO) ─► 4 Infra(inspect/health)
                                                          └► 5 Config ─► 6 DB
                                                                          └► 7 Device adapters
                                                                               └► 8 Reporting/UI ─► 9 Verify/release discipline
   Platform/Delivery/Fleet tracks 10–20 are gated behind Foundation+Scope+their Core dependency (§4).
```

**Adapter admission rule:** a new tool/adapter ships **only after three repeated
real task gaps** — the same discipline that kept `web_crawl` out of the web track.

**Out of scope for the whole program:** `web_crawl`, exploit tooling, generic
"scan everything", brute-force, credential spraying, stealth/evasion, generic
LDAP/provider shell adapters, arbitrary cloud-API token tools, broad base-prompt
additions.

---

## 6. Definition of Done (behavior, not labels)

Per-phase DoD is in each issue file. Program-level invariants every phase honors:

- **Runtime owns truth, two axes:** the **task axis** `completion_status`
  (`confirmed | partial | failed | unverified | n/a`) comes from
  `completion_status()`; the **lifecycle axis** `change_run_status` (incl.
  `rolled_back` / `rollback_failed`) is runtime-owned. `runtime_final_report` and
  the `done` event show **both, separately** — never the model's word (the finalize
  scrubbers already neutralize model ✅ on non-confirmed runs).
- **Positive evidence, no denylists:** verify CONFIRMS the intended asset state
  (target + attribution); unknown → not confirmed. **Automatic compensating
  rollback fires ONLY** after a real failed *targeted verifier* AND a reversible
  snapshot AND `rollback_kind=automatic` — **never** on a mere "no ✅". A
  timeout/cancel/no_progress/disconnect/unverified/partial after apply does **not**
  silently roll back: `change_run_status=applied_pending_verification` with an
  explicit next step. Server/process cleanup on an abandoned terminal is a
  **separate** policy (runtime owns what it started), **not** a ChangeRun rollback.
- **Every guard/pass is bounded** with a terminal (closure ≤2, auto-verifier
  1×5, approval TTL 300s); each ChangeRun phase carries its own cap.
- **New verdict types the catalog way:** catalog row → verdict code → regression
  test → support update (per `VERIFIER_COVERAGE_CATALOG.md`); no matcher
  whack-a-mole.
- **Bit-identical when off:** the `itops` flag OFF disables new **API endpoints,
  schema init, UI entrypoints AND deferred ops-tools** (not just hides the UI) —
  schema list, activation set, base-prompt bytes, and tool_search output are all
  unchanged, and the `/api/itops/*` routes are inert.

---

## 7. Net-new builds vs pure reuse (honest scope)

**Genuinely new (the real work):**
1. Windows Credential Manager vault + `secret_ref` type + secure intake endpoint + resolution-inside-dispatch (Windows-host only).
2. `it_ops.sqlite3` store (assets/profiles/scopes/snapshots/evidence/change_runs + a secret-ref *state* table) + forward-only migration runner. **No second secrets DB.**
3. Per-run `OperationScope` (asset-instance scope, not just capability class) + the executor gate (**scope miss = blocked, no fall-through**) + a separate approved scope-request flow.
4. Runtime-owned snapshot/rollback lifecycle (run_id-keyed, guaranteed-terminal) on its OWN `change_run_status` axis, with adapter-declared `rollback_kind`; automatic compensating rollback is runtime-internal (not a tool, not in CRITICAL_TOOLS); public `apply_change`/manual `rollback_change` are critical.
5. Ops post-state verifier intents (service-enabled / config-key-set / package-version / firewall-rule) added the catalog way.
6. Closing live leak surfaces: sink-level redaction on event_bus + run_journal; fix `GET /mcp/servers`; output-canary for resolved values.

**Pure reuse (do not rebuild):** executor gate ladder, tool registry + deferred
activation, approvals + digest split, SSH transport + remote verifiers, criteria/
closure/final-report, server-lifecycle ownership pattern, feature-flag system,
Settings/approval UI primitives.

---

## 8. Migration & compatibility risks (program-wide)

- **`ssh_acl.json` compat:** the flat allowlist drives `is_ssh_enabled()`
  (enabled iff ≥1 host). The Asset store must be **additive** — do not flip SSH
  on/off as a side effect; migrate hosts into assets while keeping the ACL as the
  source of truth for the SSH provider until Phase 2 supersedes it.
- **Durable vs cache schema policy:** `it_ops` uses **forward-only** migrations;
  `web_corpus` uses drop-on-change. Same folder, opposite policy — documented so
  a reviewer can't copy the wrong one.
- **In-memory scope on restart:** run-scoped OperationScope (like deferred tools)
  is lost on restart → a rehydrated run must **re-bind or fail-closed**, never run
  unscoped.
- **Feature-flag two-file sync:** `_ENV_VAR` + `elira_state` Literal must change
  together (has silently 422'd toggles twice).
- **Windows-host vault, by design:** the vault is Windows Credential Manager
  (DPAPI is the OS-internal mechanism beneath it; the app never invokes it). There
  is **no Linux/Fernet path** — the AI-server is inference-only and never receives
  ops creds. Running Elira's backend on a separate Linux host later is a distinct
  decision with its own threat model.
- **Plaintext-at-rest migration:** existing plaintext secrets (telegram token,
  MCP `env`, dbhub DSNs, `.env.local`) should migrate into the vault + rotate; a
  compat shim is needed while old paths exist.
- **MCP auto-enable sweep:** `mcp_provider` flips unclassified MCP tools to
  `enabled+require_approval`; an ops adapter delivered as MCP must be forced
  `forbidden` until classified (`ELIRA_MCP_AUTO_ENABLE=0` for ops).

---

## 9. Phase 0 (Foundation) — detailed implementation plan

See [.scratch/it-operations/FOUNDATION_IMPL.md](../.scratch/it-operations/FOUNDATION_IMPL.md)
for files, schemas, migration strategy, API/UI contract, tests, and risks.
**Foundation ships and is reviewed BEFORE Connection Enrollment begins.**
