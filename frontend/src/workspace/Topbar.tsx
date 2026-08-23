import { ChevronDown, Folder, PanelRight, Settings } from "lucide-react";
import { IconButton } from "../ui/Button";
import { ContextLibraryChip } from "./ContextLibraryChip";
import { DriftChip } from "./DriftChip";

type TopbarProps = {
  project: string;
  onPickProject: () => void;
  onSettings: () => void;
  previewOpen: boolean;
  onTogglePreview: () => void;
};

/** Minimal Workflow topbar: project, runtime status, settings and artifacts. */
export function Topbar({
  project, onPickProject, onSettings, previewOpen, onTogglePreview,
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

      <div className="ml-auto flex gap-2">
        <DriftChip />
        <ContextLibraryChip />
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
