import { API_BASE, ApiError, buildApiUrl, request, withAuth } from "./client";
import type { ChatAttachment } from "./chat";

export const DEFAULT_CODE_AGENT_MODEL = "auto";

/** Proxied favicon URL for a source origin. The endpoint literal lives here in
 *  the api layer (not in components) per the smoke-contract guard. */
export function codeAgentFaviconUrl(url: string): string {
  return buildApiUrl(`/api/code-agent/favicon?url=${encodeURIComponent(url)}`);
}
export const DEFAULT_CODE_AGENT_MAX_STEPS = 200;
export const DEFAULT_CODE_AGENT_NUM_CTX = 131072;

export type CodeAgentToolCall = {
  step: number;
  tool: string;
  arguments: Record<string, unknown>;
  result: string;
  ok?: boolean;
  exit_code?: number;
  verifier?: boolean;
  evidence?: string;
  touched_path?: string;
  old_content?: string;
  new_content?: string;
  diff_action?: "create" | "overwrite" | "edit";
};

export type CodeAgentResponse = {
  ok: boolean;
  response: string;
  steps: number;
  tool_calls: CodeAgentToolCall[];
  stop_reason: "answer" | "max_steps" | "timeout" | "context_limit" | "loop_guard" | "no_progress" | "error" | "cancelled";
  error: string | null;
  partial: boolean;
};

export type ConversationMessage = {
  role: "user" | "assistant";
  content: string;
};

export type CodeAgentMode = "code" | "search";

/** Approval policy for a run, picked in the composer's permission selector:
 *  - "ask"          — pause for the user on every gated tool (default);
 *  - "accept_edits" — auto-approve filesystem edits, still pause shell/net;
 *  - "bypass"       — auto-approve every gated tool (forbidden stays blocked). */
export type PermissionMode = "ask" | "accept_edits" | "bypass";

export type CodeAgentRunArgs = {
  message: string;
  projectRoot: string;
  model?: string;
  maxSteps?: number;
  numCtx?: number;
  mode?: CodeAgentMode;
  autoRemember?: boolean;
  conversationHistory?: ConversationMessage[];
  /** Composer attachments (images / documents) already parsed to text by
   *  `/api/chat/attach`. Carried alongside the project so the unified "Чат\Код"
   *  chip can do both at once. Frontend-only fields are stripped before send. */
  attachments?: ChatAttachment[];
  /** Persona mode (Авто / Личный / Баланс / Инженерный / Деловой / Инфраструктура); "Авто" lets Elira pick
   *  per message, a concrete mode locks it. Mirrors chat's profile_name field. */
  profileName?: string;
  /** Approval policy for this run (composer permission selector). Omitted → the
   *  backend default "ask". See {@link PermissionMode}. */
  permissionMode?: PermissionMode;
  /** Enable model reasoning for this run («Рассуждение» chip). When on, the
   *  backend streams reasoning as separate `reasoning_delta` events. Omitted →
   *  backend default (off). */
  thinking?: boolean;
  /** «Не спрашивать» chip. When on, ask_user never pauses the run for a human —
   *  Elira answers its own question with a "decide for yourself" note and keeps
   *  going. Omitted → backend default (off, questions pause as normal). */
  noQuestions?: boolean;
};

/** Single-shot (legacy). Resolves with the aggregated final dict. */
export async function runCodeAgent({
  message,
  projectRoot,
  model = DEFAULT_CODE_AGENT_MODEL,
  maxSteps = DEFAULT_CODE_AGENT_MAX_STEPS,
  numCtx = DEFAULT_CODE_AGENT_NUM_CTX,
  mode = "code",
  autoRemember = true,
  conversationHistory,
  profileName,
}: CodeAgentRunArgs): Promise<CodeAgentResponse> {
  return request<CodeAgentResponse>("/api/code-agent/run", {
    method: "POST",
    body: {
      message,
      project_root: projectRoot,
      model,
      max_steps: maxSteps,
      num_ctx: numCtx,
      mode,
      auto_remember: autoRemember,
      conversation_history: conversationHistory,
      ...(profileName ? { profile_name: profileName } : {}),
    },
  });
}

