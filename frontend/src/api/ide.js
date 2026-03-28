/**
 * ide.js — API-слой Jarvis.
 *
 * Изменения:
 *   • executeStream() — SSE-стриминг через fetch + ReadableStream
 *   • execute() — передаёт history
 *   • Всё остальное без изменений
 */

const API_BASE = import.meta.env.VITE_API_BASE_URL || `http://${window.location.hostname}:8000`;

function normalizeError(payload, status) {
  if (typeof payload === "string") return payload;
  if (Array.isArray(payload)) return payload.map((x) => x?.msg || JSON.stringify(x)).join("; ");
  if (Array.isArray(payload?.detail)) return payload.detail.map((x) => x?.msg || JSON.stringify(x)).join("; ");
  return payload?.detail || payload?.message || `Request failed: ${status}`;
}

async function parseResponse(res) {
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return await res.json();
  return await res.text();
}

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: options.method || "GET",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });
  const payload = await parseResponse(res);
  if (!res.ok) throw new Error(normalizeError(payload, res.status));
  return payload;
}

async function safeRequest(path, options = {}, fallback = null) {
  try { return await request(path, options); }
  catch (error) {
    if (fallback !== null) return typeof fallback === "function" ? fallback(error) : fallback;
    throw error;
  }
}

function normalizeArray(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.items)) return payload.items;
  if (Array.isArray(payload?.messages)) return payload.messages;
  if (Array.isArray(payload?.files)) return payload.files;
  return [];
}

function unwrapItem(payload) {
  if (!payload || typeof payload !== "object") return payload;
  return payload.item || payload.chat || payload.message || payload.data || payload;
}

function normalizeChat(item = {}) {
  return {
    ...item,
    id: item.id ?? "",
    title: item.title ?? "Новый чат",
    pinned: Boolean(item.pinned),
    memory_saved: Boolean(item.memory_saved),
  };
}

function normalizeMessage(item = {}) {
  const content = item.content ?? item.answer ?? item.response ?? item.message ?? "";
  return {
    ...item,
    id: item.id ?? `${item.role || "msg"}-${Date.now()}`,
    role: item.role ?? "assistant",
    content: typeof content === "string" ? content : String(content ?? ""),
  };
}

function extractAgentError(payload) {
  if (!payload || typeof payload !== "object") return "";
  if (payload.ok === false) {
    if (typeof payload?.meta?.error === "string" && payload.meta.error.trim()) return payload.meta.error;
    return "run_agent вернул ошибку";
  }
  return "";
}

export async function listChats() {
  const payload = await safeRequest("/api/jarvis/chats", {}, []);
  return normalizeArray(payload).map(normalizeChat);
}

export async function createChat(body = {}) {
  return normalizeChat(unwrapItem(await request("/api/jarvis/chats", { method: "POST", body })));
}

export async function renameChat(arg1, arg2) {
  const p = typeof arg1 === "object" && arg1 !== null ? arg1 : { id: arg1, title: arg2 };
  return normalizeChat(unwrapItem(await request(`/api/jarvis/chats/${encodeURIComponent(p.id)}`, {
    method: "PATCH",
    body: { title: p.title },
  })));
}

export async function pinChat(arg1, arg2) {
  const p = typeof arg1 === "object" && arg1 !== null ? arg1 : { id: arg1, pinned: arg2 };
  return normalizeChat(unwrapItem(await request(`/api/jarvis/chats/${encodeURIComponent(p.id)}/pin`, {
    method: "PATCH",
    body: { pinned: Boolean(p.pinned) },
  })));
}

export async function saveChatToMemory(arg1, arg2) {
  const p = typeof arg1 === "object" && arg1 !== null ? arg1 : { id: arg1, saved: arg2 };
  return normalizeChat(unwrapItem(await request(`/api/jarvis/chats/${encodeURIComponent(p.id)}/memory`, {
    method: "PATCH",
    body: { memory_saved: Boolean(p.saved) },
  })));
}

