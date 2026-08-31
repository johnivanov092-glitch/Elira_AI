import { API_BASE, ApiError, buildApiUrl, request, withAuth } from "./client";
import { toWireResource, type ResourceRef } from "./resources";
import type { PermissionMode } from "./workflows";

export type { PermissionMode } from "./workflows";

export const DEFAULT_CODE_AGENT_MODEL = "auto";

/** Proxied favicon URL for a source origin. The endpoint literal lives here in
 *  the api layer (not in components) per the smoke-contract guard. */
export function codeAgentFaviconUrl(url: string): string {
  return buildApiUrl(`/api/code-agent/favicon?url=${encodeURIComponent(url)}`);
}

/** Authenticated Runtime-owned proxy for a search-supplied answer image. */
export function fetchCodeAgentImage(url: string, signal?: AbortSignal): Promise<Blob> {
  return request<Blob>(`/api/code-agent/image?url=${encodeURIComponent(url)}`, {
    responseType: "blob",
    timeoutMs: 15_000,
    ...(signal ? { signal } : {}),
  });
}

export type AnswerMediaItem = {
  type: "image";
  url: string;
  source_url: string;
  title: string;
  source: string;
};

export type DocumentQa = {
  status: "passed" | "failed" | "unverified";
  sha256?: string;
  format?: "docx" | "pdf";
  renderer?: string;
  target?: string;
  attempt?: number;
  page_count?: number | null;
  expected_page_count?: number | null;
  vision_status?: "passed" | "failed" | "unverified" | "not_run";
  issues?: { code: string; message: string }[];
};

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
  /** file_gen / resource_publish: server-served download URL + filename for the
   *  produced artifact. Set by the runtime ONLY after the file is verified to exist
   *  on disk, so the UI renders a deterministic download — it never depends on the
   *  model echoing a URL. */
  download_url?: string;
  download_name?: string;
  /** resource_publish: safe project-relative source metadata. */
  project_path?: string;
  size?: number;
  sha256?: string;
  /** PDF/DOCX: server-owned render/page-count/vision verdict for these exact bytes. */
  document_qa?: DocumentQa;
  /** run_server: server-owned address of the process that actually survived
   *  startup. Used for live Preview; never parsed from model prose. */
  actual_url?: string;
  local_url?: string;
  actual_port?: number;
  port?: number;
  pid?: number;
  server_started?: boolean;
  /** run_server(kind="job"): durable lifecycle state and log location. */
  action?: "start" | "list" | "logs" | "stop" | "stop_all";
  job_id?: string;
  kind?: "server" | "job";
  status?: "running" | "completed" | "failed" | "cancelled";
  log_path?: string;
  recovered?: boolean;
  /** The RUNTIME made this call itself (auto-verifier closure pass), not the model. */
  auto_verifier?: boolean;
};

export type CodeAgentResponse = {
  ok: boolean;
  response: string;
  media?: AnswerMediaItem[];
  steps: number;
  tool_calls: CodeAgentToolCall[];
  stop_reason: "answer" | "context_limit" | "error" | "cancelled";
  error: string | null;
  partial: boolean;
};

export type ConversationMessage = {
  role: "user" | "assistant";
  content: string;
};

export type CodeAgentMode = "code" | "search";
export type ReasoningEffort = "none" | "low" | "medium" | "xhigh";

/** Approval policy for a run, picked in the composer's permission selector:
 *  - "ask"          — pause for the user on every gated tool (default);
 *  - "accept_edits" — auto-approve low-risk runtime-reversible work;
 *  - "bypass"       — the local Workflow UI choice authorizes registered tool calls. */