// ── Streaming protocol ───────────────────────────────────────────────────

export type CodeAgentStreamEvent =
  | { type: "run_started"; run_id: string }
  | { type: "run_resumed"; run_id: string; from_step: number }
  | { type: "step_started"; step: number }
  | { type: "heartbeat"; step: number }
  | { type: "delta"; step: number; text: string }
  | { type: "reasoning_delta"; step: number; text: string }
  | {
      type: "tool_started";
      step: number;
      tool: string;
      arguments: Record<string, unknown>;
    }
  | ({ type: "tool_call" } & CodeAgentToolCall)
  | {
      type: "approval_pending";
      step: number;
      tool: string;
      arguments: Record<string, unknown>;
      approval_id: string;
    }
  | { type: "approval_wait"; step: number; approval_id: string; waited_s: number }
  | { type: "question_pending"; step: number; question: string; options: string[]; question_id: string }
  | { type: "question_wait"; step: number; question_id: string; waited_s: number }
  | { type: "context_compacted"; step: number; context?: ContextUsage; rolling_summary?: string | null }
  | {
      type: "usage";
      step: number;
      prompt_tokens: number;
      completion_tokens: number;
      total_tokens: number;
      tokens_per_second: number;
      context?: ContextUsage;
      profile?: ContextProfile;
    }
  | { type: "final_response"; step: number; text: string; established_facts?: string; recent_tool_output?: string }
  | {
      type: "done";
      ok: boolean;
      steps: number;
      stop_reason: CodeAgentResponse["stop_reason"];
      error: string | null;
      partial?: boolean;
      resumable?: boolean;
      run_id?: string;
      established_facts?: string;
      recent_tool_output?: string;
      // Task-completion axis — SEPARATE from runtime `ok`. "confirmed" means every
      // success criterion was proven by a verifier; consumers must gate "solved"
      // on this, not on `ok`.
      completion_status?: CompletionStatus;
      criteria?: CriterionState[];
    };

export type CompletionStatus = "confirmed" | "partial" | "unverified" | "failed" | "none";

export type CriterionState = {
  text: string;
  status: "confirmed" | "unconfirmed" | "failed";
  verifier?: string | null;
  evidence?: string | null;
};

export type ContextUsage = {
  current_tokens: number;
  reserved_output_tokens: number;
  ctx_size: number;
  percent: number;
  free_tokens: number;
  breakdown: Record<string, number>;
};

export type ContextState = Partial<ContextUsage> & {
  last_context_usage?: ContextUsage;
  rolling_summary_text?: string;
  rolling_summary_updated_at?: number;
  rolling_summary_source?: string;
  [key: string]: unknown;
};

export type ContextProfile = {
  active_model: string;
  ctx_size: number;
  safe_input_budget: number;
  source: string;
};

// ── Approvals (Agent OS) ─────────────────────────────────────────────────

/** Resolve a pending tool-call approval. The paused agent run picks the
 *  decision up on its next poll tick. */
export async function resolveApproval(
  approvalId: string,
  decision: "approve" | "reject",
): Promise<void> {
  await request(`/api/agent-os/approvals/${encodeURIComponent(approvalId)}/${decision}`, {
    method: "POST",
    body: {},
  });
}

export type StreamHandlers = {
  onEvent?: (event: CodeAgentStreamEvent) => void;
  onRunId?: (runId: string) => void;
  onError?: (error: Error) => void;
};

export type StreamCodeAgentArgs = CodeAgentRunArgs & {
  runId?: string;
  signal?: AbortSignal;
} & StreamHandlers;

/** Stream the agent over SSE. Returns the final `done` event (or
 *  resolves with an error event if the stream was aborted). */
