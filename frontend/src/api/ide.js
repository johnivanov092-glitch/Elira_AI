import { buildApiUrl, request, safeRequest } from "./client";
import {
  diffFile,
  extractUploadedFileText,
  writeFile,
} from "./fileOps";
import {
  createGitCommit,
  getGitDiff,
  getGitLog,
  getGitStatus,
} from "./git";
import {
  closeAdvancedProject,
  getAdvancedProjectInfo,
  getAdvancedProjectTree,
  getProjectBrainStatus,
  getProjectFile,
  getProjectSnapshot,
  openAdvancedProject,
  readAdvancedProjectFile,
  runAdvancedMultiAgent,
  searchAdvancedProject,
} from "./project";
import {
  deleteLibraryFile,
  listLibraryFiles,
  uploadLibraryFile,
} from "./library";
import {
  createPipeline,
  deletePipeline,
  listPipelines,
  runPipeline,
  updatePipeline,
} from "./pipelines";
import {
  applyPatch,
  listPatchHistory,
  previewPatch,
  rollbackPatch,
  verifyPatch,
} from "./patch";
import {
  listPlugins,
  reloadPlugins,
  setPluginEnabled,
} from "./plugins";
import {
  addSmartMemory,
  deleteSmartMemory,
  getSmartMemoryStats,
  listSmartMemory,
  searchSmartMemory,
} from "./smartMemory";
import {
  getAgentOsDashboard,
  getAgentOsHealth,
  getDashboardOverview,
  getPersonaStatus,
  getPersonaVersion,
  getRuntimeStatus,
  listAgentOsLimits,
  listPersonaCandidates,
  rollbackPersona,
} from "./system";
import {
  createTask,
  deleteTask,
  getTasksOverview,
  getTaskStats,
  listTasks,
  updateTask,
} from "./tasks";
import {
  getTelegramConfig,
  getTelegramLog,
  getTelegramOverview,
  listTelegramUsers,
  startTelegramBot,
  stopTelegramBot,
  testTelegramBot,
  toggleTelegramUser,
  updateTelegramConfig,
} from "./telegram";
import { executeTerminal, getTerminalCwd } from "./terminal";
import {
  analyzeCode,
  listToolRuns,
  runPythonCode,
} from "./tools";

export {
  diffFile,
  extractUploadedFileText,
  writeFile,
} from "./fileOps";
export {
  createGitCommit,
  getGitDiff,
  getGitLog,
  getGitStatus,
} from "./git";
export {
  closeAdvancedProject,
  getAdvancedProjectInfo,
  getAdvancedProjectTree,
  getProjectBrainStatus,
  getProjectFile,
  getProjectSnapshot,
  openAdvancedProject,
  readAdvancedProjectFile,
  runAdvancedMultiAgent,
  searchAdvancedProject,
} from "./project";
export {
  deleteLibraryFile,
  listLibraryFiles,
  uploadLibraryFile,
} from "./library";
export {
  createPipeline,
  deletePipeline,
  listPipelines,
  runPipeline,
  updatePipeline,
} from "./pipelines";
export {
  applyPatch,
  listPatchHistory,
  previewPatch,
  rollbackPatch,
  verifyPatch,
} from "./patch";
export {
  listPlugins,
  reloadPlugins,
  setPluginEnabled,
} from "./plugins";
export {
  addSmartMemory,
  deleteSmartMemory,
  getSmartMemoryStats,
  listSmartMemory,
  searchSmartMemory,
} from "./smartMemory";
export {
  getAgentOsDashboard,
  getAgentOsHealth,
  getDashboardOverview,
  getPersonaStatus,
  getPersonaVersion,
  getRuntimeStatus,
  listAgentOsLimits,
  listPersonaCandidates,
  rollbackPersona,
} from "./system";
export {
  createTask,
  deleteTask,
  getTasksOverview,
  getTaskStats,
  listTasks,
  updateTask,
} from "./tasks";
export {
  getTelegramConfig,
  getTelegramLog,
  getTelegramOverview,
  listTelegramUsers,
  startTelegramBot,
  stopTelegramBot,
  testTelegramBot,
  toggleTelegramUser,
  updateTelegramConfig,
} from "./telegram";
export { executeTerminal, getTerminalCwd } from "./terminal";
export {
  analyzeCode,
  listToolRuns,
  runPythonCode,
} from "./tools";

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

