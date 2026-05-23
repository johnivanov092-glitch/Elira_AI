// advanced.js — project workspace, git, tools, smart memory and terminal API

import { request, safeRequest } from "./client";
import { withParams } from "./apiUtils";

// ── Advanced project ─────────────────────────────────────────────────────────

export async function getAdvancedProjectInfo() {
  return request("/api/advanced/project/info");
}

export async function openAdvancedProject(path) {
  return request("/api/advanced/project/open", { method: "POST", body: { path } });
}

export async function getAdvancedProjectTree({ maxDepth = 3, maxItems = 300 } = {}) {
  return request(withParams("/api/advanced/project/tree", { max_depth: maxDepth, max_items: maxItems }));
}

export async function readAdvancedProjectFile(path, maxChars) {
  const body = { path };
  if (maxChars) body.max_chars = maxChars;
  return request("/api/advanced/project/read", { method: "POST", body });
}

export async function searchAdvancedProject(query) {
  return request("/api/advanced/project/search", { method: "POST", body: { query } });
}

export async function closeAdvancedProject() {
  return request("/api/advanced/project/close");
}

export async function runAdvancedMultiAgent(body = {}) {
  return request("/api/advanced/multi-agent", { method: "POST", body });
}

// ── Git ───────────────────────────────────────────────────────────────────────

export async function getGitStatus() {
  return request("/api/git/status");
}

export async function getGitLog(limit = 20) {
  return request(withParams("/api/git/log", { limit }));
}

export async function getGitDiff(body = { repo_path: "", file_path: "" }) {
  return request("/api/git/diff", { method: "POST", body });
}

export async function createGitCommit(body = {}) {
  return request("/api/git/commit", { method: "POST", body });
}

// ── Tools ─────────────────────────────────────────────────────────────────────

export async function listToolRuns(limit = 50) {
  const payload = await request(withParams("/api/tools/run-history", { limit }));
  return payload?.runs || [];
}

export async function runPythonCode(code) {
  return request("/api/tools/run-python", { method: "POST", body: { code } });
}

export async function analyzeCode(body = {}) {
  return request("/api/tools/analyze-code", { method: "POST", body });
}

export async function diffFile(body = {}) {
  return request("/api/file-ops/diff", { method: "POST", body });
}

export async function writeFile(body = {}) {
  return request("/api/file-ops/write", { method: "POST", body });
}

// ── Smart memory ──────────────────────────────────────────────────────────────

export async function listSmartMemory(limit = 100) {
  const payload = await request(withParams("/api/smart-memory/list", { limit }));
  return payload?.items || [];
}

export async function getSmartMemoryStats() {
  return request("/api/smart-memory/stats");
}

export async function addSmartMemory(body = {}) {
  return request("/api/smart-memory/add", { method: "POST", body });
}

export async function deleteSmartMemory(id) {
  return request(`/api/smart-memory/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function searchSmartMemory(query, limit = 20) {
  const payload = await request("/api/smart-memory/search", { method: "POST", body: { query, limit } });
  return payload?.items || [];
}

// ── Terminal ──────────────────────────────────────────────────────────────────

export async function getTerminalCwd() {
  return safeRequest("/api/terminal/cwd", {}, null);
}

export async function executeTerminal(body = {}) {
  return request("/api/terminal/exec", { method: "POST", body });
}
