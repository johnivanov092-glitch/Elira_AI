import { request, safeRequest } from "./client";
import { getProjectBrainStatus } from "./project";

type QueryParam = string | number | boolean | null | undefined;

export type SystemResponse = Record<string, unknown>;
export type PersonaCandidate = Record<string, unknown>;

export type DashboardOverview = {
  stats: unknown;
  projectBrainStatus: unknown;
  personaStatus: unknown;
  runtimeStatus: unknown;
  agentOsHealth: unknown;
  agentOsDashboard: unknown;
  agentOsLimits: unknown;
  errors: string[];
};

type ItemsPayload = {
  items?: PersonaCandidate[];
  count?: number;
  [key: string]: unknown;
};

function withParams(
  path: string,
  params: Record<string, QueryParam> = {},
): string {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    query.set(key, String(value));
  });
  const suffix = query.toString();
  return suffix ? `${path}?${suffix}` : path;
}

function normalizeArray(payload: unknown): unknown[] {
  if (Array.isArray(payload)) return payload;
  if (payload && typeof payload === "object") {
    const items = (payload as { items?: unknown }).items;
    if (Array.isArray(items)) return items;
  }
  return [];
}

function formatRequestError(error: unknown, fallback = "Request failed"): string {
  if (!error) return fallback;
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === "string") return error;
  if (Array.isArray(error)) {
    return error
      .map((item) => formatRequestError(item, ""))
      .filter(Boolean)
      .join(" | ") || fallback;
  }
  if (typeof error === "object") {
    const value = error as Record<string, unknown>;
    for (const key of ["message", "detail", "msg"]) {
      if (typeof value[key] === "string" && value[key]) return value[key];
    }
    return JSON.stringify(value);
  }
  return String(error);
}

export async function getPersonaStatus(): Promise<SystemResponse> {
  return safeRequest<SystemResponse>("/api/persona/status", {}, {
    ok: false,
    active_version: 1,
    quarantine_candidates: 0,
    latest_traits: [],
    model_consistency: [],
  });
}

export async function getRuntimeStatus(): Promise<SystemResponse> {
  return safeRequest<SystemResponse>("/api/runtime/status", {}, {
    ok: false,
    storage_mode: "unknown",
    active_chat_count: 0,
    primary_engine: "",
    fallback_engines: [],
    available_engines: [],
    api_keys_present: { tavily: false },
    degraded_mode: false,
    web_warnings: [],
  });
}

export async function getAgentOsHealth(): Promise<SystemResponse> {
  return safeRequest<SystemResponse>("/api/agent-os/health", {}, {
    ok: false,
    components: [],
    warnings: [],
  });
}

export async function getAgentOsDashboard(
  windowHours = 24,
): Promise<SystemResponse> {
  return safeRequest<SystemResponse>(
    withParams("/api/agent-os/dashboard", { window_hours: windowHours }),
    {},
    {
      ok: false,
      window_hours: windowHours,
      total_agent_runs: 0,
      blocked_runs: 0,
      workflow_runs: 0,
      avg_duration_ms: 0,
      top_agents: [],
      recent_violations: [],
      limits_summary: [],
      warnings: [],
    },
  );
}

export async function listAgentOsLimits(): Promise<SystemResponse> {
  return safeRequest<SystemResponse>("/api/agent-os/limits", {}, {
    items: [],
    total: 0,
  });
}

export async function getPersonaVersion(version: string | number): Promise<SystemResponse> {
  return safeRequest<SystemResponse>(
    withParams("/api/persona/version", { version }),
    {},
    { ok: false, item: null },
  );
}

export async function listPersonaCandidates(limit = 20): Promise<ItemsPayload> {
  const payload = await safeRequest<ItemsPayload | PersonaCandidate[]>(
    withParams("/api/persona/candidates", { limit }),
    {},
    { items: [], count: 0 },
  );
  const items = normalizeArray(payload) as PersonaCandidate[];
  return {
    ...(Array.isArray(payload) ? {} : payload),
    items,
    count: Array.isArray(payload) ? items.length : payload.count ?? items.length,
  };
}

export async function rollbackPersona(
  version: string | number,
): Promise<SystemResponse> {
  return request<SystemResponse>(
    `/api/persona/rollback/${encodeURIComponent(String(version))}`,
    { method: "POST" },
  );
}

export async function getDashboardOverview(): Promise<DashboardOverview> {
  const [
    statsResult,
    projectBrainStatusResult,
    personaStatusResult,
    runtimeStatusResult,
    agentOsHealthResult,
    agentOsDashboardResult,
    agentOsLimitsResult,
  ] = await Promise.allSettled([
    request("/api/dashboard/stats"),
    getProjectBrainStatus(),
    getPersonaStatus(),
    getRuntimeStatus(),
    getAgentOsHealth(),
    getAgentOsDashboard(),
    listAgentOsLimits(),
  ]);

  const errors: string[] = [];
  const stats = statsResult.status === "fulfilled" ? statsResult.value : null;
  const projectBrainStatus = projectBrainStatusResult.status === "fulfilled" ? projectBrainStatusResult.value : null;
  const personaStatus = personaStatusResult.status === "fulfilled" ? personaStatusResult.value : null;
  const runtimeStatus = runtimeStatusResult.status === "fulfilled" ? runtimeStatusResult.value : null;
  const agentOsHealth = agentOsHealthResult.status === "fulfilled" ? agentOsHealthResult.value : null;
  const agentOsDashboard = agentOsDashboardResult.status === "fulfilled" ? agentOsDashboardResult.value : null;
  const agentOsLimits = agentOsLimitsResult.status === "fulfilled" ? agentOsLimitsResult.value : null;

  if (statsResult.status === "rejected") errors.push(`dashboard stats: ${formatRequestError(statsResult.reason)}`);
  if (projectBrainStatusResult.status === "rejected") errors.push(`project brain status: ${formatRequestError(projectBrainStatusResult.reason)}`);
  if (personaStatusResult.status === "rejected") errors.push(`persona status: ${formatRequestError(personaStatusResult.reason)}`);
  if (runtimeStatusResult.status === "rejected") errors.push(`runtime status: ${formatRequestError(runtimeStatusResult.reason)}`);
  if (agentOsHealthResult.status === "rejected") errors.push(`agent os health: ${formatRequestError(agentOsHealthResult.reason)}`);
  if (agentOsDashboardResult.status === "rejected") errors.push(`agent os dashboard: ${formatRequestError(agentOsDashboardResult.reason)}`);
  if (agentOsLimitsResult.status === "rejected") errors.push(`agent os limits: ${formatRequestError(agentOsLimitsResult.reason)}`);

  if (
    !stats &&
    !projectBrainStatus &&
    !personaStatus &&
    !runtimeStatus &&
    !agentOsHealth &&
    !agentOsDashboard &&
    !agentOsLimits &&
    errors.length
  ) {
    throw new Error(errors.join(" | "));
  }

  return {
    stats,
    projectBrainStatus,
    personaStatus,
    runtimeStatus,
    agentOsHealth,
    agentOsDashboard,
    agentOsLimits,
    errors,
  };
}
