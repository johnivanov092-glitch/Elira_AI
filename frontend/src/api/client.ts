export const API_BASE: string =
  (import.meta.env.VITE_API_BASE_URL as string) || `http://${window.location.hostname}:8000`;

export function buildApiUrl(path = ""): string {
  if (!path) return API_BASE;
  return path.startsWith("http://") || path.startsWith("https://")
    ? path
    : `${API_BASE}${path}`;
}

export function normalizeError(payload: unknown, status: number): string {
  if (typeof payload === "string") return payload;
  if (Array.isArray(payload)) {
    return payload
      .map((item: unknown) =>
        item && typeof item === "object" && "msg" in item ? String((item as Record<string, unknown>).msg) : JSON.stringify(item)
      )
      .join("; ");
  }
  const p = payload as Record<string, unknown> | null | undefined;
  if (Array.isArray(p?.detail)) {
    return (p!.detail as unknown[])
      .map((item: unknown) =>
        item && typeof item === "object" && "msg" in item ? String((item as Record<string, unknown>).msg) : JSON.stringify(item)
      )
      .join("; ");
  }
  return (
    (p?.detail as string | undefined) ||
    (p?.message as string | undefined) ||
    (p?.error as string | undefined) ||
    `Request failed: ${status}`
  );
}

export async function parseResponse(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return response.json();
  if (contentType.startsWith("text/")) return response.text();
  return response.blob();
}

export interface RequestOptions {
  method?: string;
  headers?: Record<string, string>;
  body?: unknown;
  raw?: boolean;
  responseType?: "text" | "blob" | "json";
  signal?: AbortSignal;
  [key: string]: unknown;
}

export async function request(path: string, options: RequestOptions = {}): Promise<unknown> {
  const { method = "GET", headers = {}, body, raw = false, responseType, ...rest } = options;

  const finalHeaders = new Headers(headers);
  let finalBody: BodyInit | null | undefined;

  if (body !== undefined && body !== null) {
    const isFormData = typeof FormData !== "undefined" && body instanceof FormData;
    const isBlob = typeof Blob !== "undefined" && body instanceof Blob;
    const isParams = typeof URLSearchParams !== "undefined" && body instanceof URLSearchParams;

    if (!isFormData && !isBlob && !isParams && typeof body === "object") {
      if (!finalHeaders.has("Content-Type")) {
        finalHeaders.set("Content-Type", "application/json");
      }
      finalBody = JSON.stringify(body);
    } else {
      finalBody = body as BodyInit;
    }
  }

  const response = await fetch(buildApiUrl(path), {
    method,
    headers: finalHeaders,
    body: finalBody,
    ...(rest as RequestInit),
  });

  if (raw) return response;

  let payload: unknown;
  if (responseType === "text") payload = await response.text();
  else if (responseType === "blob") payload = await response.blob();
  else payload = await parseResponse(response);

  if (!response.ok) {
    throw new Error(normalizeError(payload, response.status));
  }

  return payload;
}

export async function safeRequest<T = unknown>(
  path: string,
  options: RequestOptions = {},
  fallback: T | null = null
): Promise<unknown> {
  try {
    return await request(path, options);
  } catch (error) {
    if (fallback !== null) {
      return typeof fallback === "function" ? (fallback as (e: unknown) => unknown)(error) : fallback;
    }
    throw error;
  }
}
