import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  Check, ChevronDown, ChevronRight, FolderPlus, MessageSquare, Pencil, Pin, PinOff, Plus, Trash2, X,
} from "lucide-react";
import type { CodeSessionMeta } from "../api/codeAgent";
import { EliraMark } from "../ui/EliraMark";
import * as bg from "./backgroundRuns";
import * as folders from "./chatFolders";

/** Left rail: "New chat" + the real conversation list (per v4 layout).
 *  Sessions come from /api/code-agent/sessions. Folders are client-side
 *  (localStorage via chatFolders). Render order: folders → pinned → plain. */
export function Sidebar({
  connected, sessions, activeId, onNew, onSelect, onDelete, onRename, onTogglePin,
}: {
  connected: boolean;
  sessions: CodeSessionMeta[];
  activeId: string | null;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onTogglePin: (id: string) => void;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Папки + раскладка из localStorage-стора (вне React, как backgroundRuns).
  const fState = useSyncExternalStore(folders.subscribe, folders.getSnapshot);

  // Создание новой папки: показываем inline-поле ввода имени.
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [folderDraft, setFolderDraft] = useState("");
  const folderInputRef = useRef<HTMLInputElement | null>(null);

  // id папки под курсором при drag — для подсветки drop-таргета.
  const [dropTarget, setDropTarget] = useState<string | null>(null);

  useEffect(() => {
    if (editingId) inputRef.current?.focus();
  }, [editingId]);
  useEffect(() => {
    if (creatingFolder) folderInputRef.current?.focus();
  }, [creatingFolder]);

  function beginEdit(s: CodeSessionMeta) {
    setEditingId(s.id);
    setDraft(s.title || "");
  }
  function commit() {
    if (editingId) {
      const next = draft.trim();
      if (next) onRename(editingId, next);
    }
    setEditingId(null);
  }
  function cancel() {
    setEditingId(null);
  }

  function commitFolder() {
    const name = folderDraft.trim();
    if (name) folders.createFolder(name);
    setFolderDraft("");
    setCreatingFolder(false);
  }

  // Разбивка сессий: какие в папках, какие закреплены, какие простые.
  const inFolder = (id: string) => fState.assign[id];
  const loose = sessions.filter((s) => !inFolder(s.id));
  const pinned = loose.filter((s) => s.pinned);
  const plain = loose.filter((s) => !s.pinned);

  // Общие пропсы для строки чата — чтобы не дублировать в каждой группе.
  const rowProps = {
    activeId, editingId, draft, inputRef,
    onSelect, onDelete, onRename, onTogglePin,
    beginEdit, commit, cancel, setDraft,
  };

  return (
    <aside className="flex min-h-0 flex-col border-r border-line bg-side">
      <div className="flex items-center gap-2.5 px-4 pb-2.5 pt-3.5 text-[15px] font-medium">
        <EliraMark className="h-[22px] w-[22px] shrink-0" />
        Elira
      </div>

      <button
        type="button"
        onClick={onNew}
        className="mx-3 mb-3 flex items-center justify-center gap-2 rounded-lg border border-acl bg-acs px-3 py-2 text-[13px] font-medium text-ac transition-colors hover:brightness-110"
      >
        <Plus size={15} /> Новый чат
      </button>

      <div className="min-h-0 flex-1 overflow-auto px-2">
        <div className="flex items-center justify-between px-2 pb-1 pt-1.5">
          <span className="text-[10px] font-medium uppercase tracking-wider text-mut">Диалоги</span>
          <button
            type="button"
            onClick={() => { setFolderDraft(""); setCreatingFolder(true); }}
            aria-label="Новая папка"
            title="Новая папка"
            className="grid h-5 w-5 place-items-center rounded text-mut transition-colors hover:text-tx"
          >
            <FolderPlus size={13} />
          </button>
        </div>

        {creatingFolder && (
          <div className="mb-1 flex items-center gap-1.5 px-2 py-1">
            <FolderPlus size={14} className="shrink-0 text-mut" />
            <input
              ref={folderInputRef}
              value={folderDraft}
              onChange={(e) => setFolderDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") commitFolder();
                else if (e.key === "Escape") { setFolderDraft(""); setCreatingFolder(false); }
              }}
              onBlur={commitFolder}
              placeholder="Название папки"
              className="min-w-0 flex-1 rounded border border-acl bg-bg px-1.5 py-0.5 text-[12.5px] text-tx outline-none"
            />
          </div>
        )}

        {/* 1. Папки */}
        {fState.folders.map((f) => {
          const items = sessions.filter((s) => inFolder(s.id) === f.id);
          const collapsed = !!fState.collapsed[f.id];
          return (
            <FolderRow
              key={f.id}
              folder={f}
              count={items.length}
              collapsed={collapsed}
              isDropTarget={dropTarget === f.id}
              onDragOver={(e) => { e.preventDefault(); setDropTarget(f.id); }}
              onDragLeave={() => setDropTarget((t) => (t === f.id ? null : t))}
              onDrop={(e) => {
                e.preventDefault();
                const sid = e.dataTransfer.getData("text/elira-session");
                if (sid) folders.assignToFolder(sid, f.id);
                setDropTarget(null);
              }}
            >
              {!collapsed && (items.length === 0
                ? <div className="px-2 py-1 pl-7 text-[11.5px] text-mut">Перетащи сюда чат</div>
                : items.map((s) => <ChatRow key={s.id} s={s} indent {...rowProps} />)
              )}
            </FolderRow>
          );
        })}

        {/* 2. Закреплённые (сразу под папками) */}
        {pinned.map((s) => <ChatRow key={s.id} s={s} {...rowProps} />)}

        {/* 3. Простые */}
        {plain.map((s) => <ChatRow key={s.id} s={s} {...rowProps} />)}

        {sessions.length === 0 && fState.folders.length === 0 && (
          <div className="px-2 py-2 text-[12px] text-mut">Пока пусто — начни новый чат.</div>
        )}
      </div>

      <div className="flex items-center gap-2 border-t border-line px-3.5 py-2.5 text-[11.5px] text-t2">
        <span className={cnDot(connected)} />
        {connected ? "подключено" : "подключение…"}
      </div>
    </aside>
  );
}

