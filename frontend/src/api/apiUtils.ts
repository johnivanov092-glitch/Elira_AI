// apiUtils.ts — shared helpers for all API modules

export interface Chat {
  id: string;
  title: string;
  pinned: boolean;
  memory_saved: boolean;
  [key: string]: unknown;
}

export interface Message {
  id: string;
  role: string;
  content: string;
  [key: string]: unknown;
}

export function normalizeArray(payload: unknown): unknown[] {
  if (Array.isArray(payload)) return payload;
  const p = payload as Record<string, unknown> | null | undefined;
  if (Array.isArray(p?.items)) return p!.items as unknown[];
  if (Array.isArray(p?.messages)) return p!.messages as unknown[];
  if (Array.isArray(p?.files)) return p!.files as unknown[];
  return [];
}

export function unwrapItem(payload: unknown): unknown {
  if (!payload || typeof payload !== "object") return payload;
  const p = payload as Record<string, unknown>;
  return p.item || p.chat || p.message || p.data || payload;
}

export function normalizeSessionId(value: unknown): string | null {
  if (value === undefined || value === null || value === "") return null;
  return typeof value === "string" ? value : String(value);
}

export function normalizeChat(item: Record<string, unknown> = {}): Chat {
  return {
    ...item,
    id: (item.id as string) ?? "",
    title: (item.title as string) ?? "New chat",
    pinned: Boolean(item.pinned),
    memory_saved: Boolean(item.memory_saved),
  };
}

export function normalizeMessage(item: Record<string, unknown> = {}): Message {
  const content = (item.content ?? item.answer ?? item.response ?? item.message ?? "") as string;
  return {
    ...item,
    id: (item.id as string) ?? `${(item.role as string) || "msg"}-${Date.now()}`,
    role: (item.role as string) ?? "assistant",
    content: typeof content === "string" ? content : String(content ?? ""),
  };
}

export function extractAgentError(payload: unknown): string {
  if (!payload || typeof payload !== "object") return "";
  const p = payload as Record<string, unknown>;
  if (p.ok === false) {
    const meta = p.meta as Record<string, unknown> | undefined;
    if (typeof meta?.error === "string" && (meta.error as string).trim()) return meta.error as string;
    return "run_agent returned an error";
  }
  return "";
}

export function formatRequestError(error: unknown, fallback = "Request failed"): string {
  const value = (error as Record<string, unknown>)?.message ??
    (error as Record<string, unknown>)?.detail ??
    error;
  if (!value) return fallback;
  if (typeof value === "string") return value;
  if (Array.isArray(value))
    return value.map((item) => formatRequestError(item, "")).filter(Boolean).join(" | ") || fallback;
  if (typeof value === "object") {
    const v = value as Record<string, unknown>;
    return (v.message as string) || (v.msg as string) || JSON.stringify(value);
  }
  return String(value);
}

export function withParams(path: string, params: Record<string, unknown> = {}): string {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    query.set(key, String(value));
  });
  const suffix = query.toString();
  return suffix ? `${path}?${suffix}` : path;
}

export function isLocalApiAssetUrl(url = ""): boolean {
  return typeof url === "string" && (
    url.includes("/api/skills/download/") ||
    url.includes("/api/skills/view/") ||
    url.includes("/api/extra/")
  );
}
