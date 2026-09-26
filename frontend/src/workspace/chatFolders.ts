import {
  getChatFolders, initializeChatFolders, patchChatFolders,
  type FolderOperation, type FolderState,
} from "../api/chatFolders";
import { ApiError } from "../api/client";

export type { ChatFolder, FolderState } from "../api/chatFolders";

// SQLite owns the layout across dev/release origins. localStorage is only a
// startup cache and the one-time migration source for older installations.
const KEY = "elira.chatFolders.v1";
const EMPTY: FolderState = { folders: [], assign: {}, collapsed: {} };
let legacyError: unknown;

function validated(value: unknown): FolderState {
  if (!value || typeof value !== "object") throw new Error("Некорректные данные папок");
  const state = value as FolderState;
  const record = (item: unknown): item is Record<string, unknown> =>
    !!item && typeof item === "object" && !Array.isArray(item);
  if (!Array.isArray(state.folders) || !record(state.assign) || !record(state.collapsed)) {
    throw new Error("Некорректные данные папок");
  }
  const ids = new Set<string>();
  for (const folder of state.folders) {
    if (!folder || typeof folder.id !== "string" || !folder.id || ids.has(folder.id)
      || typeof folder.name !== "string" || !folder.name.trim()) {
      throw new Error("Некорректные данные папок");
    }
    ids.add(folder.id);
  }
  if (Object.entries(state.assign).some(([id, folder]) => !id || !ids.has(folder))
    || Object.entries(state.collapsed).some(([id, flag]) => !ids.has(id) || typeof flag !== "boolean")) {
    throw new Error("Некорректные привязки папок");
  }
  return state;
}

function load(): FolderState {
  let raw: string | null;
  try { raw = localStorage.getItem(KEY); } catch { return EMPTY; }
  if (!raw) return EMPTY;
  try { return validated(JSON.parse(raw)); }
  catch (error) { legacyError = error; return EMPTY; }
}

let state = load();
let hydrated = false;
let work: Promise<void> | null = null;
type Pending = { build: () => FolderOperation; operation?: FolderOperation };
const pending: Pending[] = [];
const listeners = new Set<() => void>();
type SyncState = { loading: boolean; error: string | null };
let syncState: SyncState = { loading: false, error: null };

function emit() { for (const listener of listeners) listener(); }
function publish(next: FolderState) {
  state = validated(next);
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* server has saved it */ }
  emit();
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export function getSnapshot(): FolderState { return state; }
export function getSyncSnapshot(): SyncState { return syncState; }

async function hydrate() {
  const saved = await getChatFolders(); // A failed GET must never become an empty PUT.
  if (saved === null && legacyError) throw legacyError;
  publish(saved === null ? await initializeChatFolders(state) : saved);
  hydrated = true;
  legacyError = undefined;
}

function drain(refresh = false): Promise<void> {
  if (work) return work;
  syncState = { loading: true, error: null };
  emit();
  work = (async () => {
    let rejectedMessage: string | null = null;
    try {
      if (!hydrated || refresh) await hydrate();
      while (pending.length) {
        const item = pending[0];
        // Freeze before sending: retrying a timed-out collapse must set the
        // same value, not toggle the server state again.
        item.operation ??= item.build();
        try {
          publish(await patchChatFolders(item.operation));
        } catch (error) {
          if (!(error instanceof ApiError) || ![404, 422].includes(error.status)) throw error;
          // Confirm this is an operation rejection, not an old/unavailable
          // endpoint. A removed folder must not block every later edit.
          const current = await getChatFolders();
          if (current === null) throw error;
          const missing = !current.folders.some((folder) => folder.id === item.operation?.folder_id);
          if (error.status === 404 && !missing) throw error;
          publish(current);
          rejectedMessage = `Изменение папок не применено: ${error.message}`;
        }
        pending.shift();
      }
      syncState = { loading: false, error: rejectedMessage };
    } catch (error) {
      // Retain the failed operation for explicit retry; never present an
      // unacknowledged mutation as saved. All PATCH operations are idempotent.
      syncState = { loading: false, error: error instanceof Error ? error.message : String(error) };
    } finally {
      work = null;
      emit();
    }
  })();
  return work;
}

/** Refresh on connection/retry, then drain any unacknowledged operations. */
export function synchronize(): Promise<void> { return drain(true); }

function enqueue(build: () => FolderOperation): void {
  pending.push({ build });
  void drain();
}

export function createFolder(name: string): string {
  const id = `fld-${crypto.randomUUID()}`;
  enqueue(() => ({ operation: "create", folder_id: id, name: name.trim() || "Папка" }));
  return id;
}
export function renameFolder(id: string, name: string): void {
  const next = name.trim();
  if (next) enqueue(() => ({ operation: "rename", folder_id: id, name: next }));
}
/** Removing a folder only releases its chats; it never deletes sessions. */
export function deleteFolder(id: string): void {
  enqueue(() => ({ operation: "delete", folder_id: id }));
}
export function assignToFolder(sessionId: string, folderId: string | null): void {
  enqueue(() => ({ operation: "assign", session_id: sessionId, folder_id: folderId }));
}
export function toggleCollapsed(id: string): void {
  enqueue(() => ({ operation: "collapse", folder_id: id, collapsed: !state.collapsed[id] }));
}