/** Папка-заголовок: имя, счётчик, сворачивание, переименование, удаление,
 *  drop-зона. Дети (вложенные чаты) рендерятся через children. */
function FolderRow({
  folder, count, collapsed, isDropTarget, onDragOver, onDragLeave, onDrop, children,
}: {
  folder: folders.ChatFolder;
  count: number;
  collapsed: boolean;
  isDropTarget: boolean;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;
  children: React.ReactNode;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(folder.name);
  const ref = useRef<HTMLInputElement | null>(null);
  useEffect(() => { if (editing) ref.current?.focus(); }, [editing]);

  function commit() {
    const next = draft.trim();
    if (next) folders.renameFolder(folder.id, next);
    setEditing(false);
  }

  return (
    <div onDragOver={onDragOver} onDragLeave={onDragLeave} onDrop={onDrop}>
      <div
        className={
          "group flex w-full items-center gap-1.5 rounded-lg px-2 py-1.5 text-left text-[12.5px] transition-colors " +
          (isDropTarget ? "bg-acs text-ac ring-1 ring-acl" : "text-t2 hover:bg-hover hover:text-tx")
        }
      >
        <button
          type="button"
          onClick={() => folders.toggleCollapsed(folder.id)}
          className="grid h-5 w-5 shrink-0 place-items-center rounded text-mut hover:text-tx"
          aria-label={collapsed ? "Развернуть" : "Свернуть"}
        >
          {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
        </button>
        {editing ? (
          <input
            ref={ref}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit();
              else if (e.key === "Escape") setEditing(false);
            }}
            onBlur={commit}
            className="min-w-0 flex-1 rounded border border-acl bg-bg px-1.5 py-0.5 text-[12.5px] text-tx outline-none"
          />
        ) : (
          <>
            <button
              type="button"
              onClick={() => folders.toggleCollapsed(folder.id)}
              className="min-w-0 flex-1 truncate text-left font-medium"
            >
              {folder.name}
            </button>
            <span className="shrink-0 text-[10.5px] text-mut">{count}</span>
            <button
              type="button"
              onClick={() => { setDraft(folder.name); setEditing(true); }}
              aria-label="Переименовать папку"
              title="Переименовать папку"
              className="grid h-5 w-5 shrink-0 place-items-center rounded text-mut opacity-0 transition-opacity hover:text-tx group-hover:opacity-100"
            >
              <Pencil size={12} />
            </button>
            <button
              type="button"
              onClick={() => folders.deleteFolder(folder.id)}
              aria-label="Удалить папку"
              title="Удалить папку (чаты выпадут наружу)"
              className="grid h-5 w-5 shrink-0 place-items-center rounded text-mut opacity-0 transition-opacity hover:text-tx group-hover:opacity-100"
            >
              <Trash2 size={12} />
            </button>
          </>
        )}
      </div>
      {children}
    </div>
  );
}

