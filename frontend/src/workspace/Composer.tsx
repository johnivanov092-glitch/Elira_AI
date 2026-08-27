import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Blocks, Brain, Check, ChevronDown, Code, FileText, Image as ImageIcon, Library, Loader2, Plus, Send, Shield, ShieldAlert, ShieldCheck, Square, Users, X } from "lucide-react";
import type { CodeAgentMode, ContextUsage, PermissionMode, ReasoningEffort } from "../api/codeAgent";
import { importResourceToLibrary } from "../api/library";
import { uploadResource, type ResourceAttachment } from "../api/resources";
import { Chip } from "../ui/Chip";
import { MicButton } from "./MicButton";
import { cn } from "../ui/cn";

type Mode = CodeAgentMode; // "code" | "search" — UI mode maps 1:1 to the agent mode.

// Persisted approval-policy choice (localStorage, like theme.ts / voice.ts) so the
// chip survives a new chat and app restart instead of resetting to "ask" each mount.
const PERMISSION_MODE_KEY = "elira.permissionMode";

// Persisted reasoning depth. Legacy "0"/"1" values are migrated on read.
const THINKING_KEY = "elira.thinking";

export type ComposerAttachControls = {
  openFilePicker: () => void;
  attachFiles: (files: File[]) => void;
};

/** Composer per v4: mode chip + "+" (project / files / skills) + plugins + send.
 *  Runs use one Elira/Auto profile. Domain and evidence routers reveal relevant
 *  tools for the current request, so a manual persona/tool profile is unnecessary.
 *  Attachments are carried regardless, so file picking lives inside the "+" menu
 *  (opened in the
 *  Shell) — Composer hands the Shell a trigger for its hidden file input. */
