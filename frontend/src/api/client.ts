type ResponseType = "text" | "blob";

export type ApiRequestOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
  raw?: boolean;
  responseType?: ResponseType;
};

export type FallbackValue<T> = T | ((error: unknown) => T | Promise<T>);

export const API_BASE: string =
  import.meta.env.VITE_API_BASE_URL || `http://${window.location.hostname}:8000`;

export function buildApiUrl(path = ""): string {
  if (!path) return API_BASE;
  return path.startsWith("http://") || path.startsWith("https://")
    ? path
    : `${API_BASE}${path}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function messageFromItem(item: unknown): string {
  if (isRecord(item) && typeof item.msg === "string") return item.msg;
  return JSON.stringify(item);
}

export function normalizeError(payload: unknown, status: number): string {
  if (typeof payload === "string") return payload;
  if (Array.isArray(payload)) {
    return payload.map(messageFromItem).join("; ");
  }
  if (isRecord(payload) && Array.isArray(payload.detail)) {
    return payload.detail.map(messageFromItem).join("; ");
  }
  if (isRecord(payload)) {
    for (const key of ["detail", "message", "error"]) {
      const value = payload[key];
      if (typeof value === "string" && value.trim()) return value;
    }
  }
  return `Request failed: ${status}`;
}

export async function parseResponse(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return response.json();
  if (contentType.startsWith("text/")) return response.text();
  return response.blob();
}

function isFetchBody(value: unknown): value is BodyInit {
  return (
    (typeof FormData !== "undefined" && value instanceof FormData) ||
    (typeof Blob !== "undefined" && value instanceof Blob) ||
    (typeof URLSearchParams !== "undefined" && value instanceof URLSearchParams) ||
    (typeof ArrayBuffer !== "undefined" && value instanceof ArrayBuffer) ||
    (typeof ReadableStream !== "undefined" && value instanceof ReadableStream) ||
    typeof value === "string"
  );
}

export async function request(path: string, options: ApiRequestOptions & { raw: true }): Promise<Response>;
export async function request<T = unknown>(path: string, options?: ApiRequestOptions): Promise<T>;
export async function request<T = unknown>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T | Response> {
  const {
    method = "GET",
    headers = {},
    body,
    raw = false,
    responseType,
    ...rest
  } = options;

  const finalHeaders = new Headers(headers);
  let finalBody: BodyInit | null | undefined;

  if (body !== undefined && body !== null) {
    if (isFetchBody(body)) {
      finalBody = body;
    } else if (typeof body === "object") {
      if (!finalHeaders.has("Content-Type")) {
        finalHeaders.set("Content-Type", "application/json");
      }
      finalBody = JSON.stringify(body);
    } else {
      finalBody = String(body);
    }
  }

  const response = await fetch(buildApiUrl(path), {
    method,
    headers: finalHeaders,
    body: finalBody,
    ...rest,
  });

  if (raw) return response;

  let payload: unknown;
  if (responseType === "text") payload = await response.text();
  else if (responseType === "blob") payload = await response.blob();
  else payload = await parseResponse(response);

  if (!response.ok) {
    throw new Error(normalizeError(payload, response.status));
  }

  return payload as T;
}

export async function safeRequest<T = unknown>(
  path: string,
  options: ApiRequestOptions = {},
  fallback: FallbackValue<T> | null = null,
): Promise<T> {
  try {
    return await request<T>(path, options);
  } catch (error) {
    if (fallback !== null) {
      return typeof fallback === "function"
        ? await (fallback as (error: unknown) => T | Promise<T>)(error)
        : fallback;
    }
    throw error;
  }
}
