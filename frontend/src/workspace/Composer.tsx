import { useState, type KeyboardEvent } from "react";
import { Blocks, Code, MessageSquare, Plus, Search, Send, Square } from "lucide-react";
import type { CodeAgentMode, ContextUsage } from "../api/codeAgent";
import { Chip } from "../ui/Chip";
import { cn } from "../ui/cn";

type Mode = "code" | "chat" | "search";

/** Composer per v4: mode chips + "+" (project / files / skills) + plugins + send.
 *  Maps the UI mode to the code-agent mode (search -> search, else code). */
export function Composer({
  value, onChange, onPlus, onPlugins, onSend, running, onStop, contextUsage,
}: {
  value: string;
  onChange: (v: string) => void;
  onPlus: () => void;
  onPlugins: () => void;
  onSend: (text: string, mode: CodeAgentMode) => void;
  running: boolean;
  onStop: () => void;
  contextUsage?: ContextUsage | null;
}) {
  const [mode, setMode] = useState<Mode>("code");
  const usage = contextUsage
    && Number.isFinite(contextUsage.current_tokens)
    && Number.isFinite(contextUsage.ctx_size)
    && Number.isFinite(contextUsage.free_tokens)
    && Number.isFinite(contextUsage.percent)
    ? contextUsage
    : null;
  const contextTone = !usage ? "" : usage.percent >= 95
    ? "border-red-500/60 text-red-400"
    : usage.percent >= 90
      ? "border-red-400/50 text-red-300"
      : usage.percent >= 80
        ? "border-orange-400/50 text-orange-300"
        : usage.percent >= 60
          ? "border-yellow-400/50 text-yellow-300"
          : "border-line text-mut";

  function submit() {
    const text = value.trim();
    if (!text || running) return;
    onSend(text, mode === "search" ? "search" : "code");
    onChange("");
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="border-t border-line px-4 pb-4 pt-3">
      <div className="mx-auto max-w-[760px]">
        <div className="mb-2 flex items-center gap-1.5">
          <Chip active={mode === "code"} icon={<Code size={13} />} onClick={() => setMode("code")}>Код</Chip>
          <Chip active={mode === "chat"} icon={<MessageSquare size={13} />} onClick={() => setMode("chat")}>Чат</Chip>
          <Chip active={mode === "search"} icon={<Search size={13} />} onClick={() => setMode("search")}>Поиск</Chip>
          <button
            type="button"
            onClick={onPlugins}
            title="Скиллы, плагины, инструменты (⌘K)"
            aria-label="Скиллы и плагины"
            className="ml-auto flex items-center gap-1.5 rounded-full border border-line px-2.5 py-1 text-[11px] text-t2 transition-colors hover:bg-hover hover:text-tx"
          >
            <Blocks size={13} /> Плагины
          </button>
          {usage && (
            <span className={cn("rounded-full border px-2 py-1 font-mono text-[10.5px]", contextTone)} title={`Контекст: ${usage.current_tokens.toLocaleString()} / ${usage.ctx_size.toLocaleString()} · свободно ${usage.free_tokens.toLocaleString()}`}>
              {Math.round(usage.percent)}% · {Math.round(usage.current_tokens / 1000)}K/{Math.round(usage.ctx_size / 1024)}K
            </span>
          )}
        </div>
        <div className="flex items-end gap-2.5 rounded-xl border border-line bg-surface px-3 py-2.5 focus-within:border-acl">
          <button
            type="button"
            onClick={onPlus}
            title="Проект, файлы, скиллы"
            aria-label="Проект, файлы, скиллы"
            className="grid h-[31px] w-[31px] shrink-0 place-items-center rounded-lg border border-line text-t2 transition-colors hover:bg-hover hover:text-tx"
          >
            <Plus size={16} />
          </button>
          <textarea
            rows={1}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={onKey}
            placeholder="Опиши задачу или перетащи файл…  Enter — отправить"
            className="max-h-[120px] flex-1 resize-none bg-transparent text-sm text-tx outline-none placeholder:text-mut"
          />
          {running ? (
            <button
              type="button"
              onClick={onStop}
              aria-label="Остановить"
              className="grid h-[33px] w-[33px] shrink-0 place-items-center rounded-lg border border-line text-t2 transition-colors hover:bg-hover hover:text-tx"
            >
              <Square size={15} />
            </button>
          ) : (
            <button
              type="button"
              onClick={submit}
              disabled={!value.trim()}
              aria-label="Отправить"
              className={cn(
                "grid h-[33px] w-[33px] shrink-0 place-items-center rounded-lg text-[#14151b] transition-opacity",
                value.trim() ? "bg-ac" : "cursor-not-allowed bg-ac/40",
              )}
            >
              <Send size={16} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