function normalizeSessionId(value) {
  if (value === undefined || value === null || value === "") return null;
  return typeof value === "string" ? value : String(value);
}

function normalizeChat(item = {}) {
  return {
    ...item,
    id: item.id ?? "",
    title: item.title ?? "New chat",
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
    return "run_agent returned an error";
  }
  return "";
}

export function isLocalApiAssetUrl(url = "") {
  return typeof url === "string" && (
    url.includes("/api/skills/download/") ||
    url.includes("/api/skills/view/") ||
    url.includes("/api/extra/")
  );
}

export async function listChats() {
  const payload = await safeRequest("/api/elira/chats", {}, []);
  return normalizeArray(payload).map(normalizeChat);
}

export async function createChat(body = {}) {
  return normalizeChat(unwrapItem(await request("/api/elira/chats", { method: "POST", body })));
}

export async function renameChat(arg1, arg2) {
  const payload = typeof arg1 === "object" && arg1 !== null ? arg1 : { id: arg1, title: arg2 };
  return normalizeChat(unwrapItem(await request(`/api/elira/chats/${encodeURIComponent(payload.id)}`, {
    method: "PATCH",
    body: { title: payload.title },
  })));
}

export async function pinChat(arg1, arg2) {
  const payload = typeof arg1 === "object" && arg1 !== null ? arg1 : { id: arg1, pinned: arg2 };
  return normalizeChat(unwrapItem(await request(`/api/elira/chats/${encodeURIComponent(payload.id)}/pin`, {
    method: "PATCH",
    body: { pinned: Boolean(payload.pinned) },
  })));
}

export async function saveChatToMemory(arg1, arg2) {
  const payload = typeof arg1 === "object" && arg1 !== null ? arg1 : { id: arg1, saved: arg2 };
  return normalizeChat(unwrapItem(await request(`/api/elira/chats/${encodeURIComponent(payload.id)}/memory`, {
    method: "PATCH",
    body: { memory_saved: Boolean(payload.saved) },
  })));
}

