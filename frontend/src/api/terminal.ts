import { request, safeRequest } from "./client";

export type TerminalCwdResponse = {
  cwd?: string;
  ok?: boolean;
  [key: string]: unknown;
};

export type ExecuteTerminalRequest = {
  command?: string;
  cwd?: string | null;
  timeout?: number;
  [key: string]: unknown;
};

export type ExecuteTerminalResponse = {
  cwd?: string;
  error?: string;
  ok?: boolean;
  stdout?: string;
  stderr?: string;
  output?: string;
  returncode?: number;
  [key: string]: unknown;
};

export async function getTerminalCwd(): Promise<TerminalCwdResponse | null> {
  return safeRequest<TerminalCwdResponse | null>("/api/terminal/cwd", {}, null);
}

export async function executeTerminal(
  body: ExecuteTerminalRequest = {},
): Promise<ExecuteTerminalResponse> {
  return request<ExecuteTerminalResponse>("/api/terminal/exec", {
    method: "POST",
    body,
  });
}
