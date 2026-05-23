// chats.js — chat management and messaging API

import { request, safeRequest } from "./client";
import { normalizeArray, unwrapItem, normalizeChat, normalizeMessage } from "./apiUtils";

export async function listChats() {
  const payload = await safeRequest("/api/elira/chats", {}, []);
  return normalizeArray(payload).map(normalizeChat);
}

export async function createChat(body = {}) {
  return normalizeChat(unwrapItem(await request("/api/elira/chats", { method: "POST", body })));
}

export async function renameChat(arg1, arg2) {
  const payload = typeof arg1 === "object" && arg1 !== null ? arg1 : { id: arg1, title: arg2 };
  return normalizeChat(unwrapItem(await request(`/api/elira/chats/${encodeURIComponent(payload.id)}`, {
    method: "PATCH",
    body: { title: payload.title },
  })));
}

export async function pinChat(arg1, arg2) {
  const payload = typeof arg1 === "object" && arg1 !== null ? arg1 : { id: arg1, pinned: arg2 };
  return normalizeChat(unwrapItem(await request(`/api/elira/chats/${encodeURIComponent(payload.id)}/pin`, {
    method: "PATCH",
    body: { pinned: Boolean(payload.pinned) },
  })));
}

export async function saveChatToMemory(arg1, arg2) {
  const payload = typeof arg1 === "object" && arg1 !== null ? arg1 : { id: arg1, saved: arg2 };
  return normalizeChat(unwrapItem(await request(`/api/elira/chats/${encodeURIComponent(payload.id)}/memory`, {
    method: "PATCH",
    body: { memory_saved: Boolean(payload.saved) },
  })));
}

export async function deleteChat(arg) {
  const id = typeof arg === "object" && arg !== null ? arg.id : arg;
  return request(`/api/elira/chats/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function getMessages(arg) {
  const chatId = typeof arg === "object" && arg !== null ? arg.chatId : arg;
  const payload = await safeRequest(`/api/elira/chats/${encodeURIComponent(chatId)}/messages`, {}, []);
  return normalizeArray(payload).map(normalizeMessage);
}

export async function addMessage(body = {}) {
  const payload = await request("/api/elira/messages", {
    method: "POST",
    body: {
      chat_id: body.chatId ?? body.chat_id ?? null,
      role: body.role ?? "user",
      content: typeof body.content === "string" ? body.content : String(body.content ?? ""),
    },
  });
  const message = normalizeMessage(unwrapItem(payload?.message ?? payload));
  return {
    ...message,
    ...payload,
    chat_id: payload?.chat_id ?? body.chatId ?? body.chat_id ?? null,
    message,
  };
}

export async function sendMessage(body = {}) {
  const payload = await addMessage(body);
  return payload.message;
}
