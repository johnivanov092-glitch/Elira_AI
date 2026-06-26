import { buildApiUrl, request, safeRequest, withAuth } from "./client";
import { consumeCodeAgentStream } from "./codeAgent";
import type { ConversationMessage, StreamHandlers } from "./codeAgent";

type UnknownRecord = Record<string, unknown>;

type ChatMutationArg = UnknownRecord | string | number | null | undefined;

type StreamEvent = UnknownRecord & {
  done?: boolean;
  error?: unknown;
  full_text?: unknown;
  meta?: unknown;
  phase?: string;
  timeline?: unknown[];
  token?: unknown;
};

type StreamCallbacks = {
  onDone?: (event: { full_text: string; meta: unknown; timeline: unknown[] }) => void;
  onError?: (error: string) => void;
  onPhase?: (event: StreamEvent) => void;
  onToken?: (token: string) => void;
};

export type Chat = UnknownRecord & {
  id: unknown;
  memory_saved: boolean;
  pinned: boolean;
  title: unknown;
};

export type ChatMessage = UnknownRecord & {
  content: string;
  id: unknown;
  role: unknown;
};

function isRecord(value: unknown): value is UnknownRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizeArray(payload: unknown): unknown[] {
  if (Array.isArray(payload)) return payload;
  if (isRecord(payload) && Array.isArray(payload.items)) return payload.items;
  if (isRecord(payload) && Array.isArray(payload.messages)) return payload.messages;
  if (isRecord(payload) && Array.isArray(payload.files)) return payload.files;
  return [];
}

function unwrapItem(payload: unknown): unknown {
  if (!isRecord(payload)) return payload;
  return payload.item || payload.chat || payload.message || payload.data || payload;
}

function normalizeSessionId(value: unknown): string | null {
  if (value === undefined || value === null || value === "") return null;
  return typeof value === "string" ? value : String(value);
}

function normalizeChat(item: unknown = {}): Chat {
  const source = isRecord(item) ? item : {};
  return {
    ...source,
    // Backend chat ids are integers; the whole frontend treats chat ids as
    // strings (state, refs, stream-registry keys). Stringify here at the API
    // boundary so comparisons like activeChatIdRef === targetChatId don't fail
    // with `42 !== "42"` — which left streamed answers stuck on "Думаю…".
    id: source.id != null && source.id !== "" ? String(source.id) : "",
    title: source.title ?? "New chat",
    pinned: Boolean(source.pinned),
    memory_saved: Boolean(source.memory_saved),
  };
}

function normalizeMessage(item: unknown = {}): ChatMessage {
  const source = isRecord(item) ? item : {};
  const content = source.content ?? source.answer ?? source.response ?? source.message ?? "";
  return {
    ...source,
    id: source.id ?? `${String(source.role || "msg")}-${Date.now()}`,
    role: source.role ?? "assistant",
    content: typeof content === "string" ? content : String(content ?? ""),
  };
}

function extractAgentError(payload: unknown): string {
  if (!isRecord(payload)) return "";
  if (payload.ok === false) {
    const meta = isRecord(payload.meta) ? payload.meta : {};
    if (typeof meta.error === "string" && meta.error.trim()) return meta.error;
    return "run_agent returned an error";
  }
  return "";
}

function argPayload(arg1: ChatMutationArg, arg2?: unknown, key = "value"): UnknownRecord {
  return isRecord(arg1) ? arg1 : { id: arg1, [key]: arg2 };
}

export function isLocalApiAssetUrl(url = ""): boolean {
  return typeof url === "string" && (
    url.includes("/api/skills/download/") ||
    url.includes("/api/skills/view/") ||
    url.includes("/api/extra/")
  );
}

export async function listChats(): Promise<Chat[]> {
  const payload = await safeRequest<unknown>("/api/chat-agent/chats", {}, []);
  return normalizeArray(payload).map(normalizeChat);
}

export async function createChat(body: UnknownRecord = {}): Promise<Chat> {
  return normalizeChat(unwrapItem(await request("/api/chat-agent/chats", { method: "POST", body })));
}

export async function renameChat(arg1: ChatMutationArg, arg2?: unknown): Promise<Chat> {
  const payload = argPayload(arg1, arg2, "title");
  return normalizeChat(unwrapItem(await request(`/api/chat-agent/chats/${encodeURIComponent(String(payload.id))}`, {
    method: "PATCH",
    body: { title: payload.title },
  })));
}

export async function pinChat(arg1: ChatMutationArg, arg2?: unknown): Promise<Chat> {
  const payload = argPayload(arg1, arg2, "pinned");
  return normalizeChat(unwrapItem(await request(`/api/chat-agent/chats/${encodeURIComponent(String(payload.id))}/pin`, {
    method: "PATCH",
    body: { pinned: Boolean(payload.pinned) },
  })));
}

export async function saveChatToMemory(arg1: ChatMutationArg, arg2?: unknown): Promise<Chat> {
  const payload = argPayload(arg1, arg2, "saved");
  return normalizeChat(unwrapItem(await request(`/api/chat-agent/chats/${encodeURIComponent(String(payload.id))}/memory`, {
    method: "PATCH",
    body: { memory_saved: Boolean(payload.saved) },
  })));
}

