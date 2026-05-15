import { request } from "./client";

export type SmartMemoryId = string | number;

export type SmartMemoryItem = {
  id?: SmartMemoryId;
  text?: string;
  category?: string;
  source?: string;
  importance?: number;
  access_count?: number;
  [key: string]: unknown;
};

export type SmartMemoryStats = Record<string, unknown>;

export type SmartMemoryWriteRequest = {
  text?: string;
  category?: string;
  importance?: number;
  [key: string]: unknown;
};

type SmartMemoryListResponse = {
  items?: SmartMemoryItem[];
  [key: string]: unknown;
};

function withParams(
  path: string,
  params: Record<string, string | number | boolean | null | undefined> = {},
): string {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    query.set(key, String(value));
  });
  const suffix = query.toString();
  return suffix ? `${path}?${suffix}` : path;
}

function normalizeItems(payload: SmartMemoryListResponse | SmartMemoryItem[]): SmartMemoryItem[] {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.items)) return payload.items;
  return [];
}

export async function listSmartMemory(limit = 100): Promise<SmartMemoryItem[]> {
  const payload = await request<SmartMemoryListResponse | SmartMemoryItem[]>(
    withParams("/api/smart-memory/list", { limit }),
  );
  return normalizeItems(payload);
}

export async function getSmartMemoryStats(): Promise<SmartMemoryStats> {
  return request<SmartMemoryStats>("/api/smart-memory/stats");
}

export async function addSmartMemory(
  body: SmartMemoryWriteRequest = {},
): Promise<unknown> {
  return request("/api/smart-memory/add", { method: "POST", body });
}

export async function deleteSmartMemory(id: SmartMemoryId): Promise<unknown> {
  return request(`/api/smart-memory/${encodeURIComponent(String(id))}`, {
    method: "DELETE",
  });
}

export async function searchSmartMemory(
  query: string,
  limit = 20,
): Promise<SmartMemoryItem[]> {
  const payload = await request<SmartMemoryListResponse | SmartMemoryItem[]>(
    "/api/smart-memory/search",
    {
      method: "POST",
      body: { query, limit },
    },
  );
  return normalizeItems(payload);
}
