type ResponseType = "text" | "blob";

export type ApiRequestOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
  raw?: boolean;
  responseType?: ResponseType;
  /** Abort the request after this many ms so a silently-hung backend fails fast
   *  instead of leaving the caller waiting forever. Default 120s; pass 0 to
   *  disable (for genuinely long ops like project indexing). Ignored if the
   *  caller already supplies its own AbortSignal. */
  timeoutMs?: number;
};

export type FallbackValue<T> = T | ((error: unknown) => T | Promise<T>);

/** Error carrying the HTTP status, so callers can distinguish 404 (genuinely
 *  missing) from a transient 5xx / network failure — critical for not treating a
 *  failed load as "empty" and overwriting good server data. */
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

// Always use the explicit IPv4 loopback address for the backend.
// Tauri desktop app: backend is always on 127.0.0.1:8000.
// Using window.location.hostname risks picking up "localhost" which on Windows
// resolves to ::1 (IPv6) — the backend only listens on IPv4 (127.0.0.1:8000).
export const API_BASE: string =
  import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

// Optional bearer token for non-local (LAN / mobile) access. The backend trusts
// loopback callers without a token, so for the desktop/dev flow this stays empty
// and no Authorization header is sent. Set VITE_ELIRA_API_TOKEN for mobile mode.
export const API_TOKEN: string = import.meta.env.VITE_ELIRA_API_TOKEN || "";

/** Attach the bearer token to a header set when one is configured. */
export function withAuth(init?: HeadersInit): Headers {
  const headers = new Headers(init);
  if (API_TOKEN && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${API_TOKEN}`);
  }
  return headers;
}

/** Poll /health until the backend answers or we give up.
 *  Returns true if backend is reachable, false on timeout.
 *  Uses manual AbortController instead of AbortSignal.timeout()
 *  for compatibility with older WebView2 versions. */
export async function waitForBackend(
  maxAttempts = 15,
  intervalMs = 2000,
  onAttempt?: (attempt: number) => void,
): Promise<boolean> {
  for (let i = 0; i < maxAttempts; i++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 4000);
    try {
      const r = await fetch(`${API_BASE}/health`, { signal: controller.signal });
      clearTimeout(timer);
      if (r.ok) return true;
    } catch {
      // network error or timeout — backend not ready yet
    } finally {
      clearTimeout(timer);
    }
    onAttempt?.(i + 1);
    await new Promise<void>((res) => setTimeout(res, intervalMs));
  }
  return false;
}

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
    timeoutMs = 120_000,
    ...rest
  } = options;

  const finalHeaders = withAuth(headers);
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

  // Inactivity timeout via a manual AbortController (AbortSignal.timeout isn't in
  // older WebView2). Skipped when the caller passes its own signal or timeoutMs=0.
  let timeoutSignal: AbortSignal | undefined;
  let timeoutTimer: ReturnType<typeof setTimeout> | undefined;
  const callerSignal = (rest as { signal?: AbortSignal }).signal;
  if (!callerSignal && timeoutMs > 0) {
    const controller = new AbortController();
    timeoutSignal = controller.signal;
    timeoutTimer = setTimeout(() => controller.abort(), timeoutMs);
  }

  let response: Response;
  try {
    response = await fetch(buildApiUrl(path), {
      method,
      headers: finalHeaders,
      body: finalBody,
      ...rest,
      ...(timeoutSignal ? { signal: timeoutSignal } : {}),
    });
  } finally {
    if (timeoutTimer) clearTimeout(timeoutTimer);
  }

  if (raw) return response;

  let payload: unknown;
  if (responseType === "text") payload = await response.text();
  else if (responseType === "blob") payload = await response.blob();
  else payload = await parseResponse(response);

  if (!response.ok) {
    throw new ApiError(normalizeError(payload, response.status), response.status);
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
