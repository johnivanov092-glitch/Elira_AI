
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

async function safeRequest(url, options, fallback) {
  try {
    return await request(url, options);
  } catch {
    return fallback();
  }
}

function asArray(payload, key = "") {
  if (Array.isArray(payload)) return payload;
  if (key && Array.isArray(payload?.[key])) return payload[key];
  if (Array.isArray(payload?.items)) return payload.items;
  if (Array.isArray(payload?.results)) return payload.results;
  if (Array.isArray(payload?.data)) return payload.data;
  return [];
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
  return loadLocal("jarvis_profile_fix_chats", []);
}

function saveChatsLocal(chats) {
  saveLocal("jarvis_profile_fix_chats", chats);
}

function getMessagesLocal(chatId) {
  return loadLocal(`jarvis_profile_fix_messages_${chatId}`, []);
}

function saveMessagesLocal(chatId, messages) {
  saveLocal(`jarvis_profile_fix_messages_${chatId}`, messages);
}

const FALLBACK_PROFILES = [
  {
    name: "Универсальный",
    description: "Простые и практичные ответы без лишней глубины.",
  },
  {
    name: "Программист",
    description: "Код, архитектура, исправления, рефакторинг.",
  },
  {
    name: "Оркестратор",
    description: "Планирование, multi-agent маршрут, orchestration flow.",
  },
  {
    name: "Исследователь",
    description: "Поиск фактов, сравнение источников, исследование.",
  },
  {
    name: "Аналитик",
    description: "Выводы, риски, структуры, практические рекомендации.",
  },
  {
    name: "Сократ",
    description: "Вопросы и направляющее рассуждение.",
  },
];

export const api = {
  health: () => request("/health"),

  listChats: async () =>
    safeRequest("/api/jarvis/chats/list", {}, async () => getChatsLocal()),

  createChat: async ({ title = "Новый чат" } = {}) =>
    safeRequest("/api/jarvis/chats/create", { method: "POST", body: { title } }, async () => {
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
    }),

  deleteChat: async ({ id }) =>
    safeRequest("/api/jarvis/chats/delete", { method: "POST", body: { id } }, async () => {
      const chats = getChatsLocal().filter((item) => item.id !== id);
      saveChatsLocal(chats);
      localStorage.removeItem(`jarvis_profile_fix_messages_${id}`);
      return { ok: true };
    }),

  pinChat: async ({ id, pinned }) =>
    safeRequest("/api/jarvis/chats/pin", { method: "POST", body: { id, pinned } }, async () => {
      const chats = getChatsLocal().map((item) =>
        item.id === id ? { ...item, pinned, updated_at: new Date().toISOString() } : item
      );
      saveChatsLocal(chats);
      return { ok: true };
    }),

  saveChatToMemory: async ({ id, saved }) =>
    safeRequest("/api/jarvis/chats/save-memory", { method: "POST", body: { id, saved } }, async () => {
      const chats = getChatsLocal().map((item) =>
        item.id === id ? { ...item, memory_saved: saved, updated_at: new Date().toISOString() } : item
      );
      saveChatsLocal(chats);
      return { ok: true };
    }),

  getMessages: async ({ chatId }) =>
    safeRequest(`/api/jarvis/chats/messages?chat_id=${encodeURIComponent(chatId)}`, {}, async () => getMessagesLocal(chatId)),

  addMessage: async ({ chatId, role, content }) =>
    safeRequest("/api/jarvis/chats/messages/add", { method: "POST", body: { chat_id: chatId, role, content } }, async () => {
      const messages = getMessagesLocal(chatId);
      const item = { id: uid("msg"), role, content, created_at: new Date().toISOString() };
      messages.push(item);
      saveMessagesLocal(chatId, messages);
      return item;
    }),

  listProfiles: async () =>
    safeRequest("/api/profiles", {}, async () => ({ profiles: FALLBACK_PROFILES, default_profile: "Универсальный" }))
      .then((payload) => {
        const profiles = asArray(payload, "profiles").map((item) => ({
          name: item.name,
          description: item.system_prompt_preview || item.description || "",
        }));
        return profiles.length ? profiles : FALLBACK_PROFILES;
      }),

  listOllamaModels: async () =>
    safeRequest("/api/models", {}, async () => ({
      models: [
        { name: "qwen3:8b" },
        { name: "qwen2.5-coder:7b" },
        { name: "deepseek-r1:8b" },
        { name: "mistral-nemo:latest" },
      ],
    })).then((payload) => {
      const models = asArray(payload, "models");
      return models.length ? models : [{ name: "qwen3:8b" }];
    }),

  listContextWindows: async () => [4096, 8192, 16384, 32768, 65536, 131072, 262144],

  execute: async ({ chatId, message, profileName = "Универсальный", model = "qwen3:8b" }) =>
    safeRequest("/api/chat/send", {
      method: "POST",
      body: {
        model_name: model,
        profile_name: profileName,
        user_input: message,
        history: getMessagesLocal(chatId).map((m) => ({ role: m.role, content: m.content })),
        use_memory: true,
        use_library: true,
      },
    }, async () => {
      const reply = {
        id: uid("msg"),
        role: "assistant",
        content: `Echo (${profileName}, ${model}): ${message}`,
        created_at: new Date().toISOString(),
      };
      const messages = getMessagesLocal(chatId);
      messages.push(reply);
      saveMessagesLocal(chatId, messages);
      return reply;
    }).then((payload) => {
      if (payload?.assistant_content) {
        return {
          id: uid("msg"),
          role: "assistant",
          content: payload.assistant_content,
          created_at: new Date().toISOString(),
        };
      }
      if (payload?.answer) {
        return {
          id: uid("msg"),
          role: "assistant",
          content: payload.answer,
          created_at: new Date().toISOString(),
        };
      }
      return payload;
    }),

  getProjectSnapshot: () =>
    safeRequest("/api/project-brain/snapshot", {}, async () => ({ files: [] })),
  getProjectFile: (path) =>
    request(`/api/project-brain/file?path=${encodeURIComponent(path)}`),

  previewPatch: ({ path, instruction, content }) =>
    request("/api/project-brain/agent/ollama/run", {
      method: "POST",
      body: {
        mode: "code",
        selected_path: path,
        selected_content: content,
        goal: instruction,
        project_files: [path],
      },
    }),
};
