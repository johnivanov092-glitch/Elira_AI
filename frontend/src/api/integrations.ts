// integrations.ts — Telegram bot and plugins API

import { request } from "./client";
import { withParams } from "./apiUtils";

// ── Telegram ────────────────────────────────────────────────────────────────

export async function getTelegramConfig() {
  return request("/api/telegram/config");
}

export async function listTelegramUsers() {
  return request("/api/telegram/users");
}

export async function getTelegramLog(limit = 30) {
  return request(withParams("/api/telegram/log", { limit }));
}

export async function getTelegramOverview(limit = 30) {
  const [config, users, log] = await Promise.all([
    getTelegramConfig(),
    listTelegramUsers(),
    getTelegramLog(limit),
  ]) as [Record<string, unknown>, Record<string, unknown>, Record<string, unknown>];
  return {
    config,
    users: (users?.users as unknown[]) || [],
    log: (log?.log as unknown[]) || [],
  };
}

export async function startTelegramBot() {
  const payload = await request("/api/telegram/start", { method: "POST" }) as Record<string, unknown>;
  if (payload?.ok === false) throw new Error((payload.error as string) || "Failed to start Telegram bot");
  return payload;
}

export async function stopTelegramBot() {
  return request("/api/telegram/stop", { method: "POST" });
}

export async function testTelegramBot() {
  return request("/api/telegram/test");
}

export async function updateTelegramConfig(body: Record<string, unknown> = {}) {
  return request("/api/telegram/config", { method: "PUT", body });
}

export async function toggleTelegramUser(body: Record<string, unknown> = {}) {
  return request("/api/telegram/users/toggle", { method: "POST", body });
}

// ── Plugins ──────────────────────────────────────────────────────────────────

export async function listPlugins() {
  const payload = await request("/api/extra/plugins/list") as Record<string, unknown>;
  return (payload?.plugins as unknown[]) || [];
}

export async function reloadPlugins() {
  return request("/api/extra/plugins/reload", { method: "POST" });
}

export async function setPluginEnabled(name: string, enabled: boolean) {
  const action = enabled ? "enable" : "disable";
  return request(`/api/extra/plugins/${action}/${encodeURIComponent(name)}`, { method: "POST" });
}