export async function deleteChat(arg) {
  const id = typeof arg === "object" && arg !== null ? arg.id : arg;
  return request(`/api/elira/chats/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function getMessages(arg) {
  const chatId = typeof arg === "object" && arg !== null ? arg.chatId : arg;
  const payload = await safeRequest(`/api/elira/chats/${encodeURIComponent(chatId)}/messages`, {}, []);
  return normalizeArray(payload).map(normalizeMessage);
}

export async function addMessage(body = {}) {
  const payload = await request("/api/elira/messages", {
    method: "POST",
    body: {
      chat_id: body.chatId ?? body.chat_id ?? null,
      role: body.role ?? "user",
      content: typeof body.content === "string" ? body.content : String(body.content ?? ""),
    },
  });
  const message = normalizeMessage(unwrapItem(payload?.message ?? payload));
  return {
    ...message,
    ...payload,
    chat_id: payload?.chat_id ?? body.chatId ?? body.chat_id ?? null,
    message,
  };
}

export async function sendMessage(body = {}) {
  const payload = await addMessage(body);
  return payload.message;
}

export async function execute(body = {}) {
  const response = await request("/api/chat/send", {
    method: "POST",
    body: {
      model_name: body.model_name ?? body.model ?? "gemma3:4b",
      profile_name: body.profile_name ?? body.profile ?? "default",
      user_input: String(body.user_input ?? body.message ?? body.prompt ?? body.text ?? body.query ?? "").trim(),
      session_id: normalizeSessionId(body.session_id ?? body.chat_id ?? body.chatId ?? null),
      history: Array.isArray(body.history) ? body.history : [],
      use_memory: body.use_memory ?? true,
      use_library: body.use_library ?? true,
      use_reflection: body.use_reflection ?? false,
    },
  });
  const routeError = extractAgentError(response);
  if (routeError) throw new Error(routeError);
  const content = response?.content ?? response?.answer ?? response?.response ?? response?.message ?? "";
  if (!String(content).trim()) throw new Error("Empty response from /api/chat/send");
  return { ...response, content: String(content) };
}

export function executeStream(body = {}, { onToken, onDone, onError, onPhase } = {}) {
  const controller = new AbortController();

  const payload = {
    model_name: body.model_name ?? body.model ?? "gemma3:4b",
    profile_name: body.profile_name ?? body.profile ?? "default",
    user_input: String(body.user_input ?? body.message ?? "").trim(),
    session_id: normalizeSessionId(body.session_id ?? body.chat_id ?? body.chatId ?? null),
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

  fetch(buildApiUrl("/api/chat/stream"), {
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
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed || !trimmed.startsWith("data: ")) continue;

            try {
              const event = JSON.parse(trimmed.slice(6));

              if (event.error) {
                onError?.(event.error);
                return;
              }

              if (event.phase && onPhase) onPhase(event);
              if (event.phase === "reflection_replace" && event.full_text) continue;
              if (event.token) onToken?.(event.token);

              if (event.done) {
                onDone?.({
                  full_text: event.full_text || "",
                  meta: event.meta || {},
                  timeline: event.timeline || [],
                });
                return;
              }
            } catch (parseError) {
              console.warn("SSE parse error:", trimmed.slice(0, 100), parseError);
            }
          }
        }

        onDone?.({ full_text: "", meta: {}, timeline: [] });
      } finally {
        reader.cancel().catch(() => {});
      }
    })
    .catch((error) => {
      if (error.name === "AbortError") return;
      onError?.(error.message || "Stream error");
    });

  return controller;
}

export async function listOllamaModels() {
  const payload = await safeRequest("/api/elira/models", {}, []);
  if (Array.isArray(payload?.models)) return { models: payload.models };
  if (Array.isArray(payload?.items)) return { models: payload.items };
  if (Array.isArray(payload)) return { models: payload };
  return { models: [] };
}

export async function getSettings() {
  return safeRequest("/api/elira/settings", {}, {});
}

export async function updateSettings(body = {}) {
  return request("/api/elira/settings", { method: "PUT", body });
}

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
  executeStream,
  listOllamaModels,
  getSettings,
  updateSettings,
  getProjectSnapshot,
  getProjectFile,
  getProjectBrainStatus,
  getPersonaStatus,
  getRuntimeStatus,
  getAgentOsHealth,
  getAgentOsDashboard,
  listAgentOsLimits,
  getPersonaVersion,
  listPersonaCandidates,
  rollbackPersona,
  getDashboardOverview,
  listPatchHistory,
  previewPatch,
  applyPatch,
  rollbackPatch,
  verifyPatch,
  extractUploadedFileText,
  listLibraryFiles,
  uploadLibraryFile,
  deleteLibraryFile,
  listTasks,
  getTaskStats,
  getTasksOverview,
  createTask,
  updateTask,
  deleteTask,
  listPipelines,
  createPipeline,
  runPipeline,
  updatePipeline,
  deletePipeline,
  getTelegramConfig,
  listTelegramUsers,
  getTelegramLog,
  getTelegramOverview,
  startTelegramBot,
  stopTelegramBot,
  testTelegramBot,
  updateTelegramConfig,
  toggleTelegramUser,
  listPlugins,
  reloadPlugins,
  setPluginEnabled,
  getAdvancedProjectInfo,
  openAdvancedProject,
  getAdvancedProjectTree,
  readAdvancedProjectFile,
  searchAdvancedProject,
  closeAdvancedProject,
  runAdvancedMultiAgent,
  getGitStatus,
  getGitLog,
  getGitDiff,
  createGitCommit,
  listToolRuns,
  runPythonCode,
  analyzeCode,
  diffFile,
  writeFile,
  listSmartMemory,
  getSmartMemoryStats,
  addSmartMemory,
  deleteSmartMemory,
  searchSmartMemory,
  getTerminalCwd,
  executeTerminal,
  isLocalApiAssetUrl,
};

export default api;
