import { Library, Loader2, Play, Square, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  listLibraryFilesTyped, toggleLibraryFile, type LibraryFile,
} from "../api/library";
import { IconButton } from "../ui/Button";
import { cn } from "../ui/cn";
import { McpBtn } from "./settings/_shared";
import { formatLibrarySize, libraryIndexLabel, useLibraryUpload } from "./libraryUi";

/** Topbar control: quick view/toggle of which library files are mixed into the
 *  agent's context (use_in_context), without opening Settings. Settings keeps
 *  the full management UI (incl. add/delete). Popover drops down-right. */
export function ContextLibraryChip() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<LibraryFile[] | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  function reload() {
    listLibraryFilesTyped().then(setItems).catch(() => setItems([]));
  }
  const { uploading, uploadError, fileRef, addFiles, openPicker } = useLibraryUpload(reload);
  // Refresh each time the popover opens, so newly attached files show up.
  useEffect(() => { if (open) reload(); }, [open]);

  // Close on outside click (same pattern as the composer popovers).
  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  async function toggle(f: LibraryFile) {
    setBusy(f.id);
    try { await toggleLibraryFile(f.id, !f.active); reload(); } catch { /* offline */ } finally { setBusy(null); }
  }

  const list = items ?? [];
  const activeCount = list.filter((f) => f.active).length;

  return (
    <div ref={ref} className="relative">
      <IconButton
        onClick={() => setOpen((v) => !v)}
        active={open || activeCount > 0}
        title="Библиотека контекста — какие файлы подмешиваются в каждый запрос"
        aria-label="Библиотека контекста"
      >
        <Library size={16} />
      </IconButton>
      {activeCount > 0 && (
        <span className="pointer-events-none absolute -right-1 -top-1 rounded-full border border-acl bg-acs px-1 text-[9px] font-semibold text-ac">
          {activeCount}
        </span>
      )}
      {open && (
        <div className="absolute right-0 top-full z-30 mt-1.5 max-h-[340px] w-[300px] overflow-y-auto rounded-lg border border-line bg-card p-1 shadow-lg">
          <input
            ref={fileRef}
            type="file"
            multiple
            className="hidden"
            onChange={(event) => { void addFiles(event.target.files); }}
          />
          <button
            type="button"
            onClick={openPicker}
            disabled={uploading}
            className="mb-1 flex w-full items-center gap-2 rounded-md border border-line px-2.5 py-2 text-left text-[11.5px] text-tx transition-colors hover:bg-hover disabled:opacity-50"
          >
            {uploading ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
            {uploading ? "Индексирование…" : "Добавить файл в Library"}
          </button>
          {uploadError && <div className="px-2.5 py-1 text-[10.5px] text-red-400">{uploadError}</div>}
          {items === null ? (
            <div className="px-2.5 py-2 text-[11px] text-mut">Загрузка…</div>
          ) : list.length === 0 ? (
            <div className="px-2.5 py-2 text-[11px] text-mut">Пусто. Добавь файл кнопкой выше или сохрани сюда чат-вложение значком Library.</div>
          ) : (
            list.map((f) => (
              <div key={f.id} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-[11.5px] hover:bg-hover">
                <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", f.active ? "bg-ac" : "bg-mut")} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-tx">{f.name}</span>
                  <span className="text-[10px] text-mut">
                    {[f.type, formatLibrarySize(f.size), libraryIndexLabel(f), f.lastUsedAt ? "добавлен в контекст" : ""].filter(Boolean).join(" · ")}
                  </span>
                </span>
                {f.active && (
                  <span className="shrink-0 rounded border border-acl bg-acs px-1 py-0.5 text-[9.5px] text-ac">активен</span>
                )}
                <McpBtn onClick={() => toggle(f)} busy={busy === f.id} label={f.active ? "Убрать из контекста" : "Добавить в контекст"}>
                  {f.active ? <Square size={13} /> : <Play size={13} />}
                </McpBtn>
              </div>
            ))
          )}
          <div className="px-2 py-1 text-[10px] text-mut">
            В prompt попадает релевантный фрагмент. Полный документ агент дочитывает страницами по необходимости.
          </div>
        </div>
      )}
    </div>
  );
}
