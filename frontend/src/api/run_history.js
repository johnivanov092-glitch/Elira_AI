const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }

  return response.json();
}

export function getRunHistoryStatus() {
  return request("/api/run-history/status");
}

export function listRuns(limit = 20) {
  return request(`/api/run-history/runs?limit=${limit}`);
}

export function getRun(runId) {
  return request(`/api/run-history/runs/${runId}`);
}

export function runWithTrace(goal, requested_by = "workspace") {
  return request("/api/run-history/run", {
    method: "POST",
    body: JSON.stringify({ goal, requested_by }),
  });
}

export function deleteRun(runId) {
  return request(`/api/run-history/runs/${runId}`, { method: "DELETE" });
}
