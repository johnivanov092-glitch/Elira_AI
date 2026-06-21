import type { CodeAgentToolCall } from "../api/codeAgent";

export type UserTurnData = { kind: "user"; id: string; text: string };

export type PendingApproval = {
  approvalId: string;
  tool: string;
  arguments: Record<string, unknown>;
  waitedS?: number;
  resolving?: boolean;
};

export type AgentTurnData = {
  kind: "agent";
  id: string;
  toolCalls: CodeAgentToolCall[];
  text: string;
  running: boolean;
  activeTool?: string;
  error?: string | null;
  stopReason?: string;
  pendingApproval?: PendingApproval;
};

export type FileEntry = {
  name: string;
  isImage: boolean;
  url?: string;
  status: "uploading" | "saved" | "error";
};

export type FilesTurnData = { kind: "files"; id: string; files: FileEntry[] };

export type Turn = UserTurnData | AgentTurnData | FilesTurnData;
