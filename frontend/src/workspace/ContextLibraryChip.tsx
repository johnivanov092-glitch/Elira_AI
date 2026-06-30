import { Library, Play, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { listLibraryFilesTyped, toggleLibraryFile, type LibraryFile } from "../api/library";
import { IconButton } from "../ui/Button";
import { cn } from "../ui/cn";
import { McpBtn } from "./settings/_shared";

// Keep in sync with backend build_library_context(max_files=...) and LibrarySection.
const LIB_CONTEXT_LIMIT = 10;

function fmtSize(bytes: number): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} КБ`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
}

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

  // The first LIB_CONTEXT_LIMIT *active* files (freshest-first) are the ones the
  // backend actually injects; mark exactly those with the "в контексте" badge.
  let injected = 0;
  const inContext = new Set<number>();
  for (const f of list) {
    if (f.active && injected < LIB_CONTEXT_LIMIT) { inContext.add(f.id); injected += 1; }
  }

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
          {items === null ? (
            <div className="px-2.5 py-2 text-[11px] text-mut">Загрузка…</div>
          ) : list.length === 0 ? (
            <div className="px-2.5 py-2 text-[11px] text-mut">Пусто. Прикрепи файл в чате (скрепка) — он сохранится сюда.</div>
          ) : (
            list.map((f) => (
              <div key={f.id} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-[11.5px] hover:bg-hover">
                <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", f.active ? "bg-ac" : "bg-mut")} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-tx">{f.name}</span>
                  <span className="text-[10px] text-mut">{[f.type, fmtSize(f.size)].filter(Boolean).join(" · ")}</span>
                </span>
                {inContext.has(f.id) && (
                  <span className="shrink-0 rounded border border-acl bg-acs px-1 py-0.5 text-[9.5px] text-ac">в контексте</span>
                )}
                <McpBtn onClick={() => toggle(f)} busy={busy === f.id} label={f.active ? "Убрать из контекста" : "Добавить в контекст"}>
                  {f.active ? <Square size={13} /> : <Play size={13} />}
                </McpBtn>
              </div>
            ))
          )}
          <div className="px-2 py-1 text-[10px] text-mut">
            Подмешиваются {LIB_CONTEXT_LIMIT} самых свежих активных. Добавить файлы / удалить — в Настройках.
          </div>
        </div>
      )}
    </div>
  );
}
