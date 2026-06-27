// Клиентский стор папок чатов. Бэкенд не знает про папки (у CodeSessionMeta нет
// поля folder), поэтому раскладку «чат → папка» и сами папки храним локально в
// localStorage. Подписка — через useSyncExternalStore, как у backgroundRuns.

const KEY = "elira.chatFolders.v1";

export type ChatFolder = { id: string; name: string };

type FolderState = {
  /** Папки в порядке создания. */
  folders: ChatFolder[];
  /** sessionId -> folderId. Сессии без записи лежат вне папок. */
  assign: Record<string, string>;
  /** id свёрнутых папок (по умолчанию все раскрыты). */
  collapsed: Record<string, boolean>;
};

const EMPTY: FolderState = { folders: [], assign: {}, collapsed: {} };

let _state: FolderState = load();
const _listeners = new Set<() => void>();

function load(): FolderState {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return EMPTY;
    const parsed = JSON.parse(raw) as Partial<FolderState>;
    return {
      folders: Array.isArray(parsed.folders) ? parsed.folders : [],
      assign: parsed.assign && typeof parsed.assign === "object" ? parsed.assign : {},
      collapsed: parsed.collapsed && typeof parsed.collapsed === "object" ? parsed.collapsed : {},
    };
  } catch {
    return EMPTY;
  }
}

function persist() {
  try { localStorage.setItem(KEY, JSON.stringify(_state)); } catch { /* quota/private mode */ }
}

function emit() {
  persist();
  for (const l of _listeners) l();
}

export function subscribe(listener: () => void): () => void {
  _listeners.add(listener);
  return () => { _listeners.delete(listener); };
}

export function getSnapshot(): FolderState {
  return _state;
}

let _seq = 0;
const newFolderId = () => `fld-${Date.now()}-${++_seq}`;

export function createFolder(name: string): string {
  const id = newFolderId();
  _state = { ..._state, folders: [..._state.folders, { id, name: name.trim() || "Папка" }] };
  emit();
  return id;
}

export function renameFolder(id: string, name: string) {
  const next = name.trim();
  if (!next) return;
  _state = {
    ..._state,
    folders: _state.folders.map((f) => (f.id === id ? { ...f, name: next } : f)),
  };
  emit();
}

/** Удаляет папку; вложенные чаты выпадают наружу (не удаляются). */
export function deleteFolder(id: string) {
  const assign = { ..._state.assign };
  for (const sid of Object.keys(assign)) if (assign[sid] === id) delete assign[sid];
  const collapsed = { ..._state.collapsed };
  delete collapsed[id];
  _state = { folders: _state.folders.filter((f) => f.id !== id), assign, collapsed };
  emit();
}

/** Кладёт чат в папку (folderId=null — вынуть наружу). */
export function assignToFolder(sessionId: string, folderId: string | null) {
  const assign = { ..._state.assign };
  if (folderId) assign[sessionId] = folderId;
  else delete assign[sessionId];
  _state = { ..._state, assign };
  emit();
}

export function toggleCollapsed(id: string) {
  _state = { ..._state, collapsed: { ..._state.collapsed, [id]: !_state.collapsed[id] } };
  emit();
}
