import { request } from "./client";

export type McpServer = {
  id: string;
  description: string;
  transport: "stdio" | "http";
  enabled: boolean;
  status: "running" | "stopped" | "crashed" | "error";
  last_error: string | null;
};

export type McpAction = "start" | "stop" | "restart";

/** One entry of data/mcp_servers.json; secret values come back masked as "●●●". */
export type McpServerConfig = Record<string, unknown>;

export async function listMcpServers(): Promise<McpServer[]> {
  return (await request<{ servers: McpServer[] }>("/api/mcp/servers")).servers;
}

export function controlMcpServer(id: string, action: McpAction): Promise<{ ok: boolean }> {
  return request(`/api/mcp/servers/${encodeURIComponent(id)}/lifecycle`, {
    method: "POST", body: { action },
  });
}

export async function getMcpServerConfig(id: string): Promise<McpServerConfig> {
  return (await request<{ config: McpServerConfig }>(`/api/mcp/servers/${encodeURIComponent(id)}/config`)).config;
}

export function updateMcpServer(id: string, config: McpServerConfig): Promise<{ ok: boolean }> {
  return request(`/api/mcp/servers/${encodeURIComponent(id)}`, { method: "PUT", body: { config } });
}

export function addMcpServer(config: McpServerConfig): Promise<{ ok: boolean }> {
  return request("/api/mcp/servers", { method: "POST", body: { config } });
}

export function setMcpServerEnabled(id: string, enabled: boolean): Promise<{ ok: boolean }> {
  return request(`/api/mcp/servers/${encodeURIComponent(id)}/enabled`, { method: "POST", body: { enabled } });
}

export function deleteMcpServer(id: string): Promise<{ ok: boolean }> {
  return request(`/api/mcp/servers/${encodeURIComponent(id)}`, { method: "DELETE" });
}
