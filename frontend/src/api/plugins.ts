import { request } from "./client";

export type PluginName = string;
export type PluginItem = Record<string, unknown>;
export type PluginResponse = Record<string, unknown>;

type PluginsPayload = {
  plugins?: PluginItem[];
  [key: string]: unknown;
};

function normalizePlugins(payload: PluginsPayload | PluginItem[]): PluginItem[] {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.plugins)) return payload.plugins;
  return [];
}

export async function listPlugins(): Promise<PluginItem[]> {
  const payload = await request<PluginsPayload | PluginItem[]>(
    "/api/extra/plugins/list",
  );
  return normalizePlugins(payload);
}

export async function reloadPlugins(): Promise<PluginResponse> {
  return request<PluginResponse>("/api/extra/plugins/reload", {
    method: "POST",
  });
}

export async function setPluginEnabled(
  name: PluginName,
  enabled: boolean,
): Promise<PluginResponse> {
  const action = enabled ? "enable" : "disable";
  return request<PluginResponse>(
    `/api/extra/plugins/${action}/${encodeURIComponent(name)}`,
    { method: "POST" },
  );
}

export async function createPlugin(
  name: string,
  category = "",
  description = "",
): Promise<PluginResponse> {
  return request<PluginResponse>("/api/extra/plugins/create", {
    method: "POST",
    body: { name, category, description },
  });
}

export async function uploadPlugin(
  filename: string,
  pyContent: string,
  manifestContent: string | null = null,
): Promise<PluginResponse> {
  return request<PluginResponse>("/api/extra/plugins/upload", {
    method: "POST",
    body: { filename, py_content: pyContent, manifest_content: manifestContent },
  });
}
