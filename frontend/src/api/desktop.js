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

export function getDesktopStatus() {
  return request("/api/desktop/status");
}

export function getDesktopInfo() {
  return request("/api/desktop/info");
}

export function getDesktopHandshake() {
  return request("/api/desktop/handshake");
}

export function getWorkspaceMeta() {
  return request("/api/desktop/workspace");
}

export function openProject(project_path) {
  return request("/api/desktop/open-project", {
    method: "POST",
    body: JSON.stringify({ project_path }),
  });
}
