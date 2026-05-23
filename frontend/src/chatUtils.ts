// chatUtils.ts — pure utility functions for Elira Chat UI

import { LIBRARY_KEY, CHAT_CONTEXT_KEY, MAX_HISTORY_PAIRS } from "./chatConstants";

// ── LocalStorage helpers ────────────────────────────────────────────────────

export function loadJson<T>(k: string, f: T): T {
  try { return JSON.parse(localStorage.getItem(k) || JSON.stringify(f)) as T; } catch { return f; }
}

export function saveJson(k: string, v: unknown): void {
  try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { console.warn("localStorage quota exceeded:", e); }
}

export function loadLibraryFiles(): unknown[] { return loadJson<unknown[]>(LIBRARY_KEY, []); }
export function saveLibraryFiles(i: unknown[]): void { saveJson(LIBRARY_KEY, i); }
export function loadChatContextMap(): Record<string, string[]> { return loadJson<Record<string, string[]>>(CHAT_CONTEXT_KEY, {}); }
export function saveChatContextMap(v: Record<string, string[]>): void { saveJson(CHAT_CONTEXT_KEY, v); }

// ── ID / title helpers ──────────────────────────────────────────────────────

export function makeId(p = "id"): string {
  return `${p}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function deriveChatTitle(t: unknown): string {
  const c = String(t || "").trim().replace(/\s+/g, " ");
  return !c ? "Новый чат" : c.length > 28 ? `${c.slice(0, 28)}…` : c;
}

// ── String / display helpers ────────────────────────────────────────────────

export function shortModelName(name: string | null | undefined): string {
  if (!name) return "model";
  if (name.toLowerCase().includes("yandex")) return "YandexGPT";
  return name;
}

export function normalizeErrorMessage(e: unknown, fb = "Ошибка"): string {
  const v = (e as Record<string, unknown>)?.message ?? (e as Record<string, unknown>)?.detail ?? e;
  if (!v) return fb;
  if (typeof v === "string") return v;
  if (Array.isArray(v)) return v.map((i) => normalizeErrorMessage(i, "")).filter(Boolean).join(" | ") || fb;
  if (typeof v === "object") {
    const obj = v as Record<string, unknown>;
    return (obj.message as string) || (obj.msg as string) || JSON.stringify(v);
  }
  return String(v);
}

export function humanizeValue(value: unknown): string {
  return String(value || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

// ── Capability / status label helpers ──────────────────────────────────────

export function capabilityLabel(key: string): string {
  return ({
    vector_memory: "Векторная память",
    screenshot: "Скриншоты",
  } as Record<string, string>)[key] || humanizeValue(key);
}

export function capabilityStateText(capability: Record<string, unknown> = {}): string {
  if (capability.available) return "Доступно";
  if (capability.reason === "optional_dependency_missing") return "Не хватает модулей";
  if (capability.reason) return humanizeValue(capability.reason);
  return "Недоступно";
}

export function capabilityModeText(mode: string): string {
  return ({
    keyword_fallback: "Резервный поиск по ключевым словам",
  } as Record<string, string>)[mode] || humanizeValue(mode);
}

export function runtimeStorageModeText(mode: string): string {
  return ({
    rooted_sqlite: "Корневой data/",
    rooted_sqlite_with_legacy_archive: "Корневой data/ + legacy archive",
    custom_data_dir: "Пользовательский data dir",
    unknown: "Неизвестно",
  } as Record<string, string>)[mode] || humanizeValue(mode);
}

export function engineListText(items: unknown): string {
  if (!Array.isArray(items) || !items.length) return "—";
  return items.map((item) => humanizeValue(item)).join(", ");
}

export function yesNoText(value: unknown): string {
  return value ? "Да" : "Нет";
}

export function formatDurationMs(value: unknown): string {
  const ms = Number(value || 0);
  if (!ms) return "0 мс";
  if (ms >= 1000) return `${(ms / 1000).toFixed(ms >= 10000 ? 0 : 1)} с`;
  return `${ms} мс`;
}

// ── Chat history helper ─────────────────────────────────────────────────────

export interface HistoryMessage {
  role: string;
  content: string;
}

export function buildHistory(msgs: Array<Record<string, unknown>> | null | undefined): HistoryMessage[] {
  if (!msgs?.length) return [];
  const p = msgs
    .filter((m) => m.role === "user" || m.role === "assistant")
    .map((m) => ({ role: m.role as string, content: (m.content as string) || "" }));
  return p.length > MAX_HISTORY_PAIRS * 2 ? p.slice(-MAX_HISTORY_PAIRS * 2) : p;
}
