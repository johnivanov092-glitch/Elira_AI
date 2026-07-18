import { request, safeRequest } from "./client";

type UnknownRecord = Record<string, unknown>;

function isRecord(value: unknown): value is UnknownRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

// ── Chat attachments (LEGACY) ───────────────────────────────────────────────
//
// LEGACY eager-attach helper. The composer now uploads durable resources via
// `uploadResource` (see api/resources.ts) and reads them on demand through the
// `resource_process` tool — it no longer calls `attachToChat`. This function and
// the `/api/chat/attach` route are kept only for back-compat until nothing else
// depends on them; new code should use the resource pipeline.

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

// Audio containers are transcribed by STT (server budget 3600s), so their upload
// needs a matching BOUNDED client timeout — a little above the backend STT budget,
// never infinite. Everything else keeps the standard 120s. Mirrors the backend
// _AUDIO_EXTS allowlist (different language, kept in sync deliberately).
const AUDIO_EXTS = new Set([
  ".ogg", ".oga", ".opus", ".wav", ".mp3", ".m4a", ".mp4", ".flac", ".webm", ".aac",
]);
const DEFAULT_ATTACH_TIMEOUT_MS = 120_000;
const AUDIO_ATTACH_TIMEOUT_MS = 3_630_000; // backend STT (3600s) + margin; NOT infinite

function isAudioFile(name: string): boolean {
  const dot = name.lastIndexOf(".");
  return dot >= 0 && AUDIO_EXTS.has(name.slice(dot).toLowerCase());
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
    timeoutMs: isAudioFile(file.name) ? AUDIO_ATTACH_TIMEOUT_MS : DEFAULT_ATTACH_TIMEOUT_MS,
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
