// Typed wrappers for the IT-Operations SSH vertical (backend: app/api/routes/itops_routes.py).
// All routes are gated on the `itops` feature flag → 404 when off. Bodies are passed as
// OBJECTS; request() serializes them (never pre-stringify — a string body 422s).
import { request } from "./client";

export type SshEffective = {
  ok: boolean;
  hostname: string;
  port: string | number;
  user?: string;
  identity_files?: string[];
  error?: string;
};

export type ObservedFingerprint = {
  ok: boolean;
  fingerprints: string[];
  note?: string;
  error?: string;
};

export type ItopsHealth = { status: string; reason?: string; hostname?: string; at?: number };

export type ItopsProfile = {
  profile_id: string;
  asset_id: string;
  transport?: string;
  user?: string;
  auth_ref: string | null;
  ssh_alias?: string;
  host_key_fingerprint: string;
  last_health: ItopsHealth;
};

export type ItopsAsset = {
  asset_id: string;
  label: string;
  kind: string;
  endpoint: string;
  lifecycle_state: string;
  profiles: ItopsProfile[];
};

export type PreviewResp = { ok: boolean; effective: SshEffective; observed_fingerprint: ObservedFingerprint };
export type EnrollResp = { ok: boolean; asset: ItopsAsset; profile: ItopsProfile };
export type VerifyResp = { ok: boolean; profile_id: string; health: ItopsHealth; detail?: unknown };
export type AssetsResp = { ok: boolean; assets: ItopsAsset[] };

// Resolve the alias's effective config (ssh -G, no network) + an OBSERVED, advisory
// host-key fingerprint. Saves nothing.
export async function sshPreview(ssh_alias: string): Promise<PreviewResp> {
  return request<PreviewResp>("/api/itops/ssh/preview", { method: "POST", body: { ssh_alias } });
}

// Save a draft asset + unverified profile. `fingerprint_reviewed: true` is the user's
// attestation that they compared the observed fingerprint out-of-band — the backend
// REQUIRES it (Literal[True]); this is only ever called once the confirm box is ticked.
export async function sshEnroll(args: { label: string; ssh_alias: string; kind?: string }): Promise<EnrollResp> {
  return request<EnrollResp>("/api/itops/ssh/enroll", {
    method: "POST",
    body: {
      label: args.label,
      ssh_alias: args.ssh_alias,
      kind: args.kind ?? "linux",
      fingerprint_reviewed: true,
    },
  });
}

// Verify a SAVED profile by id (never a browser-supplied host). Success promotes the
// asset to `enabled`; failure leaves it `draft`.
export async function verifyProfile(profile_id: string): Promise<VerifyResp> {
  return request<VerifyResp>("/api/itops/verify", { method: "POST", body: { profile_id } });
}

export async function listAssets(): Promise<AssetsResp> {
  return request<AssetsResp>("/api/itops/assets");
}

export type DiagnosticsAdapter = "healthcheck" | "linux_inventory" | "windows_inventory";

export type DiagnosticsStartResp = {
  ok: boolean;
  run_id: string;
  profile_id: string;
  adapter?: string;
  tool?: string;
  message: string;
  ttl_seconds: number;
};

// Start ONE scoped read-only diagnostic run for a saved, VERIFIED profile. The
// client picks an ADAPTER (enum) — never a tool name; the SERVER maps adapter→tool,
// mints the run_id, and binds a read-only scope to that ONE tool before the run.
// The caller then streams /api/code-agent/stream with THIS run_id + message.
export async function startDiagnostics(
  profile_id: string,
  adapter: DiagnosticsAdapter = "healthcheck",
): Promise<DiagnosticsStartResp> {
  return request<DiagnosticsStartResp>("/api/itops/diagnostics/start", {
    method: "POST",
    body: { profile_id, adapter },
  });
}

// ── evidence history (read-only) ──────────────────────────────────────────

export type EvidenceRun = {
  run_id: string;
  target_identity: string;   // "asset_id/profile_id"
  adapter: string;           // operation prefix, e.g. "linux_inventory"
  first_at: number;
  last_at: number;
  ok: number;
  failed: number;
  unsupported: number;
  count: number;
};

export type EvidenceRecord = {
  evidence_id: string;
  run_id: string;
  target_identity: string;
  scanner_vantage: string;
  operation: string;
  result: { alias?: string; command?: string; status?: string; stdout?: string; stderr?: string };
  exit_status: string;
  captured_at: number;
};