export function Composer({
  value, onChange, sessionId, onPlus, onPlugins, onSend, onSendMultiAgent, running, onStop, contextUsage, onAttachReady,
}: {
  value: string;
  onChange: (v: string) => void;
  /** Session id that owns files uploaded from this composer (bound to the run so
   *  the backend authorizes resource_process only for this session's resources). */
  sessionId: string;
  onPlus: () => void;
  onPlugins: () => void;
  onSend: (text: string, mode: CodeAgentMode, resources?: ResourceAttachment[], permissionMode?: PermissionMode, reasoningEffort?: ReasoningEffort) => void;
  /** Multi-agent run (separate pipeline endpoint, not a stream). The two flags
   *  pick one of the 4 backend workflow templates. */
  onSendMultiAgent: (text: string, useOrchestrator: boolean, useReflection: boolean, permissionMode: PermissionMode, reasoningEffort: ReasoningEffort) => void;
  running: boolean;
  onStop: () => void;
  contextUsage?: ContextUsage | null;
  /** Receives a function that opens the hidden file picker, so the "+" menu in
   *  the Shell can trigger "Прикрепить файл". Called once on mount. */
  onAttachReady?: (controls: ComposerAttachControls) => void;
}) {
  const [mode, setMode] = useState<Mode>("code");
  // "Мульти-агент" is a run MODE, not a profile: when on, submit() routes to the
  // multi-agent pipeline instead of the code-agent stream. The two flags map to
  // the backend's 4 workflow templates. Local state only — never touches the
  // global `agent_profile` setting.
  const [multiAgent, setMultiAgent] = useState(false);
  const [useOrchestrator, setUseOrchestrator] = useState(true);
  const [useReflection, setUseReflection] = useState(true);
  // Approval policy for the run (Спрашивать / Контроль риска / Без ограничений).
  // Local run-mode like multiAgent — passed per-send into the code-agent stream;
  // the backend approval gate enforces it. Persisted across chats/restarts (the
  // chip used to reset to "ask" every mount). New local installs default to
  // bypass; an explicit stored choice is preserved.
  const [permissionMode, setPermissionMode] = useState<PermissionMode>(() => {
    try {
      const v = localStorage.getItem(PERMISSION_MODE_KEY);
      return v === "ask" || v === "accept_edits" ? v : "bypass";
    } catch {
      return "bypass";
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(PERMISSION_MODE_KEY, permissionMode);
    } catch {
      /* quota / private mode — non-fatal */
    }
  }, [permissionMode]);
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort>(() => {
    try {
      const value = localStorage.getItem(THINKING_KEY);
      if (value === "1") return "xhigh";
      if (value === "low" || value === "medium" || value === "xhigh") return value;
      return "none";
    } catch {
      return "none";
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(THINKING_KEY, reasoningEffort);
    } catch {
      /* quota / private mode — non-fatal */
    }
  }, [reasoningEffort]);
  // Attached resources: the file is UPLOADED (registered) on pick — NOT processed.
  // Its content is read later, on demand, by the agent's resource_process tool.
  // Kept across both modes; cleared after each send.
  const [attachments, setAttachments] = useState<ResourceAttachment[]>([]);
  const [attaching, setAttaching] = useState(false);
  // Names of files whose UPLOAD is in flight. Drives a transient "загрузка…" chip
  // per file — this is upload, not processing/transcription; cleared as each
  // upload resolves.
  const [uploading, setUploading] = useState<string[]>([]);
  // Deferred send (Variant Б): if the user hits send while files are still
  // uploading, remember the intent and fire it automatically once `uploading`
  // drains — so the message goes out WITH the attachments instead of silently
  // dropping the not-yet-ready ones.
  const [pendingSend, setPendingSend] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const submitRef = useRef<() => void>(() => {});
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
    onAttachReady?.({
      openFilePicker: () => fileRef.current?.click(),
      attachFiles: (files) => { void onPickFiles(files); },
    });
  }, [onAttachReady, sessionId]);

  function submit() {
    const text = value.trim();
    if (!text || running) return;
    // Multi-agent runs through a separate pipeline endpoint that takes only the
    // query (no chat attachments). Route there and keep any staged files intact.
    if (multiAgent) {
      onSendMultiAgent(text, useOrchestrator, useReflection, permissionMode, reasoningEffort);
      onChange("");
      return;
    }
    // Variant Б: files still uploading/transcribing — defer this send. The
    // effect below re-fires submit() once `uploading` drains, by which point the
    // finished files are in `attachments`, so nothing is silently dropped.
    if (uploading.length > 0) {
      setPendingSend(true);
      return;
    }
    // Only successfully-uploaded resources ride along; failed chips are dropped.
    const ready = attachments.filter((a) => a.status === "ready");
    const staged = ready.length ? ready : undefined;
    onSend(text, mode, staged, permissionMode, reasoningEffort);
    onChange("");
    setAttachments([]);
  }

  // Keep a ref to the latest submit() so the deferred-send effect always calls
  // the current closure (with the finished attachments), not a stale one.
  useEffect(() => { submitRef.current = submit; });
  // Fire a deferred send once every upload has finished (Variant Б).
  useEffect(() => {
    if (pendingSend && uploading.length === 0) {
      setPendingSend(false);
      submitRef.current();
    }
  }, [pendingSend, uploading]);

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  async function onPickFiles(files: FileList | File[] | null) {
    if (!files || files.length === 0) return;
    const picked = Array.from(files);
    // Show a transient "загрузка…" chip per file while its UPLOAD is in flight
    // (registration only — no processing). The raw File is used solely here and
    // is never kept after the upload resolves.
    setUploading(picked.map((f) => f.name));
    setAttaching(true);
    try {
      for (const file of picked) {
        try {
          const ref = await uploadResource(file, sessionId);
          setAttachments((prev) => [...prev, { ...ref, status: "ready" }]);
        } catch (err) {
          const reason = err instanceof Error && err.message
            ? err.message
            : "Не удалось загрузить файл";
          setAttachments((prev) => [...prev, {
            resource_id: "", name: file.name,
            kind: "other", content_type: file.type || "application/octet-stream",
            size: file.size, status: "error", error: reason,
          }]);
        } finally {
          // Remove the first matching name so this file's spinner disappears as
          // soon as its upload finishes, even mid-batch.
          setUploading((prev) => {
            const idx = prev.indexOf(file.name);
            return idx === -1 ? prev : [...prev.slice(0, idx), ...prev.slice(idx + 1)];
          });
        }
      }
    } finally {
      setAttaching(false);
      setUploading([]);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function saveToLibrary(resourceId: string) {
    setAttachments((prev) => prev.map((item) => (
      item.resource_id === resourceId
        ? { ...item, libraryStatus: "saving", libraryError: undefined }
        : item
    )));
    try {
      await importResourceToLibrary(resourceId, { useInContext: true });
      setAttachments((prev) => prev.map((item) => (
        item.resource_id === resourceId ? { ...item, libraryStatus: "saved" } : item
      )));
    } catch (error) {
      const message = error instanceof Error ? error.message : "Не удалось сохранить в Library";
      setAttachments((prev) => prev.map((item) => (
        item.resource_id === resourceId
          ? { ...item, libraryStatus: "error", libraryError: message }
          : item
      )));
    }
  }

  return (
    <div className="border-t border-line px-4 pb-4 pt-3">
      <div className="mx-auto max-w-[760px]">
        <div className="mb-2 flex items-center gap-1.5">
          <Chip active={mode === "code"} icon={<Code size={13} />} onClick={() => setMode("code")}>Чат\Код</Chip>
          <ReasoningEffortChip
            effort={reasoningEffort}
            onChange={setReasoningEffort}
          />
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
          <PermissionModeChip mode={permissionMode} onChange={setPermissionMode} />
          {usage && <ContextGauge usage={usage} tone={contextTone} />}
        </div>
        {(attachments.length > 0 || uploading.length > 0) && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {attachments.map((a, i) => (
              <span
                key={a.resource_id || `err-${a.name}-${i}`}
                title={a.status === "error"
                  ? (a.error || "Ошибка загрузки")
                  : a.libraryStatus === "error"
                    ? (a.libraryError || "Ошибка Library")
                    : `${a.kind} · ${a.size.toLocaleString()} байт`}
                className={cn(
                  "flex items-center gap-1.5 rounded-full border px-2 py-1 text-[11px]",
                  a.status === "error" ? "border-red-500/50 text-red-400" : "border-line text-t2",
                )}
              >
                {a.kind === "image" ? <ImageIcon size={12} className="shrink-0" /> : <FileText size={12} className="shrink-0" />}
                <span className="max-w-[160px] truncate">{a.name}</span>
                {a.status === "ready" && (
                  <button
                    type="button"
                    onClick={() => { void saveToLibrary(a.resource_id); }}
                    disabled={a.libraryStatus === "saving" || a.libraryStatus === "saved"}
                    aria-label={a.libraryStatus === "saved" ? "Сохранено в Library" : "Сохранить в Library"}
                    title={a.libraryStatus === "saved" ? "Сохранено в Library и добавлено в контекст" : "Сохранить в Library"}
                    className={cn(
                      "shrink-0 transition-colors hover:text-tx disabled:cursor-default",
                      a.libraryStatus === "saved" ? "text-ac" : a.libraryStatus === "error" ? "text-red-400" : "text-mut",
                    )}
                  >
                    {a.libraryStatus === "saving"
                      ? <Loader2 size={12} className="animate-spin" />
                      : a.libraryStatus === "saved"
                        ? <Check size={12} />
                        : <Library size={12} />}
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
            {uploading.map((name, i) => (
              <span
                key={`uploading-${name}-${i}`}
                title="Файл загружается…"
                className="flex items-center gap-1.5 rounded-full border border-line px-2 py-1 text-[11px] text-t2"
              >
                <Loader2 size={12} className="shrink-0 animate-spin" />
                <span className="max-w-[160px] truncate">{name}</span>
                <span className="text-mut">загрузка…</span>
              </span>
            ))}
          </div>
        )}
        <div className="flex items-end gap-2.5 rounded-xl border border-line bg-surface px-3 py-2.5 focus-within:border-acl">
          <input
            ref={fileRef}
            type="file"
            multiple
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
              title={pendingSend ? "Отправлю, как только загрузится файл" : undefined}
              className={cn(
                "grid h-[33px] w-[33px] shrink-0 place-items-center rounded-lg text-[#14151b] transition-opacity",
                value.trim() ? "bg-ac" : "cursor-not-allowed bg-ac/40",
              )}
            >
              {pendingSend ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

const REASONING_EFFORTS: {
  value: ReasoningEffort;
  label: string;
  shortLabel: string;
  hint: string;
}[] = [
  {
    value: "none",
    label: "Выкл / минимум",
    shortLabel: "Выкл",
    hint: "Qwen: без мышления; Muse: минимальный уровень low",
  },
  {
    value: "low",
    label: "Короткое",
    shortLabel: "Коротко",
    hint: "Небольшое рассуждение для простых задач",
  },
  {
    value: "medium",
    label: "Среднее",
    shortLabel: "Средне",
    hint: "Баланс глубины, скорости и расхода контекста",
  },
  {
    value: "xhigh",
    label: "Максимальное",
    shortLabel: "Макс",
    hint: "Самое глубокое рассуждение; заметно медленнее",
  },
];

/** Compact model-neutral selector; the backend maps it to Qwen or Muse kwargs. */
function ReasoningEffortChip({
  effort,
  onChange,
}: {
  effort: ReasoningEffort;
  onChange: (value: ReasoningEffort) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  const current = REASONING_EFFORTS.find((item) => item.value === effort)
    ?? REASONING_EFFORTS[0];

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        title={`Мышление: ${current.label}`}
        aria-label={`Мышление: ${current.label}`}
        className={cn(
          "flex h-7 shrink-0 items-center gap-1 rounded-full border pl-2 pr-1.5 text-[11.5px] transition-colors",
          effort === "none"
            ? "border-line text-t2 hover:bg-hover hover:text-tx"
            : "border-acl bg-acs text-ac",
        )}
      >
        <Brain size={13} />
        <span>Мозг: {current.shortLabel}</span>
        <ChevronDown size={12} className="shrink-0" />
      </button>
      {open && (
        <div className="absolute bottom-full left-0 z-20 mb-1.5 w-[260px] rounded-lg border border-line bg-card p-1 shadow-lg">
          {REASONING_EFFORTS.map((item) => (
            <button
              key={item.value}
              type="button"
              onClick={() => { onChange(item.value); setOpen(false); }}
              className={cn(
                "flex w-full items-start gap-2.5 rounded-md px-2 py-1.5 text-left transition-colors",
                item.value === effort ? "bg-acs" : "hover:bg-hover",
              )}
            >
              <Brain size={13} className="mt-0.5 shrink-0 text-t2" />
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5 text-[12.5px] text-tx">
                  <span>{item.label}</span>
                  {item.value === effort && <Check size={12} className="text-ac" />}
                </span>
                <span className="block text-[11px] text-mut">{item.hint}</span>
              </span>
            </button>
          ))}
        </div>
      )}
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
        <div className="absolute bottom-full left-0 z-20 mb-1.5 w-[220px] rounded-lg border border-line bg-card p-1 shadow-lg">
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

/** One option in the permission selector: label + one-line explanation + icon. */
const PERMISSION_MODES: { value: PermissionMode; label: string; hint: string; icon: ReactNode }[] = [
  {
    value: "ask",
    label: "Спрашивать",
    hint: "Подтверждение перед каждым изменением",
    icon: <Shield size={13} />,
  },
  {
    value: "accept_edits",
    label: "Контроль риска",
    hint: "Обычные изменения — автоматически; опасные или неизвестные — после подтверждения",
    icon: <ShieldCheck size={13} />,
  },
  {
    value: "bypass",
    label: "Без ограничений",
    hint: "Все tool, path, asset, LAN/SSH и command действия — без внутренних подтверждений",
    icon: <ShieldAlert size={13} />,
  },
];

/** Permission-mode selector chip (after «плагины»). A single icon+chevron button
 *  whose icon/tint reflect the active mode; the popover lists the three modes
 *  with a one-line explanation each. Mirrors the chat's access-rights menu. This
 *  is a per-run policy (not a global setting) wired into the approval gate. */
function PermissionModeChip({
  mode, onChange,
}: {
  mode: PermissionMode;
  onChange: (m: PermissionMode) => void;
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

  const current = PERMISSION_MODES.find((m) => m.value === mode) ?? PERMISSION_MODES[0];

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title={`Права доступа: ${current.label}`}
        aria-label={`Права доступа: ${current.label}`}
        className={cn(
          "flex h-7 shrink-0 items-center gap-1 rounded-full border pl-2 pr-1.5 transition-colors",
          mode === "bypass"
            ? "border-orange-400/50 bg-orange-400/10 text-orange-300"
            : mode === "accept_edits"
              ? "border-acl bg-acs text-ac"
              : "border-line text-t2 hover:bg-hover hover:text-tx",
        )}
      >
        {current.icon}
        <ChevronDown size={12} className="shrink-0" />
      </button>
      {open && (
        <div className="absolute bottom-full right-0 z-20 mb-1.5 w-[268px] rounded-lg border border-line bg-card p-1 shadow-lg">
          {PERMISSION_MODES.map((m) => (
            <button
              key={m.value}
              type="button"
              onClick={() => { onChange(m.value); setOpen(false); }}
              className={cn(
                "flex w-full items-start gap-2.5 rounded-md px-2 py-1.5 text-left transition-colors",
                m.value === mode ? "bg-acs" : "hover:bg-hover",
              )}
            >
              <span className="mt-0.5 grid h-4 w-4 shrink-0 place-items-center text-t2">{m.icon}</span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5 text-[12.5px] text-tx">
                  <span className="truncate">{m.label}</span>
                  {m.value === mode && <Check size={12} className="shrink-0 text-ac" />}
                </span>
                <span className="block text-[11px] text-mut">{m.hint}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Context-window gauge: a small ring that fills as the window fills (Claude-style).
 *  Hover reveals the full usage breakdown via the native title tooltip. */
function ContextGauge({ usage, tone }: { usage: ContextUsage; tone: string }) {
  const pct = Math.max(0, Math.min(100, usage.percent));
  const toolTokens = Math.max(0, Number(usage.breakdown?.tools || 0));
  const r = 7;
  const c = 2 * Math.PI * r;
  return (
    <span
      className={cn("flex h-7 w-7 shrink-0 items-center justify-center", tone)}
      title={`Контекст: ${Math.round(pct)}% · ${usage.current_tokens.toLocaleString()} / ${usage.ctx_size.toLocaleString()} токенов · инструменты ${toolTokens.toLocaleString()} · свободно ${usage.free_tokens.toLocaleString()}`}
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
