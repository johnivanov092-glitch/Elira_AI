const API = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

async function request(url, options = {}) {
  const res = await fetch(API + url, {
    method: options.method || "GET",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });

  const contentType = res.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await res.json()
    : await res.text();

  if (!res.ok) {
    const message =
      typeof payload === "string"
        ? payload
        : payload?.detail || payload?.message || "API error";
    throw new Error(message);
  }

  return payload;
}

export function getProjectFile(path) {
  return request(`/api/project-brain/file?path=${encodeURIComponent(path)}`);
}

export function previewPatch(body) {
  return request("/api/jarvis/patch/diff", { method: "POST", body });
}

export function applyPatch(body) {
  return request("/api/jarvis/patch/apply", { method: "POST", body });
}

export function rollbackPatch(body) {
  return request("/api/jarvis/patch/rollback", { method: "POST", body });
}

export function verifyPatch(body) {
  return request("/api/jarvis/patch/verify", { method: "POST", body });
}

export function listRunHistory() {
  return request("/api/jarvis/run-history/list");
}

export function autocodeSuggest(body) {
  return request("/api/jarvis/autocode/suggest", { method: "POST", body });
}

export function autocodeLoop(body) {
  return request("/api/jarvis/autocode/loop", { method: "POST", body });
}