// Server-side summary of the most recent diagnostic runs. Read-only.
export async function listEvidenceRuns(limit = 50): Promise<{ ok: boolean; runs: EvidenceRun[] }> {
  return request<{ ok: boolean; runs: EvidenceRun[] }>(`/api/itops/evidence/runs?limit=${limit}`);
}

// Already-redacted per-command evidence for ONE run (run_id required).
export async function getEvidence(run_id: string): Promise<{ ok: boolean; run_id: string; evidence: EvidenceRecord[] }> {
  return request<{ ok: boolean; run_id: string; evidence: EvidenceRecord[] }>(
    `/api/itops/evidence?run_id=${encodeURIComponent(run_id)}`,
  );
}

// ── network inventory (read-only) ─────────────────────────────────────────

export type NetworkProfile = {
  ok: boolean;
  vantage: string;
  source_ip: string;
  allowed_cidrs: string[];           // the authorized CIDR allowlist (server-owned)
  profile: { name: string; ports: number[]; rate_limit: number; total_timeout: number; max_hosts: number; min_prefix: number };
};

export type NetworkStartResp = {
  ok: boolean;
  run_id: string;
  cidr: string;
  vantage: string;
  hosts: number;
  profile: { name: string; ports: number[] };
  message: string;
  ttl_seconds: number;
};

// The server-owned scan profile + authorized CIDR allowlist + fixed vantage (a UI hint).
export async function getNetworkProfile(): Promise<NetworkProfile> {
  return request<NetworkProfile>("/api/itops/network/profile");
}

// Start ONE bounded read-only network inventory over an AUTHORIZED CIDR. The client
// sends ONLY the CIDR (ports + vantage are server-owned); the server binds a network
// scope. The caller then streams /api/code-agent/stream with the returned run_id.
export async function startNetworkScan(cidr: string): Promise<NetworkStartResp> {
  return request<NetworkStartResp>("/api/itops/network/start", { method: "POST", body: { cidr } });
}

// ── systemd service inspect (read-only, Phase 4a) ──────────────────────────

export type SystemdInspectResp = {
  ok: boolean;
  run_id: string;
  profile_id: string;
  unit: string;
  tool: string;
  message: string;
  ttl_seconds: number;
};

// Start ONE read-only systemd service inspect. The human picks a SAVED enabled LINUX
// profile and a `.service` unit; the server strictly validates the unit name + asset
// kind and binds a systemd_service scope. The caller then streams the returned run_id;
// the model calls itops_systemd_service_inspect() with NO args.
export async function startSystemdInspect(profile_id: string, unit: string): Promise<SystemdInspectResp> {
  return request<SystemdInspectResp>("/api/itops/systemd/inspect/start", {
    method: "POST",
    body: { profile_id, unit },
  });
}

export type ConfigInspectResp = {
  ok: boolean;
  run_id: string;
  profile_id: string;
  config_id: "netdata-main";
  tool: "itops_config_inspect";
  message: string;
  ttl_seconds: number;
};

export async function startConfigInspect(
  profile_id: string,
  config_id: "netdata-main" = "netdata-main",
): Promise<ConfigInspectResp> {
  return request<ConfigInspectResp>("/api/itops/config/inspect/start", {
    method: "POST",
    body: { profile_id, config_id },
  });
}

// ── change vertical (v1) — thin proxy to the privileged executor ───────────

export type ChangePlanResp = {
  ok: boolean;
  change_run_id?: string;
  status?: string;
  error?: string;
};

export type ChangeEvidence = {
  operation: string;
  exit_status: string;
  captured_at: number;
  result: Record<string, unknown>;
};

export type ChangeStatusResp = {
  ok: boolean;
  change_run_id?: string;
  unit?: string;
  operation?: string;
  status?: string;
  verdict?: string | null;
  created_at?: number;
  updated_at?: number;
  evidence?: ChangeEvidence[];
  error?: string;
};

// Ask the privileged executor to PLAN a change for an executor-registry target_id. The
// main backend never sends host/unit/argv/keys — only the opaque target_id. Approval then
// happens out-of-band in the executor's Telegram bot; the model cannot approve or apply.
export async function startChangePlan(target_id: string): Promise<ChangePlanResp> {
  return request<ChangePlanResp>("/api/itops/change/plan", { method: "POST", body: { target_id } });
}

// Read the executor's capped status/evidence for a change run (read-only).
export async function getChangeStatus(change_run_id: string): Promise<ChangeStatusResp> {
  return request<ChangeStatusResp>(`/api/itops/change/${encodeURIComponent(change_run_id)}/status`);
}
