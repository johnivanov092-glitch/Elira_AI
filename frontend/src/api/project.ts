import { API_BASE, request, safeRequest, withAuth } from "./client";

type QueryParam = string | number | boolean | null | undefined;

export type ProjectResponse = Record<string, unknown>;

export type ProjectSnapshot = ProjectResponse & {
  files: unknown[];
};

export type ProjectBrainStatus = ProjectResponse & {
  status?: string;
};

export type ProjectTreeOptions = {
  maxDepth?: number;
  maxItems?: number;
  /** Explicit project root. When set, scopes the call to this path instead of
   *  the global open project (keeps the code-agent drawer independent of the
   *  chat's open project). */
  root?: string;
};

export type ReadProjectFileRequest = {
  path: string;
  max_chars?: number;
  root?: string;
};

export type AdvancedMultiAgentRequest = {
  query?: string;
  model_name?: string;
  context?: string;
  agents?: string[];
  use_reflection?: boolean;
  use_orchestrator?: boolean;
  project_root?: string;
  num_ctx?: number;
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

export async function getProjectSnapshot(): Promise<ProjectSnapshot> {
  const payload = await request<ProjectResponse>("/api/project-brain/snapshot");
  return {
    ...payload,
    files: Array.isArray(payload.files) ? payload.files : [],
  };
}

export async function getProjectFile(path: string): Promise<ProjectResponse> {
  return request<ProjectResponse>(
    `/api/project-brain/file?path=${encodeURIComponent(path)}`,
  );
}

export async function getProjectBrainStatus(): Promise<ProjectBrainStatus> {
  return safeRequest<ProjectBrainStatus>(
    "/api/project-brain/status",
    {},
    { status: "unknown" },
  );
}

export async function getAdvancedProjectInfo(): Promise<ProjectResponse> {
  return request<ProjectResponse>("/api/advanced/project/info");
}

export async function openAdvancedProject(
  path: string,
): Promise<ProjectResponse> {
  return request<ProjectResponse>("/api/advanced/project/open", {
    method: "POST",
    body: { path },
  });
}

export type SavedProject = { id: string; name: string; path: string; created_at?: number };

export async function listSavedProjects(): Promise<SavedProject[]> {
  const res = await request<{ ok: boolean; projects: SavedProject[] }>("/api/advanced/projects");
  return Array.isArray(res?.projects) ? res.projects : [];
}

export async function addSavedProject(path: string, name = ""): Promise<ProjectResponse> {
  return request<ProjectResponse>("/api/advanced/projects", { method: "POST", body: { path, name } });
}

export async function removeSavedProject(id: string): Promise<ProjectResponse> {
  return request<ProjectResponse>(`/api/advanced/projects/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function openSavedProject(arg: { id?: string; name?: string }): Promise<ProjectResponse> {
  return request<ProjectResponse>("/api/advanced/projects/open", {
    method: "POST",
    body: { id: arg.id || "", name: arg.name || "" },
  });
}

export async function getAdvancedProjectTree({
  maxDepth = 3,
  maxItems = 300,
  root = "",
}: ProjectTreeOptions = {}): Promise<ProjectResponse> {
  return request<ProjectResponse>(
    withParams("/api/advanced/project/tree", {
      max_depth: maxDepth,
      max_items: maxItems,
      root,
    }),
  );
}

export async function readAdvancedProjectFile(
  path: string,
  maxChars?: number,
  root?: string,
): Promise<ProjectResponse> {
  const body: ReadProjectFileRequest = { path };
  if (maxChars) body.max_chars = maxChars;
  if (root) body.root = root;
  return request<ProjectResponse>("/api/advanced/project/read", {
    method: "POST",
    body,
  });
}

export async function searchAdvancedProject(
  query: string,
): Promise<ProjectResponse> {
  return request<ProjectResponse>("/api/advanced/project/search", {
    method: "POST",
    body: { query },
  });
}

export async function closeAdvancedProject(): Promise<ProjectResponse> {
  return request<ProjectResponse>("/api/advanced/project/close");
}

export async function runAdvancedMultiAgent(
  body: AdvancedMultiAgentRequest = {},
): Promise<ProjectResponse> {
  return request<ProjectResponse>("/api/advanced/multi-agent", {
    method: "POST",
    body,
  });
}

/** SSE event shapes emitted by /api/advanced/multi-agent/stream. A `step` event
 *  fires once per workflow step (1-based index, total, human label); a `done`
 *  event carries the same payload the sync /multi-agent route returns; an
 *  `error` event carries a message. */
export type AdvancedMultiAgentStreamEvent =
  | { type: "step"; index: number; total: number; label: string }
  | ({ type: "done" } & ProjectResponse)
  | { type: "error"; error: string };

export type AdvancedMultiAgentStreamHandlers = {
  onStep?: (index: number, total: number, label: string) => void;
  onDone?: (result: ProjectResponse) => void;
  onError?: (error: Error) => void;
};

/** Stream a multi-agent run over SSE, emitting per-step progress before the
 *  final report. Mirrors codeAgent.streamCodeAgent's raw-fetch shape (the
 *  request() helper can't read a streaming body). */
export async function streamAdvancedMultiAgent(
  body: AdvancedMultiAgentRequest,
  handlers: AdvancedMultiAgentStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const { onStep, onDone, onError } = handlers;
  const isAbort = (err: unknown) => (err as Error)?.name === "AbortError";
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/api/advanced/multi-agent/stream`, {
      method: "POST",
      headers: withAuth({ "Content-Type": "application/json", Accept: "text/event-stream" }),
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if (isAbort(err)) return; // Stop button aborted the request — not an error.
    onError?.(err as Error);
    return;
  }

  if (!response.ok || !response.body) {
    onError?.(new Error(`Stream failed: HTTP ${response.status}`));
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  function dispatch(data: string) {
    const trimmed = data.trim();
    if (!trimmed) return;
    let evt: AdvancedMultiAgentStreamEvent;
    try {
      evt = JSON.parse(trimmed) as AdvancedMultiAgentStreamEvent;
    } catch {
      return; // ignore malformed lines
    }
    if (evt.type === "step") {
      onStep?.(evt.index, evt.total, evt.label);
    } else if (evt.type === "done") {
      const { type: _type, ...result } = evt;
      onDone?.(result);
    } else if (evt.type === "error") {
      onError?.(new Error(evt.error));
    }
  }

  function flush(chunk: string) {
    const dataLines = chunk
      .split("\n")
      .map((ln) => ln.trim())
      .filter((ln) => ln.startsWith("data:"))
      .map((ln) => ln.slice(5).trim());
    if (dataLines.length) dispatch(dataLines.join("\n"));
  }

  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        flush(buffer.slice(0, idx));
        buffer = buffer.slice(idx + 2);
      }
    }
    if (buffer.trim()) flush(buffer);
  } catch (err) {
    if (isAbort(err)) return; // Stop button aborted the read — not an error.
    onError?.(err as Error);
  }
}
