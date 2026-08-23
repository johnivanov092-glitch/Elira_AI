import { buildApiUrl, request, withAuth } from "./client";


export type PermissionMode = "ask" | "accept_edits" | "bypass";
export type WorkflowRequestKind = "input" | "secret" | "elevation" | "approval";
export type WorkflowRequestAction = "accept" | "decline" | "cancel";
export type WorkflowRequestStatus =
  | "pending"
  | "resolving"
  | "needs_reconciliation"
  | "resolved"
  | "declined"
  | "cancelled"
  | "expired";

export type WorkflowRequest = {
  type: "item/request";
  request_id: string;
  workflow_id: string;
  run_id: string;
  step_id: string;
  kind: WorkflowRequestKind;
  status: WorkflowRequestStatus;
  message: string;
  schema: Record<string, unknown>;
  sensitive: boolean;
  action: string;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
};

export type WorkflowRun = {
  run_id: string;
  workflow_id?: string;
  status: string;
  current_step_id?: string;
  input?: Record<string, unknown>;
  context?: Record<string, unknown>;
  step_results?: Record<string, unknown>;
  pending_steps?: string[];
  error?: Record<string, unknown>;
  requested_pause?: boolean;
  started_at?: string;
  updated_at?: string;
  finished_at?: string | null;
  trigger_source?: string;
  permission_mode?: PermissionMode;
};

export type WorkflowControlEvent = {
  id: number;
  event_id: string;
  event_type: string;
  payload: Record<string, unknown>;
  source_agent_id: string;
  created_at: string;
};

export type WorkflowEventStreamOptions = {
  afterId?: number;
  signal?: AbortSignal;
  onEvent?: (event: WorkflowControlEvent) => void;
  onCursor?: (cursor: number) => void;
};

type WorkflowRequestListResponse = {
  requests: WorkflowRequest[];
  total: number;
};

export type WorkflowRequestResolveResponse = {
  request: WorkflowRequest;
  run: WorkflowRun;
};

export type PortableVaultStatus = {
  initialized: boolean;
  locked: boolean;
  format_version: number;
  vault_id?: string;
  record_count: number;
  created_at?: number;
  updated_at?: number;
  last_used_at?: number | null;
  recovery_enabled?: boolean;
  recovery_key?: string;
};

export type PortableSecretKind =
  | "password"
  | "private_key"
  | "token"
  | "connection_string";

type NativeElevationSpec = {
  program: string;
  args?: string[];
  cwd?: string;
};

export async function runNativeElevation(
  workflowRequest: WorkflowRequest,
): Promise<Record<string, unknown>> {
  const raw = workflowRequest.schema["x-elira-elevation"];
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("Elevation request does not contain x-elira-elevation command data.");
  }
  const spec = raw as Partial<NativeElevationSpec>;
  if (typeof spec.program !== "string" || !spec.program.trim()) {
    throw new Error("Elevation request has no executable program.");
  }
  if (spec.args !== undefined && (
    !Array.isArray(spec.args) || spec.args.some((value) => typeof value !== "string")
  )) {
    throw new Error("Elevation request args must be a string array.");
  }
  const { invoke, isTauri } = await import("@tauri-apps/api/core");
  if (!isTauri()) {
    throw new Error("UAC is available only in the Elira desktop application.");
  }
  return invoke<Record<string, unknown>>("run_elevated_command", {
    requestId: workflowRequest.request_id,
    program: spec.program.trim(),
    args: spec.args ?? [],
    cwd: typeof spec.cwd === "string" && spec.cwd.trim() ? spec.cwd : null,
  });
}

export async function getPortableVaultStatus(): Promise<PortableVaultStatus> {
  return request<PortableVaultStatus>("/api/agent-os/vault/status");
}

export async function createPortableVault(passphrase: string): Promise<PortableVaultStatus> {
  return request<PortableVaultStatus>("/api/agent-os/vault/create", {
    method: "POST",
    body: { passphrase },
  });
}

export async function unlockPortableVault(
  credential: string,
  method: "passphrase" | "recovery" = "passphrase",
): Promise<PortableVaultStatus> {
  return request<PortableVaultStatus>("/api/agent-os/vault/unlock", {
    method: "POST",
    body: { method, credential },
  });
}

export async function lockPortableVault(): Promise<PortableVaultStatus> {
  return request<PortableVaultStatus>("/api/agent-os/vault/lock", { method: "POST" });
}

