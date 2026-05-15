import { request, safeRequest } from "./client";

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
};

export type ReadProjectFileRequest = {
  path: string;
  max_chars?: number;
};

export type AdvancedMultiAgentRequest = {
  query?: string;
  model_name?: string;
  context?: string;
  agents?: string[];
  use_reflection?: boolean;
  use_orchestrator?: boolean;
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

export async function getAdvancedProjectTree({
  maxDepth = 3,
  maxItems = 300,
}: ProjectTreeOptions = {}): Promise<ProjectResponse> {
  return request<ProjectResponse>(
    withParams("/api/advanced/project/tree", {
      max_depth: maxDepth,
      max_items: maxItems,
    }),
  );
}

export async function readAdvancedProjectFile(
  path: string,
  maxChars?: number,
): Promise<ProjectResponse> {
  const body: ReadProjectFileRequest = { path };
  if (maxChars) body.max_chars = maxChars;
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
