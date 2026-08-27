import { Loader2, Play, Square, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  deleteLibraryFile, listLibraryFilesTyped, toggleLibraryFile, type LibraryFile,
} from "../../api/library";
import { cn } from "../../ui/cn";
import { formatLibrarySize, libraryIndexLabel, useLibraryUpload } from "../libraryUi";
import { Loading, McpBtn, Note, Wrap } from "./_shared";

export function LibrarySection() {
  const [items, setItems] = useState<LibraryFile[] | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const reload = useCallback(() => {
    listLibraryFilesTyped().then(setItems).catch(() => setItems([]));
  }, []);
  const { uploading, uploadError, fileRef, addFiles, openPicker } = useLibraryUpload(reload);
  useEffect(() => { reload(); }, [reload]);

  async function toggle(f: LibraryFile) {
    setBusy(f.id);
    try { await toggleLibraryFile(f.id, !f.active); reload(); } catch { /* ignore */ } finally { setBusy(null); }
  }
  async function remove(id: number) {
    setBusy(id);
    try { await deleteLibraryFile(id); reload(); } catch { /* ignore */ } finally { setBusy(null); }
  }

  return (
    <Wrap title={`Библиотека${items ? ` · ${items.length}` : ""}`}>
      <Note>Активные документы участвуют в поиске. В prompt попадает только релевантный фрагмент; полный текст агент дочитывает страницами через Library runtime.</Note>
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
        className="mt-2 flex items-center gap-2 rounded-md border border-line px-3 py-2 text-[12px] text-tx transition-colors hover:bg-hover disabled:opacity-50"
      >
        {uploading ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
        {uploading ? "Индексирование…" : "Добавить файл в Library"}
      </button>
      {uploadError && <div className="mt-1 text-[11px] text-red-400">{uploadError}</div>}
      {items === null ? (
        <Loading />
      ) : items.length === 0 ? (
        <Note>Пусто. Добавь файл кнопкой выше или явно сохрани чат-вложение значком Library.</Note>
      ) : (
        <div className="mt-2 flex flex-col gap-1.5">
          {items.map((f) => (
            <div key={f.id} className="group flex items-center gap-2.5 rounded-lg border border-line px-3 py-2 text-[12.5px]">
              <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", f.active ? "bg-ac" : "bg-mut")} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-tx">{f.name}</span>
                <span className="text-[10.5px] text-mut">
                  {[f.type, formatLibrarySize(f.size), libraryIndexLabel(f), f.lastUsedAt ? "добавлен в контекст" : ""].filter(Boolean).join(" · ")}
                </span>
              </span>
              {f.active && <span className="shrink-0 rounded border border-acl bg-acs px-1.5 py-0.5 text-[10px] text-ac">активен</span>}
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
