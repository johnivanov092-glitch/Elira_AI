/**
 * CodeWorkspaceShell.tsx
 *
 * Code-agent workspace (Claude-style chat-first layout).
 *
 * Default: streaming code-agent chat takes the FULL width.
 *
 * Top toolbar carries the agent controls (back / project root / model
 * picker / num_ctx / project prompt / index / auto-remember) on the
 * left and a row of 5 drawer-trigger icons on the right:
 *   📦 Артефакты · 🌿 Git · 📁 Файлы · 🧠 RAG · 📜 История
 *
 * Click an icon → that view slides in as a right-side drawer (~420px,
 * resizable, persisted in localStorage). Click the same icon (or X,
 * or Esc) to close.  Ctrl+1..5 toggle the corresponding drawer.
 *
 * Each drawer renders IdeWorkspaceShell with forceView=<key> so the
 * tabbed shell becomes a single focused panel inside the drawer.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  Cpu,
  Database,
  FileText,
  Files,
  Folder,
  FolderOpen,
  GitBranch,
  History,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  Trash2,
  X,
} from "lucide-react";
import IdeWorkspaceShell from "./IdeWorkspaceShell";
import CodeAgentChatShell, { clearLegacyHistory, deleteHistoryFor, readLegacyHistory } from "./CodeAgentChatShell";
import { api } from "../api/ide";
import { UiIcon, IconText } from "./StatusPanels";

const ROOT_KEY = "elira_code_agent_root";
const MODEL_KEY = "elira_code_agent_model";
const STEPS_KEY = "elira_code_agent_steps";
const CTX_KEY_PREFIX = "elira_code_agent_ctx_";
const AUTO_REMEMBER_KEY = "elira_code_agent_auto_remember";
const DRAWER_KEY = "elira_code_workspace_drawer";
const DRAWER_WIDTH_KEY = "elira_code_workspace_drawer_width";
const SESSIONS_INDEX_KEY = "elira_code_agent_sessions_index_v1";
const SESSIONS_SIDEBAR_KEY = "elira_code_workspace_sidebar_collapsed";
const DEFAULT_ROOT = "D:/AIWork/Elira_AI";
const DEFAULT_MODEL = "qwen2.5-coder:7b";
const DEFAULT_MAX_STEPS = 20;
const DEFAULT_NUM_CTX = 16384;
const DEFAULT_DRAWER_WIDTH = 420;
const MIN_DRAWER_WIDTH = 280;
const MAX_DRAWER_WIDTH = 800;
const CTX_OPTIONS = [4096, 8192, 16384, 32768, 65536];
const TOOL_FRIENDLY = ["qwen2.5-coder", "qwen2.5", "qwen3", "llama3.2", "llama3.1", "mistral-nemo", "command-r"];

type Model = { name: string; size?: number };

// ── Sessions index ────────────────────────────────────────────────────

type SessionMeta = {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
};

type SessionsIndex = {
  sessions: SessionMeta[];
  activeId: string;
};

function makeSessionId(): string {
  return `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function loadSessionsIndex(): SessionsIndex {
  try {
    const raw = localStorage.getItem(SESSIONS_INDEX_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed && Array.isArray(parsed.sessions) && typeof parsed.activeId === "string") {
        return parsed as SessionsIndex;
      }
    }
  } catch {
    /* fall through */
  }
  // Bootstrap: migrate legacy single-history if present, otherwise create
  // an empty default session.
  const legacy = readLegacyHistory();
  const firstId = makeSessionId();
  const firstTitle = legacy.length > 0 ? "Импорт прошлой истории" : "Новый чат";
  if (legacy.length > 0) {
    // Move legacy turns to per-session key
    try {
      localStorage.setItem(`elira_code_agent_history_v2_${firstId}`, JSON.stringify(legacy.slice(-200)));
    } catch {}
    clearLegacyHistory();
  }
  const idx: SessionsIndex = {
    sessions: [{ id: firstId, title: firstTitle, created_at: Date.now(), updated_at: Date.now() }],
    activeId: firstId,
  };
  saveSessionsIndex(idx);
  return idx;
}

function saveSessionsIndex(idx: SessionsIndex): void {
  try { localStorage.setItem(SESSIONS_INDEX_KEY, JSON.stringify(idx)); } catch {}
}

function deriveTitle(text: string, fallback = "Новый чат"): string {
  const trimmed = (text || "").trim();
  if (!trimmed) return fallback;
  const firstLine = trimmed.split(/\r?\n/)[0];
  return firstLine.length > 50 ? firstLine.slice(0, 50) + "…" : firstLine;
}

