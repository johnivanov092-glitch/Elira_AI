const API = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

async function request(url, options = {}) {
  const res = await fetch(API + url, {
    method: options.method || "GET",
    headers: { "Content-Type": "application/json" },
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

function loadLocal(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem(key) || JSON.stringify(fallback));
  } catch {
    return fallback;
  }
}

function saveLocal(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

function uid(prefix = "id") {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function getChatsLocal() {
  return loadLocal("jarvis_repo_fix_chats", []);
}

function saveChatsLocal(chats) {
  saveLocal("jarvis_repo_fix_chats", chats);
}

function getMessagesLocal(chatId) {
  return loadLocal(`jarvis_repo_fix_messages_${chatId}`, []);
}

function saveMessagesLocal(chatId, messages) {
  saveLocal(`jarvis_repo_fix_messages_${chatId}`, messages);
}

async function safeRequest(url, options, fallback) {
  try {
    return await request(url, options);
  } catch {
    return fallback();
  }
}

export const api = {
  health: () => request("/health"),

  // ---- chat shim ----
  listChats: async () =>
    safeRequest("/api/jarvis/chats/list", {}, () => getChatsLocal()),

  createChat: async ({ title = "Новый чат" } = {}) =>
    safeRequest(
      "/api/jarvis/chats/create",
      { method: "POST", body: { title } },
      () => {
        const chats = getChatsLocal();
        const chat = {
          id: uid("chat"),
          title,
          pinned: false,
          memory_saved: false,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        };
        chats.unshift(chat);
        saveChatsLocal(chats);
        saveMessagesLocal(chat.id, [
          {
            id: uid("msg"),
            role: "assistant",
            content: "Jarvis готов. Начни новый чат или напиши сообщение.",
            created_at: new Date().toISOString(),
          },
        ]);
        return chat;
      }
    ),

  deleteChat: async ({ id }) =>
    safeRequest(
      "/api/jarvis/chats/delete",
      { method: "POST", body: { id } },
      () => {
        const chats = getChatsLocal().filter((item) => item.id !== id);
        saveChatsLocal(chats);
        localStorage.removeItem(`jarvis_repo_fix_messages_${id}`);
        return { ok: true };
      }
    ),

  renameChat: async ({ id, title }) =>
    safeRequest(
      "/api/jarvis/chats/rename",
      { method: "POST", body: { id, title } },
      () => {
        const chats = getChatsLocal().map((item) =>
          item.id === id ? { ...item, title, updated_at: new Date().toISOString() } : item
        );
        saveChatsLocal(chats);
        return { ok: true };
      }
    ),

  pinChat: async ({ id, pinned = true }) =>
    safeRequest(
      "/api/jarvis/chats/pin",
      { method: "POST", body: { id, pinned } },
      () => {
        const chats = getChatsLocal().map((item) =>
          item.id === id ? { ...item, pinned, updated_at: new Date().toISOString() } : item
        );
        saveChatsLocal(chats);
        return { ok: true };
      }
    ),

  saveChatToMemory: async ({ id, saved = true }) =>
    safeRequest(
      "/api/jarvis/chats/save-memory",
      { method: "POST", body: { id, saved } },
      () => {
        const chats = getChatsLocal().map((item) =>
          item.id === id ? { ...item, memory_saved: saved, updated_at: new Date().toISOString() } : item
        );
        saveChatsLocal(chats);
        return { ok: true };
      }
    ),

  searchChats: async ({ q = "" } = {}) =>
    safeRequest(
      `/api/jarvis/chats/search?q=${encodeURIComponent(q)}`,
      {},
      () => {
        const text = q.toLowerCase().trim();
        return getChatsLocal().filter((item) => item.title.toLowerCase().includes(text));
      }
    ),

  getMessages: async ({ chatId }) =>
    safeRequest(
      `/api/jarvis/chats/messages?chat_id=${encodeURIComponent(chatId)}`,
      {},
      () => getMessagesLocal(chatId)
    ),

  addMessage: async ({ chatId, role, content }) =>
    safeRequest(
      "/api/jarvis/chats/messages/add",
      { method: "POST", body: { chat_id: chatId, role, content } },
      () => {
        const messages = getMessagesLocal(chatId);
        const item = {
          id: uid("msg"),
          role,
          content,
          created_at: new Date().toISOString(),
        };
        messages.push(item);
        saveMessagesLocal(chatId, messages);
        return item;
      }
    ),

  execute: async ({ chatId, message, mode = "chat", model = "qwen3:8b" }) =>
    safeRequest(
      "/api/chat/send",
      { method: "POST", body: { chat_id: chatId, message, mode, model } },
      async () => {
        const reply = {
          id: uid("msg"),
          role: "assistant",
          content: `Echo (${mode}, ${model}): ${message}`,
          created_at: new Date().toISOString(),
        };
        const messages = getMessagesLocal(chatId);
        messages.push(reply);
        saveMessagesLocal(chatId, messages);
        return reply;
      }
    ),

  // ---- models / settings ----
  listOllamaModels: async () =>
    safeRequest("/api/models", {}, async () => [
      { name: "qwen3:8b", context_window: 32768 },
      { name: "llama3.1:8b", context_window: 32768 },
      { name: "qwen2.5-coder:7b", context_window: 32768 },
      { name: "deepseek-r1:8b", context_window: 32768 },
    ]),

  listContextWindows: async () => [
    4096, 8192, 16384, 32768, 65536, 131072, 262144,
  ],

  // ---- code workspace / existing endpoints ----
  getProjectSnapshot: () =>
    safeRequest("/snapshot", {}, async () => ({ files: [] })),
  getProjectFile: (path) =>
    request(`/file?path=${encodeURIComponent(path)}`),

  previewPatch: ({ path, instruction, content }) =>
    request("/agent/ollama/run", {
      method: "POST",
      body: {
        mode: "code",
        selected_path: path,
        selected_content: content,
        goal: instruction,
        project_files: [path],
      },
    }),

  diffPatch: ({ path, original, updated }) =>
    request("/api/jarvis/patch/diff", { method: "POST", body: { path, original, updated } }),
  applyPatch: ({ path, content }) =>
    request("/api/jarvis/patch/apply", { method: "POST", body: { path, content } }),
  applyPatchBatch: (items) =>
    request("/api/jarvis/patch/apply-batch", { method: "POST", body: { items } }),
  rollbackPatch: ({ path }) =>
    request("/api/jarvis/patch/rollback", { method: "POST", body: { path } }),
  verifyPatch: ({ path, content }) =>
    request("/api/jarvis/patch/verify", { method: "POST", body: { path, content } }),
  verifyPatchBatch: (items) =>
    request("/api/jarvis/patch/verify-batch", { method: "POST", body: { items } }),
  listPatchHistory: async (path = "") =>
    safeRequest(`/api/jarvis/patch/history/list?path=${encodeURIComponent(path)}`, {}, async () => []),
  getPatchHistoryItem: (id) =>
    request(`/api/jarvis/patch/history/get?id=${encodeURIComponent(id)}`),

  getProjectMap: () =>
    safeRequest("/api/jarvis/project/map", {}, async () => ({ items: [], count: 0 })),

  createFile: ({ path, content }) =>
    request("/api/jarvis/fs/create", { method: "POST", body: { path, content } }),
  deleteFile: ({ path }) =>
    request("/api/jarvis/fs/delete", { method: "POST", body: { path } }),
  renameFile: ({ old_path, new_path }) =>
    request("/api/jarvis/fs/rename", { method: "POST", body: { old_path, new_path } }),

  patchPlan: ({ goal, current_path, current_content, staged_paths }) =>
    request("/api/jarvis/patch/plan", {
      method: "POST",
      body: { goal, current_path, current_content, staged_paths },
    }),

  runTask: ({ goal, mode, current_path, staged_paths }) =>
    safeRequest("/api/jarvis/task/run", { method: "POST", body: { goal, mode, current_path, staged_paths } }, async () => ({ ok: true })),
  listTaskHistory: async () =>
    safeRequest("/api/jarvis/task/history/list", {}, async () => []),
  getTaskHistoryItem: (id) =>
    request(`/api/jarvis/task/history/get?id=${encodeURIComponent(id)}`),

  runSupervisor: ({ goal, mode, current_path, staged_paths, auto_apply }) =>
    safeRequest("/api/jarvis/supervisor/run", { method: "POST", body: { goal, mode, current_path, staged_paths, auto_apply } }, async () => ({ ok: true })),
  executeSupervisor: ({ goal, current_path, current_content, auto_apply }) =>
    safeRequest("/api/jarvis/supervisor/execute", { method: "POST", body: { goal, current_path, current_content, auto_apply } }, async () => ({ ok: true })),
  listSupervisorHistory: async () =>
    safeRequest("/api/jarvis/supervisor/history/list", {}, async () => []),
  getSupervisorHistoryItem: (id) =>
    request(`/api/jarvis/supervisor/history/get?id=${encodeURIComponent(id)}`),

  runPhase19: ({ goal, mode, selected_paths }) =>
    safeRequest("/api/jarvis/phase19/run", { method: "POST", body: { goal, mode, selected_paths } }, async () => ({ ok: true })),
  listPhase19History: async () =>
    safeRequest("/api/jarvis/phase19/history/list", {}, async () => []),
  getPhase19HistoryItem: (id) =>
    request(`/api/jarvis/phase19/history/get?id=${encodeURIComponent(id)}`),

  runPhase20: ({ goal, selected_paths }) =>
    safeRequest("/api/jarvis/phase20/run", { method: "POST", body: { goal, selected_paths } }, async () => ({ ok: true })),
  buildPhase20PreviewQueue: ({ goal, targets }) =>
    safeRequest("/api/jarvis/phase20/preview-queue", { method: "POST", body: { goal, targets } }, async () => ({ items: [] })),
  buildPhase20ExecutionState: ({ goal, queue_items, staged_paths }) =>
    safeRequest("/api/jarvis/phase20/execution-state", { method: "POST", body: { goal, queue_items, staged_paths } }, async () => ({ checkpoints: [] })),
  listPhase20History: async () =>
    safeRequest("/api/jarvis/phase20/history/list", {}, async () => []),
  getPhase20HistoryItem: (id) =>
    request(`/api/jarvis/phase20/history/get?id=${encodeURIComponent(id)}`),

  runPhase21: ({ goal, queue_items, execution_state }) =>
    safeRequest("/api/jarvis/phase21/run", { method: "POST", body: { goal, queue_items, execution_state } }, async () => ({ ok: true })),
  listPhase21History: async () =>
    safeRequest("/api/jarvis/phase21/history/list", {}, async () => []),
  getPhase21HistoryItem: (id) =>
    request(`/api/jarvis/phase21/history/get?id=${encodeURIComponent(id)}`),

  runStabilizationPreflight: ({ phase20_queue_items, phase20_execution_state, phase21_run, staged_paths }) =>
    safeRequest("/api/jarvis/stabilization/preflight", {
      method: "POST",
      body: { phase20_queue_items, phase20_execution_state, phase21_run, staged_paths },
    }, async () => ({ ready: true, checks: [], warnings: [] })),
};
