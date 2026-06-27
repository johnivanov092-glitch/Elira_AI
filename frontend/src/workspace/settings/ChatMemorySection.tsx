import { Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  addSmartMemory, deleteSmartMemory, listSmartMemory, type SmartMemoryItem,
} from "../../api/smartMemory";
import { cn } from "../../ui/cn";
import { Loading, Note, Wrap } from "./_shared";

export function ChatMemorySection() {
  const [items, setItems] = useState<SmartMemoryItem[] | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    listSmartMemory(100).then(setItems).catch(() => setItems([]));
  }, []);
  useEffect(() => { reload(); }, [reload]);

  async function add() {
    const t = text.trim();
    if (!t || busy) return;
    setBusy(true);
    try { await addSmartMemory({ text: t }); setText(""); reload(); } catch { /* ignore */ } finally { setBusy(false); }
  }
  async function remove(id: SmartMemoryItem["id"]) {
    if (id === undefined) return;
    try { await deleteSmartMemory(id); reload(); } catch { /* ignore */ }
  }

  return (
    <Wrap title={`Память чата${items ? ` · ${items.length}` : ""}`}>
      <Note>Факты код-чата (chat-agent): что-то агент запоминает сам из разговора, что-то можно добавить вручную. Используются как краткосрочная память диалога — отдельно от RAG.</Note>
      <div className="my-3 flex gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void add(); }}
          placeholder="Добавить факт в память чата…"
          className="flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-[12.5px] text-tx outline-none placeholder:text-mut focus:border-acl"
        />
        <button type="button" onClick={add} disabled={busy || !text.trim()} aria-label="Добавить" className={cn("grid h-[34px] w-[34px] shrink-0 place-items-center rounded-lg text-[#14151b]", text.trim() && !busy ? "bg-ac" : "cursor-not-allowed bg-ac/40")}>
          <Plus size={16} />
        </button>
      </div>
      {items === null ? (
        <Loading />
      ) : items.length === 0 ? (
        <Note>Пока пусто. Память наполняется по ходу диалога с код-агентом.</Note>
      ) : (
        <div className="mt-2 flex flex-col gap-1.5">
          {items.map((it, i) => (
            <div key={it.id ?? i} className="group flex items-start gap-2 rounded-lg border border-line px-3 py-2 text-[12.5px] text-t2">
              {it.category && <span className="mt-0.5 shrink-0 rounded border border-line px-1.5 text-[10px] text-mut">{it.category}</span>}
              <span className="flex-1 break-words">{String(it.text ?? "").slice(0, 240)}</span>
              {it.source && <span className="mt-0.5 shrink-0 text-[10px] text-mut">{it.source}</span>}
              <button type="button" onClick={() => remove(it.id)} aria-label="Удалить" className="grid h-5 w-5 shrink-0 place-items-center rounded text-mut opacity-0 transition-opacity hover:text-tx group-hover:opacity-100">
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </div>
      )}
    </Wrap>
  );
}
