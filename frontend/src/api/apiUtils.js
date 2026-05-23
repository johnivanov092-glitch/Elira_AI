// apiUtils.js — shared helpers for all API modules

export function normalizeArray(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.items)) return payload.items;
  if (Array.isArray(payload?.messages)) return payload.messages;
  if (Array.isArray(payload?.files)) return payload.files;
  return [];
}

export function unwrapItem(payload) {
  if (!payload || typeof payload !== "object") return payload;
  return payload.item || payload.chat || payload.message || payload.data || payload;
}

export function normalizeSessionId(value) {
  if (value === undefined || value === null || value === "") return null;
  return typeof value === "string" ? value : String(value);
}

export function normalizeChat(item = {}) {
  return {
    ...item,
    id: item.id ?? "",
    title: item.title ?? "New chat",
    pinned: Boolean(item.pinned),
    memory_saved: Boolean(item.memory_saved),
  };
}

export function normalizeMessage(item = {}) {
  const content = item.content ?? item.answer ?? item.response ?? item.message ?? "";
  return {
    ...item,
    id: item.id ?? `${item.role || "msg"}-${Date.now()}`,
    role: item.role ?? "assistant",
    content: typeof content === "string" ? content : String(content ?? ""),
  };
}

export function extractAgentError(payload) {
  if (!payload || typeof payload !== "object") return "";
  if (payload.ok === false) {
    if (typeof payload?.meta?.error === "string" && payload.meta.error.trim()) return payload.meta.error;
    return "run_agent returned an error";
  }
  return "";
}

export function formatRequestError(error, fallback = "Request failed") {
  const value = error?.message ?? error?.detail ?? error;
  if (!value) return fallback;
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map((item) => formatRequestError(item, "")).filter(Boolean).join(" | ") || fallback;
  if (typeof value === "object") return value.message || value.msg || JSON.stringify(value);
  return String(value);
}

export function withParams(path, params = {}) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    query.set(key, String(value));
  });
  const suffix = query.toString();
  return suffix ? `${path}?${suffix}` : path;
}

export function isLocalApiAssetUrl(url = "") {
  return typeof url === "string" && (
    url.includes("/api/skills/download/") ||
    url.includes("/api/skills/view/") ||
    url.includes("/api/extra/")
  );
}
