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

async function safeInvoke(command) {
  try {
    const mod = await import("@tauri-apps/api/tauri");
    return mod.invoke(command);
  } catch (_err) {
    return { running: false, pid: null, mode: "browser" };
  }
}

export async function tauriStartBackend() {
  return safeInvoke("start_backend");
}

export async function tauriStopBackend() {
  return safeInvoke("stop_backend");
}

export async function tauriBackendStatus() {
  return safeInvoke("backend_status");
}

export async function getDesktopLifecycleConfig() {
  return request("/api/desktop-lifecycle/config");
}

export async function getDesktopLifecycleEnv() {
  return request("/api/desktop-lifecycle/env");
}
