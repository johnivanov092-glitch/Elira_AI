import { FolderSearch, Loader2, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  addRagItem, deleteRagItem, getRagStats, indexProject, listRagItems,
  type RagListItem, type RagStats,
} from "../../api/codeAgent";
import { cn } from "../../ui/cn";
import { Loading, Note, Wrap } from "./_shared";

export function MemorySection({ project }: { project: string }) {
  const [stats, setStats] = useState<RagStats | null>(null);
  const [items, setItems] = useState<RagListItem[] | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const reload = useCallback(() => {
    getRagStats().then(setStats).catch(() => setStats(null));
    listRagItems(100).then((r) => setItems(r.items ?? [])).catch(() => setItems([]));
  }, []);
  useEffect(() => { reload(); }, [reload]);

  async function add() {
    const t = text.trim();
    if (!t || busy) return;
    setBusy(true);
    try { await addRagItem(t); setText(""); reload(); } catch { setMsg("Не удалось добавить."); } finally { setBusy(false); }
  }
  async function remove(id: number) {
    try { await deleteRagItem(id); reload(); } catch { /* ignore */ }
  }
  async function indexNow() {
    if (!project || busy) return;
    setBusy(true);
    setMsg("Индексирую проект…");
    try {
      const r = await indexProject({ projectRoot: project });
      setMsg(r.ok ? `Готово: ${r.files_processed ?? 0} файлов, ${r.chunks_indexed ?? 0} фрагментов.` : (r.error || "Ошибка индексации."));
      reload();
    } catch { setMsg("Ошибка индексации."); } finally { setBusy(false); }
  }

  return (
    <Wrap title={`Память (RAG)${stats ? ` · ${stats.total}` : ""}`}>
      <div className="mb-3 flex items-center gap-2 text-[12px] text-t2">
        <span className="flex-1">{stats ? `${stats.with_embeddings}/${stats.total} с эмбеддингами${stats.model ? ` · ${stats.model}` : ""}` : "загрузка…"}</span>
        <button
          type="button"
          onClick={indexNow}
          disabled={!project || busy}
          title={project ? "Проиндексировать текущий проект в RAG" : "Сначала выбери проект"}
          className={cn("flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[12px] transition-colors", project && !busy ? "border-line text-t2 hover:bg-hover hover:text-tx" : "cursor-not-allowed border-line text-mut")}
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : <FolderSearch size={13} />} Индексировать проект
        </button>
      </div>

      <div className="mb-3 flex gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void add(); }}
          placeholder="Добавить факт в память…"
          className="flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-[12.5px] text-tx outline-none placeholder:text-mut focus:border-acl"
        />
        <button type="button" onClick={add} disabled={busy || !text.trim()} aria-label="Добавить" className={cn("grid h-[34px] w-[34px] shrink-0 place-items-center rounded-lg text-[#14151b]", text.trim() && !busy ? "bg-ac" : "cursor-not-allowed bg-ac/40")}>
          <Plus size={16} />
        </button>
      </div>

      {msg && <Note>{msg}</Note>}

      {items === null ? (
        <Loading />
      ) : items.length === 0 ? (
        <Note>Память пуста. Добавь факт или проиндексируй проект.</Note>
      ) : (
        <div className="mt-2 flex flex-col gap-1.5">
          {items.map((it) => (
            <div key={it.id} className="group flex items-start gap-2 rounded-lg border border-line px-3 py-2 text-[12.5px] text-t2">
              <span className="mt-0.5 shrink-0 rounded border border-line px-1.5 text-[10px] text-mut">{it.category}</span>
              <span className="flex-1 break-words">{String(it.text ?? "").slice(0, 240)}</span>
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
