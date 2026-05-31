import { request } from "./client";

export type PipelineId = string | number;
export type PipelineItem = Record<string, unknown>;
export type PipelineLogEntry = Record<string, unknown>;
export type PipelineResponse = Record<string, unknown>;

export type PipelineWriteRequest = {
  name?: string;
  enabled?: boolean;
  [key: string]: unknown;
};

type PipelinesPayload = {
  pipelines?: PipelineItem[];
  [key: string]: unknown;
};

type PipelineLogsPayload = {
  logs?: PipelineLogEntry[];
  [key: string]: unknown;
};

function normalizePipelines(payload: PipelinesPayload | PipelineItem[]): PipelineItem[] {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.pipelines)) return payload.pipelines;
  return [];
}

function normalizePipelineLogs(payload: PipelineLogsPayload | PipelineLogEntry[]): PipelineLogEntry[] {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.logs)) return payload.logs;
  return [];
}

export async function listPipelines(): Promise<PipelineItem[]> {
  const payload = await request<PipelinesPayload | PipelineItem[]>(
    "/api/pipelines/list",
  );
  return normalizePipelines(payload);
}

export async function getPipelineLogs(
  pipelineId: PipelineId,
  limit = 20,
): Promise<PipelineLogEntry[]> {
  const payload = await request<PipelineLogsPayload | PipelineLogEntry[]>(
    `/api/pipelines/logs/${encodeURIComponent(String(pipelineId))}?limit=${encodeURIComponent(String(limit))}`,
  );
  return normalizePipelineLogs(payload);
}

export async function createPipeline(
  body: PipelineWriteRequest = {},
): Promise<PipelineResponse> {
  return request<PipelineResponse>("/api/pipelines/create", {
    method: "POST",
    body,
  });
}

export async function runPipeline(
  pipelineId: PipelineId,
): Promise<PipelineResponse> {
  return request<PipelineResponse>(
    `/api/pipelines/run/${encodeURIComponent(String(pipelineId))}`,
    { method: "POST" },
  );
}

export async function updatePipeline(
  pipelineId: PipelineId,
  body: PipelineWriteRequest = {},
): Promise<PipelineResponse> {
  return request<PipelineResponse>(
    `/api/pipelines/update/${encodeURIComponent(String(pipelineId))}`,
    { method: "PUT", body },
  );
}

export async function deletePipeline(
  pipelineId: PipelineId,
): Promise<PipelineResponse> {
  return request<PipelineResponse>(
    `/api/pipelines/delete/${encodeURIComponent(String(pipelineId))}`,
    { method: "DELETE" },
  );
}
