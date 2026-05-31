import { request, safeRequest } from "./client";

export type LibraryFileId = string | number;

export type LibraryListResponse = Record<string, unknown> | null;

export type LibraryUploadOptions = {
  useInContext?: boolean;
};

export type LibraryResponse = Record<string, unknown>;

export async function listLibraryFiles(): Promise<LibraryListResponse> {
  return safeRequest<LibraryListResponse>("/api/lib/list", {}, null);
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
