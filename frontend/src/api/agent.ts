// agent.ts — chat execution, streaming, models and settings API

import { buildApiUrl, request, safeRequest } from "./client";
import { normalizeSessionId, extractAgentError } from "./apiUtils";

interface StreamCallbacks {
  onToken?: (token: string) => void;
  onDone?: (result: { full_text: string; meta: unknown; timeline: unknown[] }) => void;
  onError?: (msg: string) => void;
  onPhase?: (event: Record<string, unknown>) => void;
}

export async function execute(body: Record<string, unknown> = {}) {
  const response = await request("/api/chat/send", {
    method: "POST",
    body: {
      model_name: body.model_name ?? body.model ?? "gemma3:4b",
      profile_name: body.profile_name ?? body.profile ?? "default",
      user_input: String(body.user_input ?? body.message ?? body.prompt ?? body.text ?? body.query ?? "").trim(),
      session_id: normalizeSessionId(body.session_id ?? body.chat_id ?? body.chatId ?? null),
      history: Array.isArray(body.history) ? body.history : [],
      use_memory: body.use_memory ?? true,
      use_library: body.use_library ?? true,
      use_reflection: body.use_reflection ?? false,
    },
  }) as Record<string, unknown>;
  const routeError = extractAgentError(response);
  if (routeError) throw new Error(routeError);
  const content = response?.content ?? response?.answer ?? response?.response ?? response?.message ?? "";
  if (!String(content).trim()) throw new Error("Empty response from /api/chat/send");
  return { ...response, content: String(content) };
}

export function executeStream(body: Record<string, unknown> = {}, { onToken, onDone, onError, onPhase }: StreamCallbacks = {}) {
  const controller = new AbortController();

  const payload = {
    model_name: body.model_name ?? body.model ?? "gemma3:4b",
    profile_name: body.profile_name ?? body.profile ?? "default",
    user_input: String(body.user_input ?? body.message ?? "").trim(),
    session_id: normalizeSessionId(body.session_id ?? body.chat_id ?? body.chatId ?? null),
    history: Array.isArray(body.history) ? body.history : [],
    num_ctx: body.num_ctx ?? 8192,
    use_memory: body.use_memory ?? true,
    use_library: body.use_library ?? true,
    use_reflection: body.use_reflection ?? false,
    use_web_search: body.use_web_search ?? true,
    use_python_exec: body.use_python_exec ?? true,
    use_image_gen: body.use_image_gen ?? true,
    use_file_gen: body.use_file_gen ?? true,
    use_http_api: body.use_http_api ?? true,
    use_sql: body.use_sql ?? true,
    use_screenshot: body.use_screenshot ?? true,
    use_encrypt: body.use_encrypt ?? true,
    use_archiver: body.use_archiver ?? true,
    use_converter: body.use_converter ?? true,
    use_regex: body.use_regex ?? true,
    use_translator: body.use_translator ?? true,
    use_csv: body.use_csv ?? true,
    use_webhook: body.use_webhook ?? true,
    use_plugins: body.use_plugins ?? true,
  };

  fetch(buildApiUrl("/api/chat/stream"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `HTTP ${response.status}`);
      }

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed || !trimmed.startsWith("data: ")) continue;

            try {
              const event = JSON.parse(trimmed.slice(6)) as Record<string, unknown>;

              if (event.error) {
                onError?.(event.error as string);
                return;
              }

              if (event.phase && onPhase) onPhase(event);
              if (event.phase === "reflection_replace" && event.full_text) continue;
              if (event.token) onToken?.(event.token as string);

              if (event.done) {
                onDone?.({
                  full_text: (event.full_text as string) || "",
                  meta: event.meta || {},
                  timeline: (event.timeline as unknown[]) || [],
                });
                return;
              }
            } catch (parseError) {
              console.warn("SSE parse error:", trimmed.slice(0, 100), parseError);
            }
          }
        }

        onDone?.({ full_text: "", meta: {}, timeline: [] });
      } finally {
        reader.cancel().catch(() => {});
      }
    })
    .catch((error: Error) => {
      if (error.name === "AbortError") return;
      onError?.(error.message || "Stream error");
    });

  return controller;
}

export async function listOllamaModels() {
  const payload = await safeRequest("/api/elira/models", {}, []) as Record<string, unknown>;
  if (Array.isArray(payload?.models)) return { models: payload.models };
  if (Array.isArray(payload?.items)) return { models: payload.items };
  if (Array.isArray(payload)) return { models: payload };
  return { models: [] };
}

export async function getSettings() {
  return safeRequest("/api/elira/settings", {}, {});
}

export async function updateSettings(body: Record<string, unknown> = {}) {
  return request("/api/elira/settings", { method: "PUT", body });
}
