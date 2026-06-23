import { useEffect, useRef, useState } from "react";
import { Check, MessageSquare, Pencil, Plus, Trash2, X } from "lucide-react";
import type { CodeSessionMeta } from "../api/codeAgent";

/** Left rail: "New chat" + the real conversation list (per v4 layout).
 *  Sessions come from /api/code-agent/sessions. Workspace features moved to
 *  Settings; skills/plugins to the ⌘K palette. */
export function Sidebar({
  connected, sessions, activeId, onNew, onSelect, onDelete, onRename,
}: {
  connected: boolean;
  sessions: CodeSessionMeta[];
  activeId: string | null;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onRename: (id: string, title: string) => void;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (editingId) inputRef.current?.focus();
  }, [editingId]);

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

  return (
    <aside className="flex min-h-0 flex-col border-r border-line bg-side">
      <div className="flex items-center gap-2.5 px-4 pb-2.5 pt-3.5 text-[15px] font-medium">
        <span className="grid h-[22px] w-[22px] place-items-center rounded-md bg-ac text-[#14151b]">
          <span className="h-2 w-2 rounded-full bg-[#14151b]" />
        </span>
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
        <div className="px-2 pb-1 pt-1.5 text-[10px] font-medium uppercase tracking-wider text-mut">
          Диалоги
        </div>
        {sessions.length === 0 ? (
          <div className="px-2 py-2 text-[12px] text-mut">Пока пусто — начни новый чат.</div>
        ) : (
          sessions.map((s) => {
            const editing = s.id === editingId;
            return (
              <div
                key={s.id}
                className={
                  "group flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left text-[12.5px] transition-colors " +
                  (s.id === activeId ? "bg-surface text-tx" : "text-t2 hover:bg-hover hover:text-tx")
                }
              >
                <MessageSquare size={16} className="shrink-0 text-mut" />
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
          })
        )}
      </div>

      <div className="flex items-center gap-2 border-t border-line px-3.5 py-2.5 text-[11.5px] text-t2">
        <span className={cnDot(connected)} />
        {connected ? "подключено" : "подключение…"}
      </div>
    </aside>
  );
}

function cnDot(connected: boolean): string {
  return connected
    ? "h-1.5 w-1.5 rounded-full bg-ac"
    : "h-1.5 w-1.5 rounded-full bg-mut animate-pulse";
}