function formatRelTime(ts: number): string {
  const diff = Date.now() - ts;
  const m = Math.floor(diff / 60_000);
  if (m < 1) return "сейчас";
  if (m < 60) return `${m}м`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}ч`;
  const d = Math.floor(h / 24);
  return `${d}д`;
}

/** Open a native folder picker via Tauri's dialog API. Returns the
 *  selected absolute path with forward slashes (normalized), or null
 *  if the user cancelled / Tauri isn't available (browser dev mode).
 */
async function pickFolder(defaultPath?: string): Promise<string | null> {
  // Tauri 1.x: @tauri-apps/api/dialog. We import dynamically so the
  // module doesn't break in non-Tauri contexts (npm run dev in browser).
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const w = window as any;
  if (!w.__TAURI__) return null;
  try {
    const mod = await import("@tauri-apps/api/dialog");
    const selected = await mod.open({
      directory: true,
      multiple: false,
      defaultPath: defaultPath || undefined,
      title: "Выбери корень проекта",
    });
    if (typeof selected === "string" && selected) {
      return selected.replace(/\\/g, "/");
    }
    return null;
  } catch (err) {
    console.error("pickFolder failed:", err);
    return null;
  }
}

type DrawerKey = "artifacts" | "git" | "filetree" | "rag" | "history";

const DRAWER_DEFS: { key: DrawerKey; label: string; icon: typeof Files; hotkey: string }[] = [
  { key: "artifacts", label: "Артефакты", icon: Files, hotkey: "1" },
  { key: "git", label: "Git", icon: GitBranch, hotkey: "2" },
  { key: "filetree", label: "Файлы", icon: FolderOpen, hotkey: "3" },
  { key: "rag", label: "RAG", icon: Database, hotkey: "4" },
  { key: "history", label: "История", icon: History, hotkey: "5" },
];


function readNumber(key: string, fallback: number): number {
  try {
    const raw = localStorage.getItem(key);
    if (raw == null) return fallback;
    const n = Number(raw);
    return Number.isFinite(n) ? n : fallback;
  } catch {
    return fallback;
  }
}
function writeNumber(key: string, n: number) {
  try { localStorage.setItem(key, String(n)); } catch {}
}
function readString(key: string, fallback: string): string {
  try { return localStorage.getItem(key) || fallback; } catch { return fallback; }
}
function writeString(key: string, v: string) {
  try { localStorage.setItem(key, v); } catch {}
}
function readBool(key: string, fallback: boolean): boolean {
  try {
    const raw = localStorage.getItem(key);
    if (raw == null) return fallback;
    return raw === "1" || raw === "true";
  } catch {
    return fallback;
  }
}
function writeBool(key: string, v: boolean) {
  try { localStorage.setItem(key, v ? "1" : "0"); } catch {}
}

function isToolFriendly(name: string): boolean {
  const low = name.toLowerCase();
  return TOOL_FRIENDLY.some((prefix) => low.startsWith(prefix));
}
function formatSize(bytes?: number): string {
  if (!bytes) return "";
  const gb = bytes / (1024 ** 3);
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  const mb = bytes / (1024 ** 2);
  return `${mb.toFixed(0)} MB`;
}


type CodeWorkspaceShellProps = {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  messages?: any[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  libraryFiles?: any[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  setLibraryFiles?: (files: any[]) => void;
  onBackToChat?: () => void;
  onSendToChat?: (text: string) => void;
};


export default function CodeWorkspaceShell(props: CodeWorkspaceShellProps) {
  const { messages, libraryFiles, setLibraryFiles, onBackToChat, onSendToChat } = props;

  const [projectRoot, setProjectRoot] = useState<string>(() => readString(ROOT_KEY, DEFAULT_ROOT));
  const [model, setModel] = useState<string>(() => readString(MODEL_KEY, DEFAULT_MODEL));
  const [maxSteps, setMaxSteps] = useState<number>(() => readNumber(STEPS_KEY, DEFAULT_MAX_STEPS));
  const [numCtx, setNumCtx] = useState<number>(() => readNumber(CTX_KEY_PREFIX + readString(MODEL_KEY, DEFAULT_MODEL), DEFAULT_NUM_CTX));
  const [autoRemember, setAutoRemember] = useState<boolean>(() => readBool(AUTO_REMEMBER_KEY, true));

  // Sessions sidebar state
  const [sessionsIndex, setSessionsIndex] = useState<SessionsIndex>(() => loadSessionsIndex());
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => readBool(SESSIONS_SIDEBAR_KEY, false));
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState<string>("");

  // Persist sessions index whenever it changes
  useEffect(() => { saveSessionsIndex(sessionsIndex); }, [sessionsIndex]);
  useEffect(() => writeBool(SESSIONS_SIDEBAR_KEY, sidebarCollapsed), [sidebarCollapsed]);

  const activeSession = useMemo(
    () => sessionsIndex.sessions.find((s) => s.id === sessionsIndex.activeId) || sessionsIndex.sessions[0],
    [sessionsIndex],
  );

  const switchSession = useCallback((id: string) => {
    setSessionsIndex((idx) => ({ ...idx, activeId: id }));
  }, []);

  const createSession = useCallback(() => {
    const id = makeSessionId();
    const now = Date.now();
    setSessionsIndex((idx) => ({
      sessions: [{ id, title: "Новый чат", created_at: now, updated_at: now }, ...idx.sessions],
      activeId: id,
    }));
  }, []);

  const deleteSession = useCallback((id: string) => {
    setSessionsIndex((idx) => {
      if (idx.sessions.length <= 1) {
        // Don't allow deleting the last session — just reset it.
        deleteHistoryFor(id);
        const fresh = makeSessionId();
        return {
          sessions: [{ id: fresh, title: "Новый чат", created_at: Date.now(), updated_at: Date.now() }],
          activeId: fresh,
        };
      }
      deleteHistoryFor(id);
      const remaining = idx.sessions.filter((s) => s.id !== id);
      const newActive = idx.activeId === id ? remaining[0].id : idx.activeId;
      return { sessions: remaining, activeId: newActive };
    });
  }, []);

  const renameSession = useCallback((id: string, title: string) => {
    setSessionsIndex((idx) => ({
      ...idx,
      sessions: idx.sessions.map((s) => (s.id === id ? { ...s, title: title.trim() || s.title, updated_at: Date.now() } : s)),
    }));
  }, []);

  // Called by the chat shell when the user submits a turn — bump
  // updated_at and auto-derive title for fresh sessions.
  const handleUserTurn = useCallback((text: string) => {
    setSessionsIndex((idx) => ({
      ...idx,
      sessions: idx.sessions.map((s) => {
        if (s.id !== idx.activeId) return s;
        const shouldRetitle = s.title === "Новый чат" || !s.title.trim();
        return {
          ...s,
          title: shouldRetitle ? deriveTitle(text) : s.title,
          updated_at: Date.now(),
        };
      }),
    }));
  }, []);

  // Drawer state (replaces the old split-view ideCollapsed)
  const [activeDrawer, setActiveDrawer] = useState<DrawerKey | null>(() => {
    const raw = readString(DRAWER_KEY, "");
    return (DRAWER_DEFS.find((d) => d.key === raw)?.key) ?? null;
  });
  const [drawerWidth, setDrawerWidth] = useState<number>(() => {
    const n = readNumber(DRAWER_WIDTH_KEY, DEFAULT_DRAWER_WIDTH);
    return Math.max(MIN_DRAWER_WIDTH, Math.min(MAX_DRAWER_WIDTH, n));
  });

  // Model picker plumbing
  const [models, setModels] = useState<Model[] | null>(null);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsError, setModelsError] = useState<string | null>(null);

  // Auto-open file (agent touched something)
  const [autoOpen, setAutoOpen] = useState<{ path: string; nonce: number } | null>(null);

  // Project prompt panel
  const [promptOpen, setPromptOpen] = useState(false);
  const [promptText, setPromptText] = useState("");
  const [promptLoading, setPromptLoading] = useState(false);
  const [promptStatus, setPromptStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [promptError, setPromptError] = useState<string | null>(null);

  // Index project state
  const [indexing, setIndexing] = useState(false);
  const [indexStatus, setIndexStatus] = useState<string | null>(null);
  const [indexError, setIndexError] = useState<string | null>(null);

  const drawerDragRef = useRef(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  // ─── persistence ──────────────────────────────────────────────────────
  useEffect(() => writeString(ROOT_KEY, projectRoot), [projectRoot]);
  useEffect(() => writeString(MODEL_KEY, model), [model]);
  useEffect(() => writeNumber(STEPS_KEY, maxSteps), [maxSteps]);
  useEffect(() => writeBool(AUTO_REMEMBER_KEY, autoRemember), [autoRemember]);
  useEffect(() => { writeNumber(CTX_KEY_PREFIX + model, numCtx); }, [model, numCtx]);
  useEffect(() => {
    const saved = readNumber(CTX_KEY_PREFIX + model, DEFAULT_NUM_CTX);
    setNumCtx(saved);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model]);
  useEffect(() => writeString(DRAWER_KEY, activeDrawer ?? ""), [activeDrawer]);
  useEffect(() => writeNumber(DRAWER_WIDTH_KEY, drawerWidth), [drawerWidth]);

  // ─── models ──────────────────────────────────────────────────────────
  const loadModels = useCallback(async () => {
    setModelsLoading(true);
    setModelsError(null);
    try {
      const { models: raw } = await api.listOllamaModels();
      const list: Model[] = (raw || [])
        .map((m: unknown) => {
          if (typeof m === "string") return { name: m };
          if (m && typeof m === "object") {
            const obj = m as Record<string, unknown>;
            const name = typeof obj.name === "string" ? obj.name
              : typeof obj.model === "string" ? obj.model : "";
            const size = typeof obj.size === "number" ? obj.size : undefined;
            return name ? { name, size } : null;
          }
          return null;
        })
        .filter((v): v is Model => Boolean(v));
      list.sort((a, b) => {
        const at = isToolFriendly(a.name) ? 0 : 1;
        const bt = isToolFriendly(b.name) ? 0 : 1;
        if (at !== bt) return at - bt;
        return a.name.localeCompare(b.name);
      });
      setModels(list);
    } catch (e) {
      setModelsError(String((e as Error)?.message || e));
      setModels([]);
    } finally {
      setModelsLoading(false);
    }
  }, []);

  useEffect(() => { loadModels(); }, [loadModels]);

  // ─── drawer drag-resize ─────────────────────────────────────────────
  useEffect(() => {
    function onMove(e: MouseEvent) {
      if (!drawerDragRef.current || !rootRef.current) return;
      const rect = rootRef.current.getBoundingClientRect();
      // Drawer lives on the RIGHT — width is distance from cursor to root's
      // right edge (= position of the drag handle).
      const w = rect.right - e.clientX;
      setDrawerWidth(Math.max(MIN_DRAWER_WIDTH, Math.min(MAX_DRAWER_WIDTH, w)));
    }
    function onUp() {
      if (drawerDragRef.current) {
        drawerDragRef.current = false;
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      }
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  const startResize = useCallback(() => {
    drawerDragRef.current = true;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, []);

  // ─── drawer behavior ────────────────────────────────────────────────
  // Toolbar icon: toggle. Clicking active icon closes the drawer.
  const toggleDrawer = useCallback((key: DrawerKey) => {
    setActiveDrawer((cur) => (cur === key ? null : key));
  }, []);

  // Drawer tab: switch only. Never closes the drawer (close is a separate X).
  const switchDrawerView = useCallback((key: DrawerKey) => {
    setActiveDrawer(key);
  }, []);

  const closeDrawer = useCallback(() => setActiveDrawer(null), []);

  // Esc closes drawer
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && activeDrawer) {
        closeDrawer();
        return;
      }
      // Ctrl+1..5 toggle drawers
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey) {
        const idx = "12345".indexOf(e.key);
        if (idx >= 0 && idx < DRAWER_DEFS.length) {
          e.preventDefault();
          toggleDrawer(DRAWER_DEFS[idx].key);
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [activeDrawer, toggleDrawer, closeDrawer]);

  // Auto-open file: open Файлы drawer when agent touches a file
  const handleAgentTouchedFile = useCallback((path: string) => {
    setActiveDrawer((cur) => cur || "filetree");
    setAutoOpen({ path, nonce: Date.now() });
  }, []);

  // ─── project prompt ─────────────────────────────────────────────────
  const openPromptEditor = useCallback(async () => {
    setPromptOpen(true);
    setPromptStatus("idle");
    setPromptError(null);
    setPromptLoading(true);
    try {
      const info = await api.getProjectPrompt(projectRoot);
      setPromptText(info.content || "");
    } catch (e) {
      setPromptError(String((e as Error)?.message || e));
      setPromptText("");
    } finally {
      setPromptLoading(false);
    }
  }, [projectRoot]);

  const savePrompt = useCallback(async () => {
    setPromptStatus("saving");
    setPromptError(null);
    try {
      await api.setProjectPromptApi(projectRoot, promptText);
      setPromptStatus("saved");
      setTimeout(() => setPromptStatus("idle"), 2000);
    } catch (e) {
      setPromptStatus("error");
      setPromptError(String((e as Error)?.message || e));
    }
  }, [projectRoot, promptText]);

  // ─── indexing ───────────────────────────────────────────────────────
  const runIndex = useCallback(async () => {
    if (indexing) return;
    setIndexing(true);
    setIndexStatus("Индексирую...");
    setIndexError(null);
    try {
      const res = await api.indexProject({ projectRoot, replace: true });
      if (!res.ok) {
        setIndexError(res.error || "Индексация не удалась");
        setIndexStatus(null);
        return;
      }
      const okMsg = `✓ ${res.files_processed} файлов, ${res.chunks_indexed} чанков в RAG`;
      const errCount = res.failed_chunks ?? 0;
      setIndexStatus(errCount ? `${okMsg} (${errCount} ошибок embedding)` : okMsg);
      if (errCount && res.chunks_indexed === 0) {
        setIndexError("Все чанки провалились — скорее всего nomic-embed-text не установлен. Запусти: ollama pull nomic-embed-text");
      }
      setTimeout(() => setIndexStatus(null), 6000);
    } catch (e) {
      setIndexError(String((e as Error)?.message || e));
      setIndexStatus(null);
    } finally {
      setIndexing(false);
    }
  }, [indexing, projectRoot]);

  // ─── model dropdown items ───────────────────────────────────────────
  const knownModels = useMemo<Model[]>(() => {
    const ms = models || [];
    if (model && !ms.some((m) => m.name === model)) {
      return [{ name: model }, ...ms];
    }
    return ms;
  }, [models, model]);

  const activeDrawerDef = DRAWER_DEFS.find((d) => d.key === activeDrawer) ?? null;

  return (
    <div ref={rootRef} style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {/* TOP BAR */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "8px 12px",
          borderBottom: "1px solid var(--border)",
          flexShrink: 0,
          flexWrap: "wrap",
        }}
      >
        {onBackToChat && (
          <button onClick={onBackToChat} className="soft-btn" style={{ fontSize: 12, padding: "5px 10px" }} title="Назад к обычному чату с Elira">
            <IconText icon={ArrowLeft} size={13} gap={6}>Назад</IconText>
          </button>
        )}

        {/* DRAWER ICONS — left-aligned, right after Back */}
        <div style={{ display: "flex", gap: 4, alignItems: "center", paddingRight: 8, borderRight: "1px solid var(--border-light)" }}>
          {DRAWER_DEFS.map(({ key, label, icon, hotkey }) => {
            const isActive = activeDrawer === key;
            return (
              <button
                key={key}
                onClick={() => toggleDrawer(key)}
                title={`${label}  (Ctrl+${hotkey})`}
                className="soft-btn"
                style={{
                  padding: "5px 8px",
                  fontSize: 11,
                  border: `1px solid ${isActive ? "var(--accent)" : "var(--border)"}`,
                  background: isActive ? "var(--accent-dim)" : "transparent",
                  color: isActive ? "var(--text-primary)" : "var(--text-muted)",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 5,
                  transition: "all 0.12s",
                }}
              >
                <UiIcon icon={icon} size={13} />
                <span style={{ fontSize: 10 }}>{label}</span>
              </button>
            );
          })}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <UiIcon icon={FolderOpen} size={13} />
          <input
            value={projectRoot}
            onChange={(e) => setProjectRoot(e.target.value)}
            placeholder="D:/AIWork/MyProject"
            spellCheck={false}
            title="Корень проекта для code-агента (можно ввести вручную или выбрать через кнопку справа)"
            style={{
              width: 220,
              padding: "5px 8px",
              borderRadius: 6,
              border: "1px solid var(--border)",
              background: "var(--bg-input)",
              color: "var(--text-primary)",
              fontSize: 11,
              outline: "none",
              fontFamily: "var(--font-mono)",
            }}
          />
          <button
            onClick={async () => {
              const picked = await pickFolder(projectRoot);
              if (picked) setProjectRoot(picked);
            }}
            className="soft-btn"
            title="Выбрать папку проекта через системный диалог"
            style={{ padding: "5px 7px", fontSize: 11 }}
          >
            <UiIcon icon={Folder} size={12} />
          </button>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <UiIcon icon={Cpu} size={13} />
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            disabled={modelsLoading}
            title={modelsError ? `Ошибка списка моделей: ${modelsError}` : "Модель Ollama для code-агента"}
            style={{
              padding: "5px 7px",
              borderRadius: 6,
              border: "1px solid var(--border)",
              background: "var(--bg-input)",
              color: "var(--text-primary)",
              fontSize: 11,
              outline: "none",
              minWidth: 170,
              maxWidth: 260,
              fontFamily: "var(--font-mono)",
            }}
          >
            {knownModels.length === 0 && (
              <option value={model}>{model || "qwen2.5-coder:7b"}</option>
            )}
            {knownModels.map((m) => (
              <option key={m.name} value={m.name}>
                {isToolFriendly(m.name) ? "★ " : "  "}
                {m.name}
                {m.size ? `  (${formatSize(m.size)})` : ""}
              </option>
            ))}
          </select>
          <button onClick={loadModels} disabled={modelsLoading} className="soft-btn" title="Обновить список моделей" style={{ padding: "5px 7px", fontSize: 11, opacity: modelsLoading ? 0.6 : 1 }}>
            <UiIcon icon={RefreshCw} size={11} />
          </button>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <span style={{ fontSize: 10, color: "var(--text-muted)" }}>steps</span>
          <input
            type="number"
            min={1}
            max={50}
            value={maxSteps}
            onChange={(e) => setMaxSteps(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
            title="Максимум tool-call шагов в одном запуске"
            style={{ width: 50, padding: "5px 6px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-input)", color: "var(--text-primary)", fontSize: 11, outline: "none", fontFamily: "var(--font-mono)" }}
          />
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <span style={{ fontSize: 10, color: "var(--text-muted)" }}>ctx</span>
          <select
            value={numCtx}
            onChange={(e) => setNumCtx(Number(e.target.value) || DEFAULT_NUM_CTX)}
            title="Размер окна контекста Ollama (num_ctx). Per-model."
            style={{ padding: "5px 7px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-input)", color: "var(--text-primary)", fontSize: 11, outline: "none", fontFamily: "var(--font-mono)" }}
          >
            {(CTX_OPTIONS.includes(numCtx) ? CTX_OPTIONS : [numCtx, ...CTX_OPTIONS]).map((n) => (
              <option key={n} value={n}>{n.toLocaleString()}</option>
            ))}
          </select>
        </div>

        <button onClick={openPromptEditor} className="soft-btn" title="Редактировать .elira/agent.md" style={{ fontSize: 11, padding: "5px 10px" }}>
          <IconText icon={FileText} size={12} gap={5}>Промпт</IconText>
        </button>

        <button onClick={runIndex} disabled={indexing} className="soft-btn" title="Проиндексировать проект в RAG" style={{ fontSize: 11, padding: "5px 10px", opacity: indexing ? 0.6 : 1 }}>
          <IconText icon={Database} size={12} gap={5}>{indexing ? "Индексирую..." : "Индексировать"}</IconText>
        </button>

        <label
          title="Сохранять short summary каждого успешного запуска агента в RAG"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
            fontSize: 11,
            color: autoRemember ? "var(--text-primary)" : "var(--text-muted)",
            cursor: "pointer",
            padding: "5px 8px",
            borderRadius: 6,
            border: "1px solid var(--border)",
            background: autoRemember ? "var(--accent-soft, rgba(99,102,241,0.15))" : "transparent",
          }}
        >
          <input type="checkbox" checked={autoRemember} onChange={(e) => setAutoRemember(e.target.checked)} style={{ margin: 0 }} />
          <span>Запоминать</span>
        </label>
      </div>

      {/* Index status banner */}
      {(indexStatus || indexError) && (
        <div
          style={{
            padding: "6px 14px",
            borderBottom: "1px solid var(--border)",
            background: indexError ? "rgba(255,107,107,0.08)" : "rgba(74,222,128,0.08)",
            color: indexError ? "#ff6b6b" : "#4ade80",
            fontSize: 11,
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <UiIcon icon={Database} size={12} />
          <span style={{ flex: 1 }}>{indexError || indexStatus}</span>
          <button onClick={() => { setIndexError(null); setIndexStatus(null); }} className="soft-btn" style={{ fontSize: 10, padding: "2px 6px" }}>
            <UiIcon icon={X} size={10} />
          </button>
        </div>
      )}

      {/* Project-prompt editor (inline) */}
      {promptOpen && (
        <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)", background: "var(--bg-surface)", flexShrink: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
            <UiIcon icon={FileText} size={13} />
            <div style={{ fontSize: 12, fontWeight: 500 }}>Проектный системный промпт</div>
            <code style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>.elira/agent.md</code>
            <div style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}>
              {promptStatus === "saved" && <span style={{ fontSize: 10, color: "#4ade80" }}>✓ Сохранено</span>}
              {promptStatus === "error" && <span style={{ fontSize: 10, color: "#ff6b6b" }}>✕ Ошибка</span>}
              <button onClick={savePrompt} disabled={promptStatus === "saving" || promptLoading} className="soft-btn" style={{ fontSize: 11, padding: "4px 10px", opacity: promptStatus === "saving" || promptLoading ? 0.5 : 1 }}>
                <IconText icon={Save} size={12} gap={4}>Сохранить</IconText>
              </button>
              <button onClick={() => setPromptOpen(false)} className="soft-btn" style={{ fontSize: 11, padding: "4px 8px" }}>
                <UiIcon icon={X} size={12} />
              </button>
            </div>
          </div>
          {promptError && <div style={{ fontSize: 10, color: "#ff6b6b", marginBottom: 6, fontFamily: "var(--font-mono)" }}>{promptError}</div>}
          <textarea
            value={promptText}
            onChange={(e) => setPromptText(e.target.value)}
            disabled={promptLoading}
            placeholder="Правила проекта: стиль кода, запрещённые папки, конвенции коммитов..."
            spellCheck={false}
            style={{ width: "100%", minHeight: 120, maxHeight: 300, padding: "8px 10px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-input)", color: "var(--text-primary)", fontSize: 11, outline: "none", fontFamily: "var(--font-mono)", resize: "vertical", boxSizing: "border-box", lineHeight: 1.5 }}
          />
        </div>
      )}

      {/* MAIN ROW: chat full-width + optional right drawer */}
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        {/* Sessions sidebar — left of chat */}
        {!sidebarCollapsed && (
          <div
            style={{
              flex: "0 0 200px",
              minWidth: 180,
              maxWidth: 280,
              borderRight: "1px solid var(--border)",
              display: "flex",
              flexDirection: "column",
              background: "var(--bg-sidebar)",
              minHeight: 0,
            }}
          >
            {/* Sidebar header */}
            <div style={{ padding: "8px 10px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
              <UiIcon icon={MessageSquare} size={13} />
              <div style={{ fontSize: 11, fontWeight: 600, flex: 1 }}>Сессии</div>
              <button
                onClick={() => setSidebarCollapsed(true)}
                className="soft-btn"
                title="Скрыть боковую панель"
                style={{ padding: "3px 5px", fontSize: 10 }}
              >
                <UiIcon icon={PanelLeftClose} size={11} />
              </button>
            </div>
            <div style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
              <button
                onClick={createSession}
                style={{
                  width: "100%",
                  padding: "6px 10px",
                  borderRadius: 6,
                  border: "1px solid var(--accent)",
                  background: "var(--accent)",
                  color: "#fff",
                  fontSize: 11,
                  cursor: "pointer",
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 5,
                  fontWeight: 600,
                }}
              >
                <UiIcon icon={Plus} size={12} />
                <span>Новый чат</span>
              </button>
            </div>
            {/* Sessions list */}
            <div style={{ flex: 1, overflow: "auto", padding: "4px 6px" }}>
              {sessionsIndex.sessions.length === 0 && (
                <div style={{ padding: 12, fontSize: 11, color: "var(--text-muted)", textAlign: "center" }}>Нет сессий</div>
              )}
              {sessionsIndex.sessions.map((s) => {
                const isActive = s.id === sessionsIndex.activeId;
                const isEditing = editingSessionId === s.id;
                return (
                  <div
                    key={s.id}
                    onClick={() => !isEditing && switchSession(s.id)}
                    style={{
                      padding: "6px 8px",
                      marginBottom: 2,
                      borderRadius: 6,
                      cursor: isEditing ? "default" : "pointer",
                      background: isActive ? "var(--bg-surface-active)" : "transparent",
                      border: `1px solid ${isActive ? "var(--accent)" : "transparent"}`,
                      transition: "all 0.1s",
                      position: "relative",
                    }}
                    onMouseEnter={(e) => { if (!isActive) (e.currentTarget as HTMLDivElement).style.background = "var(--bg-surface-hover)"; }}
                    onMouseLeave={(e) => { if (!isActive) (e.currentTarget as HTMLDivElement).style.background = "transparent"; }}
                  >
                    {isEditing ? (
                      <input
                        autoFocus
                        value={editingTitle}
                        onChange={(e) => setEditingTitle(e.target.value)}
                        onBlur={() => { renameSession(s.id, editingTitle); setEditingSessionId(null); }}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") { renameSession(s.id, editingTitle); setEditingSessionId(null); }
                          if (e.key === "Escape") { setEditingSessionId(null); }
                        }}
                        style={{ width: "100%", padding: "3px 6px", borderRadius: 4, border: "1px solid var(--accent)", background: "var(--bg-input)", color: "var(--text-primary)", fontSize: 11, outline: "none", fontFamily: "inherit" }}
                      />
                    ) : (
                      <>
                        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                          <div
                            style={{
                              flex: 1,
                              minWidth: 0,
                              fontSize: 11,
                              fontWeight: isActive ? 600 : 400,
                              color: isActive ? "var(--text-primary)" : "var(--text-secondary)",
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            }}
                          >
                            {s.title || "Без названия"}
                          </div>
                          <div style={{ display: "flex", gap: 1, opacity: 0.7 }}>
                            <button
                              onClick={(e) => { e.stopPropagation(); setEditingSessionId(s.id); setEditingTitle(s.title); }}
                              title="Переименовать"
                              style={{ border: "none", background: "transparent", cursor: "pointer", color: "var(--text-muted)", padding: 2, display: "flex", alignItems: "center" }}
                            >
                              <UiIcon icon={Pencil} size={10} />
                            </button>
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                if (confirm(`Удалить сессию «${s.title}»? Вся история этой сессии будет потеряна.`)) deleteSession(s.id);
                              }}
                              title="Удалить"
                              style={{ border: "none", background: "transparent", cursor: "pointer", color: "var(--text-muted)", padding: 2, display: "flex", alignItems: "center" }}
                            >
                              <UiIcon icon={Trash2} size={10} />
                            </button>
                          </div>
                        </div>
                        <div style={{ fontSize: 9, color: "var(--text-muted)", marginTop: 2 }}>{formatRelTime(s.updated_at)}</div>
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Show "open sidebar" button when collapsed */}
        {sidebarCollapsed && (
          <button
            onClick={() => setSidebarCollapsed(false)}
            title="Показать список сессий"
            style={{
              flex: "0 0 24px",
              minWidth: 24,
              border: "none",
              borderRight: "1px solid var(--border)",
              background: "var(--bg-sidebar)",
              color: "var(--text-muted)",
              cursor: "pointer",
              padding: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <UiIcon icon={PanelLeftOpen} size={13} />
          </button>
        )}

        {/* Chat — always present, fills remaining space */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <CodeAgentChatShell
            sessionId={activeSession?.id || ""}
            projectRoot={projectRoot}
            model={model}
            maxSteps={maxSteps}
            numCtx={numCtx}
            autoRemember={autoRemember}
            onAgentTouchedFile={handleAgentTouchedFile}
            onUserTurn={handleUserTurn}
            onRequestNewSession={createSession}
          />
        </div>

        {activeDrawer && (
          <>
            {/* Resize handle between chat (left) and drawer (right) */}
            <div
              onMouseDown={startResize}
              style={{ width: 4, cursor: "col-resize", background: "var(--border)", flexShrink: 0, position: "relative" }}
              title="Тяни чтобы изменить ширину"
            >
              <div
                style={{
                  position: "absolute",
                  top: "50%",
                  left: "50%",
                  transform: "translate(-50%, -50%)",
                  width: 1,
                  height: 24,
                  background: "var(--text-muted)",
                  opacity: 0.4,
                }}
              />
            </div>

            {/* Drawer (RIGHT side) */}
            <div
              style={{
                flex: `0 0 ${drawerWidth}px`,
                minWidth: MIN_DRAWER_WIDTH,
                maxWidth: MAX_DRAWER_WIDTH,
                display: "flex",
                flexDirection: "column",
                background: "var(--bg-root)",
                borderLeft: "1px solid var(--border)",
                boxShadow: "var(--shadow-float)",
                animation: "drawerSlideIn 200ms ease",
              }}
            >
              {/* Drawer header: in-drawer tabs + close button */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  padding: "6px 8px",
                  borderBottom: "1px solid var(--border)",
                  flexShrink: 0,
                  gap: 2,
                  overflow: "hidden",
                }}
              >
                {/* Compact tab row — 5 buttons that share the drawer width.
                    Shows only the icon at narrow widths, icon + label when
                    there is room. */}
                <div style={{ display: "flex", gap: 2, flex: 1, minWidth: 0, overflow: "hidden" }}>
                  {DRAWER_DEFS.map(({ key, label, icon, hotkey }) => {
                    const isActive = activeDrawer === key;
                    return (
                      <button
                        key={key}
                        onClick={() => switchDrawerView(key)}
                        title={`${label}  (Ctrl+${hotkey})`}
                        className="soft-btn"
                        style={{
                          flex: 1,
                          minWidth: 0,
                          padding: "5px 6px",
                          fontSize: 11,
                          border: `1px solid ${isActive ? "var(--accent)" : "transparent"}`,
                          background: isActive ? "var(--accent-dim)" : "transparent",
                          color: isActive ? "var(--text-primary)" : "var(--text-muted)",
                          display: "inline-flex",
                          alignItems: "center",
                          justifyContent: "center",
                          gap: 5,
                          overflow: "hidden",
                          transition: "all 0.12s",
                          fontWeight: isActive ? 600 : 400,
                        }}
                      >
                        <UiIcon icon={icon} size={13} />
                        {/* Show label only when drawer is wide enough */}
                        {drawerWidth > 360 && (
                          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 10 }}>
                            {label}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
                <button
                  onClick={closeDrawer}
                  className="soft-btn"
                  title="Закрыть (Esc)"
                  style={{ fontSize: 11, padding: "5px 8px", marginLeft: 4, flexShrink: 0 }}
                >
                  <UiIcon icon={X} size={12} />
                </button>
              </div>

              {/* Drawer body — single IDE view via forceView */}
              <div style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
                <IdeWorkspaceShell
                  messages={messages as never}
                  libraryFiles={libraryFiles as never}
                  setLibraryFiles={setLibraryFiles as never}
                  onSendToChat={onSendToChat}
                  autoOpenFile={autoOpen?.path}
                  autoOpenNonce={autoOpen?.nonce}
                  forceView={activeDrawer}
                />
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export type { CodeWorkspaceShellProps };