export type CodeAgentRunArgs = {
  message: string;
  projectRoot: string;
  model?: string;
  numCtx?: number;
  mode?: CodeAgentMode;
  autoRemember?: boolean;
  conversationHistory?: ConversationMessage[];
  /** Durable resources (files) attached to this run, by ResourceRef. Only the
   *  resource_id crosses the wire — the raw File, bytes, extracted text, and any
   *  path stay client-/server-side. The model reads them via `resource_process`,
   *  never as auto-injected text. */
  resources?: ResourceRef[];
  /** Session id retained for wire compatibility; durable resources are not
   *  authorized or scoped by a transient chat/run binding. */
  sessionId?: string;
  /** Compatibility field. The UI always sends "Авто"; backend domain and
   *  evidence routers select internal policies per request. */
  profileName?: string;
  /** Approval policy for this run (composer permission selector). Omitted → the
   *  backend default "ask". See {@link PermissionMode}. */
  permissionMode?: PermissionMode;
  /** Model-neutral reasoning depth for this run. Qwen can disable reasoning;
   *  Muse maps none to its lowest native level. Omitted → none. */
  reasoningEffort?: ReasoningEffort;
};

// ── Streaming protocol ───────────────────────────────────────────────────

export type PlanArtifact = {
  goal: string;
  current_state: string;
  ordered_steps: string[];
  acceptance_checks: string[];
  risks: string[];
  current_step: number;
};

export type CodeAgentStreamEvent =
  | { type: "run_started"; run_id: string }
  | { type: "run_resumed"; run_id: string; from_step: number }
  | { type: "step_started"; step: number }
  | { type: "heartbeat"; step?: number; phase?: "planning" }
  | { type: "planning_started"; run_id: string }
  | { type: "plan_ready"; run_id: string; plan: PlanArtifact }
  | { type: "planning_fallback"; run_id: string; reason: string }
  | {
      type: "phase_changed";
      run_id: string;
      phase: "execution" | "verification";
      applied_thinking_mode: string;
    }
  | { type: "delta"; step: number; text: string }
  | { type: "reasoning_delta"; step: number; text: string }
  | {
      type: "tool_started";
      step: number;
      tool: string;
      arguments: Record<string, unknown>;
    }
  | ({ type: "tool_call" } & CodeAgentToolCall)
  | { type: "context_prepared"; step: number; context: ContextUsage }
  | { type: "context_compacted"; step: number; context?: ContextUsage; rolling_summary?: string | null }
  | {
      type: "usage";
      step: number;
      prompt_tokens: number;
      cached_prompt_tokens?: number;
      cache_hit_ratio?: number;
      completion_tokens: number;
      total_tokens: number;
      prompt_tokens_per_second?: number;
      tokens_per_second: number;
      ttft_ms?: number;
      context?: ContextUsage;
      profile?: ContextProfile;
    }
  | {
      type: "final_response";
      step: number;
      text: string;
      answer_status?: AnswerStatus;
      established_facts?: string;
      recent_tool_output?: string;
      media?: AnswerMediaItem[];
    }
  | ({
      type: "context_resolved";
      step: number;
      run_id?: string;
    } & ContextResolution)
  | {
      // Delivery session: informational slice boundary — the run CONTINUES on the
      // same run_id after context-window rollover.
      // Never a terminal event; exactly one `done` still closes the stream.
      type: "delivery_continuing";
      run_id?: string;
      slice: number;
      next_slice: number;
      auto_continuation: number;
      stop_reason: string;
      progress?: {
        new_touched_paths?: number;
        criteria_confirmed_delta?: number;
        checklist_completed_delta?: number;
      };
    }
  | {
      type: "done";
      ok: boolean;
      steps: number;
      stop_reason: CodeAgentResponse["stop_reason"];
      answer_status?: AnswerStatus;
      error: string | null;
      partial?: boolean;
      resumable?: boolean;
      run_id?: string;
      established_facts?: string;
      recent_tool_output?: string;
      // Delivery session: server-derived "what comes next" (first open checklist
      // item / first unconfirmed criterion) on an honest-partial terminal.
      next_milestone?: string;
      // How many automatic continuations this run consumed (absent for 1-slice runs).
      auto_continuations?: number;
      // Task-completion axis — SEPARATE from runtime `ok`. "confirmed" means every
      // success criterion was proven by a verifier; consumers must gate "solved"
      // on this, not on `ok`.
      completion_status?: CompletionStatus;
      criteria?: CriterionState[];
    };

