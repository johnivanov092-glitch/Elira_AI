import { buildApiUrl, request, safeRequest, withAuth } from "./client";
import { consumeCodeAgentStream } from "./codeAgent";
import type { ConversationMessage, StreamHandlers } from "./codeAgent";

type UnknownRecord = Record<string, unknown>;

function isRecord(value: unknown): value is UnknownRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizeSessionId(value: unknown): string | null {
  if (value === undefined || value === null || value === "") return null;
  return typeof value === "string" ? value : String(value);
}

// ── "Чат" mode → code-agent stream ──────────────────────────────────────────
//
// The "Чат" composer chip uses the same /api/code-agent/stream core as code
// mode, but keeps the chat UI entrypoint and attachment upload flow.

export type ChatAttachment = {
  ok: boolean;
  filename: string;
  kind: "image" | "document";
  text: string;
  chars: number;
  note?: string;
  // Frontend-only fields, never serialized to the backend:
  //  - `file` keeps the original File so the chip can optionally be saved to the
  //    Library (/api/lib/add) on send, reusing the existing upload channel.
  //  - `toLibrary` is the per-chip "save to Library" toggle (off by default).
  file?: File;
  toLibrary?: boolean;
};

export type StreamChatPlannerArgs = StreamHandlers & {
  message: string;
  sessionId?: string | null;
  projectRoot?: string;
  model?: string;
  conversationHistory?: ConversationMessage[];
  attachments?: ChatAttachment[];
  numCtx?: number;
  signal?: AbortSignal;
};

/**
 * Stream the "Чат" mode over SSE, emitting CodeAgentStreamEvents so the
 * existing background-run wiring can consume it untouched. Matches the
 * `(handlers: StreamHandlers & { signal }) => Promise<void>` invoker contract.
 */
export async function streamChatPlanner(args: StreamChatPlannerArgs): Promise<void> {
  const {
    message,
    sessionId = null,
    projectRoot = "",
    model = "local-model",
    conversationHistory = [],
    attachments = [],
    numCtx = 131072,
    signal,
    onEvent,
    onRunId,
    onError,
  } = args;

  // Strip frontend-only fields (the raw File, the toLibrary toggle) before the
  // attachments cross the wire — the backend only consumes the parsed metadata.
  const wireAttachments = attachments.map(({ file: _file, toLibrary: _toLibrary, ...rest }) => rest);

  const payload: UnknownRecord = {
    message: message.trim(),
    project_root: projectRoot,
    model,
    num_ctx: numCtx,
    mode: "code",
    auto_remember: true,
    conversation_history: conversationHistory,
    session_id: normalizeSessionId(sessionId),
    attachments: wireAttachments,
  };

  let response: Response;
  try {
    response = await fetch(buildApiUrl("/api/code-agent/stream"), {
      method: "POST",
      headers: withAuth({ "Content-Type": "application/json", Accept: "text/event-stream" }),
      body: JSON.stringify(payload),
      signal,
    });
  } catch (err) {
    if ((err as DOMException)?.name === "AbortError") return;
    onError?.(err as Error);
    return;
  }

  if (!response.ok) {
    const text = await response.text().catch(() => "");
    onError?.(new Error(text || `HTTP ${response.status}`));
    return;
  }

  await consumeCodeAgentStream(response, { onEvent, onRunId, onError });
}

/**
 * Upload a single file to the "Чат" attach endpoint. The backend parses it
 * locally (vision for images, extract/OCR for documents) and returns the
 * extracted text. Multipart FormData is passed through `request` untouched.
 */
export async function attachToChat(file: File): Promise<ChatAttachment> {
  const form = new FormData();
  form.append("file", file, file.name);
  const result = await request<UnknownRecord>("/api/chat/attach", {
    method: "POST",
    body: form,
  });
  const rec = isRecord(result) ? result : {};
  const kind = rec.kind === "image" ? "image" : "document";
  return {
    ok: rec.ok !== false,
    filename: String(rec.filename ?? file.name),
    kind,
    text: String(rec.text ?? ""),
    chars: typeof rec.chars === "number" ? rec.chars : 0,
    note: rec.note ? String(rec.note) : undefined,
    // Keep the source File so the chip can optionally be saved to the Library.
    file,
    toLibrary: false,
  };
}

export async function listLocalModels(): Promise<{ models: unknown[] }> {
  const payload = await safeRequest<unknown>("/api/chat-agent/models", {}, []);
  if (isRecord(payload) && Array.isArray(payload.models)) return { models: payload.models };
  if (isRecord(payload) && Array.isArray(payload.items)) return { models: payload.items };
  if (Array.isArray(payload)) return { models: payload };
  return { models: [] };
}
