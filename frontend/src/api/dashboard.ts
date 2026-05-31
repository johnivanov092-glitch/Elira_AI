// dashboard.ts — project brain, persona, runtime, agent OS status API

import { request, safeRequest } from "./client";
import { normalizeArray, withParams, formatRequestError } from "./apiUtils";

export async function getProjectSnapshot() {
  const payload = await request("/api/project-brain/snapshot") as Record<string, unknown>;
  return { ...payload, files: Array.isArray(payload?.files) ? payload.files : [] };
}

export async function getProjectFile(path: string) {
  return request(`/api/project-brain/file?path=${encodeURIComponent(path)}`);
}

export async function getProjectBrainStatus() {
  return safeRequest("/api/project-brain/status", {}, { status: "unknown" });
}

export async function getPersonaStatus() {
  return safeRequest("/api/persona/status", {}, {
    ok: false, active_version: 1, quarantine_candidates: 0, latest_traits: [], model_consistency: [],
  });
}

export async function getRuntimeStatus() {
  return safeRequest("/api/runtime/status", {}, {
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

export async function getAgentOsHealth() {
  return safeRequest("/api/agent-os/health", {}, { ok: false, components: [], warnings: [] });
}

export async function getAgentOsDashboard(windowHours = 24) {
  return safeRequest(withParams("/api/agent-os/dashboard", { window_hours: windowHours }), {}, {
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
  });
}

export async function listAgentOsLimits() {
  return safeRequest("/api/agent-os/limits", {}, { items: [], total: 0 });
}

export async function getPersonaVersion(version: string | number) {
  return safeRequest(withParams("/api/persona/version", { version }), {}, { ok: false, item: null });
}

export async function listPersonaCandidates(limit = 20) {
  const payload = await safeRequest(withParams("/api/persona/candidates", { limit }), {}, { items: [], count: 0 }) as Record<string, unknown>;
  return { ...payload, items: normalizeArray(payload), count: (payload?.count as number) ?? normalizeArray(payload).length };
}

export async function rollbackPersona(version: string | number) {
  return request(`/api/persona/rollback/${encodeURIComponent(version)}`, { method: "POST" });
}

export async function getDashboardOverview() {
  const [statsResult, projectBrainStatusResult, personaStatusResult, runtimeStatusResult, agentOsHealthResult, agentOsDashboardResult, agentOsLimitsResult] = await Promise.allSettled([
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

  if (!stats && !projectBrainStatus && !personaStatus && !runtimeStatus && !agentOsHealth && !agentOsDashboard && !agentOsLimits && errors.length) {
    throw new Error(errors.join(" | "));
  }

  return { stats, projectBrainStatus, personaStatus, runtimeStatus, agentOsHealth, agentOsDashboard, agentOsLimits, errors };
}
