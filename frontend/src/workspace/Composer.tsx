import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Blocks, BookmarkPlus, Check, ChevronDown, Code, FileText, Image as ImageIcon, Plus, Search, Send, Square, Users, X } from "lucide-react";
import type { CodeAgentMode, ContextUsage } from "../api/codeAgent";
import { attachToChat, type ChatAttachment } from "../api/chat";
import { uploadLibraryFile } from "../api/library";
import { getActiveProfile, listProfiles, setActiveProfile, type ProfileInfo } from "../api/profiles";
import { Chip } from "../ui/Chip";
import { MicButton } from "./MicButton";
import { cn } from "../ui/cn";

type Mode = CodeAgentMode; // "code" | "search" — UI mode maps 1:1 to the agent mode.

/** Composer per v4: mode chips + "+" (project / files / skills) + plugins + send.
 *  The merged "Чат\Код" chip is the "code" mode; "Поиск" is "search". Both modes
 *  carry attachments, so file picking lives inside the "+" menu (opened in the
 *  Shell) — Composer hands the Shell a trigger for its hidden file input. */
export function Composer({
  value, onChange, onPlus, onPlugins, onSend, onSendMultiAgent, running, onStop, contextUsage, onAttachReady,
}: {
  value: string;
  onChange: (v: string) => void;
  onPlus: () => void;
  onPlugins: () => void;
  onSend: (text: string, mode: CodeAgentMode, attachments?: ChatAttachment[]) => void;
  /** Multi-agent run (separate pipeline endpoint, not a stream). The two flags
   *  pick one of the 4 backend workflow templates. */
  onSendMultiAgent: (text: string, useOrchestrator: boolean, useReflection: boolean) => void;
  running: boolean;
  onStop: () => void;
  contextUsage?: ContextUsage | null;
  /** Receives a function that opens the hidden file picker, so the "+" menu in
   *  the Shell can trigger "Прикрепить файл". Called once on mount. */
  onAttachReady?: (openFilePicker: () => void) => void;
}) {
  const [mode, setMode] = useState<Mode>("code");
  // "Мульти-агент" is a run MODE, not a profile: when on, submit() routes to the
  // multi-agent pipeline instead of the code-agent stream. The two flags map to
  // the backend's 4 workflow templates. Local state only — never touches the
  // global `agent_profile` setting.
  const [multiAgent, setMultiAgent] = useState(false);
  const [useOrchestrator, setUseOrchestrator] = useState(true);
  const [useReflection, setUseReflection] = useState(true);
  // Attachments (images + documents) parsed to text by the backend on pick. Kept
  // across both modes; cleared after each send. The project root and these files
  // travel together to the same code-agent stream.
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [attaching, setAttaching] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const usage = contextUsage
    && Number.isFinite(contextUsage.current_tokens)
    && Number.isFinite(contextUsage.ctx_size)
    && Number.isFinite(contextUsage.free_tokens)
    && Number.isFinite(contextUsage.percent)
    ? contextUsage
    : null;
  // Stroke color for the context gauge ring, escalating as the window fills.
  const contextTone = !usage ? "" : usage.percent >= 95
    ? "text-red-400"
    : usage.percent >= 90
      ? "text-red-300"
      : usage.percent >= 80
        ? "text-orange-300"
        : usage.percent >= 60
          ? "text-yellow-300"
          : "text-ac";

  // Hand the Shell a trigger for the hidden file input so the "+" menu's
  // "Прикрепить файл" can open it. Registered once on mount.
  useEffect(() => {
    onAttachReady?.(() => fileRef.current?.click());
  }, [onAttachReady]);

  function submit() {
    const text = value.trim();
    if (!text || running) return;
    // Multi-agent runs through a separate pipeline endpoint that takes only the
    // query (no chat attachments). Route there and keep any staged files intact.
    if (multiAgent) {
      onSendMultiAgent(text, useOrchestrator, useReflection);
      onChange("");
      return;
    }
    const staged = attachments.length ? attachments : undefined;
    // Fire-and-forget: persist any chips the user marked for the Library to
    // data/uploads (/api/lib/add) so the document is reusable across chats. The
    // chat send itself is unaffected — it still carries the parsed text inline.
    if (staged) {
      for (const a of staged) {
        if (a.toLibrary && a.ok && a.file) {
          void uploadLibraryFile(a.file, { useInContext: true }).catch(() => { /* best-effort */ });
        }
      }
    }
    onSend(text, mode, staged);
    onChange("");
    setAttachments([]);
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  async function onPickFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setAttaching(true);
    try {
      for (const file of Array.from(files)) {
        try {
          const att = await attachToChat(file);
          setAttachments((prev) => [...prev, att]);
        } catch {
          setAttachments((prev) => [...prev, {
            ok: false, filename: file.name,
            kind: file.type.startsWith("image/") ? "image" : "document",
            text: "", chars: 0, note: "Не удалось обработать файл",
          }]);
        }
      }
    } finally {
      setAttaching(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  return (
    <div className="border-t border-line px-4 pb-4 pt-3">
      <div className="mx-auto max-w-[760px]">
        <div className="mb-2 flex items-center gap-1.5">
          <Chip active={mode === "code"} icon={<Code size={13} />} onClick={() => setMode("code")}>Чат\Код</Chip>
          <Chip active={mode === "search"} icon={<Search size={13} />} onClick={() => setMode("search")}>Поиск</Chip>
          <ProfilePicker />
          <MicButton onText={(t) => onChange(value ? `${value} ${t}` : t)} disabled={running} />
          <MultiAgentChip
            active={multiAgent}
            useOrchestrator={useOrchestrator}
            useReflection={useReflection}
            onToggleActive={() => setMultiAgent((v) => !v)}
            onChangeOrchestrator={setUseOrchestrator}
            onChangeReflection={setUseReflection}
          />
          <button
            type="button"
            onClick={onPlugins}
            title="Скиллы, плагины, инструменты (⌘K)"
            aria-label="Скиллы и плагины"
            className="ml-auto flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-line text-t2 transition-colors hover:bg-hover hover:text-tx"
          >
            <Blocks size={13} />
          </button>
          {usage && <ContextGauge usage={usage} tone={contextTone} />}
        </div>
        {attachments.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {attachments.map((a, i) => (
              <span
                key={`${a.filename}-${i}`}
                title={a.ok ? `${a.chars.toLocaleString()} симв.${a.note ? ` · ${a.note}` : ""}` : (a.note || "Ошибка")}
                className={cn(
                  "flex items-center gap-1.5 rounded-full border px-2 py-1 text-[11px]",
                  a.ok ? "border-line text-t2" : "border-red-500/50 text-red-400",
                )}
              >
                {a.kind === "image" ? <ImageIcon size={12} className="shrink-0" /> : <FileText size={12} className="shrink-0" />}
                <span className="max-w-[160px] truncate">{a.filename}</span>
                {a.ok && a.file && (
                  <button
                    type="button"
                    onClick={() => setAttachments((prev) => prev.map((x, j) => (j === i ? { ...x, toLibrary: !x.toLibrary } : x)))}
                    aria-pressed={Boolean(a.toLibrary)}
                    title={a.toLibrary ? "Будет сохранён в Библиотеку (доступен в любом чате). Нажми, чтобы отменить" : "Сохранить в Библиотеку, чтобы переиспользовать в других чатах"}
                    aria-label="Сохранить в Библиотеку"
                    className={cn(
                      "shrink-0 transition-colors",
                      a.toLibrary ? "text-ac" : "text-mut hover:text-tx",
                    )}
                  >
                    <BookmarkPlus size={12} />
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setAttachments((prev) => prev.filter((_, j) => j !== i))}
                  aria-label="Убрать вложение"
                  className="shrink-0 text-mut transition-colors hover:text-tx"
                >
                  <X size={12} />
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="flex items-end gap-2.5 rounded-xl border border-line bg-surface px-3 py-2.5 focus-within:border-acl">
          <input
            ref={fileRef}
            type="file"
            multiple
            accept="image/*,.pdf,.txt,.md,.csv,.docx,.doc,.rtf,.json"
            className="hidden"
            onChange={(e) => onPickFiles(e.target.files)}
          />
          <button
            type="button"
            onClick={onPlus}
            disabled={attaching}
            title="Проект, файлы, скиллы"
            aria-label="Проект, файлы, скиллы"
            className="grid h-[31px] w-[31px] shrink-0 place-items-center rounded-lg border border-line text-t2 transition-colors hover:bg-hover hover:text-tx disabled:opacity-60"
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

/** "Мульти-агент" run-mode chip: icon + caption below it. Clicking the body
 *  toggles the mode (when on, the Composer routes submit() to the multi-agent
 *  pipeline instead of the code-agent stream). The chevron opens a popover with
 *  two checkboxes — «Планирование» (use_orchestrator) and «Саморевью»
 *  (use_reflection) — which together pick one of the 4 backend workflow
 *  templates. This is NOT a profile: it never writes the global agent_profile. */
function MultiAgentChip({
  active, useOrchestrator, useReflection, onToggleActive, onChangeOrchestrator, onChangeReflection,
}: {
  active: boolean;
  useOrchestrator: boolean;
  useReflection: boolean;
  onToggleActive: () => void;
  onChangeOrchestrator: (on: boolean) => void;
  onChangeReflection: (on: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close the popover on any outside click.
  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <div
        className={cn(
          "flex h-7 shrink-0 items-center gap-1 rounded-full border pl-2 pr-1.5 transition-colors",
          active ? "border-acl bg-acs text-ac" : "border-line text-t2 hover:bg-hover hover:text-tx",
        )}
      >
        <button
          type="button"
          onClick={onToggleActive}
          title={active ? "Мульти-агент включён — следующий запрос пойдёт по пайплайну" : "Включить мульти-агентный режим"}
          aria-pressed={active}
          aria-label="Мульти-агент"
          className="flex items-center leading-none"
        >
          <Users size={13} />
        </button>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          title="Настройки мульти-агента"
          aria-label="Настройки мульти-агента"
          className="shrink-0 rounded text-mut transition-colors hover:text-tx"
        >
          <ChevronDown size={12} className="shrink-0" />
        </button>
      </div>
      {open && (
        <div className="absolute bottom-full left-0 z-20 mb-1.5 w-[220px] rounded-lg border border-line bg-surface p-1 shadow-lg">
          <MultiAgentOption
            checked={useOrchestrator}
            onChange={onChangeOrchestrator}
            label="Планирование"
            hint="Оркестратор разбивает задачу на шаги перед исполнением"
          />
          <MultiAgentOption
            checked={useReflection}
            onChange={onChangeReflection}
            label="Саморевью"
            hint="Ревьюер проверяет и уточняет итоговый ответ"
          />
        </div>
      )}
    </div>
  );
}

/** One checkbox row inside the MultiAgentChip popover. */
function MultiAgentOption({
  checked, onChange, label, hint,
}: {
  checked: boolean;
  onChange: (on: boolean) => void;
  label: string;
  hint: string;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      role="checkbox"
      aria-checked={checked}
      className="flex w-full items-start gap-2.5 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-hover"
    >
      <span
        className={cn(
          "mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded border",
          checked ? "border-acl bg-acs text-ac" : "border-line text-transparent",
        )}
      >
        <Check size={11} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-[12.5px] text-tx">{label}</span>
        <span className="block text-[11px] text-mut">{hint}</span>
      </span>
    </button>
  );
}

/** Context-window gauge: a small ring that fills as the window fills (Claude-style).
 *  Hover reveals the full usage breakdown via the native title tooltip. */
function ContextGauge({ usage, tone }: { usage: ContextUsage; tone: string }) {
  const pct = Math.max(0, Math.min(100, usage.percent));
  const r = 7;
  const c = 2 * Math.PI * r;
  return (
    <span
      className={cn("flex h-7 w-7 shrink-0 items-center justify-center", tone)}
      title={`Контекст: ${Math.round(pct)}% · ${usage.current_tokens.toLocaleString()} / ${usage.ctx_size.toLocaleString()} токенов · свободно ${usage.free_tokens.toLocaleString()}`}
      aria-label={`Контекст заполнен на ${Math.round(pct)}%`}
    >
      <svg width="18" height="18" viewBox="0 0 18 18" className="-rotate-90">
        <circle cx="9" cy="9" r={r} fill="none" stroke="currentColor" strokeWidth="2" className="text-line" />
        <circle
          cx="9" cy="9" r={r} fill="none"
          stroke="currentColor" strokeWidth="2" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - pct / 100)}
        />
      </svg>
    </span>
  );
}

/** Active-agent-profile picker for the Composer slice. Self-contained: loads the
 *  profile list (/api/profiles) and the active one (/api/elira/settings →
 *  agent_profile) on mount, and switches the SAME global setting on pick —
 *  mirroring Settings' ProfilesSection, so the two stay in sync. */
function ProfilePicker() {
  const [profiles, setProfiles] = useState<ProfileInfo[] | null>(null);
  const [settings, setSettings] = useState<Record<string, unknown> | null>(null);
  const [active, setActive] = useState("");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    listProfiles().then((p) => { if (alive) setProfiles(p); });
    getActiveProfile()
      .then(({ settings: s, active: a }) => { if (alive) { setSettings(s); setActive(a); } })
      .catch(() => { /* offline */ });
    return () => { alive = false; };
  }, []);

  // Close the menu on any outside click.
  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  async function pick(name: string) {
    setOpen(false);
    if (busy || !settings || name === active) return;
    const prev = active;
    setActive(name); // optimistic
    setBusy(true);
    try {
      await setActiveProfile(name, settings);
      setSettings({ ...settings, agent_profile: name });
    } catch {
      setActive(prev); // revert on failure
    } finally {
      setBusy(false);
    }
  }

  // Hide entirely when the server has no profiles (or is offline).
  if (profiles !== null && profiles.length === 0) return null;

  const current = profiles?.find((p) => p.name === active);
  const label = current?.name || active || "Режим";
  const icon = current?.icon || "•";

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={busy || profiles === null}
        title="Режим Elira (Авто / Личный / Баланс / Инженерный)"
        aria-label="Режим Elira"
        className="flex h-7 items-center gap-1.5 rounded-full border border-line px-2.5 text-[11px] text-t2 transition-colors hover:bg-hover hover:text-tx disabled:opacity-60"
      >
        <span className="text-[12px] leading-none">{icon}</span>
        <span className="max-w-[120px] truncate">{label}</span>
        <ChevronDown size={12} className="shrink-0 text-mut" />
      </button>
      {open && profiles && profiles.length > 0 && (
        <div className="absolute bottom-full left-0 z-20 mb-1.5 max-h-[280px] w-[240px] overflow-y-auto rounded-lg border border-line bg-surface p-1 shadow-lg">
          {profiles.map((p) => (
            <button
              key={p.name}
              type="button"
              onClick={() => pick(p.name)}
              disabled={busy}
              className={cn(
                "flex w-full items-start gap-2.5 rounded-md px-2 py-1.5 text-left transition-colors disabled:opacity-60",
                p.name === active ? "bg-acs" : "hover:bg-hover",
              )}
            >
              <span className="mt-0.5 w-4 shrink-0 text-center text-[13px]">{p.icon || "•"}</span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5 text-[12.5px] text-tx">
                  <span className="truncate">{p.name}</span>
                  {p.name === active && <Check size={12} className="shrink-0 text-ac" />}
                  {p.is_default && p.name !== active && <span className="shrink-0 text-[9.5px] text-mut">по умолчанию</span>}
                </span>
                {p.short && <span className="block truncate text-[11px] text-mut">{p.short}</span>}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
