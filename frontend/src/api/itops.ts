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

export type DiagnosticsAdapter = "healthcheck" | "linux_inventory";

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