export async function streamCodeAgent(args: StreamCodeAgentArgs): Promise<void> {
  const {
    message,
    projectRoot,
    model = DEFAULT_CODE_AGENT_MODEL,
    maxSteps = DEFAULT_CODE_AGENT_MAX_STEPS,
    numCtx = DEFAULT_CODE_AGENT_NUM_CTX,
    mode = "code",
    autoRemember = true,
    conversationHistory,
    attachments,
    profileName,
    permissionMode,
    thinking,
    noQuestions,
    runId,
    signal,
    onEvent,
    onRunId,
    onError,
  } = args;

  // Strip frontend-only fields (the raw File, the toLibrary toggle) before the
  // attachments cross the wire — the backend only consumes the parsed metadata.
  const wireAttachments = (attachments ?? []).map(
    ({ file: _file, toLibrary: _toLibrary, ...rest }) => rest,
  );

  const url = `${API_BASE}/api/code-agent/stream`;
  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: withAuth({ "Content-Type": "application/json", Accept: "text/event-stream" }),
      body: JSON.stringify({
        message,
        project_root: projectRoot,
        model,
        max_steps: maxSteps,
        num_ctx: numCtx,
        mode,
        auto_remember: autoRemember,
        conversation_history: conversationHistory,
        run_id: runId,
        ...(profileName ? { profile_name: profileName } : {}),
        ...(permissionMode ? { permission_mode: permissionMode } : {}),
        ...(thinking ? { thinking: true } : {}),
        ...(noQuestions ? { no_questions: true } : {}),
        ...(wireAttachments.length ? { attachments: wireAttachments } : {}),
      }),
      signal,
    });
  } catch (err) {
    if ((err as DOMException)?.name === "AbortError") return;
    onError?.(err as Error);
    return;
  }

  await consumeCodeAgentStream(response, { onEvent, onRunId, onError });
}

