import { request, safeRequest } from "./client";

type QueryParam = string | number | boolean | null | undefined;

export type PatchResponse = Record<string, unknown>;

export type PatchHistoryOptions = {
  path?: string;
  limit?: number;
};

export type PatchRequest = {
  path?: string;
  content?: string;
  new_content?: string;
  [key: string]: unknown;
};

type PatchItemsPayload = {
  items?: unknown[];
  [key: string]: unknown;
};

function withParams(
  path: string,
  params: Record<string, QueryParam> = {},
): string {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    query.set(key, String(value));
  });
  const suffix = query.toString();
  return suffix ? `${path}?${suffix}` : path;
}

function normalizeItems(payload: PatchItemsPayload | unknown[]): unknown[] {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.items)) return payload.items;
  return [];
}

export async function listPatchHistory({
  path = "",
  limit = 20,
}: PatchHistoryOptions = {}): Promise<PatchItemsPayload> {
  const payload = await safeRequest<PatchItemsPayload | unknown[]>(
    withParams("/api/elira/patch/history/list", { path, limit }),
    {},
    { items: [] },
  );
  return {
    ...(Array.isArray(payload) ? {} : payload),
    items: normalizeItems(payload),
  };
}

export async function previewPatch(
  body: PatchRequest = {},
): Promise<PatchResponse> {
  return request<PatchResponse>("/api/elira/patch/diff", {
    method: "POST",
    body,
  });
}

export async function applyPatch(
  body: PatchRequest = {},
): Promise<PatchResponse> {
  return request<PatchResponse>("/api/elira/patch/apply", {
    method: "POST",
    body,
  });
}

export async function rollbackPatch(
  body: PatchRequest = {},
): Promise<PatchResponse> {
  return request<PatchResponse>("/api/elira/patch/rollback", {
    method: "POST",
    body,
  });
}

export async function verifyPatch(
  body: PatchRequest = {},
): Promise<PatchResponse> {
  return request<PatchResponse>("/api/elira/patch/verify", {
    method: "POST",
    body,
  });
}