export async function deleteChat(arg) {
  const id = typeof arg === "object" && arg !== null ? arg.id : arg;
  return request(`/api/jarvis/chats/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function getMessages(arg) {
  const chatId = typeof arg === "object" && arg !== null ? arg.chatId : arg;
  const payload = await safeRequest(`/api/jarvis/chats/${encodeURIComponent(chatId)}/messages`, {}, []);
  return normalizeArray(payload).map(normalizeMessage);
}

export async function addMessage(body = {}) {
  return normalizeMessage(unwrapItem(await request("/api/jarvis/messages", {
    method: "POST",
    body: {
      chat_id: body.chatId ?? body.chat_id ?? null,
      role: body.role ?? "user",
      content: typeof body.content === "string" ? body.content : String(body.content ?? ""),
    },
  })));
}

export async function sendMessage(body = {}) { return addMessage(body); }

export async function execute(body = {}) {
  const response = await request("/api/chat/send", {
    method: "POST",
    body: {
      model_name: body.model_name ?? body.model ?? "gemma3:4b",
      profile_name: body.profile_name ?? body.profile ?? "default",
      user_input: String(body.user_input ?? body.message ?? body.prompt ?? body.text ?? body.query ?? "").trim(),
      history: Array.isArray(body.history) ? body.history : [],
      use_memory: body.use_memory ?? true,
      use_library: body.use_library ?? true,
      use_reflection: body.use_reflection ?? false,
    },
  });
  const routeError = extractAgentError(response);
  if (routeError) throw new Error(routeError);
  const content = response?.content ?? response?.answer ?? response?.response ?? response?.message ?? "";
  if (!String(content).trim()) throw new Error("Пустой ответ от /api/chat/send");
  return { ...response, content: String(content) };
}


// ═══════════════════════════════════════════════════════════════
// SSE-СТРИМИНГ
// ═══════════════════════════════════════════════════════════════

/**
 * Стриминг ответа через SSE.
 *
 * @param {Object}   body         — параметры запроса
 * @param {Function} onToken      — вызывается для каждого токена: onToken(token: string)
 * @param {Function} onDone       — вызывается по завершении: onDone({ full_text, meta, timeline })
 * @param {Function} onError      — вызывается при ошибке: onError(errorMessage: string)
 * @param {Function} [onPhase]    — опционально: вызывается при смене фазы (tools_done, reflection)
 * @returns {AbortController}     — для отмены запроса
 */
export function executeStream(body = {}, { onToken, onDone, onError, onPhase } = {}) {
  const controller = new AbortController();

  const payload = {
    model_name: body.model_name ?? body.model ?? "gemma3:4b",
    profile_name: body.profile_name ?? body.profile ?? "default",
    user_input: String(body.user_input ?? body.message ?? "").trim(),
    history: Array.isArray(body.history) ? body.history : [],
    num_ctx: body.num_ctx ?? 8192,
    use_memory: body.use_memory ?? true,
    use_library: body.use_library ?? true,
    use_reflection: body.use_reflection ?? false,
    use_web_search: body.use_web_search ?? true,
    use_python_exec: body.use_python_exec ?? true,
    use_image_gen: body.use_image_gen ?? true,
    use_file_gen: body.use_file_gen ?? true,
    use_http_api: body.use_http_api ?? true,
    use_sql: body.use_sql ?? true,
    use_screenshot: body.use_screenshot ?? true,
    use_encrypt: body.use_encrypt ?? true,
    use_archiver: body.use_archiver ?? true,
    use_converter: body.use_converter ?? true,
    use_regex: body.use_regex ?? true,
    use_translator: body.use_translator ?? true,
    use_csv: body.use_csv ?? true,
    use_webhook: body.use_webhook ?? true,
    use_plugins: body.use_plugins ?? true,
  };

  fetch(`${API_BASE}/api/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `HTTP ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          // Парсим SSE-события из буфера
          const lines = buffer.split("\n");
          buffer = lines.pop() || ""; // Неполная строка остаётся в буфере

          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed || !trimmed.startsWith("data: ")) continue;

            try {
              const event = JSON.parse(trimmed.slice(6));

              if (event.error) {
                onError?.(event.error);
                return;
              }

              if (event.phase && onPhase) {
                onPhase(event);
              }

              // Reflection заменяет весь текст
              if (event.phase === "reflection_replace" && event.full_text) {
                onPhase?.(event);
                continue;
              }

              if (event.token) {
                onToken?.(event.token);
              }

              if (event.done) {
                onDone?.({
                  full_text: event.full_text || "",
                  meta: event.meta || {},
                  timeline: event.timeline || [],
                });
                return;
              }
            } catch (parseErr) {
              console.warn("SSE parse error:", trimmed.slice(0, 100), parseErr);
            }
          }
        }

        // Если стрим закончился без done-пакета
        onDone?.({ full_text: "", meta: {}, timeline: [] });
      } finally {
        reader.cancel().catch(() => {});
      }
    })
    .catch((err) => {
      if (err.name === "AbortError") return;
      onError?.(err.message || "Stream error");
    });

  return controller;
}


export async function listOllamaModels() {
  const payload = await safeRequest("/api/jarvis/models", {}, []);
  if (Array.isArray(payload?.models)) return { models: payload.models };
  if (Array.isArray(payload?.items)) return { models: payload.items };
  if (Array.isArray(payload)) return { models: payload };
  return { models: [] };
}

export async function getSettings() { return safeRequest("/api/jarvis/settings", {}, {}); }
export async function updateSettings(body = {}) {
  return request("/api/jarvis/settings", { method: "PUT", body });
}
export async function searchJarvis(query = "") { return normalizeArray(await safeRequest(`/api/jarvis/search?q=${encodeURIComponent(query)}`, {}, [])); }
export async function listProjects() { return normalizeArray(await safeRequest("/api/jarvis/projects", {}, [])); }

export async function getProjectSnapshot() {
  const payload = await request("/api/project-brain/snapshot");
  return { ...payload, files: Array.isArray(payload?.files) ? payload.files : [] };
}
export async function getProjectFile(path) { return request(`/api/project-brain/file?path=${encodeURIComponent(path)}`); }
export async function getProjectBrainStatus() { return safeRequest("/api/project-brain/status", {}, { status: "unknown" }); }

export async function listPatchHistory({ path = "", limit = 20 } = {}) {
  const query = new URLSearchParams();
  if (path) query.set("path", path);
  if (limit) query.set("limit", String(limit));
  const payload = await safeRequest(`/api/jarvis/patch/history/list${query.toString() ? `?${query.toString()}` : ""}`, {}, { items: [] });
  return { ...payload, items: normalizeArray(payload) };
}
export async function previewPatch(body = {}) { return request("/api/jarvis/patch/diff", { method: "POST", body }); }
export async function applyPatch(body = {}) { return request("/api/jarvis/patch/apply", { method: "POST", body }); }
export async function rollbackPatch(body = {}) { return request("/api/jarvis/patch/rollback", { method: "POST", body }); }
export async function verifyPatch(body = {}) { return request("/api/jarvis/patch/verify", { method: "POST", body }); }
export async function listRunHistory() {
  const payload = await safeRequest("/api/jarvis/run-history/list", {}, { items: [] });
  return { ...payload, items: normalizeArray(payload) };
}
export async function autocodeSuggest(body = {}) { return request("/api/jarvis/autocode/suggest", { method: "POST", body }); }
export async function autocodeLoop(body = {}) { return request("/api/jarvis/autocode/loop", { method: "POST", body }); }

export const api = {
  listChats, createChat, renameChat, pinChat, saveChatToMemory, deleteChat,
  getMessages, addMessage, sendMessage, execute, executeStream, listOllamaModels,
  getSettings, updateSettings, searchJarvis, listProjects,
  getProjectSnapshot, getProjectFile, getProjectBrainStatus,
  listPatchHistory, previewPatch, applyPatch, rollbackPatch, verifyPatch,
  listRunHistory, autocodeSuggest, autocodeLoop,
};

export default api;
