import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, ChevronDown, CircleAlert, CircleDashed, Code, FileSearch, FolderOpen, Globe, ListChecks, Sparkles, UploadCloud, Wand2, X } from "lucide-react";
import { waitForBackend } from "../api/client";
import type { ResourceAttachment } from "../api/resources";
import {
  type CodeAgentMode,
  type CodeSessionMeta,
  type PermissionMode,
  createCodeSession,
  deleteCodeSession,
  getCodeSession,
  getVerifyCommand,
  listCodeSessions,
  patchCodeSession,
  setVerifyCommand,
} from "../api/codeAgent";
import type { Turn } from "./types";
import { pickFolder } from "../pickFolder";
import { cn } from "../ui/cn";
import { deriveArtifacts, fileArtifactKey, downloadArtifactKey } from "./artifacts";
import { Sidebar } from "./Sidebar";
import { Topbar, type MainTab } from "./Topbar";
import { Composer, type ComposerAttachControls } from "./Composer";
import { Transcript } from "./Transcript";
import { PreviewPanel } from "./PreviewPanel";
import { CommandPalette, type PaletteAction } from "./CommandPalette";
import { Settings, type SettingsSection } from "./Settings";
import { PipelinesShell } from "./PipelinesShell";
import { TerminalDock } from "./TerminalDock";
import { useAgentRun } from "./useAgentRun";
import * as bg from "./backgroundRuns";
import { bindingFromSession, persistProjectSelection } from "./sessionBinding";
import { taskHistoryItems } from "./taskHistory";

let _draftSeq = 0;
const newDraftKey = () => `draft-${Date.now()}-${++_draftSeq}`;

/** Unified workspace (v4). Phase 1: real transcript wired to the code-agent
 *  stream. Settings/preview/palette content + token streaming land in later
 *  phases; old shells are deleted in Phase 5. */
