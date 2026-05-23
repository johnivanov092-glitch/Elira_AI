// chatUtils.js — pure utility functions for Elira Chat UI

import { LIBRARY_KEY, CHAT_CONTEXT_KEY, MAX_HISTORY_PAIRS } from "./chatConstants";

// ── LocalStorage helpers ────────────────────────────────────────────────────

export function loadJson(k, f) {
  try { return JSON.parse(localStorage.getItem(k) || JSON.stringify(f)); } catch { return f; }
}

export function saveJson(k, v) {
  try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { console.warn("localStorage quota exceeded:", e); }
}

export function loadLibraryFiles() { return loadJson(LIBRARY_KEY, []); }
export function saveLibraryFiles(i) { saveJson(LIBRARY_KEY, i); }
export function loadChatContextMap() { return loadJson(CHAT_CONTEXT_KEY, {}); }
export function saveChatContextMap(v) { saveJson(CHAT_CONTEXT_KEY, v); }

// ── ID / title helpers ──────────────────────────────────────────────────────

export function makeId(p = "id") {
  return `${p}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function deriveChatTitle(t) {
  const c = String(t || "").trim().replace(/\s+/g, " ");
  return !c ? "Новый чат" : c.length > 28 ? `${c.slice(0, 28)}…` : c;
}

// ── String / display helpers ────────────────────────────────────────────────

export function shortModelName(name) {
  if (!name) return "model";
  if (name.toLowerCase().includes("yandex")) return "YandexGPT";
  return name;
}

export function normalizeErrorMessage(e, fb = "Ошибка") {
  const v = e?.message ?? e?.detail ?? e;
  if (!v) return fb;
  if (typeof v === "string") return v;
  if (Array.isArray(v)) return v.map(i => normalizeErrorMessage(i, "")).filter(Boolean).join(" | ") || fb;
  if (typeof v === "object") return v.message || v.msg || JSON.stringify(v);
  return String(v);
}

export function humanizeValue(value) {
  return String(value || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

// ── Capability / status label helpers ──────────────────────────────────────

export function capabilityLabel(key) {
  return {
    vector_memory: "Векторная память",
    screenshot: "Скриншоты",
  }[key] || humanizeValue(key);
}

export function capabilityStateText(capability = {}) {
  if (capability.available) return "Доступно";
  if (capability.reason === "optional_dependency_missing") return "Не хватает модулей";
  if (capability.reason) return humanizeValue(capability.reason);
  return "Недоступно";
}

export function capabilityModeText(mode) {
  return {
    keyword_fallback: "Резервный поиск по ключевым словам",
  }[mode] || humanizeValue(mode);
}

export function runtimeStorageModeText(mode) {
  return {
    rooted_sqlite: "Корневой data/",
    rooted_sqlite_with_legacy_archive: "Корневой data/ + legacy archive",
    custom_data_dir: "Пользовательский data dir",
    unknown: "Неизвестно",
  }[mode] || humanizeValue(mode);
}

export function engineListText(items) {
  if (!Array.isArray(items) || !items.length) return "—";
  return items.map((item) => humanizeValue(item)).join(", ");
}

export function yesNoText(value) {
  return value ? "Да" : "Нет";
}

export function formatDurationMs(value) {
  const ms = Number(value || 0);
  if (!ms) return "0 мс";
  if (ms >= 1000) return `${(ms / 1000).toFixed(ms >= 10000 ? 0 : 1)} с`;
  return `${ms} мс`;
}

// ── Chat history helper ─────────────────────────────────────────────────────

export function buildHistory(msgs) {
  if (!msgs?.length) return [];
  const p = msgs
    .filter(m => m.role === "user" || m.role === "assistant")
    .map(m => ({ role: m.role, content: m.content || "" }));
  return p.length > MAX_HISTORY_PAIRS * 2 ? p.slice(-MAX_HISTORY_PAIRS * 2) : p;
}
