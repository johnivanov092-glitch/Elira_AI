// library.ts — file library, text extraction and patch API

import { request, safeRequest } from "./client";
import { normalizeArray, withParams } from "./apiUtils";

export async function extractUploadedFileText(file: File) {
  const formData = new FormData();
  formData.append("file", file);
  return request("/api/files/extract-text", { method: "POST", body: formData });
}

export async function listLibraryFiles() {
  return safeRequest("/api/lib/list", {}, null);
}

export async function uploadLibraryFile(file: File, { useInContext = false } = {}) {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("use_in_context", String(useInContext));
  return request("/api/lib/add", { method: "POST", body: formData });
}

export async function deleteLibraryFile(id: string | number) {
  return request(`/api/lib/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function listPatchHistory({ path = "", limit = 20 } = {}) {
  const payload = await safeRequest(withParams("/api/elira/patch/history/list", { path, limit }), {}, { items: [] });
  return { ...(payload as Record<string, unknown>), items: normalizeArray(payload) };
}

export async function previewPatch(body: Record<string, unknown> = {}) {
  return request("/api/elira/patch/diff", { method: "POST", body });
}

export async function applyPatch(body: Record<string, unknown> = {}) {
  return request("/api/elira/patch/apply", { method: "POST", body });
}

export async function rollbackPatch(body: Record<string, unknown> = {}) {
  return request("/api/elira/patch/rollback", { method: "POST", body });
}

export async function verifyPatch(body: Record<string, unknown> = {}) {
  return request("/api/elira/patch/verify", { method: "POST", body });
}
