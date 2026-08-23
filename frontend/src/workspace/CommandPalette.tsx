import { useEffect, useMemo, useState } from "react";
import {
  Brain, Code2, Cpu, Globe, PanelRight,
  Package, Palette, Search, Send, Server, TerminalSquare, type LucideIcon,
} from "lucide-react";
import { cn } from "../ui/cn";
import type { SettingsSection } from "./Settings";

export type PaletteAction =
  | { kind: "settings"; section?: SettingsSection }
  | { kind: "preview" }
  | { kind: "prefill"; text: string };

type Item = { id: string; group: string; label: string; sub: string; icon: LucideIcon; action: PaletteAction };

const ITEMS: Item[] = [
  { id: "web", group: "Инструменты", label: "Веб-поиск", sub: "web_search", icon: Globe, action: { kind: "prefill", text: "Найди в интернете: " } },
  { id: "sandbox", group: "Инструменты", label: "Python-песочница", sub: "sandbox_run", icon: Code2, action: { kind: "prefill", text: "Запусти в песочнице Python: " } },
  { id: "bash", group: "Инструменты", label: "Shell", sub: "run_bash", icon: TerminalSquare, action: { kind: "prefill", text: "Выполни команду: " } },
  { id: "recall", group: "Инструменты", label: "Поиск по памяти", sub: "recall", icon: Brain, action: { kind: "prefill", text: "Вспомни из памяти: " } },
  { id: "mcp", group: "Плагины", label: "Плагины и MCP", sub: "управление через агента", icon: Package, action: { kind: "prefill", text: "Настрой или подключи MCP-интеграцию через workflow: " } },
  { id: "model", group: "Возможности", label: "Модель и провайдер", sub: "настройки", icon: Cpu, action: { kind: "settings", section: "model" } },
  { id: "memory", group: "Возможности", label: "Память", sub: "настройки", icon: Brain, action: { kind: "settings", section: "memory" } },
  { id: "tg", group: "Возможности", label: "Telegram", sub: "управление через агента", icon: Send, action: { kind: "prefill", text: "Настрой Telegram-интеграцию через workflow: " } },
  { id: "ssh", group: "Возможности", label: "SSH / MCP", sub: "управление через агента", icon: Server, action: { kind: "prefill", text: "Настрой SSH или MCP через workflow: " } },
  { id: "theme", group: "Возможности", label: "Тема", sub: "настройки", icon: Palette, action: { kind: "settings", section: "theme" } },
  { id: "prev", group: "Возможности", label: "Превью", sub: "панель артефактов", icon: PanelRight, action: { kind: "preview" } },
];

const GROUPS = ["Инструменты", "Скиллы", "Плагины", "Возможности"];

export function CommandPalette({ onAction, onClose }: { onAction: (a: PaletteAction) => void; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    return ITEMS.filter((i) => !s || `${i.label} ${i.sub} ${i.id}`.toLowerCase().includes(s));
  }, [q]);
  // Flattened visual order (groups in order) so Arrow/Enter navigation matches
  // what the user sees (FIX-19).
  const ordered = useMemo(() => GROUPS.flatMap((g) => filtered.filter((i) => i.group === g)), [filtered]);

  useEffect(() => { setSel(0); }, [q]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") { onClose(); return; }
      if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, ordered.length - 1)); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
      else if (e.key === "Enter") {
        e.preventDefault();
        const item = ordered[sel];
        if (item) { onAction(item.action); onClose(); }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, onAction, ordered, sel]);

  return (
    <div className="fixed inset-0 z-40 flex justify-center bg-black/50 pt-[12vh]" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label="Палитра команд" className="h-max w-[560px] max-w-[92%] overflow-hidden rounded-xl border border-line bg-card" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2.5 border-b border-line px-3.5 py-3">
          <Search size={16} className="text-mut" />
          <input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Найти скилл, плагин, инструмент или возможность…"
            className="flex-1 bg-transparent text-[14px] text-tx outline-none placeholder:text-mut"
          />
          <span className="rounded border border-line px-1.5 font-mono text-[10.5px] text-mut">esc</span>
        </div>
        <div className="max-h-[50vh] overflow-auto p-1.5">
          {GROUPS.map((g) => {
            const rows = filtered.filter((i) => i.group === g);
            if (rows.length === 0) return null;
            return (
              <div key={g}>
                <div className="px-2.5 pb-1 pt-2.5 text-[10.5px] font-medium uppercase tracking-wider text-mut">{g}</div>
                {rows.map((i) => (
                  <button
                    key={i.id}
                    type="button"
                    onClick={() => { onAction(i.action); onClose(); }}
                    onMouseEnter={() => setSel(ordered.findIndex((o) => o.id === i.id))}
                    className={cn(
                      "flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-hover hover:text-tx",
                      ordered[sel]?.id === i.id ? "bg-hover text-tx" : "text-t2",
                    )}
                  >
                    <span className="grid h-7 w-7 shrink-0 place-items-center rounded-md border border-line bg-surface"><i.icon size={15} /></span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] text-tx">{i.label}</span>
                      <span className="block truncate text-[11.5px] text-mut">{i.sub}</span>
                    </span>
                  </button>
                ))}
              </div>
            );
          })}
          {filtered.length === 0 && (
            <div className={cn("px-3 py-6 text-center text-[12.5px] text-mut")}>Ничего не найдено</div>
          )}
        </div>
      </div>
    </div>
  );
}
