import { request, safeRequest } from "./client";

type UnknownRecord = Record<string, unknown>;

function isRecord(value: unknown): value is UnknownRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

// ── Chat attachments → code-agent stream ────────────────────────────────────
//
// The composer's file picker uploads via `attachToChat`; the parsed metadata
// rides along with the project on the unified /api/code-agent/stream call
// (see `streamCodeAgent`). No separate chat stream invoker is needed.

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
