import { API_TOKEN, buildApiUrl, request } from "./client";

/** A shell tab of the Console panel; Elira types into the same tabs (run_bash terminal=…). */
export type TerminalSession = {
  id: string;
  profile: string;
  title: string;
  cwd: string;
  busy: boolean;
  alive: boolean;
  active: boolean;
};

export type TerminalProfile = { id: string; title: string };

export type TerminalState = { sessions: TerminalSession[]; profiles: TerminalProfile[] };

export function listTerminals(): Promise<TerminalState> {
  return request<TerminalState>("/api/terminal/sessions");
}

export function openTerminal(profile: string, cwd: string, cols: number, rows: number): Promise<TerminalState & { session: TerminalSession }> {
  return request("/api/terminal/sessions", { method: "POST", body: { profile, cwd, cols, rows } });
}

export function closeTerminal(id: string): Promise<TerminalState> {
  return request(`/api/terminal/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function focusTerminal(id: string): Promise<TerminalState> {
  return request("/api/terminal/active", { method: "POST", body: { id } });
}

/** ws:// address of a tab; a browser cannot set headers on a WebSocket, so the token rides in the query. */
export function terminalSocketUrl(id: string): string {
  const url = new URL(buildApiUrl(`/api/terminal/sessions/${encodeURIComponent(id)}/ws`));
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  if (API_TOKEN) url.searchParams.set("token", API_TOKEN);
  return url.toString();
}
