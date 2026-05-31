import { request } from "./client";

type QueryParam = string | number | boolean | null | undefined;

export type TelegramResponse = Record<string, unknown>;

export type TelegramConfig = TelegramResponse;
export type TelegramUser = Record<string, unknown>;
export type TelegramLogItem = Record<string, unknown>;

export type TelegramConfigUpdate = {
  bot_token?: string;
  allowed_users?: string;
  [key: string]: unknown;
};

export type TelegramUserToggleRequest = {
  chat_id?: string | number;
  allowed?: boolean;
  [key: string]: unknown;
};

export type TelegramOverview = {
  config: TelegramConfig;
  users: TelegramUser[];
  log: TelegramLogItem[];
};

type TelegramUsersPayload = {
  users?: TelegramUser[];
  [key: string]: unknown;
};

type TelegramLogPayload = {
  log?: TelegramLogItem[];
  [key: string]: unknown;
};

function withParams(
  path: string,
  params: Record<string, QueryParam> = {},
): string {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    query.set(key, String(value));
  });
  const suffix = query.toString();
  return suffix ? `${path}?${suffix}` : path;
}

function errorMessage(payload: TelegramResponse, fallback: string): string {
  const error = payload.error;
  return typeof error === "string" && error.trim() ? error : fallback;
}

export async function getTelegramConfig(): Promise<TelegramConfig> {
  return request<TelegramConfig>("/api/telegram/config");
}

export async function listTelegramUsers(): Promise<TelegramUsersPayload> {
  return request<TelegramUsersPayload>("/api/telegram/users");
}

export async function getTelegramLog(limit = 30): Promise<TelegramLogPayload> {
  return request<TelegramLogPayload>(withParams("/api/telegram/log", { limit }));
}

export async function getTelegramOverview(
  limit = 30,
): Promise<TelegramOverview> {
  const [config, users, log] = await Promise.all([
    getTelegramConfig(),
    listTelegramUsers(),
    getTelegramLog(limit),
  ]);
  return {
    config,
    users: Array.isArray(users.users) ? users.users : [],
    log: Array.isArray(log.log) ? log.log : [],
  };
}

export async function startTelegramBot(): Promise<TelegramResponse> {
  const payload = await request<TelegramResponse>("/api/telegram/start", {
    method: "POST",
  });
  if (payload.ok === false) {
    throw new Error(errorMessage(payload, "Failed to start Telegram bot"));
  }
  return payload;
}

export async function stopTelegramBot(): Promise<TelegramResponse> {
  return request<TelegramResponse>("/api/telegram/stop", { method: "POST" });
}

export async function testTelegramBot(): Promise<TelegramResponse> {
  return request<TelegramResponse>("/api/telegram/test");
}

export async function updateTelegramConfig(
  body: TelegramConfigUpdate = {},
): Promise<TelegramResponse> {
  return request<TelegramResponse>("/api/telegram/config", {
    method: "PUT",
    body,
  });
}

export async function toggleTelegramUser(
  body: TelegramUserToggleRequest = {},
): Promise<TelegramResponse> {
  return request<TelegramResponse>("/api/telegram/users/toggle", {
    method: "POST",
    body,
  });
}
