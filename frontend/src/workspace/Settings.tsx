import { useState } from "react";
import { BookMarked, Brain, Cpu, MessageSquare, Palette, Sparkles, Volume2, X, type LucideIcon } from "lucide-react";
import { cn } from "../ui/cn";
import { ModelSection } from "./settings/ModelSection";
import { PersonaSection } from "./settings/PersonaSection";
import { MemorySection } from "./settings/MemorySection";
import { LibrarySection } from "./settings/LibrarySection";
import { ChatMemorySection } from "./settings/ChatMemorySection";
import { VoiceSection } from "./settings/VoiceSection";
import { ThemeSection } from "./settings/ThemeSection";

export type SettingsSection =
  | "model" | "persona" | "memory" | "library" | "chatmemory"
  | "voice" | "theme";

const NAV: { id: SettingsSection; label: string; icon: LucideIcon }[] = [
  { id: "model", label: "Модель", icon: Cpu },
  { id: "persona", label: "Личность", icon: Sparkles },
  { id: "memory", label: "Память", icon: Brain },
  { id: "library", label: "Библиотека", icon: BookMarked },
  { id: "chatmemory", label: "Память чата", icon: MessageSquare },
  { id: "voice", label: "Голос", icon: Volume2 },
  { id: "theme", label: "Тема", icon: Palette },
];

export function Settings({ model, onModel, onClose, project, initialSection }: { model: string; onModel: (m: string) => void; onClose: () => void; project: string; initialSection?: SettingsSection }) {
  const [section, setSection] = useState<SettingsSection>(initialSection ?? "model");

  return (
    <div className="fixed inset-0 z-30 flex justify-center bg-black/50 pt-[42px]" onClick={onClose}>
      <div className="flex h-[min(720px,88vh)] w-[min(960px,94vw)] overflow-hidden rounded-xl border border-line bg-card" onClick={(e) => e.stopPropagation()}>
        <nav className="flex w-[200px] shrink-0 flex-col gap-0.5 border-r border-line p-2">
          <div className="px-2.5 pb-1.5 pt-1 text-[13.5px] font-medium">Настройки</div>
          {NAV.map((n) => (
            <button
              key={n.id}
              type="button"
              onClick={() => setSection(n.id)}
              className={cn(
                "flex items-center gap-2.5 whitespace-nowrap rounded-lg px-2.5 py-2 text-left text-[12.5px] transition-colors",
                section === n.id ? "bg-acs text-ac" : "text-t2 hover:bg-hover hover:text-tx",
              )}
            >
              <n.icon size={15} className="shrink-0" /> {n.label}
            </button>
          ))}
        </nav>

        <div className="relative min-w-0 flex-1 overflow-auto p-4">
          <button type="button" onClick={onClose} aria-label="Закрыть" className="absolute right-3 top-3 grid h-6 w-6 place-items-center rounded-md border border-line text-t2 hover:bg-hover hover:text-tx">
            <X size={14} />
          </button>
          {section === "model" && <ModelSection model={model} onModel={onModel} />}
          {section === "persona" && <PersonaSection />}
          {section === "memory" && <MemorySection project={project} />}
          {section === "library" && <LibrarySection />}
          {section === "chatmemory" && <ChatMemorySection />}
          {section === "voice" && <VoiceSection />}
          {section === "theme" && <ThemeSection />}
        </div>
      </div>
    </div>
  );
}
