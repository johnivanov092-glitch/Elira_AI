import { request, safeRequest } from "./client";

export type LibraryFileId = string | number;

export type LibraryListResponse = Record<string, unknown> | null;

export type LibraryUploadOptions = {
  useInContext?: boolean;
};

export type LibraryResponse = {
  ok?: boolean;
  error?: unknown;
  [key: string]: unknown;
};

function requireLibrarySuccess(response: LibraryResponse): LibraryResponse {
  if (response?.ok === true) return response;
  const error = typeof response?.error === "string" && response.error.trim()
    ? response.error.trim()
    : "Library operation failed";
  throw new Error(error);
}

/** One library file as returned by /api/lib/list (raw `files` columns). */
export type LibraryFile = {
  id: number;
  name: string;
  size: number;
  type: string;
  source: string;
  /** 0|1 on the wire; normalized to a boolean by `listLibraryFiles`. */
  active: boolean;
  status: "ready" | "capped" | "preview_only" | "failed" | "legacy_preview" | string;
  contentChars: number;
  previewChars: number;
  lastUsedAt: string;
  created_at: string;
};

type RawLibraryRow = {
  id?: number;
  name?: string;
  size?: number;
  type?: string;
  source?: string;
  use_in_context?: number | boolean;
  status?: string;
  content_chars?: number;
  preview_chars?: number;
  last_used_at?: string | null;
  created_at?: string;
};

export async function listLibraryFiles(): Promise<LibraryListResponse> {
  return safeRequest<LibraryListResponse>("/api/lib/list", {}, null);
}

/** Typed view over /api/lib/list: items normalized to `LibraryFile`, in the
 *  backend's freshest-first order (so the first active items are the ones
 *  `build_library_context` actually injects). Returns [] when offline. */
export async function listLibraryFilesTyped(): Promise<LibraryFile[]> {
  const r = await safeRequest<{ items?: RawLibraryRow[] } | null>("/api/lib/list", {}, null);
  const rows = Array.isArray(r?.items) ? r!.items : [];
  return rows.map((row) => ({
    id: Number(row.id ?? 0),
    name: String(row.name ?? ""),
    size: Number(row.size ?? 0),
    type: String(row.type ?? ""),
    source: String(row.source ?? ""),
    active: Boolean(row.use_in_context),
    status: String(row.status ?? "legacy_preview"),
    contentChars: Number(row.content_chars ?? 0),
    previewChars: Number(row.preview_chars ?? 0),
    lastUsedAt: String(row.last_used_at ?? ""),
    created_at: String(row.created_at ?? ""),
  }));
}

/** Flip a file's `use_in_context` flag. POST /api/lib/toggle (FormData). */
export async function toggleLibraryFile(
  id: LibraryFileId,
  enabled: boolean,
): Promise<LibraryResponse> {
  const formData = new FormData();
  formData.append("file_id", String(id));
  formData.append("enabled", String(enabled));
  return requireLibrarySuccess(await request<LibraryResponse>("/api/lib/toggle", {
    method: "POST",
    body: formData,
  }));
}

export async function uploadLibraryFile(
  file: File,
  { useInContext = false }: LibraryUploadOptions = {},
): Promise<LibraryResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("use_in_context", String(useInContext));
  return requireLibrarySuccess(await request<LibraryResponse>("/api/lib/add", {
    method: "POST",
    body: formData,
  }));
}

export async function importResourceToLibrary(
  resourceId: string,
  { useInContext = true }: LibraryUploadOptions = {},
): Promise<LibraryResponse> {
  const formData = new FormData();
  formData.append("resource_id", resourceId);
  formData.append("use_in_context", String(useInContext));
  return requireLibrarySuccess(await request<LibraryResponse>("/api/lib/import-resource", {
    method: "POST",
    body: formData,
  }));
}

export async function deleteLibraryFile(
  id: LibraryFileId,
): Promise<LibraryResponse> {
  return requireLibrarySuccess(await request<LibraryResponse>(`/api/lib/${encodeURIComponent(String(id))}`, {
    method: "DELETE",
  }));
}