export async function resumeCodeAgent(
  runId: string,
  handlers: StreamHandlers & { signal?: AbortSignal },
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/api/code-agent/runs/${encodeURIComponent(runId)}/resume`, {
      method: "POST",
      headers: withAuth({ Accept: "text/event-stream" }),
      signal: handlers.signal,
    });
  } catch (err) {
    if ((err as DOMException)?.name === "AbortError") return;
    handlers.onError?.(err as Error);
    return;
  }
  // 409 = the run is no longer resumable (already resumed/consumed, or the
  // journal moved on). The "Продолжить" button was stale. Recover gracefully:
  // synthesize a terminal `done` that clears the resumable flag and leaves a
  // friendly note, instead of surfacing a hard "Stream failed: HTTP 409".
  if (response.status === 409) {
    const headerRunId = response.headers.get("X-Run-Id") || runId;
    if (headerRunId) handlers.onRunId?.(headerRunId);
    handlers.onEvent?.({
      type: "done",
      ok: false,
      steps: 0,
      stop_reason: "cancelled",
      error: "Этот запуск уже нельзя продолжить — начните новое сообщение.",
      resumable: false,
      run_id: headerRunId,
    });
    return;
  }
  await consumeCodeAgentStream(response, handlers);
}

// The backend heartbeats every ~10s, so no bytes for this long means it died or
// the connection stalled. Without this the UI shows "Думает…" forever (FIX-13).
const SSE_INACTIVITY_MS = 90_000;

/** reader.read() that rejects if no chunk arrives within `ms`. The timer is
 *  cleared as soon as a chunk (or a real error) resolves, so it never leaks
 *  across the many reads of a long stream. */
function readWithInactivityTimeout<T>(
  reader: ReadableStreamDefaultReader<T>,
  ms: number,
): Promise<ReadableStreamReadResult<T>> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error("Соединение с агентом прервалось — нет ответа. Попробуй ещё раз.")),
      ms,
    );
    reader.read().then(
      (result) => { clearTimeout(timer); resolve(result); },
      (err) => { clearTimeout(timer); reject(err); },
    );
  });
}

export async function consumeCodeAgentStream(response: Response, handlers: StreamHandlers): Promise<void> {
  const { onEvent, onRunId, onError } = handlers;
  const headerRunId = response.headers.get("X-Run-Id");
  if (headerRunId && onRunId) onRunId(headerRunId);

  if (!response.ok || !response.body) {
    onError?.(new Error(`Stream failed: HTTP ${response.status}`));
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  function dispatch(data: string) {
    const trimmed = data.trim();
    if (!trimmed) return;
    try {
      const evt = JSON.parse(trimmed) as CodeAgentStreamEvent;
      onEvent?.(evt);
    } catch {
      // ignore malformed lines
    }
  }

  try {
    for (;;) {
      const { value, done } = await readWithInactivityTimeout(reader, SSE_INACTIVITY_MS);
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      // SSE event boundary is a blank line
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const chunk = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        // Each chunk may have one or more "data: ..." lines
        const dataLines = chunk
          .split("\n")
          .map((ln) => ln.trim())
          .filter((ln) => ln.startsWith("data:"))
          .map((ln) => ln.slice(5).trim());
        if (dataLines.length) dispatch(dataLines.join("\n"));
      }
    }
    // Flush any tail
    if (buffer.trim()) {
      const dataLines = buffer
        .split("\n")
        .map((ln) => ln.trim())
        .filter((ln) => ln.startsWith("data:"))
        .map((ln) => ln.slice(5).trim());
      if (dataLines.length) dispatch(dataLines.join("\n"));
    }
  } catch (err) {
    if ((err as DOMException)?.name === "AbortError") return;
    // Free the socket on an inactivity timeout / read error before surfacing it.
    try { await reader.cancel(); } catch { /* already closed */ }
    onError?.(err as Error);
  }
}

/** Deliver a human answer to a paused ask_user question; the run continues. */
export async function answerQuestion(questionId: string, answer: string): Promise<void> {
  await request(`/api/code-agent/questions/${encodeURIComponent(questionId)}/answer`, {
    method: "POST",
    body: { answer },
  });
}

export async function cancelCodeAgent(runId: string): Promise<{ ok: boolean; found: boolean }> {
  return request<{ ok: boolean; found: boolean; run_id: string }>("/api/code-agent/cancel", {
    method: "POST",
    body: { run_id: runId },
  });
}

// ── Project system prompt CRUD ───────────────────────────────────────────

export type ProjectPromptInfo = {
  ok: boolean;
  exists: boolean;
  content: string;
  path?: string;
  error?: string;
};

export async function getProjectPrompt(projectRoot: string): Promise<ProjectPromptInfo> {
  const qs = new URLSearchParams({ project_root: projectRoot }).toString();
  return request<ProjectPromptInfo>(`/api/code-agent/project-prompt?${qs}`);
}

export async function setProjectPromptApi(projectRoot: string, content: string): Promise<ProjectPromptInfo> {
  return request<ProjectPromptInfo>("/api/code-agent/project-prompt", {
    method: "PUT",
    body: { project_root: projectRoot, content },
  });
}

// ── Project verify command (opt-in E2E gate, .elira/verify) ────────────────

/** The project's verify command ("" if none) plus a `suggested` default guessed
 *  from the project's marker files (pytest/npm/cargo/…), returned only when
 *  nothing is configured yet. After the agent edits files it must run the
 *  command green before it can declare the task done. */
export async function getVerifyCommand(projectRoot: string): Promise<{ command: string; suggested: string }> {
  const qs = new URLSearchParams({ project_root: projectRoot }).toString();
  const res = await request<{ ok: boolean; command: string; suggested?: string }>(`/api/code-agent/verify-command?${qs}`);
  return { command: res.command || "", suggested: res.suggested || "" };
}

/** Set the verify command; an empty string clears it (removes .elira/verify). */
export async function setVerifyCommand(projectRoot: string, command: string): Promise<string> {
  const res = await request<{ ok: boolean; command: string }>("/api/code-agent/verify-command", {
    method: "PUT",
    body: { project_root: projectRoot, command },
  });
  return res.command || "";
}

// ── History summarization ────────────────────────────────────────────────

export type SummarizeHistoryArgs = {
  messages: ConversationMessage[];
  model?: string;
  numCtx?: number;
};

export type SummarizeHistoryResult = {
  ok: boolean;
  summary: string;
  turn_count: number;
  error: string | null;
};

export async function summarizeHistory({
  messages,
  model = DEFAULT_CODE_AGENT_MODEL,
  numCtx = DEFAULT_CODE_AGENT_NUM_CTX,
}: SummarizeHistoryArgs): Promise<SummarizeHistoryResult> {
  return request<SummarizeHistoryResult>("/api/code-agent/summarize-history", {
    method: "POST",
    body: { messages, model, num_ctx: numCtx },
  });
}

// ── Sessions (server-side storage) ────────────────────────────────────────

export type CodeSessionMeta = {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  project_root: string | null;
  model: string | null;
  num_ctx: number | null;
  pinned: boolean;
};

export type CodeSessionFull = CodeSessionMeta & {
  // Turns shape mirrors the frontend's local Turn[] — typed as unknown
  // because parser lives in CodeAgentChatShell.
  turns: unknown[];
  context_state?: ContextState | null;
  task_ledger?: TaskLedgerEntry[];
  pinned_items?: unknown[];
  compression_events?: unknown[];
};

export type TaskLedgerEntry = {
  timestamp: number;
  type: "tool_call" | "final" | "error" | "compression";
  action: string;
  result: string;
};

export async function listCodeSessions(query?: string): Promise<CodeSessionMeta[]> {
  const qs = query ? `?${new URLSearchParams({ query }).toString()}` : "";
  const res = await request<{ ok: boolean; sessions: CodeSessionMeta[] }>(`/api/code-agent/sessions${qs}`);
  return res.sessions || [];
}

export async function getCodeSession(sessionId: string): Promise<CodeSessionFull | null> {
  try {
    const res = await request<{ ok: boolean; session: CodeSessionFull }>(`/api/code-agent/sessions/${encodeURIComponent(sessionId)}`);
    return res.session || null;
  } catch (err) {
    // 404 = genuinely gone → null (caller may start fresh). Any other error
    // (network / 5xx) is RE-THROWN: it must NOT masquerade as an empty session,
    // or the caller seeds a blank transcript and the next save overwrites the
    // real turns on the server (data loss).
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

export type CodeSessionCreateArgs = {
  title?: string;
  projectRoot?: string;
  model?: string;
  numCtx?: number;
};

export async function createCodeSession(args: CodeSessionCreateArgs = {}): Promise<CodeSessionFull> {
  const res = await request<{ ok: boolean; session: CodeSessionFull }>("/api/code-agent/sessions", {
    method: "POST",
    body: {
      title: args.title,
      project_root: args.projectRoot,
      model: args.model,
      num_ctx: args.numCtx,
    },
  });
  return res.session;
}

export type CodeSessionPatch = {
  title?: string;
  projectRoot?: string;
  model?: string;
  numCtx?: number;
  pinned?: boolean;
  turns?: unknown[];
  contextState?: ContextState | null;
  taskLedger?: TaskLedgerEntry[];
};

export async function patchCodeSession(sessionId: string, patch: CodeSessionPatch): Promise<CodeSessionFull> {
  const res = await request<{ ok: boolean; session: CodeSessionFull }>(`/api/code-agent/sessions/${encodeURIComponent(sessionId)}`, {
    method: "PATCH",
    body: {
      title: patch.title,
      project_root: patch.projectRoot,
      model: patch.model,
      num_ctx: patch.numCtx,
      pinned: patch.pinned,
      turns: patch.turns,
      context_state: patch.contextState,
      task_ledger: patch.taskLedger,
    },
  });
  return res.session;
}

export async function deleteCodeSession(sessionId: string): Promise<boolean> {
  try {
    const res = await request<{ ok: boolean; removed: boolean }>(`/api/code-agent/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
    return !!res.removed;
  } catch {
    return false;
  }
}

