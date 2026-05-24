// chats.ts — chat management and messaging API

import { request, safeRequest } from "./client";
import { normalizeArray, unwrapItem, normalizeChat, normalizeMessage } from "./apiUtils";

export async function listChats() {
  const payload = await safeRequest("/api/elira/chats", {}, []);
  return normalizeArray(payload).map(item => normalizeChat(item as Record<string, unknown>));
}

export async function createChat(body: Record<string, unknown> = {}) {
  return normalizeChat(unwrapItem(await request("/api/elira/chats", { method: "POST", body })) as Record<string, unknown>);
}

export async function renameChat(arg1: Record<string, unknown> | string, arg2?: string) {
  const payload = typeof arg1 === "object" && arg1 !== null ? arg1 : { id: arg1, title: arg2 };
  return normalizeChat(unwrapItem(await request(`/api/elira/chats/${encodeURIComponent(payload.id as string)}`, {
    method: "PATCH",
    body: { title: payload.title },
  })) as Record<string, unknown>);
}

export async function pinChat(arg1: Record<string, unknown> | string, arg2?: boolean) {
  const payload = typeof arg1 === "object" && arg1 !== null ? arg1 : { id: arg1, pinned: arg2 };
  return normalizeChat(unwrapItem(await request(`/api/elira/chats/${encodeURIComponent(payload.id as string)}/pin`, {
    method: "PATCH",
    body: { pinned: Boolean(payload.pinned) },
  })) as Record<string, unknown>);
}

export async function saveChatToMemory(arg1: Record<string, unknown> | string, arg2?: boolean) {
  const payload = typeof arg1 === "object" && arg1 !== null ? arg1 : { id: arg1, saved: arg2 };
  return normalizeChat(unwrapItem(await request(`/api/elira/chats/${encodeURIComponent(payload.id as string)}/memory`, {
    method: "PATCH",
    body: { memory_saved: Boolean(payload.saved) },
  })) as Record<string, unknown>);
}

export async function deleteChat(arg: Record<string, unknown> | string) {
  const id = typeof arg === "object" && arg !== null ? arg.id : arg;
  return request(`/api/elira/chats/${encodeURIComponent(id as string)}`, { method: "DELETE" });
}

export async function getMessages(arg: Record<string, unknown> | string) {
  const chatId = typeof arg === "object" && arg !== null ? arg.chatId : arg;
  const payload = await safeRequest(`/api/elira/chats/${encodeURIComponent(chatId as string)}/messages`, {}, []);
  return normalizeArray(payload).map(item => normalizeMessage(item as Record<string, unknown>));
}

export async function addMessage(body: Record<string, unknown> = {}) {
  const payload = await request("/api/elira/messages", {
    method: "POST",
    body: {
      chat_id: (body.chatId ?? body.chat_id ?? null) as string | null,
      role: (body.role ?? "user") as string,
      content: typeof body.content === "string" ? body.content : String(body.content ?? ""),
    },
  }) as Record<string, unknown>;
  const message = normalizeMessage(unwrapItem((payload?.message ?? payload) as unknown) as Record<string, unknown>);
  return {
    ...message,
    ...payload,
    chat_id: payload?.chat_id ?? body.chatId ?? body.chat_id ?? null,
    message,
  };
}

export async function sendMessage(body: Record<string, unknown> = {}) {
  const payload = await addMessage(body);
  return (payload as Record<string, unknown>).message;
}
