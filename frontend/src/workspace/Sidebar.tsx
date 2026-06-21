import { MessageSquare, Plus, Trash2 } from "lucide-react";
import type { CodeSessionMeta } from "../api/codeAgent";

/** Left rail: "New chat" + the real conversation list (per v4 layout).
 *  Sessions come from /api/code-agent/sessions. Workspace features moved to
 *  Settings; skills/plugins to the ⌘K palette. */
export function Sidebar({
  connected, sessions, activeId, onNew, onSelect, onDelete,
}: {
  connected: boolean;
  sessions: CodeSessionMeta[];
  activeId: string | null;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}) {
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
          sessions.map((s) => (
            <div
              key={s.id}
              className={
                "group flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left text-[12.5px] transition-colors " +
                (s.id === activeId ? "bg-surface text-tx" : "text-t2 hover:bg-hover hover:text-tx")
              }
            >
              <button type="button" onClick={() => onSelect(s.id)} className="flex min-w-0 flex-1 items-center gap-2.5 text-left">
                <MessageSquare size={16} className="shrink-0 text-mut" />
                <span className="flex-1 truncate">{s.title || "Без названия"}</span>
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
            </div>
          ))
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
