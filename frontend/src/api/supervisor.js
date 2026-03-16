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

export function getSupervisorStatus() {
  return request("/api/supervisor/status");
}

export function listAgents() {
  return request("/api/supervisor/agents");
}

export function bootstrapAgents() {
  return request("/api/supervisor/bootstrap", { method: "POST" });
}

export function runGoal(goal, requested_by = "workspace") {
  return request("/api/supervisor/run", {
    method: "POST",
    body: JSON.stringify({ goal, requested_by }),
  });
}

export function scheduleGoal(goal, delay_seconds = 5) {
  return request("/api/supervisor/schedule", {
    method: "POST",
    body: JSON.stringify({ goal, delay_seconds }),
  });
}
