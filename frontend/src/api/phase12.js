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

export function getPhase12Status() {
  return request("/api/phase12/status");
}

export function listExecutions(limit = 50) {
  return request(`/api/phase12/executions?limit=${limit}`);
}

export function getExecution(executionId) {
  return request(`/api/phase12/executions/${executionId}`);
}

export function getExecutionEvents(executionId) {
  return request(`/api/phase12/executions/${executionId}/events`);
}

export function getExecutionArtifacts(executionId) {
  return request(`/api/phase12/executions/${executionId}/artifacts`);
}

export function startExecution(goal, mode = "autonomous_dev", metadata = {}) {
  return request("/api/phase12/executions/start", {
    method: "POST",
    body: JSON.stringify({ goal, mode, metadata }),
  });
}