export async function createPortableSecret(args: {
  kind: PortableSecretKind;
  value: string;
  assetId?: string;
  lifecycle?: "temporary" | "persistent";
}): Promise<{ ok: true; secret_ref: string }> {
  return request<{ ok: true; secret_ref: string }>("/api/agent-os/vault/secrets", {
    method: "POST",
    body: {
      kind: args.kind,
      value: args.value,
      asset_id: args.assetId ?? null,
      lifecycle: args.lifecycle ?? "persistent",
    },
  });
}

export async function listPendingWorkflowRequests(): Promise<WorkflowRequest[]> {
  const response = await request<WorkflowRequestListResponse>(
    "/api/agent-os/workflow-requests?status=actionable",
  );
  return Array.isArray(response.requests) ? response.requests : [];
}

export async function resolveWorkflowRequest(
  requestId: string,
  action: WorkflowRequestAction,
  values: Record<string, unknown> = {},
): Promise<WorkflowRequestResolveResponse> {
  return request<WorkflowRequestResolveResponse>(
    `/api/agent-os/workflow-requests/${encodeURIComponent(requestId)}/resolve`,
    {
      method: "POST",
      body: { action, values },
    },
  );
}

function readStreamChunk(
  reader: ReadableStreamDefaultReader<Uint8Array>,
): Promise<ReadableStreamReadResult<Uint8Array>> {
  return reader.read();
}

function streamBoundary(buffer: string): { index: number; length: number } | null {
  const match = /\r?\n\r?\n/.exec(buffer);
  return match ? { index: match.index, length: match[0].length } : null;
}

function dispatchWorkflowFrame(
  frame: string,
  options: WorkflowEventStreamOptions,
): void {
  let cursor: number | undefined;
  const dataLines: string[] = [];
  for (const rawLine of frame.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (line.startsWith("id:")) {
      const parsed = Number(line.slice(3).trim());
      if (Number.isSafeInteger(parsed) && parsed >= 0) cursor = parsed;
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }
  if (dataLines.length === 0) return;
  try {
    const payload = JSON.parse(dataLines.join("\n")) as Partial<WorkflowControlEvent> & {
      type?: string;
      cursor?: number;
    };
    if (
      payload.type === "stream/ready"
      && Number.isSafeInteger(payload.cursor)
      && payload.cursor !== undefined
      && payload.cursor >= 0
      && (cursor === undefined || cursor === payload.cursor)
    ) {
      options.onCursor?.(payload.cursor);
      return;
    }
    if (
      cursor !== undefined
      && typeof payload.id === "number"
      && payload.id === cursor
      && typeof payload.event_id === "string"
      && typeof payload.event_type === "string"
      && payload.payload !== null
      && typeof payload.payload === "object"
      && !Array.isArray(payload.payload)
      && typeof payload.source_agent_id === "string"
      && typeof payload.created_at === "string"
    ) {
      options.onCursor?.(cursor);
      options.onEvent?.(payload as WorkflowControlEvent);
    }
  } catch {
    // Ignore one malformed event; the durable cursor/reconnect path remains live.
  }
}

export async function streamWorkflowEvents(
  options: WorkflowEventStreamOptions = {},
): Promise<void> {
  const params = new URLSearchParams();
  if (options.afterId !== undefined) params.set("after_id", String(options.afterId));
  const suffix = params.size > 0 ? `?${params.toString()}` : "";
  let response: Response;
  try {
    response = await fetch(buildApiUrl(`/api/agent-os/events/stream${suffix}`), {
      method: "GET",
      headers: withAuth({ Accept: "text/event-stream" }),
      signal: options.signal,
    });
  } catch (error) {
    if ((error as DOMException)?.name === "AbortError") return;
    throw error;
  }
  if (!response.ok || !response.body) {
    throw new Error(`Workflow event stream failed: HTTP ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await readStreamChunk(reader);
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = streamBoundary(buffer);
      while (boundary) {
        dispatchWorkflowFrame(buffer.slice(0, boundary.index), options);
        buffer = buffer.slice(boundary.index + boundary.length);
        boundary = streamBoundary(buffer);
      }
    }
    buffer += decoder.decode();
    if (buffer.trim()) dispatchWorkflowFrame(buffer, options);
  } catch (error) {
    try { await reader.cancel(); } catch { /* stream already closed */ }
    if ((error as DOMException)?.name === "AbortError") return;
    throw error;
  }
}