// ── Rough token estimator ────────────────────────────────────────────────

// ── RAG: project indexing + manual recall ───────────────────────────────

export type IndexProjectArgs = {
  projectRoot: string;
  patterns?: string[];
  replace?: boolean;
};

export type IndexProjectResult = {
  ok: boolean;
  files_processed?: number;
  chunks_indexed?: number;
  failed_chunks?: number;
  patterns?: string[];
  errors?: string[];
  error?: string;
};

export async function indexProject({
  projectRoot,
  patterns,
  replace = true,
}: IndexProjectArgs): Promise<IndexProjectResult> {
  return request<IndexProjectResult>("/api/code-agent/index-project", {
    method: "POST",
    body: { project_root: projectRoot, patterns, replace },
    timeoutMs: 600_000, // indexing a large repo can take minutes — don't abort early
  });
}

export type RecallItem = {
  id: number;
  text: string;
  category: string;
  importance?: number;
  score?: number;
};

export type RecallResult = {
  ok: boolean;
  items: RecallItem[];
  count?: number;
  error?: string;
};

export async function recallFromRag(
  query: string,
  topK: number = 10,
  minScore: number = 0.3,
  projectRoot?: string,
): Promise<RecallResult> {
  return request<RecallResult>("/api/code-agent/recall", {
    method: "POST",
    body: { query, top_k: topK, min_score: minScore, project_root: projectRoot },
  });
}