export default function WorkspaceShell() {
  const [tab, setTab] = useState<MainTab>("chat");
  const [previewOpen, setPreviewOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  // Which Settings section to open at — set by the command palette (FIX-19),
  // undefined = default ("Модель") when opened from the topbar.
  const [settingsSection, setSettingsSection] = useState<SettingsSection | undefined>(undefined);
  const [menuOpen, setMenuOpen] = useState(false);
  const [verifyOpen, setVerifyOpen] = useState(false);
  // Trigger for the Composer's hidden file input, registered via onAttachReady.
  // Lets the "+" menu's "Прикрепить файл" open the picker that lives in Composer.
  const attachControls = useRef<ComposerAttachControls | null>(null);
  const [project, setProject] = useState("");
  const [connected, setConnected] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [input, setInput] = useState("");
  const [model, setModel] = useState("auto");
  const [sessions, setSessions] = useState<CodeSessionMeta[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  // The stable key the run/snapshot is filed under. Always non-null: a generated
  // draft id for a fresh chat (so the run keeps a stable home before the server
  // session id exists), or the server id once a saved chat is opened. The manager
  // is rekeyed from draft -> server id when createCodeSession resolves.
  const [activeKey, setActiveKey] = useState<string>(() => newDraftKey());
  // FIX-3: when a session fails to LOAD (network / 5xx, not a genuine 404) we must
  // not seed a blank transcript or arm persist — otherwise the next save overwrites
  // the real turns on the server. Track failed keys to also block sending on them,
  // and surface a banner so the user knows the history is intact server-side.
  const loadFailedKeys = useRef(new Set<string>());
  const [loadError, setLoadError] = useState<string | null>(null);

  const run = useAgentRun(activeKey, project, model);
  const scrollRef = useRef<HTMLDivElement>(null);
  const artifacts = useMemo(() => deriveArtifacts(run.turns), [run.turns]);
  const lastFileKey = useRef("");

  const refreshSessions = useCallback(() => {
    listCodeSessions().then(setSessions).catch(() => { /* offline */ });
  }, []);
  useEffect(() => { refreshSessions(); }, [refreshSessions]);

  // Map from a run key (draft id or server id) to its server session id, for
  // sessions whose run finished while off-screen. Lets background persistence
  // resolve the right server id without React state churn.
  const keyToServerId = useRef(new Map<string, string>());
  // In-flight createCodeSession calls, keyed by run key. Both onSend's eager
  // create and makePersist's lazy create funnel through this so a run that
  // finishes before the eager create resolves does NOT spawn a second session.
  const creatingByKey = useRef(new Map<string, Promise<string>>());

  // Create (or reuse) the server session for a run key exactly once. Rekeys the
  // live background run onto the server id and adopts it on screen if that run
  // is still the one displayed. Returns the resolved server id.
  const ensureServerId = useCallback((runKey: string, title: string, proj: string, mdl: string): Promise<string> => {
    const known = keyToServerId.current.get(runKey);
    if (known) return Promise.resolve(known);
    const pending = creatingByKey.current.get(runKey);
    if (pending) return pending;
    const p = createCodeSession({ title, projectRoot: proj, model: mdl })
      .then((s) => {
        keyToServerId.current.set(runKey, s.id);
        keyToServerId.current.set(s.id, s.id);
        // Move the live run from the draft key onto the server id, keeping its
        // persist binding. Adopt the new key on screen only if we haven't
        // navigated away in the meantime.
        bg.rekey(runKey, s.id);
        bg.setPersist(s.id, makePersist(s.id, proj, mdl));
        setActiveKey((cur) => (cur === runKey ? s.id : cur));
        setSessionId((cur) => (cur === runKey || cur === null ? s.id : cur));
        refreshSessions();
        return s.id;
      })
      .finally(() => { creatingByKey.current.delete(runKey); });
    creatingByKey.current.set(runKey, p);
    return p;
  // makePersist is defined below; it is stable across renders (same deps).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshSessions]);

  // Build a persist closure bound to a specific run key + its project/model.
  // Registered with the manager at send/restore time, so a finished run saves
  // itself even when its chat is not the one on screen (background completion).
  const makePersist = useCallback((runKey: string, proj: string, mdl: string): bg.PersistFn => {
    return (snap) => {
      const turns = snap.turns;
      if (!turns.some((t) => t.kind === "user")) return;
      (async () => {
        try {
          const id = await ensureServerId(runKey, deriveTitle(turns), proj, mdl);
          await patchCodeSession(id, { title: deriveTitle(turns), turns: serializeTurns(turns), projectRoot: proj, model: mdl, contextState: snap.contextState, taskLedger: snap.taskLedger });
          refreshSessions();
        } catch { /* offline; keep local */ }
      })();
    };
  }, [refreshSessions, ensureServerId]);

  useEffect(() => {
    let alive = true;
    waitForBackend(20, 1500).then((ok) => { if (alive) setConnected(ok); });
    // Keep the connection indicator live: re-check /health periodically so a
    // backend that dies mid-session flips to "disconnected" instead of showing
    // "connected" forever (FIX-13). waitForBackend(1, 0) is a single 4s-bounded
    // probe (WebView2-safe manual AbortController).
    const id = setInterval(() => {
      waitForBackend(1, 0).then((ok) => { if (alive) setConnected(ok); });
    }, 15_000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  useEffect(() => {
    // Auto-open the preview when a new file OR download artifact appears — a
    // generated .docx (file_gen) has no text preview but still deserves the panel.
    const key = fileArtifactKey(artifacts) || downloadArtifactKey(artifacts);
    if (key && key !== lastFileKey.current) {
      lastFileKey.current = key;
      setPreviewOpen(true);
    }
  }, [artifacts]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    // Stick-to-bottom (FIX-17): only auto-scroll when the user is already near the
    // bottom, so scrolling up to read earlier output isn't yanked back down on
    // every new token/turn. ~120px threshold tolerates minor drift.
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [run.turns]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function onPaletteAction(a: PaletteAction) {
    if (a.kind === "settings") { setSettingsSection(a.section); setSettingsOpen(true); }
    else if (a.kind === "pipelines") setTab("pipe");
    else if (a.kind === "preview") setPreviewOpen(true);
    else if (a.kind === "prefill") setInput((v) => (v.trim() ? v.trimEnd() + " " : "") + a.text);
  }

  async function pick() {
    const p = await pickFolder(project || undefined);
    if (!p) return;
    try {
      await persistProjectSelection(sessionId, p, patchCodeSession);
    } catch {
      setLoadError("Не удалось привязать папку к этому чату. Проект не изменён.");
      return;
    }
    setProject(p);
    setLoadError(null);
    // Keep the sidebar metadata and the next background persist aligned with
    // the server-owned session binding. A draft is persisted on first send.
    if (sessionId) {
      setSessions((prev) => prev.map((s) => (s.id === sessionId ? { ...s, project_root: p } : s)));
      bg.setPersist(activeKey, makePersist(activeKey, p, model));
    }
  }

  function onSend(text: string, mode: CodeAgentMode, resources?: ResourceAttachment[], permissionMode?: PermissionMode, thinking?: boolean, noQuestions?: boolean) {
    // No project required: the backend defaults to a scratch workspace, so chat
    // works out of the box. Picking a folder targets a specific project.
    const msg = text.trim();
    if (!msg) return;
    // FIX-3: refuse to send into a chat whose history failed to load — persisting
    // this run would overwrite the intact server turns with a blank transcript.
    if (loadFailedKeys.current.has(activeKey)) {
      setLoadError("Чат не загрузился — не отправляю, чтобы не потерять историю. Открой чат заново.");
      return;
    }
    // Bind persistence to THIS run's key before it starts, so the run saves
    // itself when it finishes — even if you've switched to another chat by then
    // (background completion). The closure captures the run's own project/model.
    bg.setPersist(activeKey, makePersist(activeKey, project, model));
    run.send(text, mode, resources, permissionMode, thinking, noQuestions);
    // Create the session eagerly so it appears in the sidebar as soon as you
    // send — not only when the run finishes. ensureServerId dedupes against the
    // persist closure's own lazy create, so the run is saved exactly once.
    if (!sessionId && !keyToServerId.current.has(activeKey)) {
      void ensureServerId(activeKey, msg.slice(0, 48) || "Новый чат", project, model)
        .catch(() => { /* offline; the persist closure retries the create */ });
    }
  }

  function onSendMultiAgent(text: string, useOrchestrator: boolean, useReflection: boolean) {
    // Multi-agent runs through a separate pipeline endpoint (not the streaming
    // code-agent), but is persisted and surfaced in the sidebar exactly like a
    // normal run — so mirror onSend's persist binding + eager session create.
    const msg = text.trim();
    if (!msg) return;
    bg.setPersist(activeKey, makePersist(activeKey, project, model));
    run.sendMultiAgent(text, useOrchestrator, useReflection);
    if (!sessionId && !keyToServerId.current.has(activeKey)) {
      void ensureServerId(activeKey, msg.slice(0, 48) || "Новый чат", project, model)
        .catch(() => { /* offline; the persist closure retries the create */ });
    }
  }

  function newChat() {
    // A new chat gets a fresh draft key. The previous chat's run (if any) keeps
    // streaming in the background under its own key — switching never cancels it.
    const key = newDraftKey();
    setActiveKey(key);
    bg.seed(key, []);
    setSessionId(null);
    // A new chat starts fresh (scratch): clear the inherited project path and
    // model so the topbar resets and the agent isn't pinned to the previous
    // chat's folder. selectSession() restores both from a saved session.
    setProject("");
    setModel("auto");
    setTab("chat");
  }

  async function selectSession(id: string) {
    setTab("chat");
    setSessionId(id);
    keyToServerId.current.set(id, id);
    // Switch the displayed run to this session's key. If it already has a live
    // background run, seed() is a no-op and we re-attach to the running snapshot.
    setActiveKey(id);
    try {
      const s = await getCodeSession(id);
      loadFailedKeys.current.delete(id);
      setLoadError(null);
      if (!bg.isRunning(id)) {
        bg.seed(id, s ? deserializeTurns(s.turns) : [], s?.task_ledger || [], s?.context_state || null);
      }
      const binding = bindingFromSession(s);
      bg.setPersist(id, makePersist(id, binding.projectRoot, binding.model));
      setProject(binding.projectRoot);
      setModel(binding.model);
    } catch {
      // Load FAILED (network / 5xx): do NOT seed a blank transcript and do NOT arm
      // persist — a later save would push the empty snapshot over the real server
      // turns. Mark the key unsafe so onSend also refuses until a successful reload.
      loadFailedKeys.current.add(id);
      bg.setPersist(id, null);
      setLoadError("Не удалось загрузить чат. История на сервере не тронута — попробуй открыть его ещё раз.");
    }
  }

  async function renameSession(id: string, title: string) {
    const next = title.trim();
    if (!next) return;
    try { await patchCodeSession(id, { title: next }); } catch { /* ignore */ }
    refreshSessions();
  }

  async function togglePin(id: string) {
    const s = sessions.find((x) => x.id === id);
    const next = !s?.pinned;
    // Оптимистично — чтобы строка прыгнула наверх сразу, без ожидания сети.
    setSessions((prev) => prev.map((x) => (x.id === id ? { ...x, pinned: next } : x)));
    try { await patchCodeSession(id, { pinned: next }); } catch { /* ignore */ }
    refreshSessions();
  }

  async function deleteSession(id: string) {
    bg.stop(id);
    try { await deleteCodeSession(id); } catch { /* ignore */ }
    keyToServerId.current.delete(id);
    if (id === sessionId || id === activeKey) {
      const key = newDraftKey();
      setActiveKey(key);
      bg.seed(key, []);
      setSessionId(null);
      setProject("");
      setModel("auto");
    }
    refreshSessions();
  }

  const showPreview = previewOpen && tab === "chat";

  return (
    <div
      className={cn(
        "grid h-screen grid-rows-[minmax(0,1fr)] overflow-hidden bg-bg font-sans text-tx",
        showPreview ? "grid-cols-[236px_minmax(0,1fr)_minmax(360px,460px)]" : "grid-cols-[236px_minmax(0,1fr)]",
      )}
    >
      <Sidebar
        connected={connected}
        sessions={sessions}
        activeId={sessionId}
        onNew={newChat}
        onSelect={selectSession}
        onDelete={deleteSession}
        onRename={renameSession}
        onTogglePin={togglePin}
      />

      <section className="relative flex min-h-0 min-w-0 flex-col">
        <Topbar
          project={project}
          tab={tab}
          onTab={setTab}
          onPickProject={pick}
          onSettings={() => { setSettingsSection(undefined); setSettingsOpen(true); }}
          previewOpen={previewOpen}
          onTogglePreview={() => setPreviewOpen((v) => !v)}
          terminalOpen={terminalOpen}
          onToggleTerminal={() => setTerminalOpen((v) => !v)}
        />

        <div
          ref={scrollRef}
          className={cn("relative min-h-0 flex-1 overflow-auto", dragging && "ring-2 ring-inset ring-acl")}
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            const files = Array.from(e.dataTransfer.files);
            if (files.length) attachControls.current?.attachFiles(files);
          }}
        >
          {tab === "pipe" ? (
            <PipelinesShell />
          ) : run.turns.length > 0 ? (
            <>
              <Transcript turns={run.turns} onApprove={run.approve} onApproveAll={run.approveAll} onResume={run.resume} onAnswer={run.answer} />
              <TaskHistory ledger={run.taskLedger} turns={run.turns} />
            </>
          ) : (
            <ChatEmptyState hasProject={!!project} onPick={pick} onSuggest={(p) => setInput(p)} />
          )}
          {dragging && (
            <div className="pointer-events-none absolute inset-0 grid place-items-center bg-bg/70 text-t2">
              <div className="flex items-center gap-2 text-sm"><UploadCloud size={18} /> Отпусти — файл прикрепится к сообщению</div>
            </div>
          )}
        </div>

        {terminalOpen && <TerminalDock onClose={() => setTerminalOpen(false)} />}

        {run.autoApprove && (
          <div className="border-t border-acl bg-acs px-4 py-1.5 text-center text-[11.5px] text-ac">
            Авто-одобрение включено для этой сессии — агент действует без запроса (сбросится в новом чате)
          </div>
        )}

        {loadError && (
          <div className="flex items-center justify-between gap-2 border-t border-red-500/40 bg-red-500/10 px-4 py-1.5 text-[11.5px] text-red-300">
            <span>{loadError}</span>
            <button type="button" onClick={() => setLoadError(null)} className="shrink-0 px-1 text-red-300/80 hover:text-red-200" aria-label="Скрыть">×</button>
          </div>
        )}

        <Composer key={activeKey} value={input} onChange={setInput} sessionId={activeKey} onPlus={() => setMenuOpen((v) => !v)} onPlugins={() => setPaletteOpen(true)} onSend={onSend} onSendMultiAgent={onSendMultiAgent} running={run.running} onStop={run.stop} contextUsage={run.contextUsage} onAttachReady={(controls) => { attachControls.current = controls; }} />

        {menuOpen && <PlusMenu onClose={() => setMenuOpen(false)} onPickProject={pick} onPickFile={() => attachControls.current?.openFilePicker()} onEditVerify={() => setVerifyOpen(true)} hasProject={!!project} />}
        {verifyOpen && <VerifyCommandModal projectRoot={project} onClose={() => setVerifyOpen(false)} />}
      </section>

      {showPreview && <PreviewPanel artifacts={artifacts} project={project} onClose={() => setPreviewOpen(false)} />}

      {settingsOpen && <Settings model={model} onModel={setModel} onClose={() => setSettingsOpen(false)} project={project} initialSection={settingsSection} />}
      {paletteOpen && <CommandPalette onAction={onPaletteAction} onClose={() => setPaletteOpen(false)} />}
    </div>
  );
}

function TaskHistory({ ledger, turns }: { ledger: bg.RunSnapshot["taskLedger"]; turns: Turn[] }) {
  const items = taskHistoryItems(ledger, turns);
  if (items.length === 0) return null;
  return (
    <details className="mx-auto mb-5 max-w-[760px] border-t border-line px-5 pt-2 text-[12px]">
      <summary className="flex cursor-pointer list-none items-center gap-2 py-1.5 text-t2 hover:text-tx">
        <ListChecks size={14} className="text-ac" />
        <span className="font-medium">Задачи чата</span>
        <span className="text-mut">· последние {items.length}</span>
        <ChevronDown size={13} className="ml-auto text-mut" />
      </summary>
      <ul className="divide-y divide-line/70 border-t border-line/70">
        {items.map((item) => {
          const Icon = item.type === "final" ? CheckCircle2 : item.type === "error" ? CircleAlert : CircleDashed;
          const state = item.type === "final" ? "готово" : item.type === "error" ? "не завершено" : "частично";
          const cls = item.type === "final" ? "text-success" : item.type === "error" ? "text-danger" : "text-ac";
          return (
            <li key={`${item.timestamp}:${item.action}`} className="flex min-w-0 items-start gap-2 py-2">
              <Icon size={13} className={`${cls} mt-0.5 shrink-0`} />
              <span className="min-w-0 flex-1 truncate text-t2" title={item.label}>{item.label}</span>
              <span className={`${cls} shrink-0 text-[11px]`}>{state}</span>
            </li>
          );
        })}
      </ul>
    </details>
  );
}

const SUGGESTIONS: { icon: typeof Code; title: string; text: string; prompt: string }[] = [
  { icon: Code, title: "Создать страницу", text: "лендинг / HTML по описанию", prompt: "Создай аккуратный одностраничный лендинг: " },
  { icon: Globe, title: "Найти в вебе", text: "поиск и краткая сводка", prompt: "Найди в интернете и кратко суммируй: " },
  { icon: FileSearch, title: "Разобрать код", text: "объяснить файл проекта", prompt: "Объясни, что делает и как устроен файл: " },
  { icon: Wand2, title: "Рефакторинг", text: "улучшить структуру кода", prompt: "Предложи и аккуратно применю рефакторинг: " },
];

function ChatEmptyState({ hasProject, onPick, onSuggest }: { hasProject: boolean; onPick: () => void; onSuggest: (prompt: string) => void }) {
  return (
    <div className="mx-auto flex h-full max-w-[600px] flex-col items-center justify-center gap-6 px-6 text-center">
      <div className="flex flex-col items-center gap-3">
        <span className="grid h-14 w-14 place-items-center rounded-2xl bg-acs text-ac">
          <Sparkles size={26} />
        </span>
        <div className="text-[22px] font-semibold tracking-tight">Чем помочь?</div>
        <p className="max-w-[440px] text-[13.5px] leading-relaxed text-t2">
          Опиши задачу внизу — отвечу сразу. Агент сам запускает инструменты,
          показывает вызовы и превью результата.
        </p>
      </div>

      <div className="grid w-full grid-cols-2 gap-2.5">
        {SUGGESTIONS.map((s) => (
          <button
            key={s.title}
            type="button"
            onClick={() => onSuggest(s.prompt)}
            className="group flex items-start gap-3 rounded-xl border border-line bg-surface p-3 text-left transition-colors hover:border-acl hover:bg-hover"
          >
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-line bg-card text-ac">
              <s.icon size={15} />
            </span>
            <span className="min-w-0">
              <span className="block text-[12.5px] font-medium text-tx">{s.title}</span>
              <span className="block text-[11.5px] text-mut">{s.text}</span>
            </span>
          </button>
        ))}
      </div>

      <button
        type="button"
        onClick={onPick}
        className="flex items-center gap-2 text-[12.5px] text-t2 transition-colors hover:text-tx"
      >
        <FolderOpen size={14} className="text-ac" />
        {hasProject ? "Сменить папку проекта" : "Выбрать папку проекта — работать в твоём коде"}
      </button>
    </div>
  );
}

function PlusMenu({ onClose, onPickProject, onPickFile, onEditVerify, hasProject }: { onClose: () => void; onPickProject: () => void; onPickFile: () => void; onEditVerify: () => void; hasProject: boolean }) {
  return (
    <>
      <div className="fixed inset-0 z-10" onClick={onClose} />
      <div className="absolute bottom-[68px] left-4 z-20 w-[240px] rounded-xl border border-line bg-card p-1.5">
        <button type="button" onClick={() => { onClose(); void onPickProject(); }} className="flex w-full items-center rounded-lg px-2.5 py-2 text-left text-[12.5px] text-t2 transition-colors hover:bg-hover hover:text-tx">
          Выбрать папку проекта
        </button>
        <button type="button" onClick={() => { onClose(); onPickFile(); }} className="flex w-full items-center rounded-lg px-2.5 py-2 text-left text-[12.5px] text-t2 transition-colors hover:bg-hover hover:text-tx">
          Прикрепить файл
        </button>
        <button
          type="button"
          disabled={!hasProject}
          onClick={() => { onClose(); onEditVerify(); }}
          title={hasProject ? "Команда проверки — агент не закроет задачу, пока она не зелёная" : "Сначала выбери папку проекта"}
          className="flex w-full items-center rounded-lg px-2.5 py-2 text-left text-[12.5px] text-t2 transition-colors hover:bg-hover hover:text-tx disabled:cursor-not-allowed disabled:opacity-45"
        >
          Проверка проекта…
        </button>
      </div>
    </>
  );
}

/** Edit the project's opt-in verify command (.elira/verify). When set, the agent
 *  must run it green after edits before it can declare a task done. */
function VerifyCommandModal({ projectRoot, onClose }: { projectRoot: string; onClose: () => void }) {
  const [command, setCommand] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // True while the input holds Elira's auto-suggested command (nothing saved yet)
  // and the user hasn't edited it — drives the "предложено по проекту" hint.
  const [isSuggested, setIsSuggested] = useState(false);

  useEffect(() => {
    let alive = true;
    getVerifyCommand(projectRoot)
      .then(({ command: cmd, suggested }) => {
        if (!alive) return;
        if (cmd) { setCommand(cmd); setIsSuggested(false); }
        else if (suggested) { setCommand(suggested); setIsSuggested(true); }
      })
      .catch(() => { if (alive) setErr("Не удалось загрузить"); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [projectRoot]);

  async function save() {
    setSaving(true);
    setErr(null);
    try {
      const saved = await setVerifyCommand(projectRoot, command.trim());
      setCommand(saved);
      onClose();
    } catch {
      setErr("Не удалось сохранить");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div className="w-[min(520px,92vw)] rounded-xl border border-line bg-card p-4" onClick={(e) => e.stopPropagation()}>
        <div className="mb-1 flex items-center justify-between">
          <div className="text-[13.5px] font-medium text-tx">Команда проверки проекта</div>
          <button type="button" onClick={onClose} aria-label="Закрыть" className="grid h-6 w-6 place-items-center rounded-md border border-line text-t2 hover:bg-hover hover:text-tx"><X size={14} /></button>
        </div>
        <p className="mb-3 text-[11.5px] leading-relaxed text-mut">
          После правок агент не закроет задачу, пока эта команда не вернёт успех (exit 0).
          Пусто = отключить. Хранится в <span className="font-mono">.elira/verify</span> проекта.
        </p>
        <input
          value={loading ? "" : command}
          onChange={(e) => { setCommand(e.target.value); setIsSuggested(false); }}
          disabled={loading || saving}
          placeholder={loading ? "Загрузка…" : "напр. pytest -q  /  npm test"}
          onKeyDown={(e) => { if (e.key === "Enter") void save(); }}
          className="mb-1 w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-[12.5px] text-tx outline-none focus:border-acl disabled:opacity-60"
        />
        {isSuggested && !err && (
          <div className="mb-1 text-[11px] text-ac">Предложено по проекту — сохрани или поправь.</div>
        )}
        {err && <div className="mb-1 text-[11.5px] text-red-400">{err}</div>}
        <div className="mt-3 flex items-center justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded-lg border border-line px-3 py-1.5 text-[12.5px] text-t2 transition-colors hover:bg-hover hover:text-tx">Отмена</button>
          <button type="button" onClick={() => void save()} disabled={loading || saving} className="rounded-lg bg-ac px-3 py-1.5 text-[12.5px] font-medium text-[#14151b] transition-opacity hover:opacity-90 disabled:opacity-50">
            {saving ? "Сохраняю…" : "Сохранить"}
          </button>
        </div>
      </div>
    </div>
  );
}

function deriveTitle(turns: Turn[]): string {
  const first = turns.find((t) => t.kind === "user");
  const text = first && first.kind === "user" ? first.text : "";
  return text.trim().slice(0, 48) || "Новый чат";
}

/** Drop transient fields (running flag, active tool, blob URLs) before saving. */
function serializeTurns(turns: Turn[]): unknown[] {
  return turns.map((t) => {
    if (t.kind === "agent") return { ...t, running: false, activeTool: undefined };
    if (t.kind === "files") return { ...t, files: t.files.map((f) => ({ ...f, url: undefined })) };
    return t;
  });
}

/** Parse stored turns back into the live shape; tolerant of legacy/unknown rows. */
function deserializeTurns(raw: unknown[]): Turn[] {
  if (!Array.isArray(raw)) return [];
  const out: Turn[] = [];
  for (const x of raw) {
    if (!x || typeof x !== "object" || !("kind" in x)) continue;
    const t = x as Turn;
    if (t.kind === "user" || t.kind === "files") out.push(t);
    else if (t.kind === "agent") out.push({ ...t, running: false, activeTool: undefined });
  }
  return out;
}
