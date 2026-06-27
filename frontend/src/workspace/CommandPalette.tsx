import { useEffect, useMemo, useState } from "react";
import {
  Brain, Code2, Cpu, GitBranch, Globe, Image as ImageIcon, LayoutDashboard, PanelRight,
  Package, Palette, Search, Send, Server, TerminalSquare, type LucideIcon,
} from "lucide-react";
import { cn } from "../ui/cn";

export type PaletteAction =
  | { kind: "settings" }
  | { kind: "pipelines" }
  | { kind: "preview" }
  | { kind: "prefill"; text: string };

type Item = { id: string; group: string; label: string; sub: string; icon: LucideIcon; action: PaletteAction };

const ITEMS: Item[] = [
  { id: "web", group: "Инструменты", label: "Веб-поиск", sub: "web_search", icon: Globe, action: { kind: "prefill", text: "Найди в интернете: " } },
  { id: "sandbox", group: "Инструменты", label: "Python-песочница", sub: "sandbox_run", icon: Code2, action: { kind: "prefill", text: "Запусти в песочнице Python: " } },
  { id: "bash", group: "Инструменты", label: "Shell", sub: "run_bash", icon: TerminalSquare, action: { kind: "prefill", text: "Выполни команду: " } },
  { id: "recall", group: "Инструменты", label: "Поиск по памяти", sub: "recall", icon: Brain, action: { kind: "prefill", text: "Вспомни из памяти: " } },
  { id: "image", group: "Скиллы", label: "Генерация изображений", sub: "SDXL", icon: ImageIcon, action: { kind: "prefill", text: "Сгенерируй изображение: " } },
  { id: "mcp", group: "Плагины", label: "Плагины и MCP", sub: "управление плагинами, подключить MCP", icon: Package, action: { kind: "settings" } },
  { id: "model", group: "Возможности", label: "Модель и провайдер", sub: "настройки", icon: Cpu, action: { kind: "settings" } },
  { id: "memory", group: "Возможности", label: "Память", sub: "настройки", icon: Brain, action: { kind: "settings" } },
  { id: "dash", group: "Возможности", label: "Дашборд", sub: "метрики", icon: LayoutDashboard, action: { kind: "settings" } },
  { id: "tg", group: "Возможности", label: "Telegram", sub: "одобрения", icon: Send, action: { kind: "settings" } },
  { id: "ssh", group: "Возможности", label: "SSH / MCP", sub: "настройки", icon: Server, action: { kind: "settings" } },
  { id: "theme", group: "Возможности", label: "Тема", sub: "настройки", icon: Palette, action: { kind: "settings" } },
  { id: "pipe", group: "Возможности", label: "Пайплайны", sub: "режим", icon: GitBranch, action: { kind: "pipelines" } },
  { id: "prev", group: "Возможности", label: "Превью", sub: "панель артефактов", icon: PanelRight, action: { kind: "preview" } },
];

const GROUPS = ["Инструменты", "Скиллы", "Плагины", "Возможности"];

export function CommandPalette({ onAction, onClose }: { onAction: (a: PaletteAction) => void; onClose: () => void }) {
  const [q, setQ] = useState("");

  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") onClose(); }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    return ITEMS.filter((i) => !s || `${i.label} ${i.sub} ${i.id}`.toLowerCase().includes(s));
  }, [q]);

  return (
    <div className="fixed inset-0 z-40 flex justify-center bg-black/50 pt-[12vh]" onClick={onClose}>
      <div className="h-max w-[560px] max-w-[92%] overflow-hidden rounded-xl border border-line bg-card" onClick={(e) => e.stopPropagation()}>
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
                    className="flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left text-t2 transition-colors hover:bg-hover hover:text-tx"
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