export async function deleteChat(arg: ChatMutationArg): Promise<unknown> {
  const id = isRecord(arg) ? arg.id : arg;
  return request(`/api/chat-agent/chats/${encodeURIComponent(String(id))}`, { method: "DELETE" });
}

export async function getMessages(arg: ChatMutationArg): Promise<ChatMessage[]> {
  const chatId = isRecord(arg) ? arg.chatId : arg;
  const payload = await safeRequest<unknown>(`/api/chat-agent/chats/${encodeURIComponent(String(chatId))}/messages`, {}, []);
  return normalizeArray(payload).map(normalizeMessage);
}

export async function addMessage(body: UnknownRecord = {}): Promise<UnknownRecord & { chat_id: unknown; message: ChatMessage }> {
  const payload = await request<unknown>("/api/chat-agent/messages", {
    method: "POST",
    body: {
      chat_id: body.chatId ?? body.chat_id ?? null,
      role: body.role ?? "user",
      content: typeof body.content === "string" ? body.content : String(body.content ?? ""),
    },
  });
  const payloadRecord = isRecord(payload) ? payload : {};
  const message = normalizeMessage(unwrapItem(payloadRecord.message ?? payload));
  return {
    ...message,
    ...payloadRecord,
    chat_id: payloadRecord.chat_id ?? body.chatId ?? body.chat_id ?? null,
    message,
  };
}

export async function sendMessage(body: UnknownRecord = {}): Promise<ChatMessage> {
  const payload = await addMessage(body);
  return payload.message;
}

export async function execute(body: UnknownRecord = {}): Promise<UnknownRecord & { content: string }> {
  const payload: UnknownRecord = {
    model_name: body.model_name ?? body.model ?? "local-model",
    profile_name: body.profile_name ?? body.profile ?? "default",
    user_input: String(body.user_input ?? body.message ?? body.prompt ?? body.text ?? body.query ?? "").trim(),
    session_id: normalizeSessionId(body.session_id ?? body.chat_id ?? body.chatId ?? null),
    history: Array.isArray(body.history) ? body.history : [],
    num_ctx: body.num_ctx ?? 131072,
  };
  const response = await request<unknown>("/api/chat-agent/send", {
    method: "POST",
    body: payload,
  });
  const routeError = extractAgentError(response);
  if (routeError) throw new Error(routeError);

  const responseRecord = isRecord(response) ? response : {};
  const content = responseRecord.content ?? responseRecord.answer ?? responseRecord.response ?? responseRecord.message ?? "";
  if (!String(content).trim()) throw new Error("Empty response from /api/chat-agent/send");
  return { ...responseRecord, content: String(content) };
}

export function executeStream(
  body: UnknownRecord = {},
  { onToken, onDone, onError, onPhase }: StreamCallbacks = {},
): AbortController {
  const controller = new AbortController();

  const payload: UnknownRecord = {
    model_name: body.model_name ?? body.model ?? "local-model",
    profile_name: body.profile_name ?? body.profile ?? "default",
    user_input: String(body.user_input ?? body.message ?? "").trim(),
    session_id: normalizeSessionId(body.session_id ?? body.chat_id ?? body.chatId ?? null),
    history: Array.isArray(body.history) ? body.history : [],
    num_ctx: body.num_ctx ?? 131072,
  };

  fetch(buildApiUrl("/api/chat-agent/stream"), {
    method: "POST",
    headers: withAuth({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `HTTP ${response.status}`);
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error("Streaming response body is not available");

      // Aborting the fetch signal does NOT reliably reject an in-flight
      // reader.read() in WebView2/Chromium, so the loop would keep draining
      // tokens after Stop. Explicitly cancel the reader when the signal fires.
      const onAbort = () => { reader.cancel().catch(() => {}); };
      if (controller.signal.aborted) onAbort();
      else controller.signal.addEventListener("abort", onAbort);

      const decoder = new TextDecoder();
      let buffer = "";

      try {
        while (true) {
          if (controller.signal.aborted) break;
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed || !trimmed.startsWith("data: ")) continue;

            try {
              const event = JSON.parse(trimmed.slice(6)) as StreamEvent;

              if (event.error) {
                onError?.(String(event.error));
                return;
              }

              if (event.phase && onPhase) onPhase(event);
              if (event.phase === "reflection_replace" && event.full_text) continue;
              if (event.token) onToken?.(event.token as string);

              if (event.done) {
                onDone?.({
                  full_text: (event.full_text as string) || "",
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

        if (!controller.signal.aborted) onDone?.({ full_text: "", meta: {}, timeline: [] });
      } finally {
        controller.signal.removeEventListener("abort", onAbort);
        reader.cancel().catch(() => {});
      }
    })
    .catch((error: unknown) => {
      if (error instanceof Error && error.name === "AbortError") return;
      onError?.(error instanceof Error ? error.message || "Stream error" : "Stream error");
    });

  return controller;
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

export async function getSettings(): Promise<UnknownRecord> {
  return safeRequest<UnknownRecord>("/api/chat-agent/settings", {}, {});
}

export async function updateSettings(body: UnknownRecord = {}): Promise<unknown> {
  return request("/api/chat-agent/settings", { method: "PUT", body });
}
