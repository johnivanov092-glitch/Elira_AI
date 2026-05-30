/**
 * Spotlight global search API client.
 *
 * Backend endpoint: GET /api/spotlight/search?q=<query>
 * Returns four grouped buckets so the overlay UI can render them
 * with their own section headers and icons. Always returns all
 * four keys; empty buckets are an empty list, not absent.
 */
import { request } from "./client";

export type SpotlightHitType = "chat" | "session" | "rag" | "file";

export type SpotlightChatHit = {
  type: "chat";
  id: string;
  title: string;
  snippet: string;
  updated_at?: string | number | null;
};

export type SpotlightSessionHit = {
  type: "session";
  id: string;
  title: string;
  snippet: string;
  updated_at?: number;
};

export type SpotlightRagHit = {
  type: "rag";
  id: string;
  title: string;
  snippet: string;
  category?: string;
  score?: number;
};

export type SpotlightFileHit = {
  type: "file";
  id: string;
  title: string;
  snippet: string;
  updated_at?: string | null;
};

export type SpotlightHit =
  | SpotlightChatHit
  | SpotlightSessionHit
  | SpotlightRagHit
  | SpotlightFileHit;

export type SpotlightResponse = {
  query: string;
  chats: SpotlightChatHit[];
  sessions: SpotlightSessionHit[];
  rag: SpotlightRagHit[];
  files: SpotlightFileHit[];
  total: number;
};

export async function spotlightSearch(
  query: string,
  options?: { signal?: AbortSignal },
): Promise<SpotlightResponse> {
  const q = encodeURIComponent(query);
  return request<SpotlightResponse>(`/api/spotlight/search?q=${q}`, {
    signal: options?.signal,
  });
}
