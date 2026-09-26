import { request } from "./client";

export type ChatFolder = { id: string; name: string };
export type FolderState = {
  folders: ChatFolder[];
  assign: Record<string, string>;
  collapsed: Record<string, boolean>;
};
export type FolderOperation =
  | { operation: "create" | "rename"; folder_id: string; name: string }
  | { operation: "delete"; folder_id: string }
  | { operation: "assign"; session_id: string; folder_id: string | null }
  | { operation: "collapse"; folder_id: string; collapsed: boolean };

const PATH = "/api/code-agent/chat-folders";

export async function getChatFolders(): Promise<FolderState | null> {
  const result = await request<{ ok: boolean; state: FolderState | null }>(PATH, { timeoutMs: 10_000 });
  return result.state;
}

export async function initializeChatFolders(state: FolderState): Promise<FolderState> {
  const result = await request<{ ok: boolean; state: FolderState }>(PATH, {
    method: "PUT", body: { state }, timeoutMs: 10_000,
  });
  return result.state;
}

export async function patchChatFolders(operation: FolderOperation): Promise<FolderState> {
  const result = await request<{ ok: boolean; state: FolderState }>(PATH, {
    method: "PATCH", body: operation, timeoutMs: 10_000,
  });
  return result.state;
}
