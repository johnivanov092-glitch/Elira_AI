import { API_BASE, request } from "./client";

export const DEFAULT_CODE_AGENT_MODEL = "qwen2.5-coder:7b";
export const DEFAULT_CODE_AGENT_MAX_STEPS = 20;
export const DEFAULT_CODE_AGENT_NUM_CTX = 16384;

export type CodeAgentToolCall = {
  step: number;
  tool: string;
  arguments: Record<string, unknown>;
  result: string;
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
  stop_reason: "answer" | "max_steps" | "error" | "cancelled";
  error: string | null;
};

export type ConversationMessage = {
  role: "user" | "assistant";
  content: string;
};

export type CodeAgentRunArgs = {
  message: string;
  projectRoot: string;
  model?: string;
  maxSteps?: number;
  numCtx?: number;
  autoRemember?: boolean;
  conversationHistory?: ConversationMessage[];
};

/** Single-shot (legacy). Resolves with the aggregated final dict. */
export async function runCodeAgent({
  message,
  projectRoot,
  model = DEFAULT_CODE_AGENT_MODEL,
  maxSteps = DEFAULT_CODE_AGENT_MAX_STEPS,
  numCtx = DEFAULT_CODE_AGENT_NUM_CTX,
  autoRemember = true,
  conversationHistory,
}: CodeAgentRunArgs): Promise<CodeAgentResponse> {
  return request<CodeAgentResponse>("/api/code-agent/run", {
    method: "POST",
    body: {
      message,
      project_root: projectRoot,
      model,
      max_steps: maxSteps,
      num_ctx: numCtx,
      auto_remember: autoRemember,
      conversation_history: conversationHistory,
    },
  });
}

// ── Streaming protocol ───────────────────────────────────────────────────

export type CodeAgentStreamEvent =
  | { type: "run_started"; run_id: string }
  | { type: "step_started"; step: number }
  | ({ type: "tool_call" } & CodeAgentToolCall)
  | { type: "final_response"; step: number; text: string }
  | {
      type: "done";
      ok: boolean;
      steps: number;
      stop_reason: CodeAgentResponse["stop_reason"];
      error: string | null;
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
    maxSteps = DEFAULT_CODE_AGENT_MAX_STEPS,
    numCtx = DEFAULT_CODE_AGENT_NUM_CTX,
    autoRemember = true,
    conversationHistory,
    runId,
    signal,
    onEvent,
    onRunId,
    onError,
  } = args;

  const url = `${API_BASE}/api/code-agent/stream`;
  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({
        message,
        project_root: projectRoot,
        model,
        max_steps: maxSteps,
        num_ctx: numCtx,
        auto_remember: autoRemember,
        conversation_history: conversationHistory,
        run_id: runId,
      }),
      signal,
    });
  } catch (err) {
    if ((err as DOMException)?.name === "AbortError") return;
    onError?.(err as Error);
    return;
  }

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
  numCtx = DEFAULT_CODE_AGENT_NUM_CTX,
}: SummarizeHistoryArgs): Promise<SummarizeHistoryResult> {
  return request<SummarizeHistoryResult>("/api/code-agent/summarize-history", {
    method: "POST",
    body: { messages, model, num_ctx: numCtx },
  });
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
): Promise<RecallResult> {
  return request<RecallResult>("/api/code-agent/recall", {
    method: "POST",
    body: { query, top_k: topK, min_score: minScore },
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

