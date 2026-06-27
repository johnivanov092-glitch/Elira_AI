import { Loader2 } from "lucide-react";
import { type ReactNode } from "react";

export function McpBtn({ onClick, busy, label, children }: { onClick: () => void; busy: boolean; label: string; children: ReactNode }) {
  return (
    <button type="button" onClick={onClick} disabled={busy} aria-label={label} title={label} className="grid h-6 w-6 shrink-0 place-items-center rounded-md border border-line text-t2 transition-colors hover:bg-hover hover:text-tx disabled:opacity-50">
      {busy ? <Loader2 size={12} className="animate-spin" /> : children}
    </button>
  );
}

export function Wrap({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <div className="mb-3 pr-8 text-[13px] font-medium">{title}</div>
      {children}
    </div>
  );
}

export function Note({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-line px-3 py-2.5 text-[12.5px] text-mut">{children}</div>;
}

export function Loading() {
  return <div className="flex items-center gap-2 px-1 py-2 text-[12.5px] text-mut"><Loader2 size={14} className="animate-spin" /> загрузка…</div>;
}
