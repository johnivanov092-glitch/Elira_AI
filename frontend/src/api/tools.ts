import { request } from "./client";

type QueryParam = string | number | boolean | null | undefined;

export type ToolRun = Record<string, unknown>;
export type ToolResponse = Record<string, unknown>;

export type AnalyzeCodeRequest = {
  code?: string;
  language?: string;
  [key: string]: unknown;
};

type ToolRunsPayload = {
  runs?: ToolRun[];
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

function normalizeRuns(payload: ToolRunsPayload | ToolRun[]): ToolRun[] {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.runs)) return payload.runs;
  return [];
}

export async function listToolRuns(limit = 50): Promise<ToolRun[]> {
  const payload = await request<ToolRunsPayload | ToolRun[]>(
    withParams("/api/tools/run-history", { limit }),
  );
  return normalizeRuns(payload);
}

export async function runPythonCode(code: string): Promise<ToolResponse> {
  return request<ToolResponse>("/api/tools/run-python", {
    method: "POST",
    body: { code },
  });
}

export async function analyzeCode(
  body: AnalyzeCodeRequest = {},
): Promise<ToolResponse> {
  return request<ToolResponse>("/api/tools/analyze-code", {
    method: "POST",
    body,
  });
}
