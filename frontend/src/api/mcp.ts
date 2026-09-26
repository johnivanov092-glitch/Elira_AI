import { request } from "./client";

export type McpServer = {
  id: string;
  transport: "stdio" | "http";
  enabled: boolean;
  status: "running" | "stopped" | "crashed" | "error";
  last_error: string | null;
};

export type McpAction = "start" | "stop" | "restart";

export async function listMcpServers(): Promise<McpServer[]> {
  return (await request<{ servers: McpServer[] }>("/api/mcp/servers")).servers;
}

export function controlMcpServer(id: string, action: McpAction): Promise<{ ok: boolean }> {
  return request(`/api/mcp/servers/${encodeURIComponent(id)}/lifecycle`, {
    method: "POST", body: { action },
  });
}