// ── RAG store admin (list / delete / stats) ──────────────────────────────

export type RagStats = {
  ok: boolean;
  total: number;
  with_embeddings: number;
  model?: string;
  by_category?: Record<string, number>;
  error?: string;
};

export type RagListItem = {
  id: number;
  text: string;
  category: string;
  importance: number;
  access_count?: number;
  created_at?: string;
};

export type RagListResult = {
  ok: boolean;
  items: RagListItem[];
  count?: number;
};

export async function getRagStats(): Promise<RagStats> {
  return request<RagStats>("/api/advanced/rag/stats");
}

export async function listRagItems(limit: number = 200): Promise<RagListResult> {
  const qs = new URLSearchParams({ limit: String(limit) }).toString();
  return request<RagListResult>(`/api/advanced/rag/list?${qs}`);
}

export async function deleteRagItem(itemId: number): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/advanced/rag/${itemId}`, { method: "DELETE" });
}

export async function clearRagCategory(category?: string): Promise<{ ok: boolean; deleted: number; category: string | null }> {
  const qs = category ? `?${new URLSearchParams({ category }).toString()}` : "";
  return request<{ ok: boolean; deleted: number; category: string | null }>(
    `/api/advanced/rag/clear${qs}`,
    { method: "DELETE" },
  );
}

export async function addRagItem(text: string, category: string = "fact", importance: number = 5): Promise<{ ok: boolean; id?: number; has_embedding?: boolean; error?: string }> {
  return request("/api/advanced/rag/add", {
    method: "POST",
    body: { text, category, importance },
  });
}

// ── File watcher (realtime auto-reindex of RAG on source edits) ───────

export type WatcherStatus = {
  watching: boolean;
  project_root: string;
  started_at?: number;
  events_seen?: number;
  reindex_runs?: number;
  last_error?: string | null;
};

export async function startProjectWatcher(
  projectRoot: string,
): Promise<{ ok: boolean; already_watching?: boolean; project_root: string }> {
  return request("/api/code-agent/watcher/start", {
    method: "POST",
    body: { project_root: projectRoot },
  });
}

export async function stopProjectWatcher(
  projectRoot: string,
): Promise<{ ok: boolean; was_watching?: boolean; project_root: string }> {
  return request("/api/code-agent/watcher/stop", {
    method: "POST",
    body: { project_root: projectRoot },
  });
}

export async function getProjectWatcherStatus(projectRoot: string): Promise<WatcherStatus> {
  const qs = new URLSearchParams({ project_root: projectRoot }).toString();
  return request<WatcherStatus>(`/api/code-agent/watcher/status?${qs}`);
}

// ── SSH allowlist (security gate for the SshToolProvider) ─────────────

export type SshConfig = {
  enabled: boolean;
  allowed_hosts: string[];
};

export async function getSshConfig(): Promise<SshConfig> {
  return request<SshConfig>("/api/code-agent/ssh/config");
}

export async function setSshConfig(allowedHosts: string[]): Promise<SshConfig> {
  return request<SshConfig>("/api/code-agent/ssh/config", {
    method: "POST",
    body: { allowed_hosts: allowedHosts },
  });
}

// ── MCP servers ─────────────────────────────────────────────────────

export type McpServerSpec = {
  id: string;
  command: string;
  args: string[];
  env: Record<string, string>;
  enabled: boolean;
  status?: "stopped" | "running" | "crashed" | "error";
  last_error?: string | null;
};

export type McpServersResponse = {
  servers: McpServerSpec[];
};

export type McpActionResponse = {
  ok: boolean;
  already_running?: boolean;
  was_running?: boolean;
  server_id?: string;
  server_info?: Record<string, unknown>;
  error?: string;
};

export async function listMcpServers(): Promise<McpServersResponse> {
  return request<McpServersResponse>("/api/code-agent/mcp/servers");
}

export async function saveMcpServers(
  servers: Omit<McpServerSpec, "status" | "last_error">[],
): Promise<{ ok: boolean; servers: McpServerSpec[] }> {
  return request("/api/code-agent/mcp/servers", {
    method: "POST",
    body: { servers },
  });
}

export async function startMcpServer(serverId: string): Promise<McpActionResponse> {
  return request<McpActionResponse>("/api/code-agent/mcp/start", {
    method: "POST",
    body: { server_id: serverId },
  });
}

export async function stopMcpServer(serverId: string): Promise<McpActionResponse> {
  return request<McpActionResponse>("/api/code-agent/mcp/stop", {
    method: "POST",
    body: { server_id: serverId },
  });
}

export async function restartMcpServer(serverId: string): Promise<McpActionResponse> {
  return request<McpActionResponse>("/api/code-agent/mcp/restart", {
    method: "POST",
    body: { server_id: serverId },
  });
}

export async function getMcpServerTools(serverId: string): Promise<{ server_id: string; tools: Array<{ name: string; description?: string }> }> {
  const qs = new URLSearchParams({ server_id: serverId }).toString();
  return request(`/api/code-agent/mcp/tools?${qs}`);
}

/** Empty-history context usage seeded with the live ctx_size — lets the
 *  composer show the window meter (0%) before the first model turn. */
export async function fetchContextProfile(
  model?: string,
  numCtx?: number,
): Promise<ContextUsage | null> {
  const params = new URLSearchParams();
  if (model) params.set("model", model);
  if (numCtx && numCtx > 0) params.set("num_ctx", String(numCtx));
  const qs = params.toString();
  try {
    const res = await request<{ ok: boolean; context?: ContextUsage }>(
      `/api/code-agent/context-profile${qs ? `?${qs}` : ""}`,
    );
    return res.context ?? null;
  } catch {
    return null;
  }
}

/** Coarse token estimate. Russian/Cyrillic is ~3 chars/token; ASCII/code
 *  is closer to 4. We compute per-character class to be reasonable. */
export function estimateTokens(text: string): number {
  if (!text) return 0;
  let cyr = 0;
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i);
    if (code >= 0x0400 && code <= 0x04ff) cyr++;
  }
  const ascii = text.length - cyr;
  return Math.ceil(cyr / 2.8 + ascii / 4);
}
