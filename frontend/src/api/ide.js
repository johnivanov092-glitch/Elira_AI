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

function normalizeArrayPayload(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.items)) return payload.items;
  if (Array.isArray(payload?.data)) return payload.data;
  if (Array.isArray(payload?.results)) return payload.results;
  if (Array.isArray(payload?.chats)) return payload.chats;
  if (Array.isArray(payload?.messages)) return payload.messages;
  if (Array.isArray(payload?.files)) return payload.files;
  return [];
}

// ------------------------------
// Chat / shell API
// ------------------------------
export async function listChats() {
  const payload = await safeRequest("/api/jarvis/chats", {}, []);
  return normalizeArrayPayload(payload);
}

export async function createChat(body = {}) {
  return request("/api/jarvis/chats", { method: "POST", body });
}

export async function renameChat(arg1, arg2) {
  const params =
    typeof arg1 === "object" && arg1 !== null
      ? arg1
      : { id: arg1, title: arg2 };
  return request(`/api/jarvis/chats/${encodeURIComponent(params.id)}`, {
    method: "PATCH",
    body: { title: params.title },
  });
}

export async function pinChat(arg1, arg2) {
  const params =
    typeof arg1 === "object" && arg1 !== null
      ? arg1
      : { id: arg1, pinned: arg2 };
  return request(`/api/jarvis/chats/${encodeURIComponent(params.id)}/pin`, {
    method: "PATCH",
    body: { pinned: Boolean(params.pinned) },
  });
}

export async function saveChatToMemory(arg1, arg2) {
  const params =
    typeof arg1 === "object" && arg1 !== null
      ? arg1
      : { id: arg1, saved: arg2 };

  // Try a dedicated route first; fall back to generic chat patch if needed.
  return safeRequest(
    `/api/jarvis/chats/${encodeURIComponent(params.id)}/memory`,
    {
      method: "PATCH",
      body: { saved: Boolean(params.saved) },
    },
    () =>
      request(`/api/jarvis/chats/${encodeURIComponent(params.id)}`, {
        method: "PATCH",
        body: { memory_saved: Boolean(params.saved) },
      })
  );
}

export async function deleteChat(arg) {
  const id = typeof arg === "object" && arg !== null ? arg.id : arg;
  return request(`/api/jarvis/chats/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function getMessages(arg) {
  const chatId = typeof arg === "object" && arg !== null ? arg.chatId : arg;
  const payload = await safeRequest(
    `/api/jarvis/chats/${encodeURIComponent(chatId)}/messages`,
    {},
    []
  );
  return normalizeArrayPayload(payload);
}

export async function addMessage(body = {}) {
  return request("/api/jarvis/messages", { method: "POST", body });
}

export async function sendMessage(body = {}) {
  return request("/api/jarvis/messages", { method: "POST", body });
}

export async function execute(body = {}) {
  return request("/api/chat/send", { method: "POST", body });
}

export async function listOllamaModels() {
  const payload = await safeRequest("/api/jarvis/models", {}, []);
  if (Array.isArray(payload?.models)) return { models: payload.models };
  if (Array.isArray(payload?.items)) return { models: payload.items };
  if (Array.isArray(payload)) return { models: payload };
  return { models: [] };
}

export async function getSettings() {
  return safeRequest("/api/jarvis/settings", {}, {});
}

export async function updateSettings(body = {}) {
  return request("/api/jarvis/settings", { method: "PUT", body });
}

export async function searchJarvis(query = "") {
  const payload = await safeRequest(
    `/api/jarvis/search?q=${encodeURIComponent(query)}`,
    {},
    []
  );
  return normalizeArrayPayload(payload);
}

export async function listProjects() {
  const payload = await safeRequest("/api/jarvis/projects", {}, []);
  return normalizeArrayPayload(payload);
}

// ------------------------------
// IDE / project brain API
// ------------------------------
export async function getProjectSnapshot() {
  const payload = await request("/api/project-brain/snapshot");
  return {
    ...payload,
    files: normalizeArrayPayload(payload?.files ? payload : { files: [] }).length
      ? payload.files
      : Array.isArray(payload?.files)
      ? payload.files
      : [],
  };
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

  const payload = await safeRequest(
    `/api/jarvis/patch/history/list${query.toString() ? `?${query.toString()}` : ""}`,
    {},
    { items: [] }
  );
  return { ...payload, items: normalizeArrayPayload(payload) };
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
  const payload = await safeRequest("/api/jarvis/run-history/list", {}, { items: [] });
  return { ...payload, items: normalizeArrayPayload(payload) };
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
  saveChatToMemory,
  deleteChat,
  getMessages,
  addMessage,
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