export type CompletionStatus = "confirmed" | "partial" | "unverified" | "failed" | "none";
export type AnswerStatus = "complete" | "degraded" | "needs_input";

export type CriterionState = {
  text: string;
  status: "confirmed" | "unconfirmed" | "failed" | "skipped";
  verifier?: string | null;
  evidence?: string | null;
  /** Closed by the runtime's own auto-verifier pass (not a model-made call). */
  auto_verified?: boolean;
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

export type ContextResolution = {
  requested_context_mode?: "server" | "offline";
  requested_context_cap?: number | null;
  server_context_window?: number;
  effective_context_window?: number;
  limiting_source?: string;
  context_profile_source?: string;
  reserved_output_tokens?: number;
  reserved_system_tokens?: number;
  safety_margin_tokens?: number;
  safe_input_budget?: number;
  compaction_thresholds?: Record<
    string,
    { percent?: number; tokens?: number }
  >;
  thinking?: boolean;
  reasoning_effort?: ReasoningEffort;
};

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
    mode = "code",
    autoRemember = true,
    conversationHistory,
    resources,
    sessionId,
    profileName,
    permissionMode,
    reasoningEffort,
    runId,
    signal,
    onEvent,
    onRunId,
    onError,
  } = args;

  // Only resource_id crosses the wire — never the raw File, bytes, extracted
  // text, or any filesystem path. The backend re-derives the rest from the store.
  const wireResources = (resources ?? []).map(toWireResource);

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
        mode,
        auto_remember: autoRemember,
        conversation_history: conversationHistory,
        run_id: runId,
        ...(profileName ? { profile_name: profileName } : {}),
        ...(permissionMode ? { permission_mode: permissionMode } : {}),
        ...(reasoningEffort ? { reasoning_effort: reasoningEffort } : {}),
        ...(sessionId ? { session_id: sessionId } : {}),
        ...(wireResources.length ? { resources: wireResources } : {}),
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
      const { value, done } = await reader.read();
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
    // Free the socket on a transport read error before surfacing it.
    try { await reader.cancel(); } catch { /* already closed */ }
    onError?.(err as Error);
  }
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
}: SummarizeHistoryArgs): Promise<SummarizeHistoryResult> {
  return request<SummarizeHistoryResult>("/api/code-agent/summarize-history", {
    method: "POST",
    body: { messages, model },
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
  // "final" is a SOLVED task (completion_status === "confirmed", or a no-criteria
  // answer). "partial" = ran ok but the task is unverified/partial (NOT solved).
  // "error" = runtime failure or a failed verifier. Consumers must never read
  // "final" as solved unless it truly is.
  type: "tool_call" | "final" | "partial" | "error" | "compression";
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
  files_scanned?: number;
  files_indexed?: number;
  files_unchanged?: number;
  files_failed?: number;
  chunks_indexed?: number;
  chunks_created?: number;
  chunks_reused?: number;
  failed_chunks?: number;
  deleted_chunks?: number;
  stale_files_removed?: number;
  repositories?: number;
  resume_required?: boolean;
  complete?: boolean;
  patterns?: string[];
  errors?: string[];
  error?: string;
};

export type ProjectCorpusStatus = {
  ok: boolean;
  project_root: string;
  project_scope: string;
  files: number;
  chunks: number;
  repositories: number;
  indexed_at?: string | null;
  by_status: Record<string, number>;
  by_language: Record<string, number>;
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
    timeoutMs: 0,
  });
}

export async function getProjectCorpusStatus(projectRoot: string): Promise<ProjectCorpusStatus> {
  const qs = new URLSearchParams({ project_root: projectRoot }).toString();
  return request<ProjectCorpusStatus>(`/api/code-agent/corpus/status?${qs}`);
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
  embedding_enabled?: boolean;
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

/** Empty-history context usage seeded with the live ctx_size — lets the
 *  composer show the window meter (0%) before the first model turn. */
export async function fetchContextProfile(
  model?: string,
): Promise<ContextUsage | null> {
  const params = new URLSearchParams();
  if (model) params.set("model", model);
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
