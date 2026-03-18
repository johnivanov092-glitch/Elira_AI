
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
  return loadLocal("jarvis_fix7_chats", []);
}

function saveChatsLocal(chats) {
  saveLocal("jarvis_fix7_chats", chats);
}

function getMessagesLocal(chatId) {
  return loadLocal(`jarvis_fix7_messages_${chatId}`, []);
}

function saveMessagesLocal(chatId, messages) {
  saveLocal(`jarvis_fix7_messages_${chatId}`, messages);
}

async function safeRequest(url, options, fallback) {
  try {
    return await request(url, options);
  } catch {
    return fallback();
  }
}

export const api = {
  listChats: async () =>
    safeRequest("/api/jarvis/chats/list", {}, async () => getChatsLocal()),

  createChat: async ({ title = "Новый чат" } = {}) =>
    safeRequest(
      "/api/jarvis/chats/create",
      { method: "POST", body: { title } },
      async () => {
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
        saveMessagesLocal(chat.id, []);
        return chat;
      }
    ),

  renameChat: async ({ id, title }) =>
    safeRequest(
      "/api/jarvis/chats/rename",
      { method: "POST", body: { id, title } },
      async () => {
        const chats = getChatsLocal().map((item) =>
          item.id === id ? { ...item, title, updated_at: new Date().toISOString() } : item
        );
        saveChatsLocal(chats);
        return { ok: true };
      }
    ),

  deleteChat: async ({ id }) =>
    safeRequest(
      "/api/jarvis/chats/delete",
      { method: "POST", body: { id } },
      async () => {
        const chats = getChatsLocal().filter((item) => item.id !== id);
        saveChatsLocal(chats);
        localStorage.removeItem(`jarvis_fix7_messages_${id}`);
        return { ok: true };
      }
    ),

  pinChat: async ({ id, pinned }) =>
    safeRequest(
      "/api/jarvis/chats/pin",
      { method: "POST", body: { id, pinned } },
      async () => {
        const chats = getChatsLocal().map((item) =>
          item.id === id ? { ...item, pinned, updated_at: new Date().toISOString() } : item
        );
        saveChatsLocal(chats);
        return { ok: true };
      }
    ),

  saveChatToMemory: async ({ id, saved }) =>
    safeRequest(
      "/api/jarvis/chats/save-memory",
      { method: "POST", body: { id, saved } },
      async () => {
        const chats = getChatsLocal().map((item) =>
          item.id === id ? { ...item, memory_saved: saved, updated_at: new Date().toISOString() } : item
        );
        saveChatsLocal(chats);
        return { ok: true };
      }
    ),

  getMessages: async ({ chatId }) =>
    safeRequest(
      `/api/jarvis/chats/messages?chat_id=${encodeURIComponent(chatId)}`,
      {},
      async () => getMessagesLocal(chatId)
    ),

  addMessage: async ({ chatId, role, content }) =>
    safeRequest(
      "/api/jarvis/chats/messages/add",
      { method: "POST", body: { chat_id: chatId, role, content } },
      async () => {
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

  execute: async ({ chatId, message, mode = "chat", model = "qwen3:8b", profile_name = "Универсальный" }) =>
    safeRequest(
      "/api/chat/send",
      {
        method: "POST",
        body: {
          model_name: model,
          profile_name,
          user_input: message,
          history: getMessagesLocal(chatId).map((m) => ({ role: m.role, content: m.content })),
          use_memory: true,
          use_library: true,
        },
      },
      async () => ({
        content: `Echo (${mode}, ${model}): ${message}`,
      })
    ).then((payload) => {
      if (payload?.assistant_content) return { content: payload.assistant_content };
      if (payload?.answer) return { content: payload.answer };
      if (payload?.content) return { content: payload.content };
      return { content: String(payload || "") };
    }),

  listOllamaModels: async () =>
    safeRequest("/api/models", {}, async () => [
      { name: "qwen3:8b" },
      { name: "qwen2.5-coder:7b" },
      { name: "llama3.1:8b" },
    ]),
};