type RowProps = {
  s: CodeSessionMeta;
  indent?: boolean;
  activeId: string | null;
  editingId: string | null;
  draft: string;
  inputRef: React.Ref<HTMLInputElement>;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onTogglePin: (id: string) => void;
  beginEdit: (s: CodeSessionMeta) => void;
  commit: () => void;
  cancel: () => void;
  setDraft: (v: string) => void;
};

/** Одна строка чата: лампа стрима, заголовок/инлайн-правка, закреп, правка,
 *  удаление. Перетаскивается (draggable) в папку. */
function ChatRow({
  s, indent, activeId, editingId, draft, inputRef,
  onSelect, onDelete, onRename, onTogglePin, beginEdit, commit, cancel, setDraft,
}: RowProps) {
  const editing = s.id === editingId;
  void onRename; // правка идёт через commit() владельца; проп держим в типе для полноты
  return (
    <div
      draggable={!editing}
      onDragStart={(e) => {
        e.dataTransfer.setData("text/elira-session", s.id);
        e.dataTransfer.effectAllowed = "move";
      }}
      className={
        "group flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left text-[12.5px] transition-colors " +
        (indent ? "pl-7 " : "") +
        (s.id === activeId ? "bg-surface text-tx" : "text-t2 hover:bg-hover hover:text-tx")
      }
    >
      <StreamDot sessionId={s.id} />
      {editing ? (
        <>
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit();
              else if (e.key === "Escape") cancel();
            }}
            onBlur={commit}
            className="min-w-0 flex-1 rounded border border-acl bg-bg px-1.5 py-0.5 text-[12.5px] text-tx outline-none"
          />
          <button
            type="button"
            onMouseDown={(e) => { e.preventDefault(); commit(); }}
            aria-label="Сохранить"
            title="Сохранить"
            className="grid h-5 w-5 shrink-0 place-items-center rounded text-mut hover:text-ac"
          >
            <Check size={13} />
          </button>
          <button
            type="button"
            onMouseDown={(e) => { e.preventDefault(); cancel(); }}
            aria-label="Отмена"
            title="Отмена"
            className="grid h-5 w-5 shrink-0 place-items-center rounded text-mut hover:text-tx"
          >
            <X size={13} />
          </button>
        </>
      ) : (
        <>
          <button type="button" onClick={() => onSelect(s.id)} className="min-w-0 flex-1 truncate text-left">
            {s.title || "Без названия"}
          </button>
          <button
            type="button"
            onClick={() => onTogglePin(s.id)}
            aria-label={s.pinned ? "Открепить" : "Закрепить"}
            title={s.pinned ? "Открепить" : "Закрепить"}
            className={
              "grid h-5 w-5 shrink-0 place-items-center rounded transition-opacity hover:text-tx " +
              (s.pinned ? "text-ac opacity-100" : "text-mut opacity-0 group-hover:opacity-100")
            }
          >
            {s.pinned ? <PinOff size={13} /> : <Pin size={13} />}
          </button>
          <button
            type="button"
            onClick={() => beginEdit(s)}
            aria-label="Переименовать диалог"
            title="Переименовать"
            className="grid h-5 w-5 shrink-0 place-items-center rounded text-mut opacity-0 transition-opacity hover:text-tx group-hover:opacity-100"
          >
            <Pencil size={13} />
          </button>
          <button
            type="button"
            onClick={() => onDelete(s.id)}
            aria-label="Удалить диалог"
            title="Удалить"
            className="grid h-5 w-5 shrink-0 place-items-center rounded text-mut opacity-0 transition-opacity hover:text-tx group-hover:opacity-100"
          >
            <Trash2 size={13} />
          </button>
        </>
      )}
    </div>
  );
}

/** Мигающая лампочка стрима. Подписана на свой run в backgroundRuns; светит
 *  (animate-pulse) только пока в этом чате идёт стрим, иначе занимает место
 *  иконкой чата — чтобы выравнивание строк не прыгало. */
function StreamDot({ sessionId }: { sessionId: string }) {
  const running = useSyncExternalStore(
    (cb) => bg.subscribe(sessionId, cb),
    () => bg.isRunning(sessionId),
  );
  if (running) {
    return (
      <span className="grid h-4 w-4 shrink-0 place-items-center" title="Идёт ответ…">
        <span className="h-2 w-2 rounded-full bg-ac animate-pulse" />
      </span>
    );
  }
  return <MessageSquare size={16} className="shrink-0 text-mut" />;
}

function cnDot(connected: boolean): string {
  return connected
    ? "h-1.5 w-1.5 rounded-full bg-ac"
    : "h-1.5 w-1.5 rounded-full bg-mut animate-pulse";
}
