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

export function getMultiAgentStatus() {
  return request("/api/multi-agent/status");
}

export function listMultiAgents() {
  return request("/api/multi-agent/agents");
}

export function bootstrapMultiAgents() {
  return request("/api/multi-agent/bootstrap", { method: "POST" });
}

export function runMultiAgent(goal, options = {}) {
  return request("/api/multi-agent/run", {
    method: "POST",
    body: JSON.stringify({
      goal,
      auto_apply: !!options.auto_apply,
      run_checks: options.run_checks !== false,
    }),
  });
}

export function listMultiAgentRuns(limit = 20) {
  return request(`/api/multi-agent/runs?limit=${limit}`);
}
