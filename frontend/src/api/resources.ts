import { API_BASE, withAuth } from "./client";

// Durable media resources (R1). Uploading a file only REGISTERS the original and
// returns a ResourceRef; nothing is extracted/transcribed/OCR'd at upload time.
// The file's content is read later, on demand, by the agent's `resource_process`
// tool — never eagerly, and never by putting bytes/text into the request here.

export type ResourceKind = "audio" | "video" | "image" | "document" | "archive" | "other";

const RESOURCE_KINDS: ResourceKind[] = [
  "audio", "video", "image", "document", "archive", "other",
];

/** The model-safe reference the backend returns and the stream re-sends. Carries
 *  metadata only — no bytes, no absolute/storage path. */
export type ResourceRef = {
  resource_id: string;
  name: string;
  kind: ResourceKind;
  content_type: string;
  size: number;
};

/** Composer chip state: a ResourceRef plus its upload lifecycle. The raw File is
 *  intentionally NOT kept here — it is used only while the upload is in flight. */
export type ResourceAttachment = ResourceRef & {
  status: "uploading" | "ready" | "error";
  error?: string;
  libraryStatus?: "saving" | "saved" | "error";
  libraryError?: string;
};

export function normalizeKind(value: unknown): ResourceKind {
  const kind = String(value ?? "");
  return (RESOURCE_KINDS as string[]).includes(kind) ? (kind as ResourceKind) : "other";
}

/**
 * Register an uploaded file as a durable resource owned by `sessionId`. Returns
 * ONLY the ResourceRef — the backend performs no processing and puts nothing
 * about the file's content into any response. Throws on a non-2xx upload.
 */
export async function uploadResource(file: File, sessionId: string): Promise<ResourceRef> {
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("session_id", sessionId);
  // No explicit Content-Type: the browser sets the multipart boundary itself.
  const res = await fetch(`${API_BASE}/api/media/resources`, {
    method: "POST",
    headers: withAuth({}),
    body: form,
  });
  if (!res.ok) {
    let detail = `resource upload failed: ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string" && body.detail.trim()) detail = body.detail.trim();
    } catch {
      // Keep the stable status fallback when the response is not JSON.
    }
    throw new Error(detail);
  }
  const rec = (await res.json()) as Record<string, unknown>;
  const resourceId = String(rec.resource_id ?? "");
  const size = typeof rec.size === "number" ? rec.size : NaN;
  if (!/^[0-9a-f]{32}$/.test(resourceId) || !Number.isFinite(size) || size < 0) {
    throw new Error("invalid resource response");
  }
  return {
    resource_id: resourceId,
    name: String(rec.name ?? file.name),
    kind: normalizeKind(rec.kind),
    content_type: String(rec.content_type ?? file.type ?? "application/octet-stream"),
    size,
  };
}

/** The wire shape sent back to /api/code-agent/stream — resource_id is all the
 *  backend trusts (it re-derives everything else from the store). */
export function toWireResource(ref: ResourceRef): { resource_id: string } {
  return { resource_id: ref.resource_id };
}
