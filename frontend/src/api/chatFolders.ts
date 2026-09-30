import { ApiError, normalizeError, request } from "./client";

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

export type FolderArchive = { blob: Blob; filename: string; chatCount: number };

/** Fetch the folder's ZIP archive (one Markdown file per chat of the folder). */
export async function fetchFolderArchive(folderId: string): Promise<FolderArchive> {
  const response = await request(`${PATH}/${encodeURIComponent(folderId)}/archive`, {
    raw: true,
    timeoutMs: 60_000,
  });
  if (!response.ok) {
    const text = await response.text();
    let detail: string;
    try { detail = normalizeError(JSON.parse(text), response.status); }
    catch { detail = normalizeError(text, response.status); }
    throw new ApiError(detail, response.status);
  }
  const blob = await response.blob();
  const filename = filenameFromDisposition(response.headers.get("content-disposition")) ?? `${folderId}.zip`;
  const chatCount = Number(response.headers.get("x-chat-count") || 0);
  return { blob, filename, chatCount };
}

/** Read the attachment file name from Content-Disposition (RFC 5987 first). */
function filenameFromDisposition(disposition: string | null): string | null {
  if (!disposition) return null;
  const star = /filename\*=[Uu][Tt][Ff]-8''([^;]+)/.exec(disposition);
  if (star) {
    try { return decodeURIComponent(star[1]); } catch { /* use the plain fallback */ }
  }
  const plain = /filename="([^"]+)"/.exec(disposition);
  return plain ? plain[1] : null;
}
