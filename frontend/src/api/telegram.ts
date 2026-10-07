import { request } from "./client";

export type TelegramStatus = {
  ok: boolean;
  running: boolean;
  stopping: boolean;
  has_token: boolean;
  token_preview: string;
  legacy_token_present: boolean;
  bot_username?: string;
};

export type TelegramUser = {
  chat_id: number;
  username?: string | null;
  first_name?: string | null;
  allowed: number | boolean;
};

export function getTelegramStatus(): Promise<TelegramStatus> {
  return request<TelegramStatus>("/api/telegram/status");
}

export function setTelegramToken(botTokenRef: string): Promise<TelegramStatus> {
  return request<TelegramStatus>("/api/telegram/config", { method: "POST", body: { bot_token_ref: botTokenRef } });
}

export function startTelegramBot(): Promise<TelegramStatus> {
  return request<TelegramStatus>("/api/telegram/start", { method: "POST" });
}

export function stopTelegramBot(): Promise<TelegramStatus> {
  return request<TelegramStatus>("/api/telegram/stop", { method: "POST" });
}

export function testTelegramBot(): Promise<{ ok: boolean; bot_username: string; bot_name: string }> {
  return request("/api/telegram/test", { method: "POST" });
}

export async function listTelegramUsers(): Promise<TelegramUser[]> {
  const res = await request<{ ok: boolean; users: TelegramUser[] }>("/api/telegram/users");
  return res.users ?? [];
}

export function setTelegramUserAccess(chatId: number, allowed: boolean): Promise<unknown> {
  return request(`/api/telegram/users/${encodeURIComponent(String(chatId))}`, { method: "POST", body: { allowed } });
}
