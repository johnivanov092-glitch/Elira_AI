import { request, safeRequest } from "./client";

export type LibraryFileId = string | number;

export type LibraryListResponse = Record<string, unknown> | null;

export type LibraryUploadOptions = {
  useInContext?: boolean;
};

export type LibraryResponse = Record<string, unknown>;

/** One library file as returned by /api/lib/list (raw `files` columns). */
export type LibraryFile = {
  id: number;
  name: string;
  size: number;
  type: string;
  source: string;
  /** 0|1 on the wire; normalized to a boolean by `listLibraryFiles`. */
  active: boolean;
  created_at: string;
};

type RawLibraryRow = {
  id?: number;
  name?: string;
  size?: number;
  type?: string;
  source?: string;
  use_in_context?: number | boolean;
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
  return request<LibraryResponse>("/api/lib/toggle", {
    method: "POST",
    body: formData,
  });
}

export async function uploadLibraryFile(
  file: File,
  { useInContext = false }: LibraryUploadOptions = {},
): Promise<LibraryResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("use_in_context", String(useInContext));
  return request<LibraryResponse>("/api/lib/add", {
    method: "POST",
    body: formData,
  });
}

export async function deleteLibraryFile(
  id: LibraryFileId,
): Promise<LibraryResponse> {
  return request<LibraryResponse>(`/api/lib/${encodeURIComponent(String(id))}`, {
    method: "DELETE",
  });
}
