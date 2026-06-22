import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Code, FileSearch, FolderOpen, Globe, Sparkles, UploadCloud, Wand2 } from "lucide-react";
import { waitForBackend } from "../api/client";
import {
  type CodeAgentMode,
  type CodeSessionMeta,
  createCodeSession,
  deleteCodeSession,
  getCodeSession,
  listCodeSessions,
  patchCodeSession,
} from "../api/codeAgent";
import type { Turn } from "./types";
import { pickFolder } from "../pickFolder";
import { cn } from "../ui/cn";
import { deriveArtifacts, fileArtifactKey } from "./artifacts";
import { Sidebar } from "./Sidebar";
import { Topbar, type MainTab } from "./Topbar";
import { Composer } from "./Composer";
import { Transcript } from "./Transcript";
import { PreviewPanel } from "./PreviewPanel";
import { CommandPalette, type PaletteAction } from "./CommandPalette";
import { Settings } from "./Settings";
import { PipelinesShell } from "./PipelinesShell";
import { TerminalDock } from "./TerminalDock";
import { useAgentRun } from "./useAgentRun";

/** Unified workspace (v4). Phase 1: real transcript wired to the code-agent
 *  stream. Settings/preview/palette content + token streaming land in later
 *  phases; old shells are deleted in Phase 5. */
export default function WorkspaceShell() {
  const [tab, setTab] = useState<MainTab>("chat");
  const [previewOpen, setPreviewOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [project, setProject] = useState("");
  const [connected, setConnected] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [input, setInput] = useState("");
  const [model, setModel] = useState("auto");
  const [sessions, setSessions] = useState<CodeSessionMeta[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);

  const run = useAgentRun(project, model);
  const scrollRef = useRef<HTMLDivElement>(null);
  const artifacts = useMemo(() => deriveArtifacts(run.turns), [run.turns]);
  const lastFileKey = useRef("");

  const refreshSessions = useCallback(() => {
    listCodeSessions().then(setSessions).catch(() => { /* offline */ });
  }, []);
  useEffect(() => { refreshSessions(); }, [refreshSessions]);

  // Persist the conversation when a run finishes (lazy-create the session).
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !run.running) {
      const turns = run.turns;
      if (turns.some((t) => t.kind === "user")) {
        (async () => {
          try {
            let id = sessionId;
            if (!id) {
              const s = await createCodeSession({ title: deriveTitle(turns), projectRoot: project, model });
              id = s.id;
              setSessionId(id);
            }
            await patchCodeSession(id, { title: deriveTitle(turns), turns: serializeTurns(turns), projectRoot: project, model, contextState: run.contextUsage, taskLedger: run.taskLedger });
            refreshSessions();
          } catch { /* offline; keep local */ }
        })();
      }
    }
    wasRunning.current = run.running;
  }, [run.running, run.turns, run.contextUsage, run.taskLedger, sessionId, project, model, refreshSessions]);

  useEffect(() => {
    let alive = true;
    waitForBackend(20, 1500).then((ok) => { if (alive) setConnected(ok); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    const key = fileArtifactKey(artifacts);
    if (key && key !== lastFileKey.current) {
      lastFileKey.current = key;
      setPreviewOpen(true);
    }
  }, [artifacts]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
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
    if (a.kind === "settings") setSettingsOpen(true);
    else if (a.kind === "pipelines") setTab("pipe");
    else if (a.kind === "preview") setPreviewOpen(true);
    else if (a.kind === "prefill") setInput((v) => (v.trim() ? v.trimEnd() + " " : "") + a.text);
  }

  async function pick() {
    const p = await pickFolder(project || undefined);
    if (p) setProject(p);
  }

  function onSend(text: string, mode: CodeAgentMode) {
    // No project required: the backend defaults to a scratch workspace, so chat
    // works out of the box. Picking a folder targets a specific project.
    run.send(text, mode);
  }

  function newChat() {
    run.reset([]);
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
    try {
      const s = await getCodeSession(id);
      run.reset(s ? deserializeTurns(s.turns) : [], s?.task_ledger || [], s?.context_state || null);
      if (s?.project_root) setProject(s.project_root);
      if (s?.model) setModel(s.model);
    } catch {
      run.reset([]);
    }
  }

  async function deleteSession(id: string) {
    try { await deleteCodeSession(id); } catch { /* ignore */ }
    if (id === sessionId) { setSessionId(null); run.reset([]); }
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
      />

      <section className="relative flex min-h-0 min-w-0 flex-col">
        <Topbar
          project={project}
          tab={tab}
          onTab={setTab}
          onPickProject={pick}
          onSettings={() => setSettingsOpen(true)}
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
            if (files.length) run.addFiles(files);
          }}
        >
          {tab === "pipe" ? (
            <PipelinesShell />
          ) : run.turns.length > 0 ? (
            <Transcript turns={run.turns} onApprove={run.approve} onApproveAll={run.approveAll} onResume={run.resume} />
          ) : (
            <ChatEmptyState hasProject={!!project} onPick={pick} onSuggest={(p) => setInput(p)} />
          )}
          {dragging && (
            <div className="pointer-events-none absolute inset-0 grid place-items-center bg-bg/70 text-t2">
              <div className="flex items-center gap-2 text-sm"><UploadCloud size={18} /> Отпусти — файл уйдёт в чат и в память</div>
            </div>
          )}
        </div>

        {terminalOpen && <TerminalDock onClose={() => setTerminalOpen(false)} />}

        {run.autoApprove && (
          <div className="border-t border-acl bg-acs px-4 py-1.5 text-center text-[11.5px] text-ac">
            Авто-одобрение включено для этой сессии — агент действует без запроса (сбросится в новом чате)
          </div>
        )}

        <Composer value={input} onChange={setInput} onPlus={() => setMenuOpen((v) => !v)} onPlugins={() => setPaletteOpen(true)} onSend={onSend} running={run.running} onStop={run.stop} contextUsage={run.contextUsage} />

        {menuOpen && <PlusMenu onClose={() => setMenuOpen(false)} onPickProject={pick} />}
      </section>

      {showPreview && <PreviewPanel artifacts={artifacts} project={project} onClose={() => setPreviewOpen(false)} />}

      {settingsOpen && <Settings model={model} onModel={setModel} onClose={() => setSettingsOpen(false)} project={project} />}
      {paletteOpen && <CommandPalette onAction={onPaletteAction} onClose={() => setPaletteOpen(false)} />}
    </div>
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

function PlusMenu({ onClose, onPickProject }: { onClose: () => void; onPickProject: () => void }) {
  return (
    <>
      <div className="fixed inset-0 z-10" onClick={onClose} />
      <div className="absolute bottom-[68px] left-4 z-20 w-[218px] rounded-xl border border-line bg-card p-1.5">
        <button type="button" onClick={() => { onClose(); void onPickProject(); }} className="flex w-full items-center rounded-lg px-2.5 py-2 text-left text-[12.5px] text-t2 transition-colors hover:bg-hover hover:text-tx">
          Выбрать папку проекта
        </button>
        <button type="button" onClick={onClose} className="flex w-full items-center rounded-lg px-2.5 py-2 text-left text-[12.5px] text-t2 transition-colors hover:bg-hover hover:text-tx">
          Прикрепить файл
        </button>
      </div>
    </>
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
