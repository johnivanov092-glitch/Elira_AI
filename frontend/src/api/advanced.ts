// advanced.ts — project workspace, git, tools, smart memory and terminal API

import { request, safeRequest } from "./client";
import { withParams } from "./apiUtils";

// ── Advanced project ─────────────────────────────────────────────────────────

export async function getAdvancedProjectInfo() {
  return request("/api/advanced/project/info");
}

export async function openAdvancedProject(path: string) {
  return request("/api/advanced/project/open", { method: "POST", body: { path } });
}

export async function getAdvancedProjectTree({ maxDepth = 3, maxItems = 300 } = {}) {
  return request(withParams("/api/advanced/project/tree", { max_depth: maxDepth, max_items: maxItems }));
}

export async function readAdvancedProjectFile(path: string, maxChars?: number) {
  const body: Record<string, unknown> = { path };
  if (maxChars) body.max_chars = maxChars;
  return request("/api/advanced/project/read", { method: "POST", body });
}

export async function searchAdvancedProject(query: string) {
  return request("/api/advanced/project/search", { method: "POST", body: { query } });
}

export async function closeAdvancedProject() {
  return request("/api/advanced/project/close");
}

export async function runAdvancedMultiAgent(body: Record<string, unknown> = {}) {
  return request("/api/advanced/multi-agent", { method: "POST", body });
}

// ── Git ───────────────────────────────────────────────────────────────────────

export async function getGitStatus() {
  return request("/api/git/status");
}

export async function getGitLog(limit = 20) {
  return request(withParams("/api/git/log", { limit }));
}

export async function getGitDiff(body: Record<string, unknown> = { repo_path: "", file_path: "" }) {
  return request("/api/git/diff", { method: "POST", body });
}

export async function createGitCommit(body: Record<string, unknown> = {}) {
  return request("/api/git/commit", { method: "POST", body });
}

// ── Tools ─────────────────────────────────────────────────────────────────────

export async function listToolRuns(limit = 50) {
  const payload = await request(withParams("/api/tools/run-history", { limit })) as Record<string, unknown>;
  return (payload?.runs as unknown[]) || [];
}

export async function runPythonCode(code: string) {
  return request("/api/tools/run-python", { method: "POST", body: { code } });
}

export async function analyzeCode(body: Record<string, unknown> = {}) {
  return request("/api/tools/analyze-code", { method: "POST", body });
}

export async function diffFile(body: Record<string, unknown> = {}) {
  return request("/api/file-ops/diff", { method: "POST", body });
}

export async function writeFile(body: Record<string, unknown> = {}) {
  return request("/api/file-ops/write", { method: "POST", body });
}

// ── Smart memory ──────────────────────────────────────────────────────────────

export async function listSmartMemory(limit = 100) {
  const payload = await request(withParams("/api/smart-memory/list", { limit })) as Record<string, unknown>;
  return (payload?.items as unknown[]) || [];
}

export async function getSmartMemoryStats() {
  return request("/api/smart-memory/stats");
}

export async function addSmartMemory(body: Record<string, unknown> = {}) {
  return request("/api/smart-memory/add", { method: "POST", body });
}

export async function deleteSmartMemory(id: string | number) {
  return request(`/api/smart-memory/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function searchSmartMemory(query: string, limit = 20) {
  const payload = await request("/api/smart-memory/search", { method: "POST", body: { query, limit } }) as Record<string, unknown>;
  return (payload?.items as unknown[]) || [];
}

// ── Terminal ──────────────────────────────────────────────────────────────────

export async function getTerminalCwd() {
  return safeRequest("/api/terminal/cwd", {}, null);
}

export async function executeTerminal(body: Record<string, unknown> = {}) {
  return request("/api/terminal/exec", { method: "POST", body });
}
