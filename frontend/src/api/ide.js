import { buildApiUrl, request, safeRequest } from "./client";

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

function formatRequestError(error, fallback = "Request failed") {
  const value = error?.message ?? error?.detail ?? error;
  if (!value) return fallback;
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map((item) => formatRequestError(item, "")).filter(Boolean).join(" | ") || fallback;
  if (typeof value === "object") return value.message || value.msg || JSON.stringify(value);
  return String(value);
}

function withParams(path, params = {}) {
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

export async function getProjectSnapshot() {
  const payload = await request("/api/project-brain/snapshot");
  return { ...payload, files: Array.isArray(payload?.files) ? payload.files : [] };
}

export async function getProjectFile(path) {
  return request(`/api/project-brain/file?path=${encodeURIComponent(path)}`);
}

export async function getProjectBrainStatus() {
  return safeRequest("/api/project-brain/status", {}, { status: "unknown" });
}

export async function getPersonaStatus() {
  return safeRequest("/api/persona/status", {}, { ok: false, active_version: 1, quarantine_candidates: 0, latest_traits: [], model_consistency: [] });
}

export async function getRuntimeStatus() {
  return safeRequest("/api/runtime/status", {}, {
    ok: false,
    storage_mode: "unknown",
    active_chat_count: 0,
    primary_engine: "",
    fallback_engines: [],
    available_engines: [],
    api_keys_present: { tavily: false },
    degraded_mode: false,
    web_warnings: [],
  });
}

export async function getPersonaVersion(version) {
  return safeRequest(withParams("/api/persona/version", { version }), {}, { ok: false, item: null });
}

export async function listPersonaCandidates(limit = 20) {
  const payload = await safeRequest(withParams("/api/persona/candidates", { limit }), {}, { items: [], count: 0 });
  return { ...payload, items: normalizeArray(payload), count: payload?.count ?? normalizeArray(payload).length };
}

export async function rollbackPersona(version) {
  return request(`/api/persona/rollback/${encodeURIComponent(version)}`, { method: "POST" });
}

export async function getDashboardOverview() {
  const [statsResult, projectBrainStatusResult, personaStatusResult, runtimeStatusResult] = await Promise.allSettled([
    request("/api/dashboard/stats"),
    getProjectBrainStatus(),
    getPersonaStatus(),
    getRuntimeStatus(),
  ]);

  const errors = [];
  const stats = statsResult.status === "fulfilled" ? statsResult.value : null;
  const projectBrainStatus = projectBrainStatusResult.status === "fulfilled" ? projectBrainStatusResult.value : null;
  const personaStatus = personaStatusResult.status === "fulfilled" ? personaStatusResult.value : null;
  const runtimeStatus = runtimeStatusResult.status === "fulfilled" ? runtimeStatusResult.value : null;

  if (statsResult.status === "rejected") {
    errors.push(`dashboard stats: ${formatRequestError(statsResult.reason)}`);
  }
  if (projectBrainStatusResult.status === "rejected") {
    errors.push(`project brain status: ${formatRequestError(projectBrainStatusResult.reason)}`);
  }
  if (personaStatusResult.status === "rejected") {
    errors.push(`persona status: ${formatRequestError(personaStatusResult.reason)}`);
  }
  if (runtimeStatusResult.status === "rejected") {
    errors.push(`runtime status: ${formatRequestError(runtimeStatusResult.reason)}`);
  }
  if (!stats && !projectBrainStatus && !personaStatus && !runtimeStatus && errors.length) {
    throw new Error(errors.join(" | "));
  }

  return { stats, projectBrainStatus, personaStatus, runtimeStatus, errors };
}

export async function listPatchHistory({ path = "", limit = 20 } = {}) {
  const payload = await safeRequest(withParams("/api/elira/patch/history/list", { path, limit }), {}, { items: [] });
  return { ...payload, items: normalizeArray(payload) };
}

export async function previewPatch(body = {}) {
  return request("/api/elira/patch/diff", { method: "POST", body });
}

export async function applyPatch(body = {}) {
  return request("/api/elira/patch/apply", { method: "POST", body });
}

export async function rollbackPatch(body = {}) {
  return request("/api/elira/patch/rollback", { method: "POST", body });
}

export async function verifyPatch(body = {}) {
  return request("/api/elira/patch/verify", { method: "POST", body });
}

export async function extractUploadedFileText(file) {
  const formData = new FormData();
  formData.append("file", file);
  return request("/api/files/extract-text", { method: "POST", body: formData });
}

export async function listLibraryFiles() {
  return safeRequest("/api/lib/list", {}, null);
}

export async function uploadLibraryFile(file, { useInContext = false } = {}) {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("use_in_context", String(useInContext));
  return request("/api/lib/add", { method: "POST", body: formData });
}

export async function deleteLibraryFile(id) {
  return request(`/api/lib/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function listTasks(status) {
  return request(withParams("/api/tasks/list", { status }));
}

export async function getTaskStats() {
  return request("/api/tasks/stats");
}

export async function getTasksOverview(filter = "active") {
  let tasks = [];
  if (filter === "active") {
    const [todo, inProgress] = await Promise.all([
      listTasks("todo"),
      listTasks("in_progress"),
    ]);
    tasks = [...(todo?.tasks || []), ...(inProgress?.tasks || [])];
  } else if (filter === "all") {
    const data = await listTasks();
    tasks = data?.tasks || [];
  } else {
    const data = await listTasks(filter);
    tasks = data?.tasks || [];
  }
  const stats = await getTaskStats();
  return { tasks, stats };
}

export async function createTask(body = {}) {
  return request("/api/tasks/create", { method: "POST", body });
}

export async function updateTask(taskId, body = {}) {
  return request(`/api/tasks/update/${encodeURIComponent(taskId)}`, { method: "PUT", body });
}

export async function deleteTask(taskId) {
  return request(`/api/tasks/delete/${encodeURIComponent(taskId)}`, { method: "DELETE" });
}

export async function listPipelines() {
  const payload = await request("/api/pipelines/list");
  return payload?.pipelines || [];
}

export async function createPipeline(body = {}) {
  return request("/api/pipelines/create", { method: "POST", body });
}

export async function runPipeline(pipelineId) {
  return request(`/api/pipelines/run/${encodeURIComponent(pipelineId)}`, { method: "POST" });
}

export async function updatePipeline(pipelineId, body = {}) {
  return request(`/api/pipelines/update/${encodeURIComponent(pipelineId)}`, { method: "PUT", body });
}

export async function deletePipeline(pipelineId) {
  return request(`/api/pipelines/delete/${encodeURIComponent(pipelineId)}`, { method: "DELETE" });
}

export async function getTelegramConfig() {
  return request("/api/telegram/config");
}

export async function listTelegramUsers() {
  return request("/api/telegram/users");
}

export async function getTelegramLog(limit = 30) {
  return request(withParams("/api/telegram/log", { limit }));
}

export async function getTelegramOverview(limit = 30) {
  const [config, users, log] = await Promise.all([
    getTelegramConfig(),
    listTelegramUsers(),
    getTelegramLog(limit),
  ]);
  return {
    config,
    users: users?.users || [],
    log: log?.log || [],
  };
}

export async function startTelegramBot() {
  const payload = await request("/api/telegram/start", { method: "POST" });
  if (payload?.ok === false) throw new Error(payload.error || "Failed to start Telegram bot");
  return payload;
}

export async function stopTelegramBot() {
  return request("/api/telegram/stop", { method: "POST" });
}

export async function testTelegramBot() {
  return request("/api/telegram/test");
}

export async function updateTelegramConfig(body = {}) {
  return request("/api/telegram/config", { method: "PUT", body });
}

export async function toggleTelegramUser(body = {}) {
  return request("/api/telegram/users/toggle", { method: "POST", body });
}

export async function listPlugins() {
  const payload = await request("/api/extra/plugins/list");
  return payload?.plugins || [];
}

export async function reloadPlugins() {
  return request("/api/extra/plugins/reload", { method: "POST" });
}

export async function setPluginEnabled(name, enabled) {
  const action = enabled ? "enable" : "disable";
  return request(`/api/extra/plugins/${action}/${encodeURIComponent(name)}`, { method: "POST" });
}

export async function getAdvancedProjectInfo() {
  return request("/api/advanced/project/info");
}

export async function openAdvancedProject(path) {
  return request("/api/advanced/project/open", { method: "POST", body: { path } });
}

export async function getAdvancedProjectTree({ maxDepth = 3, maxItems = 300 } = {}) {
  return request(withParams("/api/advanced/project/tree", {
    max_depth: maxDepth,
    max_items: maxItems,
  }));
}

export async function readAdvancedProjectFile(path, maxChars) {
  const body = { path };
  if (maxChars) body.max_chars = maxChars;
  return request("/api/advanced/project/read", { method: "POST", body });
}

export async function searchAdvancedProject(query) {
  return request("/api/advanced/project/search", { method: "POST", body: { query } });
}

export async function closeAdvancedProject() {
  return request("/api/advanced/project/close");
}

export async function runAdvancedMultiAgent(body = {}) {
  return request("/api/advanced/multi-agent", { method: "POST", body });
}

export async function getGitStatus() {
  return request("/api/git/status");
}

export async function getGitLog(limit = 20) {
  return request(withParams("/api/git/log", { limit }));
}

export async function getGitDiff(body = { repo_path: "", file_path: "" }) {
  return request("/api/git/diff", { method: "POST", body });
}

export async function createGitCommit(body = {}) {
  return request("/api/git/commit", { method: "POST", body });
}

export async function listToolRuns(limit = 50) {
  const payload = await request(withParams("/api/tools/run-history", { limit }));
  return payload?.runs || [];
}

export async function runPythonCode(code) {
  return request("/api/tools/run-python", { method: "POST", body: { code } });
}

export async function analyzeCode(body = {}) {
  return request("/api/tools/analyze-code", { method: "POST", body });
}

export async function diffFile(body = {}) {
  return request("/api/file-ops/diff", { method: "POST", body });
}

export async function writeFile(body = {}) {
  return request("/api/file-ops/write", { method: "POST", body });
}

export async function listSmartMemory(limit = 100) {
  const payload = await request(withParams("/api/smart-memory/list", { limit }));
  return payload?.items || [];
}

export async function getSmartMemoryStats() {
  return request("/api/smart-memory/stats");
}

export async function addSmartMemory(body = {}) {
  return request("/api/smart-memory/add", { method: "POST", body });
}

export async function deleteSmartMemory(id) {
  return request(`/api/smart-memory/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function searchSmartMemory(query, limit = 20) {
  const payload = await request("/api/smart-memory/search", {
    method: "POST",
    body: { query, limit },
  });
  return payload?.items || [];
}

export async function getTerminalCwd() {
  return safeRequest("/api/terminal/cwd", {}, null);
}

export async function executeTerminal(body = {}) {
  return request("/api/terminal/exec", { method: "POST", body });
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
