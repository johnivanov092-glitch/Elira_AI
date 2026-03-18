const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

async function parseResponse(res) {
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return await res.json();
  }
  return await res.text();
}

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: options.method || "GET",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });

  const payload = await parseResponse(res);

  if (!res.ok) {
    const detail =
      typeof payload === "string"
        ? payload
        : payload?.detail || payload?.message || `Request failed: ${res.status}`;
    throw new Error(detail);
  }

  return payload;
}

async function safeRequest(path, options = {}, fallback = null) {
  try {
    return await request(path, options);
  } catch (error) {
    if (fallback !== null) {
      return typeof fallback === "function" ? fallback(error) : fallback;
    }
    throw error;
  }
}

// ------------------------------
// Chat / shell API (backward compatible)
// ------------------------------
export async function listChats() {
  return safeRequest("/api/jarvis/chats", {}, { items: [] });
}

export async function createChat(body = {}) {
  return request("/api/jarvis/chats", { method: "POST", body });
}

export async function renameChat(id, body = {}) {
  return request(`/api/jarvis/chats/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body,
  });
}

export async function pinChat(id, pinned = true) {
  return request(`/api/jarvis/chats/${encodeURIComponent(id)}/pin`, {
    method: "PATCH",
    body: { pinned },
  });
}

export async function deleteChat(id) {
  return request(`/api/jarvis/chats/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function getMessages(chatId) {
  return safeRequest(
    `/api/jarvis/chats/${encodeURIComponent(chatId)}/messages`,
    {},
    { items: [] }
  );
}

export async function sendMessage(body = {}) {
  return request("/api/jarvis/messages", { method: "POST", body });
}

export async function execute(body = {}) {
  return request("/api/chat/send", { method: "POST", body });
}

export async function listOllamaModels() {
  const payload = await safeRequest("/api/jarvis/models", {}, { items: [] });
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.items)) return payload.items;
  if (Array.isArray(payload?.models)) return payload.models;
  return [];
}

export async function getSettings() {
  return safeRequest("/api/jarvis/settings", {}, {});
}

export async function updateSettings(body = {}) {
  return request("/api/jarvis/settings", { method: "PUT", body });
}

export async function searchJarvis(query = "") {
  return safeRequest(
    `/api/jarvis/search?q=${encodeURIComponent(query)}`,
    {},
    { items: [] }
  );
}

export async function listProjects() {
  return safeRequest("/api/jarvis/projects", {}, { items: [] });
}

// ------------------------------
// IDE / project brain API
// ------------------------------
export async function getProjectSnapshot() {
  const payload = await request("/api/project-brain/snapshot");
  if (Array.isArray(payload?.files)) return payload;
  return { ...payload, files: [] };
}

export async function getProjectFile(path) {
  return request(`/api/project-brain/file?path=${encodeURIComponent(path)}`);
}

export async function getProjectBrainStatus() {
  return safeRequest("/api/project-brain/status", {}, { status: "unknown" });
}

export async function listPatchHistory({ path = "", limit = 20 } = {}) {
  const query = new URLSearchParams();
  if (path) query.set("path", path);
  if (limit) query.set("limit", String(limit));

  return safeRequest(
    `/api/jarvis/patch/history/list${query.toString() ? `?${query.toString()}` : ""}`,
    {},
    { items: [] }
  );
}

export async function previewPatch(body = {}) {
  return request("/api/jarvis/patch/diff", { method: "POST", body });
}

export async function applyPatch(body = {}) {
  return request("/api/jarvis/patch/apply", { method: "POST", body });
}

export async function rollbackPatch(body = {}) {
  return request("/api/jarvis/patch/rollback", { method: "POST", body });
}

export async function verifyPatch(body = {}) {
  return request("/api/jarvis/patch/verify", { method: "POST", body });
}

export async function listRunHistory() {
  return safeRequest("/api/jarvis/run-history/list", {}, { items: [] });
}

export async function autocodeSuggest(body = {}) {
  return request("/api/jarvis/autocode/suggest", { method: "POST", body });
}

export async function autocodeLoop(body = {}) {
  return request("/api/jarvis/autocode/loop", { method: "POST", body });
}

// ------------------------------
// Backward-compatible object export
// ------------------------------
export const api = {
  listChats,
  createChat,
  renameChat,
  pinChat,
  deleteChat,
  getMessages,
  sendMessage,
  execute,
  listOllamaModels,
  getSettings,
  updateSettings,
  searchJarvis,
  listProjects,
  getProjectSnapshot,
  getProjectFile,
  getProjectBrainStatus,
  listPatchHistory,
  previewPatch,
  applyPatch,
  rollbackPatch,
  verifyPatch,
  listRunHistory,
  autocodeSuggest,
  autocodeLoop,
};

export default api;
