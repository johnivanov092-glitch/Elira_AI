import { ChevronDown, Folder, PanelRight, Settings, TerminalSquare } from "lucide-react";
import { IconButton } from "../ui/Button";
import { cn } from "../ui/cn";

export type MainTab = "chat" | "pipe";

type TopbarProps = {
  project: string;
  tab: MainTab;
  onTab: (tab: MainTab) => void;
  onPickProject: () => void;
  onSettings: () => void;
  previewOpen: boolean;
  onTogglePreview: () => void;
  terminalOpen: boolean;
  onToggleTerminal: () => void;
};

/** Topbar per v4: project-folder PATH (left) + tabs Чат|Пайплайны +
 *  gear (Settings) + preview toggle AFTER the gear. No model name, no tokens. */
export function Topbar({
  project, tab, onTab, onPickProject, onSettings, previewOpen, onTogglePreview, terminalOpen, onToggleTerminal,
}: TopbarProps) {
  return (
    <header className="flex items-center gap-3 border-b border-line px-3 py-2">
      <button
        type="button"
        onClick={onPickProject}
        title="Папка проекта"
        className="flex max-w-[280px] items-center gap-2 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-[11.5px] text-t2 transition-colors hover:bg-hover hover:text-tx"
      >
        <Folder size={14} className="shrink-0 text-ac" />
        <span className="truncate font-mono">{project || "Выбрать папку проекта"}</span>
        <ChevronDown size={13} className="shrink-0 text-mut" />
      </button>

      <div className="ml-1 flex gap-0.5 rounded-lg border border-line bg-surface p-0.5">
        <TabButton active={tab === "chat"} onClick={() => onTab("chat")}>Чат</TabButton>
        <TabButton active={tab === "pipe"} onClick={() => onTab("pipe")}>Пайплайны</TabButton>
      </div>

      <div className="ml-auto flex gap-2">
        <IconButton onClick={onToggleTerminal} active={terminalOpen} title="Терминал" aria-label="Терминал">
          <TerminalSquare size={16} />
        </IconButton>
        <IconButton onClick={onSettings} title="Настройки" aria-label="Настройки">
          <Settings size={16} />
        </IconButton>
        <IconButton onClick={onTogglePreview} active={previewOpen} title="Превью" aria-label="Превью">
          <PanelRight size={16} />
        </IconButton>
      </div>
    </header>
  );
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-md px-3.5 py-1 text-xs font-medium transition-colors",
        active ? "bg-acs text-ac" : "text-t2 hover:bg-hover hover:text-tx",
      )}
    >
      {children}
    </button>
  );
}
