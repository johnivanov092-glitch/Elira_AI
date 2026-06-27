import { Play, Square, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  deleteLibraryFile, listLibraryFilesTyped, toggleLibraryFile, type LibraryFile,
} from "../../api/library";
import { cn } from "../../ui/cn";
import { Loading, McpBtn, Note, Wrap } from "./_shared";

// Number of freshest active files build_library_context() actually injects.
// Keep in sync with backend build_library_context(max_files=...).
const LIB_CONTEXT_LIMIT = 10;

function fmtSize(bytes: number): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} КБ`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
}

export function LibrarySection() {
  const [items, setItems] = useState<LibraryFile[] | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const reload = useCallback(() => {
    listLibraryFilesTyped().then(setItems).catch(() => setItems([]));
  }, []);
  useEffect(() => { reload(); }, [reload]);

  async function toggle(f: LibraryFile) {
    setBusy(f.id);
    try { await toggleLibraryFile(f.id, !f.active); reload(); } catch { /* ignore */ } finally { setBusy(null); }
  }
  async function remove(id: number) {
    setBusy(id);
    try { await deleteLibraryFile(id); reload(); } catch { /* ignore */ } finally { setBusy(null); }
  }

  // The first LIB_CONTEXT_LIMIT *active* files (in freshest-first order) are the
  // ones the backend actually injects; older active files stay bookmarked but
  // fall out of the window. Mark exactly those.
  let injected = 0;
  const inContext = new Set<number>();
  for (const f of items ?? []) {
    if (f.active && injected < LIB_CONTEXT_LIMIT) { inContext.add(f.id); injected += 1; }
  }

  return (
    <Wrap title={`Библиотека${items ? ` · ${items.length}` : ""}`}>
      <Note>Набукмаренные файлы. Активные подмешиваются в контекст код-агента — реально попадают только {LIB_CONTEXT_LIMIT} самых свежих (бейдж «в контексте»), остальные ждут очереди. Файлы добавляются из чата (скрепка).</Note>
      {items === null ? (
        <Loading />
      ) : items.length === 0 ? (
        <Note>Пусто. Прикрепи файл в чате код-агента — он сохранится сюда.</Note>
      ) : (
        <div className="mt-2 flex flex-col gap-1.5">
          {items.map((f) => (
            <div key={f.id} className="group flex items-center gap-2.5 rounded-lg border border-line px-3 py-2 text-[12.5px]">
              <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", f.active ? "bg-ac" : "bg-mut")} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-tx">{f.name}</span>
                <span className="text-[10.5px] text-mut">{[f.type, fmtSize(f.size)].filter(Boolean).join(" · ")}</span>
              </span>
              {inContext.has(f.id) && <span className="shrink-0 rounded border border-acl bg-acs px-1.5 py-0.5 text-[10px] text-ac">в контексте</span>}
              <McpBtn onClick={() => toggle(f)} busy={busy === f.id} label={f.active ? "Убрать из контекста" : "Добавить в контекст"}>
                {f.active ? <Square size={13} /> : <Play size={13} />}
              </McpBtn>
              <button type="button" onClick={() => remove(f.id)} disabled={busy === f.id} aria-label="Удалить" className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-mut opacity-0 transition-opacity hover:text-tx group-hover:opacity-100 disabled:opacity-50">
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </div>
      )}
    </Wrap>
  );
}
