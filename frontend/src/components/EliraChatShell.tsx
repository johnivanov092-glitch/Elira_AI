/**
 * EliraChatShell.tsx — v3 (TypeScript)
 */

import React, { useEffect, useMemo, useRef, useState, useCallback } from "react";
import {
  BarChart3, BookOpen, Bot, BrainCircuit, Braces, CalendarDays, Code2, Download,
  FileText, Files, FolderOpen, Globe, LayoutDashboard, ListTodo, Menu, MessageSquare,
  Moon, Paperclip, Pause, Pencil, Pin, Play, RefreshCw, ScrollText, Search, Send,
  Settings, Square, Sun, Trash2, Users, Workflow,
} from "lucide-react";
import { LucideIcon } from "lucide-react";
import { api, executeStream } from "../api/ide";
import { createStreamRegistry } from "../streamRegistry";
import { waitForBackend } from "../api/client";
import CodeWorkspaceShell from "./CodeWorkspaceShell";
import PlannerKeywordsPanel from "./PlannerKeywordsPanel";
import MarkdownRenderer from "./MarkdownRenderer";
import ArtifactPanel from "./ArtifactPanel";
import MemoryPanel from "./MemoryPanel";
import ProjectPanel from "./ProjectPanel";
import SpotlightOverlay from "./SpotlightOverlay";
import type { SpotlightHit } from "../api/spotlight";
import { toast } from "./ToastHost";
import "../styles/markdown.css";
import { PROFILE_DESCRIPTIONS, SKILLS } from "../chatConstants";
import {
  loadLibraryFiles, saveLibraryFiles, loadChatContextMap, saveChatContextMap,
  makeId, deriveChatTitle, shortModelName, normalizeErrorMessage, buildHistory, isAutoModel,
} from "../chatUtils";
import {
  UiIcon, IconText, PanelNotice,
  CapabilityStatusSection, PersonaStatusSection, RuntimeStatusSection, AgentOsStatusSection,
} from "./StatusPanels";

// ── Local interfaces ────────────────────────────────────────────────────────

interface LibraryFile {
  id: string;
  name: string;
  size: number;
  type: string;
  uploaded_at?: string;
  preview?: string;
  use_in_context?: boolean;
  source?: string;
  [key: string]: unknown;
}

interface ChatMessage {
  id: string;
  role: string;
  content: string;
  created_at?: string;
  [key: string]: unknown;
}

interface ChatItem {
  id: string;
  title?: string;
  pinned?: boolean;
  memory_saved?: boolean;
  [key: string]: unknown;
}

interface TaskItem {
  id: string;
  title: string;
  description?: string;
  category?: string;
  priority?: string;
  status?: string;
  due_date?: string;
  completed_at?: string;
  [key: string]: unknown;
}

interface PipelineItem {
  id: string;
  name: string;
  task_type?: string;
  interval_minutes?: number;
  enabled?: boolean;
  run_count?: number;
  last_run?: string;
  next_run?: string;
  last_result?: unknown;
  last_result_preview?: unknown;
  last_error?: string;
  [key: string]: unknown;
}

interface PipelineLogEntry {
  id?: string | number;
  started_at?: string;
  finished_at?: string;
  ok?: boolean | number;
  result?: unknown;
  error?: string;
  [key: string]: unknown;
}

interface TgUser {
  chat_id: string | number;
  first_name?: string;
  last_name?: string;
  username?: string;
  allowed?: boolean;
  [key: string]: unknown;
}

interface TgLogEntry {
  direction?: string;
  created_at?: string;
  text?: string;
  [key: string]: unknown;
}

interface Plugin {
  name: string;
  enabled?: boolean;
  icon?: string;
  description?: string;
  version?: string;
  [key: string]: unknown;
}

interface ChartData {
  labels: string[];
  values: number[];
  valueLabel: string;
}

interface TaskForm {
  title: string;
  description: string;
  category: string;
  priority: string;
  due_date: string;
  [key: string]: unknown;
}

interface PipeForm {
  name: string;
  task_type: string;
  interval_minutes: number;
  task_data: Record<string, string>;
  [key: string]: unknown;
}

type RouteMap = Record<string, string[]>;

// Shared module-level stream registry for the regular chat (same mechanism the
// code agent uses). Survives re-renders/unmounts; a stream started for one chat
// keeps running and finalizes via its targetChatId closure even after switching.
const chatRuns = createStreamRegistry<{ text: string; phase: string }>();

// ── Helper functions ────────────────────────────────────────────────────────

const PIPELINE_TYPE_LABELS: Record<string, string> = {
  prompt: "Промпт",
  web_search: "Веб-поиск",
  plugin: "Плагин",
  workflow: "Workflow",
  http: "HTTP",
};

const PIPELINE_TASK_DATA_KEYS: Record<string, string> = {
  prompt: "prompt",
  web_search: "query",
  plugin: "plugin_name",
  workflow: "workflow_id",
  http: "url",
};

const PIPELINE_TASK_PLACEHOLDERS: Record<string, string> = {
  prompt: "Промпт для LLM",
  web_search: "Поисковый запрос",
  plugin: "Имя плагина",
  workflow: "Workflow ID",
  http: "URL",
};
// Shown under "Автоматические задачи по расписанию", changes with the type.
const PIPELINE_TYPE_DESCRIPTIONS: Record<string, string> = {
  prompt: "Отправляет промпт в LLM по расписанию и сохраняет ответ. Веб-поиск выключен — модель отвечает из своих знаний.",
  web_search: "Выполняет поисковый запрос по расписанию и сохраняет найденные результаты. Кол-во результатов задаётся ниже.",
  plugin: "Запускает указанный плагин с аргументами по расписанию.",
  workflow: "Запускает workflow по его ID (движок Workflows) по расписанию.",
  http: "Делает HTTP-запрос на URL (webhook / внешний API) по расписанию и сохраняет ответ.",
};
const PIPELINE_WEB_MODE_DESCRIPTIONS: Record<string, string> = {
  "": "Обычный поиск: Tavily + DuckDuckGo + Wikipedia.",
  news: "Поиск по новостным источникам (свежие новости).",
  local_news: "Приоритет локальным (KZ) новостным сайтам — nur.kz, tengrinews.kz и т.п.",
};
const PIPELINE_DISPLAY_TIME_ZONE = "Asia/Almaty";

function getPipelineTaskDataKey(taskType: string): string {
  return PIPELINE_TASK_DATA_KEYS[taskType] || "prompt";
}

function getPipelineTaskInputValue(taskData: Record<string, string>): string {
  return taskData.prompt || taskData.query || taskData.plugin_name || taskData.workflow_id || taskData.url || "";
}

function parsePipelineResult(value: unknown): unknown {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!trimmed) return null;
  try { return JSON.parse(trimmed); } catch { return trimmed; }
}

function isPipelineResultVisible(value: unknown): boolean {
  return value !== undefined && value !== null && value !== "";
}

function isRecordValue(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function parsePipelineTimestamp(raw: string): Date | null {
  if (!raw) return null;
  const normalized = /(?:z|[+-]\d{2}:?\d{2})$/i.test(raw) ? raw : `${raw}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatPipelineTimestamp(raw?: string): string {
  if (!raw) return "Без даты";
  const date = parsePipelineTimestamp(raw);
  return date ? date.toLocaleString("ru-RU", { timeZone: PIPELINE_DISPLAY_TIME_ZONE }) : raw;
}

function formatPipelineLogDate(log: PipelineLogEntry): string {
  return formatPipelineTimestamp(log.finished_at || log.started_at);
}

function isPipelineLogOk(log: PipelineLogEntry): boolean {
  return log.ok === true || log.ok === 1;
}

function getPipelineOutputLogs(pipeline: PipelineItem, logs: PipelineLogEntry[]): PipelineLogEntry[] {
  if (logs.length > 0) return logs;
  const preview = isPipelineResultVisible(pipeline.last_result_preview) ? pipeline.last_result_preview : pipeline.last_result;
  if (!isPipelineResultVisible(preview)) return [];
  return [{
    id: "last_result",
    started_at: pipeline.last_run,
    finished_at: pipeline.last_run,
    ok: !pipeline.last_error,
    result: preview,
    error: pipeline.last_error,
  }];
}

function formatPipelineFallbackResult(value: unknown): string {
  const parsed = parsePipelineResult(value);
  if (parsed === null || parsed === undefined) return "";
  if (typeof parsed === "string") return parsed;
  if (isRecordValue(parsed)) {
    const result = parsed as Record<string, unknown>;
    if (typeof result.answer === "string" && result.answer.trim()) return result.answer;
    if (typeof result.body === "string" && result.body.trim()) return result.body;
    return JSON.stringify(parsed, null, 2);
  }
  return String(parsed);
}

function splitSearchSnippet(body: string): string[] {
  const normalized = body.replace(/\s+/g, " ").trim();
  if (!normalized) return [];

  const sentences = normalized.split(/(?<=[.!?])\s+(?=[A-ZА-ЯЁ0-9])/u);
  const items: string[] = [];

  for (const sentence of sentences) {
    const text = sentence.trim();
    if (!text || /^\[Текст обрезан\]$/i.test(text) || /^Новости за сегодня\.?$/i.test(text)) continue;
    if (/^Все материалы\b/i.test(text)) continue;

    if (/^\d{1,2}\s+[а-яё]+\s+\d{4}\s*г\.,?\s*\d{1,2}:\d{2}\.?$/iu.test(text) && items.length > 0) {
      items[items.length - 1] = `${items[items.length - 1]} ${text}`;
      continue;
    }

    items.push(text);
  }

  return items.length > 0 ? items : [normalized];
}

function SearchSnippetText({ body }: { body: string }): JSX.Element {
  const parts = splitSearchSnippet(body);
  if (parts.length <= 1) {
    return (
      <div style={{marginTop:4,color:"var(--text)",whiteSpace:"normal",overflowWrap:"anywhere",wordBreak:"normal",lineHeight:1.45}}>
        {parts[0] || body}
      </div>
    );
  }

  return (
    <ul style={{margin:"6px 0 0 16px",padding:0,color:"var(--text)",display:"grid",gap:4,lineHeight:1.45}}>
      {parts.map((part, index) => (
        <li key={`${part.slice(0, 24)}-${index}`} style={{whiteSpace:"normal",overflowWrap:"anywhere",wordBreak:"normal"}}>
          {part}
        </li>
      ))}
    </ul>
  );
}

function PipelineResultView({ value }: { value: unknown }): JSX.Element {
  const parsed = parsePipelineResult(value);

  if (parsed === null || parsed === undefined || parsed === "") {
    return <div style={{color:"var(--text-muted)"}}>Пустой результат</div>;
  }

  if (typeof parsed === "string") {
    return <div style={{whiteSpace:"pre-wrap",wordBreak:"break-word"}}>{parsed}</div>;
  }

  if (!isRecordValue(parsed)) {
    return <div style={{whiteSpace:"pre-wrap",wordBreak:"break-word"}}>{String(parsed)}</div>;
  }

  const results = Array.isArray(parsed.results) ? parsed.results.filter(isRecordValue) : [];
  if (results.length > 0) {
    const enginesAttempted = Array.isArray(parsed.engines_attempted) ? parsed.engines_attempted.map(String).filter(Boolean) : [];
    const enginesUsed = Array.isArray(parsed.engines_used) ? parsed.engines_used.map(String).filter(Boolean) : [];
    const engineErrors = isRecordValue(parsed.engine_errors)
      ? Object.entries(parsed.engine_errors).filter(([, message]) => String(message || "").trim())
      : [];
    return (
      <div style={{display:"grid",gap:8}}>
        {typeof parsed.query === "string" && <div style={{color:"var(--text-muted)"}}>Запрос: {parsed.query}</div>}
        {(enginesAttempted.length > 0 || enginesUsed.length > 0) && (
          <div style={{color:"var(--text-muted)"}}>
            {enginesAttempted.length > 0 && <span>Проверены: {enginesAttempted.join(", ")}</span>}
            {enginesAttempted.length > 0 && enginesUsed.length > 0 && <span> • </span>}
            {enginesUsed.length > 0 && <span>Сработали: {enginesUsed.join(", ")}</span>}
          </div>
        )}
        {engineErrors.length > 0 && (
          <div style={{color:"#f59e0b",display:"grid",gap:2}}>
            {engineErrors.map(([engine, message]) => (
              <div key={engine}>{engine}: {String(message)}</div>
            ))}
          </div>
        )}
        {results.map((item, index) => {
          const title = typeof item.title === "string" ? item.title : `Результат ${index + 1}`;
          const href = typeof item.href === "string" ? item.href : "";
          const body = typeof item.body === "string" ? item.body : "";
          const engine = typeof item.engine === "string" ? item.engine : "";
          return (
            <div key={`${href || title}-${index}`} style={{padding:"7px 8px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-surface)",display:"grid",gridTemplateColumns:"24px minmax(0, 1fr)",gap:6,minWidth:0,overflow:"hidden"}}>
              <div style={{fontWeight:700,color:"var(--text-muted)"}}>{index + 1}.</div>
              <div style={{minWidth:0}}>
                {href ? <a href={href} target="_blank" rel="noreferrer" style={{color:"var(--accent)",fontWeight:600,overflowWrap:"anywhere"}}>{title}</a> : <span style={{fontWeight:600,overflowWrap:"anywhere"}}>{title}</span>}
                {body && <SearchSnippetText body={body} />}
                {engine && <div style={{marginTop:4,color:"var(--text-muted)"}}>Источник поиска: {engine}</div>}
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  if (typeof parsed.answer === "string" && parsed.answer.trim()) {
    return <div style={{whiteSpace:"pre-wrap",wordBreak:"break-word"}}>{parsed.answer}</div>;
  }

  if (typeof parsed.body === "string" && parsed.body.trim()) {
    return (
      <div>
        {typeof parsed.status === "number" && <div style={{marginBottom:4,color:"var(--text-muted)"}}>HTTP {parsed.status}</div>}
        <div style={{whiteSpace:"pre-wrap",wordBreak:"break-word"}}>{parsed.body}</div>
      </div>
    );
  }

  if (typeof parsed.error === "string" && parsed.error.trim()) {
    return <div style={{color:"#f44336",whiteSpace:"pre-wrap",wordBreak:"break-word"}}>{parsed.error}</div>;
  }

  return <pre style={{margin:0,whiteSpace:"pre-wrap",wordBreak:"break-word"}}>{formatPipelineFallbackResult(parsed)}</pre>;
}

function getChatContextFiles(lib: LibraryFile[], chatId: string): LibraryFile[] {
  if (!chatId) return [];
  const map = loadChatContextMap();
  const ids = new Set(map[chatId] || []);
  return lib.filter(i => ids.has(i.id) && i.preview);
}

async function fileToLibraryRecord(file: File): Promise<LibraryFile> {
  let preview = "";
  const name = file.name || "";
  const ext = name.split(".").pop()?.toLowerCase() ?? "";

  const textExts = ["txt","md","json","js","jsx","ts","tsx","py","css","html","htm","yml","yaml","xml","csv","log","ini","toml","bat","cmd","ps1","sh","sql","rb","php","java","c","cpp","h","hpp","cs","go","rs","swift","kt","r","m","lua","pl","tcl","asm","cfg","conf","env"];
  const isText = file.type.startsWith("text/") || textExts.includes(ext);
  if (isText) try { preview = (await file.text()).slice(0, 12000); } catch {}

  const serverExts = ["pdf","docx","doc","xlsx","xls","xlsm","zip","bas","vbs","vba","cls","frm","rsc"];
  if (serverExts.includes(ext)) try {
    const d = await api.extractUploadedFileText(file) as Record<string, unknown>;
    preview = ((d.text as string) || "").slice(0, 12000);
  } catch {}

  return { id: makeId("lib"), name: file.name, size: file.size, type: file.type || ext || "unknown", uploaded_at: new Date().toISOString(), preview, use_in_context: true, source: "upload" };
}

// Мемоизированный компонент сообщения
const MessageItem = React.memo(function MessageItem({ msg }: { msg: ChatMessage }) {
  return (
    <div className={`message-row ${msg.role}`}>
      <div className={`message-bubble smaller-text ${msg.role === "assistant" ? "assistant-bubble" : "user-bubble"}`}>
        {msg.role === "assistant" ? <MarkdownRenderer content={msg.content}/> : msg.content}
      </div>
    </div>
  );
});

// ── Main component ──────────────────────────────────────────────────────────

export default function EliraChatShell(): JSX.Element {
  const fileRef = useRef<HTMLInputElement>(null);
  const msgRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const streamRef = useRef<AbortController | null>(null);
  const stoppedRef = useRef(false);
  const initRef = useRef(false);
  // Per-chat stream state now lives in the shared module-level `chatRuns`
  // registry (see streamRegistry.ts). When the user switches chats mid-stream
  // the previous stream keeps running there and finalizes via its targetChatId
  // closure even if `chatId` (the visible one) has changed.
  // ref to the active chat so onDone closures can compare against current view
  const activeChatIdRef = useRef("");

  const [mainTab, setMainTab] = useState("chat");
  const [sideTab, setSideTab] = useState("chats");
  const [spotlightOpen, setSpotlightOpen] = useState(false);
  // When the user picks a code-agent session from Spotlight we need to
  // tell CodeWorkspaceShell to switch to that session. Lifted here as
  // a "request" that the child consumes once and clears.
  const [codeSessionRequest, setCodeSessionRequest] = useState<string | null>(null);
  const [model, setModel] = useState("gemma3:4b");
  const [modelOpts, setModelOpts] = useState<unknown[]>([]);
  const [profile, setProfile] = useState("Универсальный");
  const [skills, setSkills] = useState<string[]>(["web_search","file_context","memory","pdf_reader","python_exec","code_analysis","file_gen","translator","converter","archiver","http_api","screenshot","image_gen"]);
  const [chats, setChats] = useState<ChatItem[]>([]);
  const [chatId, setChatId] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sideSearch, setSideSearch] = useState("");
  const [libSearch, setLibSearch] = useState("");
  const [error, setError] = useState("");
  const [drag, setDrag] = useState(false);
  const [working, setWorking] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [streamText, setStreamText] = useState("");
  const [phase, setPhase] = useState("");
  // Re-render the sidebar streaming dot when a chat stream starts/ends.
  const [, setRunsTick] = useState(0);
  useEffect(() => chatRuns.subscribe(() => setRunsTick((t) => t + 1)), []);
  const [libraryFiles, setLibraryFiles] = useState<LibraryFile[]>(loadLibraryFiles() as LibraryFile[]);
  const [selLibId, setSelLibId] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [renameVal, setRenameVal] = useState("");
  const [showPanel, setShowPanel] = useState(false);
  const [showExportMenu, setShowExportMenu] = useState(false);
  const [mobileSidebar, setMobileSidebar] = useState(false);
  const [pluginList, setPluginList] = useState<Plugin[]>([]);
  const [dashData, setDashData] = useState<Record<string, unknown> | null>(null);
  const [projectBrainStatus, setProjectBrainStatus] = useState<Record<string, unknown> | null>(null);
  const [personaStatus, setPersonaStatus] = useState<Record<string, unknown> | null>(null);
  const [runtimeStatus, setRuntimeStatus] = useState<Record<string, unknown> | null>(null);
  const [agentOsHealth, setAgentOsHealth] = useState<Record<string, unknown> | null>(null);
  const [agentOsDashboard, setAgentOsDashboard] = useState<Record<string, unknown> | null>(null);
  const [agentOsLimits, setAgentOsLimits] = useState<Record<string, unknown> | null>(null);
  const [personaBusy, setPersonaBusy] = useState(false);
  const [dashboardError, setDashboardError] = useState("");
  const [pipelinesList, setPipelinesList] = useState<PipelineItem[]>([]);
  const [pipelineLogsById, setPipelineLogsById] = useState<Record<string, PipelineLogEntry[]>>({});
  const [pipelinesError, setPipelinesError] = useState("");
  const [pipeForm, setPipeForm] = useState<PipeForm>({name:"",task_type:"prompt",interval_minutes:60,task_data:{prompt:""}});
  const [tasksList, setTasksList] = useState<TaskItem[]>([]);
  const [tasksError, setTasksError] = useState("");
  const [taskFilter, setTaskFilter] = useState("active");
  const [taskForm, setTaskForm] = useState<TaskForm>({title:"",description:"",category:"general",priority:"medium",due_date:""});
  const [taskStats, setTaskStats] = useState<Record<string, unknown> | null>(null);
  const [editingTask, setEditingTask] = useState<string | null>(null);
  const [tgConfig, setTgConfig] = useState<Record<string, unknown> | null>(null);
  const [tgUsers, setTgUsers] = useState<TgUser[]>([]);
  const [tgLog, setTgLog] = useState<TgLogEntry[]>([]);
  const [telegramError, setTelegramError] = useState("");
  const [tgTokenInput, setTgTokenInput] = useState("");
  const [tgTab, setTgTab] = useState("setup");
  const [multiAgent, setMultiAgent] = useState(false);
  const [orchestrationEnabled, setOrchestrationEnabled] = useState(false);
  const [chartData, setChartData] = useState<ChartData | null>(null);
  const [ollamaContext, setOllamaContext] = useState(8192);
  const [settingsModel, setSettingsModel] = useState("gemma3:4b");
  const [settingsProfile, setSettingsProfile] = useState("Универсальный");
  const [settingsContext, setSettingsContext] = useState(8192);
  const [settingsSaved, setSettingsSaved] = useState(false);
  const [routeMap, setRouteMap] = useState<RouteMap>({ code: [], project: [], research: [], chat: [] });
  const [theme, setTheme] = useState(() => localStorage.getItem("elira_theme") || "dark");
  const [backendStatus, setBackendStatus] = useState<"checking" | "online" | "offline">("checking");
  const [backendAttempt, setBackendAttempt] = useState(0);

  useEffect(() => { startupWithHealthCheck(); return () => { if (streamRef.current) { streamRef.current.abort(); streamRef.current = null; } }; }, []);
  useEffect(() => { activeChatIdRef.current = chatId; }, [chatId]);
  useEffect(() => { if (msgRef.current) msgRef.current.scrollTop = msgRef.current.scrollHeight; }, [messages, chatId]);
  useEffect(() => {
    if (!error) return;
    if (error.startsWith("Tasks: ")) setTasksError(error.replace(/^Tasks:\s*/, ""));
    if (error.startsWith("Telegram: ")) setTelegramError(error.replace(/^Telegram:\s*/, ""));
    if (error.startsWith("Pipelines: ")) setPipelinesError(error.replace(/^Pipelines:\s*/, ""));
    if (error.startsWith("Dashboard: ")) setDashboardError(error.replace(/^Dashboard:\s*/, ""));
  }, [error]);
  useEffect(() => { if (streaming && msgRef.current) { const id = requestAnimationFrame(() => { msgRef.current && (msgRef.current.scrollTop = msgRef.current.scrollHeight); }); return () => cancelAnimationFrame(id); } }, [streamText, streaming]);
  useEffect(() => { if (!taRef.current) return; taRef.current.style.height = "36px"; taRef.current.style.height = `${Math.min(120, taRef.current.scrollHeight)}px`; }, [input]);

  useEffect(() => {
    if (!showExportMenu) return;
    const h = (e: MouseEvent) => { if (!(e.target as Element).closest(".export-dropdown-wrap")) setShowExportMenu(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [showExportMenu]);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("elira_theme", theme);
  }, [theme]);

  const workingRef = useRef(false);
  workingRef.current = working;
  useEffect(() => {
    function onGlobalKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key === "n") { e.preventDefault(); newChat(false); }
      if (e.key === "Escape" && workingRef.current) {
        e.preventDefault();
        stoppedRef.current = true;
        if (streamRef.current) { streamRef.current.abort(); streamRef.current = null; }
        setStreamText(""); setStreaming(false); setWorking(false); setPhase("");
      }
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === "T") { e.preventDefault(); setTheme(t => { const order = ["dark","light","cursor","cyber","glass","minimal"]; const i = order.indexOf(t); return order[(i + 1) % order.length]; }); }
    }
    window.addEventListener("keydown", onGlobalKey);
    return () => window.removeEventListener("keydown", onGlobalKey);
  }, []);

  useEffect(() => {
    api.listLibraryFiles().then(d => {
      const data = d as Record<string, unknown>;
      if (data?.ok && Array.isArray(data.items) && data.items.length) {
        const ctxMap = loadChatContextMap();
        const activeIds = new Set(Object.values(ctxMap).flat());
        setLibraryFiles(prev => {
          const merged = [...(data.items as Record<string, unknown>[]).map(i => ({...i, id: `db-${i.id}`, source: "sqlite", use_in_context: activeIds.has(`db-${i.id}`)})), ...prev.filter(f => f.source !== "sqlite")] as LibraryFile[];
          const seen = new Set<string>();
          const unique = merged.filter(f => { const k = f.name + f.size; if (seen.has(k)) return false; seen.add(k); return true; });
          saveLibraryFiles(unique);
          return unique;
        });
      }
    }).catch(() => {});
  }, []);

  useEffect(() => {
    const lastMsg = messages[messages.length - 1];
    if (lastMsg?.role === "assistant" && /```\w*\n[\s\S]{20,}?```/.test(lastMsg.content || "")) {
      setShowPanel(true);
    }
  }, [messages]);

  async function startupWithHealthCheck() {
    if (initRef.current) return;
    setBackendStatus("checking");
    const ok = await waitForBackend(20, 2000, (attempt) => setBackendAttempt(attempt));
    if (!ok) {
      setBackendStatus("offline");
      return;
    }
    setBackendStatus("online");
    bootstrapApp();
  }

  async function bootstrapApp() {
    if (initRef.current) return;
    initRef.current = true;
    try {
      const [m, c, settings] = await Promise.all([api.listOllamaModels(), api.listChats(), api.getSettings()]) as [Record<string, unknown>, unknown[], Record<string, unknown>];
      const ml = Array.isArray(m?.models) ? m.models as unknown[] : Array.isArray(m) ? m as unknown[] : [];
      const rawSavedModel = (settings?.default_model as string) || "gemma3:4b";
      const savedProfile = (settings?.agent_profile as string) || "Универсальный";
      const savedCtx = (settings?.ollama_context as number) || 8192;
      const getName = (item: unknown) => typeof item === "string" ? item : ((item as Record<string, unknown>).name || (item as Record<string, unknown>).model || "") as string;
      const fallbackModel = ml.length ? getName(ml[0]) : "gemma3:4b";
      const savedModel = isAutoModel(rawSavedModel) ? fallbackModel : rawSavedModel;
      const preferred = ml.find((item) => getName(item) === savedModel);
      const chosenModel = preferred ? getName(preferred) : ml.length ? getName(ml[0]) : "gemma3:4b";
      setModelOpts(ml);
      setModel(chosenModel);
      setProfile(savedProfile);
      setOllamaContext(savedCtx);
      setSettingsModel(savedModel);
      setSettingsProfile(savedProfile);
      setSettingsContext(savedCtx);
      if (settings?.route_model_map) setRouteMap(settings.route_model_map as RouteMap);
      setOrchestrationEnabled(Boolean(settings?.orchestration_enabled));
      setChats((c || []) as ChatItem[]);
      setChatId("");
      setMessages([]);
      setInput("");
      setRenaming(false);
      setStreamText("");
      setStreaming(false);
      setPhase("");
    } catch (e) { setError(normalizeErrorMessage(e)); }
  }

  async function refreshModels() {
    try {
      const m = await api.listOllamaModels() as Record<string, unknown>;
      const ml = Array.isArray(m?.models) ? m.models as unknown[] : Array.isArray(m) ? m as unknown[] : [];
      setModelOpts(ml);
      return ml;
    } catch { return []; }
  }

  function buildSettingsPayload(overrides: Record<string, unknown> = {}) {
    const defaultModel = isAutoModel(settingsModel) ? (model || "gemma3:4b") : settingsModel;
    return {
      ollama_context: settingsContext,
      default_model: defaultModel,
      agent_profile: settingsProfile,
      route_model_map: routeMap,
      orchestration_enabled: orchestrationEnabled,
      ...overrides,
    };
  }

  async function saveSettings(overrides: Record<string, unknown> = {}, applyDefaults = true) {
    const payload = buildSettingsPayload(overrides);
    await api.updateSettings(payload);
    if (typeof payload.default_model === "string") setSettingsModel(payload.default_model);
    if (typeof payload.orchestration_enabled === "boolean") setOrchestrationEnabled(payload.orchestration_enabled);
    if (applyDefaults) {
      if (typeof payload.default_model === "string") setModel(payload.default_model);
      setProfile(settingsProfile);
      setOllamaContext(settingsContext);
    }
    setSettingsSaved(true);
    setTimeout(() => setSettingsSaved(false), 2000);
  }

  async function toggleOrchestration(nextEnabled: boolean) {
    setOrchestrationEnabled(nextEnabled);
    if (nextEnabled) setMultiAgent(false);
    setSettingsSaved(false);
    try {
      await saveSettings({ orchestration_enabled: nextEnabled }, false);
    } catch (e) {
      setOrchestrationEnabled(!nextEnabled);
      setError(normalizeErrorMessage(e));
    }
  }

  async function loadPipelineLogsForIds(ids: string[]) {
    const uniqueIds = Array.from(new Set(ids.filter(Boolean)));
    if (!uniqueIds.length) {
      setPipelineLogsById({});
      return;
    }

    const pairs = await Promise.all(uniqueIds.map(async (id) => {
      try {
        const logs = await api.getPipelineLogs(id, 20) as PipelineLogEntry[];
        return [id, logs] as const;
      } catch {
        return [id, []] as const;
      }
    }));
    setPipelineLogsById(Object.fromEntries(pairs));
  }

  async function loadPipelines() {
    setPipelinesError("");
    try {
      const pipelines = await api.listPipelines() as PipelineItem[];
      setPipelinesList(pipelines);
      await loadPipelineLogsForIds(pipelines.map(p => p.id));
    } catch (e) {
      const message = normalizeErrorMessage(e);
      setPipelinesList([]);
      setPipelineLogsById({});
      setPipelinesError(message);
      setError(`Pipelines: ${message}`);
    }
  }

  async function loadTelegram() {
    setTelegramError("");
    try {
      const data = await api.getTelegramOverview(30) as Record<string, unknown>;
      setTgConfig(data.config as Record<string, unknown>);
      setTgUsers((data.users as TgUser[]) || []);
      setTgLog((data.log as TgLogEntry[]) || []);
    } catch (e) {
      const message = normalizeErrorMessage(e);
      setTgConfig(null); setTgUsers([]); setTgLog([]);
      setTelegramError(message);
      setError(`Telegram: ${message}`);
    }
  }

  async function loadTasks(filter?: string) {
    const f = filter || taskFilter;
    setTasksError("");
    try {
      const data = await api.getTasksOverview(f) as Record<string, unknown>;
      setTasksList((data.tasks as TaskItem[]) || []);
      setTaskStats((data.stats as Record<string, unknown>) || null);
    } catch (e) {
      const message = normalizeErrorMessage(e);
      setTasksList([]); setTaskStats(null);
      setTasksError(message);
      setError(`Tasks: ${message}`);
    }
  }

  async function loadDashboard() {
    setDashboardError("");
    try {
      const data = await api.getDashboardOverview() as Record<string, unknown>;
      setDashData((data.stats as Record<string, unknown>) || null);
      setProjectBrainStatus((data.projectBrainStatus as Record<string, unknown>) || null);
      setPersonaStatus((data.personaStatus as Record<string, unknown>) || null);
      setRuntimeStatus((data.runtimeStatus as Record<string, unknown>) || null);
      setAgentOsHealth((data.agentOsHealth as Record<string, unknown>) || null);
      setAgentOsDashboard((data.agentOsDashboard as Record<string, unknown>) || null);
      setAgentOsLimits((data.agentOsLimits as Record<string, unknown>) || null);
      const message = Array.isArray(data.errors) ? (data.errors as string[]).filter(Boolean).join(" | ") : "";
      setDashboardError(message);
      setError(message ? `Dashboard: ${message}` : "");
    } catch (e) {
      const message = normalizeErrorMessage(e);
      setDashData(null); setProjectBrainStatus(null); setPersonaStatus(null);
      setRuntimeStatus(null); setAgentOsHealth(null); setAgentOsDashboard(null); setAgentOsLimits(null);
      setDashboardError(message);
      setError(`Dashboard: ${message}`);
    }
  }

  async function handlePersonaRollback(version: unknown) {
    if (!version) return;
    setPersonaBusy(true);
    try {
      await api.rollbackPersona(version as string);
      await loadDashboard();
    } catch (e) {
      setDashboardError(normalizeErrorMessage(e));
      setError(`Dashboard: ${normalizeErrorMessage(e)}`);
    } finally { setPersonaBusy(false); }
  }

  async function loadPluginList() {
    try {
      setPluginList(await api.listPlugins() as Plugin[]);
    } catch (e) {
      setPluginList([]);
      setError(`Plugins: ${normalizeErrorMessage(e)}`);
    }
  }

  async function loadChats(sel = "") {
    const next = (await api.listChats() as ChatItem[]) || [];
    setChats(next);
    if (sel) setChatId(sel);
    return next;
  }

  function resetDraftChat(clearError = false) {
    // Note: do NOT abort streamRef — that would kill a background stream
    // belonging to another chat. The view just resets to draft.
    activeChatIdRef.current = "";
    setChatId(""); setMessages([]); setInput(""); setRenaming(false);
    setStreamText(""); setStreaming(false); setWorking(false); setPhase(""); setShowExportMenu(false);
    streamRef.current = null;
    if (clearError) setError("");
  }

  async function newChat(silent = false) {
    try {
      setMessages([]); setInput(""); setRenaming(false); setStreamText(""); setStreaming(false); setPhase("");
      const c = await api.createChat({ title: "Новый чат", clean: true }) as ChatItem;
      activeChatIdRef.current = c.id;
      await loadChats(c.id); setChatId(c.id); setSideTab("chats");
      if (!silent) setError(""); return c;
    } catch (e) { setError(normalizeErrorMessage(e)); return null; }
  }

  async function openChat(id: string) {
    try {
      // Do NOT abort the in-flight stream for the previous chat — it must
      // finish in the background and append its result via onDone's
      // targetChatId closure. We just visually swap to the new chat here.
      // Update the ref BEFORE setChatId so any onError/onDone callback that
      // fires synchronously in the same tick sees the new active chat and
      // doesn't leak the previous chat's error into the new view.
      activeChatIdRef.current = id;
      setChatId(id);
      const loaded = (await api.getMessages({ chatId: id }) as ChatMessage[]) || [];
      setMessages(loaded);
      // Restore the per-chat stream buffer if this chat is mid-stream;
      // otherwise clear the UI.
      const slot = chatRuns.get(id);
      if (slot) {
        setStreamText(slot.buffer.text);
        setStreaming(true);
        setWorking(true);
        setPhase(slot.buffer.phase);
        streamRef.current = slot.controller;
      } else {
        setStreamText("");
        setStreaming(false);
        setWorking(false);
        setPhase("");
        streamRef.current = null;
      }
      setSideTab("chats"); setMainTab("chat"); setRenaming(false); setMobileSidebar(false);
    } catch (e) { setError(normalizeErrorMessage(e)); }
  }

  // ── Spotlight (Cmd/Ctrl+K) ──────────────────────────────────────
  // Global keyboard listener: open the overlay from anywhere unless
  // the user is typing in an input/textarea — we still trigger inside
  // text fields because that's the universal expectation (Cursor,
  // VS Code, Slack, Linear all behave this way).
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSpotlightOpen((open) => !open);
      }
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const handleSpotlightPick = useCallback(
    (hit: SpotlightHit) => {
      if (hit.type === "chat") {
        // hit.id was stringified on the backend; chat ids are integers.
        void openChat(hit.id);
        return;
      }
      if (hit.type === "session") {
        setMainTab("code");
        setCodeSessionRequest(hit.id);
        return;
      }
      if (hit.type === "file") {
        setMainTab("chat");
        setSideTab("library");
        // No way to focus a specific file yet — sidebar shows full list.
        return;
      }
      if (hit.type === "rag") {
        setMainTab("chat");
        setSideTab("memory");
        return;
      }
    },
    [openChat],
  );

  async function renameActive() {
    const t = renameVal.trim();
    if (!t || !chatId) return;
    try { await api.renameChat({ id: chatId, title: t }); await loadChats(chatId); setRenaming(false); }
    catch (e) { setError(normalizeErrorMessage(e)); }
  }

  async function autoRenameChat(targetChatId: string, text: string, chatList: ChatItem[] = chats) {
    const a = chatList.find(c => String(c.id) === String(targetChatId));
    if (!targetChatId || !a || (a.title && a.title !== "Новый чат")) return;
    try {
      await api.renameChat({ id: targetChatId, title: deriveChatTitle(text) });
      await loadChats(targetChatId);
    } catch {}
  }

  function exportChat(fmt: string) {
    if (!messages.length) return;
    const title = chats.find(c => c.id === chatId)?.title || "Чат Elira AI";
    const safe = title.slice(0,40).replace(/[^\wЀ-ӿ]/g,"_");
    const ts = new Date().toLocaleString("ru-RU");
    let blob: Blob, ext: string;
    if (fmt === "md") {
      const body = messages.map(m => `### ${m.role==="user"?"Вы":"Elira"}\n\n${m.content}`).join("\n\n---\n\n");
      blob = new Blob([`# ${title}\n\n> Экспорт: ${ts} | Сообщений: ${messages.length}\n\n---\n\n${body}`], {type:"text/markdown;charset=utf-8"});
      ext = ".md";
    } else if (fmt === "json") {
      const data = { title, exported_at: new Date().toISOString(), message_count: messages.length, messages: messages.map(m => ({ role: m.role, content: m.content, created_at: m.created_at || null })) };
      blob = new Blob([JSON.stringify(data, null, 2)], {type:"application/json;charset=utf-8"});
      ext = ".json";
    } else if (fmt === "html") {
      const msgs = messages.map(m => {
        const who = m.role==="user" ? "Вы" : "Elira";
        const bg = m.role==="user" ? "#e3f2fd" : "#f5f5f5";
        const content = m.content.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/\n/g,"<br>");
        return `<div style="margin:12px 0;padding:12px 16px;border-radius:10px;background:${bg}"><strong>${who}</strong><div style="margin-top:6px;white-space:pre-wrap">${content}</div></div>`;
      }).join("\n");
      const html = `<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"><title>${title}</title></head><body>${msgs}</body></html>`;
      blob = new Blob([html], {type:"text/html;charset=utf-8"});
      ext = ".html";
    } else {
      const body = messages.map((m) => `${m.role === "user" ? "Вы" : "Elira"}:\n${m.content}`).join("\n\n" + "═".repeat(40) + "\n\n");
      blob = new Blob([`${title}\nЭкспорт: ${ts}\n\n${body}`], {type:"text/plain;charset=utf-8"});
      ext = ".txt";
    }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = safe + ext; a.click();
    URL.revokeObjectURL(a.href);
  }

  function detectTableInText(text: string): ChartData | null {
    const rows = (text||"").match(/\|.+\|/g);
    if (!rows || rows.length < 3) return null;
    const data = rows
      .filter(r => !/^\s*\|[-:| ]+\|\s*$/.test(r))
      .map(r => r.split("|").map(c=>c.trim()).filter(Boolean));
    if (data.length < 2) return null;
    const headers = data[0];
    const numIdx = headers.findIndex((_,i) => data.slice(1).some(r => r[i] && !isNaN(parseFloat(r[i]))));
    if (numIdx === -1) return null;
    const labelIdx = numIdx === 0 ? 1 : 0;
    return {
      labels: data.slice(1).map(r => r[labelIdx]||""),
      values: data.slice(1).map(r => parseFloat(r[numIdx])||0),
      valueLabel: headers[numIdx]||"Значение",
    };
  }

  async function handleSend() {
    const text = input.trim();
    if (!text || working) return;
    try {
      setWorking(true); setStreaming(true); setStreamText(""); setError(""); setPhase(""); stoppedRef.current = false;
      const requestModel = orchestrationEnabled ? "auto" : (isAutoModel(model) ? (settingsModel || "gemma3:4b") : model);
      let activeChatId = chatId;
      const created = await api.addMessage({ chatId: activeChatId || null, role: "user", content: text }) as Record<string, unknown>;
      const userMsg = (created?.message || created) as ChatMessage;
      activeChatId = String(created?.chat_id ?? activeChatId ?? "");
      let currentChats = chats;
      if (!chatId && activeChatId) { currentChats = await loadChats(activeChatId); setChatId(activeChatId); }
      const nextMessages = [...messages, userMsg];
      setMessages(nextMessages); setInput("");
      await autoRenameChat(activeChatId, text, currentChats);
      const history = buildHistory(nextMessages as Array<Record<string, unknown>>);

      const cf = getChatContextFiles(libraryFiles, activeChatId);
      const tl = text.toLowerCase();
      const wantsFiles = cf.length > 0 && (tl.includes("файл")||tl.includes("документ")||tl.includes("библиотек")||tl.includes("загруженн")||tl.includes("прочитай")||tl.includes("опиши")||tl.includes("file")||tl.includes("document")||tl.includes("pdf")||tl.includes("резюме")||tl.includes("отчёт")||tl.includes("отчет")||tl.includes("что в ")||tl.includes("покажи содержимое")||tl.includes("проанализируй"));
      let cp = wantsFiles ? "\n\nФайлы пользователя:\n" + cf.map(f => `=== ${f.name} ===\n${(f.preview||"").slice(0, 1500)}`).join("\n\n") : "";

      const wantsProjectContext = (tl.includes("проект")||tl.includes("project")||tl.includes("repo")||tl.includes("repository")||tl.includes("репозитор")||tl.includes("код")||tl.includes("codebase")||tl.includes("backend")||tl.includes("frontend")||tl.includes("структур")||tl.includes("tree")||tl.includes("директор")||tl.includes("каталог")||tl.includes("папк")||tl.includes("readme")||tl.includes("модул")||tl.includes("компонент"));
      if (wantsProjectContext) {
        try {
          const projInfo = await api.getAdvancedProjectInfo() as Record<string, unknown>;
          if (projInfo.ok) {
            const projTree = await api.getAdvancedProjectTree({ maxDepth: 2, maxItems: 50 }) as Record<string, unknown>;
            if (projTree.ok && Array.isArray(projTree.items) && projTree.items.length) {
              const fileList = (projTree.items as Record<string, unknown>[]).filter(i => i.type === "file").map(i => i.path).join(", ");
              cp += `\n\nОткрыт проект: ${projInfo.name as string} (${projTree.count} файлов)\nФайлы: ${fileList.slice(0, 800)}`;
            }
          }
        } catch {}
      }

      if (multiAgent) {
        const useOrch = profile === "Оркестратор";
        const useRefl = skills.includes("reflection");
        const modeLabel = [useOrch && "Оркестратор", "Агенты", useRefl && "Рефлексия"].filter(Boolean).join(" → ");
        setPhase(`✨ ${modeLabel}...`);
        try {
          const data = await api.runAdvancedMultiAgent({ query: `${text}${cp}`, model_name: model, context: "", agents: ["researcher","programmer","analyst"], use_reflection: useRefl, use_orchestrator: useOrch }) as Record<string, unknown>;
          if (data?.ok === false) throw new Error(normalizeErrorMessage(data?.error || data?.detail || "HTTP error"));
          const final = ((data?.report as string) || "").trim() || "Multi-agent не вернул результат";
          try { await api.addMessage({ chatId: activeChatId, role: "assistant", content: final }); } catch {}
          setMessages(prev => [...prev, { id: `a-${Date.now()}`, role: "assistant", content: final }]);
          setError(""); setStreamText(""); setStreaming(false); setWorking(false); setPhase("");
          return;
        } catch (e) {
          const msg = normalizeErrorMessage(e);
          setError(msg); setStreamText(""); setStreaming(false); setWorking(false); setPhase(""); return;
        }
      }

      let fullText = "";
      // Capture the chat id at submission time. If the user navigates away
      // mid-stream, this still points to the chat that *asked* the question
      // so the finalized message lands in the right place.
      const targetChatId = activeChatId;
      const ctrl = executeStream(
        { model_name: requestModel, profile_name: profile, user_input: `${text}${cp}`, session_id: activeChatId || null, history, num_ctx: ollamaContext, direct_llm: !orchestrationEnabled, use_memory: skills.includes("memory"), use_library: skills.includes("file_context"), use_reflection: skills.includes("reflection"), use_web_search: skills.includes("web_search"), use_python_exec: skills.includes("python_exec"), use_image_gen: skills.includes("image_gen"), use_file_gen: skills.includes("file_gen"), use_http_api: skills.includes("http_api"), use_sql: skills.includes("sql_query"), use_screenshot: skills.includes("screenshot"), use_encrypt: skills.includes("encrypt"), use_archiver: skills.includes("archiver"), use_converter: skills.includes("converter"), use_regex: skills.includes("regex"), use_translator: skills.includes("translator"), use_csv: skills.includes("csv_analysis"), use_webhook: skills.includes("webhook"), use_plugins: skills.includes("plugins") },
        {
          onToken(t: string) {
            fullText += t;
            // Mirror into the per-chat buffer so the stream survives switches.
            chatRuns.update(targetChatId, { text: fullText, phase: "" });
            // Update the visible state only if user is still on this chat.
            if (activeChatIdRef.current === targetChatId) {
              setStreamText(fullText);
              setPhase("");
            }
          },
          onPhase(ev: Record<string, unknown>) {
            if (ev.phase === "reflection_replace" && ev.full_text) {
              fullText = ev.full_text as string;
              chatRuns.update(targetChatId, { text: fullText, phase: "" });
              if (activeChatIdRef.current === targetChatId) setStreamText(fullText);
            } else if (ev.message) {
              chatRuns.update(targetChatId, { text: fullText, phase: ev.message as string });
              if (activeChatIdRef.current === targetChatId) setPhase(ev.message as string);
            }
          },
          onDone({ full_text }: { full_text?: string }) {
            if (stoppedRef.current) return;
            // CRITICAL: prefer the accumulated streamed text. Backend's
            // `full_text` is the post-processed version (identity_guard,
            // persona, provenance, ...) and historically it sometimes
            // mangles markdown structure that the streamed text had
            // correctly. Fall back to backend text only if stream is empty.
            const final = fullText.trim() ? fullText : (full_text || "");
            const isStillHere = activeChatIdRef.current === targetChatId;
            // Persist to backend regardless of which chat is visible.
            api.addMessage({ chatId: targetChatId, role: "assistant", content: final }).catch(() => {});
            // Finish the run (removes it + fires the sidebar dot subscription).
            chatRuns.end(targetChatId);
            if (isStillHere) {
              setMessages(prev => [...prev, { id: `a-${Date.now()}`, role: "assistant", content: final }]);
              const _cd = detectTableInText(final); _cd ? setChartData(_cd) : setChartData(null);
              setStreamText(""); setStreaming(false); setWorking(false); setPhase("");
              streamRef.current = null;
            }
          },
          onError(msg: string) {
            chatRuns.end(targetChatId);
            if (activeChatIdRef.current === targetChatId) {
              setError(normalizeErrorMessage(msg));
              setStreamText(""); setStreaming(false); setWorking(false); setPhase("");
              streamRef.current = null;
            }
          },
        }
      );
      // Register the stream so chat switches preserve it (subscription updates
      // the sidebar dot).
      chatRuns.start(targetChatId, ctrl, { text: "", phase: "" });
      streamRef.current = ctrl;
    } catch (e) { setError(normalizeErrorMessage(e)); setStreamText(""); setStreaming(false); setWorking(false); setPhase(""); }
  }

  async function deleteChat(id: string) {
    try { await api.deleteChat({ id }); const next = chats.filter(c => c.id !== id); setChats(next); if (chatId === id) { if (next.length) await openChat(next[0].id); else resetDraftChat(); } }
    catch (e) { setError(normalizeErrorMessage(e)); }
  }
  async function pinChat(id: string, p: boolean | undefined) { try { await api.pinChat({ id, pinned: !p }); await loadChats(chatId); } catch (e) { setError(normalizeErrorMessage(e)); } }
  async function saveToMemory(id: string, s: boolean | undefined) { try { await api.saveChatToMemory({ id, saved: !s }); await loadChats(chatId); } catch (e) { setError(normalizeErrorMessage(e)); } }

  async function handleFiles(fl: FileList | null) {
    const files = Array.from(fl || []); if (!files.length) return;
    const recs: LibraryFile[] = [];
    const failed: string[] = [];
    for (const f of files) {
      try {
        recs.push(await fileToLibraryRecord(f));
        await api.uploadLibraryFile(f, { useInContext: false });
      } catch (e) {
        failed.push(`${f.name}: ${normalizeErrorMessage(e)}`);
      }
    }
    if (recs.length) {
      const next = [...recs, ...libraryFiles];
      setLibraryFiles(next); saveLibraryFiles(next);
      // Don't yank the user to the Files tab when they attach from the chat
      // composer — stay in the chat (the file is added to context + toast).
      // Only select the new file if they're already in the Files view.
      if (sideTab === "library") setSelLibId(recs[0]?.id || "");
      if (chatId) {
        const map = loadChatContextMap();
        map[chatId] = Array.from(new Set([...recs.map(r => r.id), ...(map[chatId] || [])]));
        saveChatContextMap(map);
      }
      const names = recs.map(r => r.name).join(", ");
      toast.success(
        recs.length === 1
          ? `Прикреплён файл: ${names}`
          : `Прикреплено ${recs.length} файлов: ${names}`,
      );
    }
    if (failed.length) {
      toast.error(`Не загрузилось:\n${failed.join("\n")}`);
    }
  }
  // Counter-based drag tracking — `dragenter`/`dragleave` fire for every
  // child element, so a simple boolean would flicker. We increment on
  // enter and decrement on leave; the overlay is visible while count > 0.
  const dragCounterRef = useRef(0);
  function onDrop(e: React.DragEvent) {
    e.preventDefault(); e.stopPropagation();
    dragCounterRef.current = 0;
    setDrag(false);
    handleFiles(e.dataTransfer.files);
  }
  function onDragOver(e: React.DragEvent) {
    // Required for the drop to fire; effect hints the cursor.
    e.preventDefault(); e.stopPropagation();
    if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
  }
  function onDragEnter(e: React.DragEvent) {
    e.preventDefault(); e.stopPropagation();
    // Only react when the drag actually carries files (not e.g. text from
    // a selection inside the page).
    const hasFiles = Array.from(e.dataTransfer?.types || []).includes("Files");
    if (!hasFiles) return;
    dragCounterRef.current += 1;
    if (dragCounterRef.current === 1) setDrag(true);
  }
  function onDragLeave(e: React.DragEvent) {
    e.preventDefault(); e.stopPropagation();
    dragCounterRef.current = Math.max(0, dragCounterRef.current - 1);
    if (dragCounterRef.current === 0) setDrag(false);
  }

  async function removeLib(id: string) {
    try { if (String(id).startsWith("db-")) { await api.deleteLibraryFile(String(id).slice(3)); } } catch {}
    const n = libraryFiles.filter(i => i.id !== id); setLibraryFiles(n); saveLibraryFiles(n);
    const m = loadChatContextMap();
    saveChatContextMap(Object.fromEntries(Object.entries(m).map(([k,v]) => [k,(v||[]).filter(x=>x!==id)])));
    if (selLibId === id) setSelLibId(n[0]?.id || "");
  }
  function toggleCtx(id: string, on: boolean) {
    const n = libraryFiles.map(i => i.id === id ? {...i, use_in_context: on} : i);
    setLibraryFiles(n); saveLibraryFiles(n);
    if (!chatId) return;
    const m = loadChatContextMap(); const s = new Set(m[chatId]||[]);
    on ? s.add(id) : s.delete(id); m[chatId] = Array.from(s); saveChatContextMap(m);
  }
  function toggleSkill(id: string) { setSkills(p => p.includes(id) ? p.filter(s => s !== id) : [...p, id]); }
  function handleStop() {
    stoppedRef.current = true;
    if (streamRef.current) { streamRef.current.abort(); streamRef.current = null; }
    if (streamText) {
      setMessages(prev => [...prev, { id: `a-${Date.now()}`, role: "assistant", content: streamText + "\n\n*[остановлено]*" }]);
      api.addMessage({ chatId, role: "assistant", content: streamText + "\n\n*[остановлено]*" }).catch(() => {});
    }
    setStreamText(""); setStreaming(false); setWorking(false); setPhase("");
  }
  function selectAllLib(on: boolean) {
    const next = libraryFiles.map(i => ({ ...i, use_in_context: on })); setLibraryFiles(next); saveLibraryFiles(next);
    if (!chatId) return;
    const m = loadChatContextMap(); m[chatId] = on ? libraryFiles.map(i => i.id) : []; saveChatContextMap(m);
  }

  async function updateTaskStatus(taskId: string, status: string) {
    try { await api.updateTask(taskId, { status }); setTasksError(""); setError(""); await loadTasks(); }
    catch (e) { const message = normalizeErrorMessage(e); setTasksError(message); setError(`Tasks: ${message}`); }
  }
  async function deleteTaskItem(taskId: string) {
    if (!confirm("Удалить задачу?")) return;
    try { await api.deleteTask(taskId); setTasksError(""); setError(""); await loadTasks(); }
    catch (e) { const message = normalizeErrorMessage(e); setTasksError(message); setError(`Tasks: ${message}`); }
  }
  async function startTelegramBot() {
    try { const data = await api.startTelegramBot() as Record<string, unknown>; setTelegramError(""); if (data?.ok === false) throw new Error((data.error as string) || "Ошибка запуска"); setError(""); await loadTelegram(); }
    catch (e) { const message = normalizeErrorMessage(e); setTelegramError(message); setError(`Telegram: ${message}`); }
  }
  async function stopTelegramBot() {
    try { await api.stopTelegramBot(); setTelegramError(""); setError(""); await loadTelegram(); }
    catch (e) { const message = normalizeErrorMessage(e); setTelegramError(message); setError(`Telegram: ${message}`); }
  }
  async function testTelegramBot() {
    try {
      const data = await api.testTelegramBot() as Record<string, unknown>;
      setTelegramError(""); setError("");
      if (data?.ok) {
        toast.success(`Бот: @${data.bot_username as string} (${data.bot_name as string})`);
      } else {
        toast.error((data?.error as string) || "Telegram: неизвестная ошибка");
      }
    } catch (e) {
      const message = normalizeErrorMessage(e);
      setTelegramError(message);
      setError(`Telegram: ${message}`);
      toast.error(`Telegram: ${message}`);
    }
  }
  async function saveTelegramToken() {
    if (!tgTokenInput.trim()) return;
    try { await api.updateTelegramConfig({ bot_token: tgTokenInput.trim() }); setTelegramError(""); setTgTokenInput(""); setError(""); await loadTelegram(); }
    catch (e) { const message = normalizeErrorMessage(e); setTelegramError(message); setError(`Telegram: ${message}`); }
  }
  async function updateTelegramAllowedUsers(val: string) {
    try { await api.updateTelegramConfig({ allowed_users: val }); setTelegramError(""); setError(""); await loadTelegram(); }
    catch (e) { const message = normalizeErrorMessage(e); setTelegramError(message); setError(`Telegram: ${message}`); }
  }
  async function toggleTelegramUserAccess(user: TgUser) {
    try { await api.toggleTelegramUser({ chat_id: user.chat_id, allowed: !user.allowed }); setTelegramError(""); setError(""); await loadTelegram(); }
    catch (e) { const message = normalizeErrorMessage(e); setTelegramError(message); setError(`Telegram: ${message}`); }
  }
  async function createPipeline() {
    if (!pipeForm.name) return;
    try { await api.createPipeline(pipeForm); setPipelinesError(""); setPipeForm({name:"",task_type:"prompt",interval_minutes:60,task_data:{prompt:""}}); setError(""); await loadPipelines(); }
    catch (e) { const message = normalizeErrorMessage(e); setPipelinesError(message); setError(`Pipelines: ${message}`); }
  }
  async function runPipelineNow(pipelineId: string) {
    try { await api.runPipeline(pipelineId); setPipelinesError(""); setError(""); await loadPipelines(); }
    catch (e) { const message = normalizeErrorMessage(e); setPipelinesError(message); setError(`Pipelines: ${message}`); }
  }
  async function togglePipelineEnabled(pipeline: PipelineItem) {
    try { await api.updatePipeline(pipeline.id, { enabled: !pipeline.enabled }); setPipelinesError(""); setError(""); await loadPipelines(); }
    catch (e) { const message = normalizeErrorMessage(e); setPipelinesError(message); setError(`Pipelines: ${message}`); }
  }
  async function deletePipeline(pipelineId: string) {
    if (!confirm("Удалить?")) return;
    try { await api.deletePipeline(pipelineId); setPipelinesError(""); setError(""); await loadPipelines(); }
    catch (e) { const message = normalizeErrorMessage(e); setPipelinesError(message); setError(`Pipelines: ${message}`); }
  }
  async function reloadPlugins() {
    try {
      const data = await api.reloadPlugins() as Record<string, unknown>;
      setPluginList(((data.loaded as string[]) || []).map((n: string) => ({ name: n, enabled: true })));
      setError(""); await loadPluginList();
    } catch (e) { setError(`Plugins: ${normalizeErrorMessage(e)}`); }
  }
  async function togglePluginState(plugin: Plugin) {
    try { await api.setPluginEnabled(plugin.name, !plugin.enabled); setError(""); await loadPluginList(); }
    catch (e) { setError(`Plugins: ${normalizeErrorMessage(e)}`); }
  }

  function handleKeyDown(e: React.KeyboardEvent) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); } }

  const fChats = useMemo(() => { const q = sideSearch.trim().toLowerCase(); return q ? chats.filter(c => (c.title||"").toLowerCase().includes(q)) : chats; }, [sideSearch, chats]);
  const pinned = useMemo(() => fChats.filter(c => c.pinned), [fChats]);
  const regular = useMemo(() => fChats.filter(c => !c.pinned), [fChats]);
  const memChats = useMemo(() => chats.filter(c => c.memory_saved), [chats]);
  const fLib = useMemo(() => { const q = libSearch.trim().toLowerCase(); return q ? libraryFiles.filter(i => `${i.name} ${i.preview||""}`.toLowerCase().includes(q)) : libraryFiles; }, [libSearch, libraryFiles]);
  const selLib = useMemo(() => libraryFiles.find(i => i.id === selLibId) || libraryFiles[0] || null, [libraryFiles, selLibId]);
  const ctxF = useMemo(() => getChatContextFiles(libraryFiles, chatId), [libraryFiles, chatId]);

  const getName = useCallback((item: unknown) => typeof item === "string" ? item : ((item as Record<string, unknown>).name || (item as Record<string, unknown>).model || "") as string, []);

  // ── Backend offline / connecting screens ───────────────────────────────────
  if (backendStatus === "checking") return (
    <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",height:"100vh",background:"var(--bg)",gap:16,color:"var(--text)"}}>
      <svg width="48" height="48" viewBox="0 0 64 64" fill="none"><defs><linearGradient id="jg2" x1="12" y1="10" x2="52" y2="54" gradientUnits="userSpaceOnUse"><stop stopColor="#7C3AED"/><stop offset="1" stopColor="#06B6D4"/></linearGradient></defs><rect x="5" y="5" width="54" height="54" rx="14" fill="#0B1020"/><circle cx="32" cy="32" r="14" stroke="url(#jg2)" strokeWidth="3"><animateTransform attributeName="transform" type="rotate" from="0 32 32" to="360 32 32" dur="2s" repeatCount="indefinite"/></circle><circle cx="32" cy="32" r="6" fill="url(#jg2)"/></svg>
      <div style={{fontSize:16,fontWeight:600}}>Elira AI</div>
      <div style={{fontSize:13,color:"var(--text-muted)"}}>Подключение к бэкенду… попытка {backendAttempt}</div>
      <div style={{fontSize:11,color:"var(--text-muted)",opacity:0.6}}>http://127.0.0.1:8000</div>
    </div>
  );

  if (backendStatus === "offline") return (
    <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",height:"100vh",background:"var(--bg)",gap:16,color:"var(--text)",textAlign:"center",padding:"0 24px"}}>
      <svg width="48" height="48" viewBox="0 0 64 64" fill="none"><rect x="5" y="5" width="54" height="54" rx="14" fill="#0B1020"/><circle cx="32" cy="32" r="18" stroke="#f44336" strokeWidth="3"/><line x1="22" y1="22" x2="42" y2="42" stroke="#f44336" strokeWidth="3" strokeLinecap="round"/><line x1="42" y1="22" x2="22" y2="42" stroke="#f44336" strokeWidth="3" strokeLinecap="round"/></svg>
      <div style={{fontSize:16,fontWeight:600}}>Бэкенд недоступен</div>
      <div style={{fontSize:12,color:"var(--text-muted)",maxWidth:360,lineHeight:1.6}}>
        Не удалось подключиться к <code style={{background:"rgba(255,255,255,0.08)",padding:"1px 6px",borderRadius:4}}>http://127.0.0.1:8000</code> после 20 попыток.<br/>
        Убедитесь, что запущен <strong>Elira.bat</strong>, и нажмите кнопку ниже.
      </div>
      <button
        onClick={() => { initRef.current = false; setBackendStatus("checking"); setBackendAttempt(0); startupWithHealthCheck(); }}
        style={{padding:"8px 24px",borderRadius:8,background:"var(--accent)",color:"#fff",border:"none",cursor:"pointer",fontSize:13,fontWeight:600}}
      >
        Повторить подключение
      </button>
    </div>
  );

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  if (mainTab === "code") return (
    <>
      <CodeWorkspaceShell
        messages={messages as any}
        libraryFiles={libraryFiles as any}
        setLibraryFiles={setLibraryFiles as any}
        onBackToChat={() => setMainTab("chat")}
        onSendToChat={(txt: string) => { setMainTab("chat"); setTimeout(() => setInput(txt), 100); }}
        externalSessionRequest={codeSessionRequest}
        onSessionRequestConsumed={() => setCodeSessionRequest(null)}
      />
      <SpotlightOverlay
        open={spotlightOpen}
        onClose={() => setSpotlightOpen(false)}
        onPick={handleSpotlightPick}
      />
    </>
  );

  const navItems: [string, string, LucideIcon][] = [
    ["chats", "Чаты", MessageSquare],
    ["project", "Проекты", FolderOpen],
    ["library", "Файлы", Files],
    ["memory", "Память", BrainCircuit],
    ["tasks", "Задачи", ListTodo],
    ["dashboard", "Панель", LayoutDashboard],
    ["pipelines", "Пайплайны", Workflow],
    ["telegram", "Telegram", Send],
    ["settings", "Настройки", Settings],
  ];

  return (
    <div
      className="elira-shell"
      style={showPanel && sideTab === "chats" ? {gridTemplateColumns: "200px 1fr auto"} : undefined}
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      {drag && (
        <div className="elira-drop-overlay" aria-hidden="true">
          <div className="elira-drop-overlay-inner">
            <div className="elira-drop-overlay-title">📎 Отпусти, чтобы прикрепить</div>
            <div className="elira-drop-overlay-sub">
              Файл попадёт в библиотеку и подключится как контекст текущего чата.
            </div>
          </div>
        </div>
      )}
      {mobileSidebar && <div className="mobile-overlay" onClick={()=>setMobileSidebar(false)}/>}
      <aside className={`elira-sidebar ${mobileSidebar?"mobile-open":""}`}>
        <button className="sidebar-newchat-btn" onClick={() => newChat(false)}>+ Новый чат</button>
        <div className="sidebar-nav">
          {navItems.map(([k, l, Icon]) => (
            <button key={k} className={`sidebar-nav-item ${sideTab === k ? "active" : ""}`} onClick={() => {
              setSideTab(k); setMobileSidebar(false);
              if(k==="settings"){setSettingsModel(isAutoModel(model) ? settingsModel : model);setSettingsProfile(profile);setSettingsContext(ollamaContext);setSettingsSaved(false);refreshModels();loadPluginList();}
              if(k==="dashboard"){loadDashboard();}
              if(k==="pipelines"){loadPipelines();}
              if(k==="tasks"){loadTasks();}
              if(k==="telegram"){loadTelegram();}
            }}>
              <IconText icon={Icon}>{l}</IconText>
            </button>
          ))}
        </div>
        <div className="sidebar-nav-item search-shell">
          <UiIcon icon={Search} size={12} style={{opacity:0.65}} />
          <input className="sidebar-search-input" value={sideSearch} onChange={e => setSideSearch(e.target.value)} placeholder="Поиск" />
        </div>
        {sideTab === "chats" && (
          <div className="chat-list" style={{flex:1,minHeight:0}}>
            {pinned.length > 0 && <div className="sidebar-section-title">Закреплённые</div>}
            {pinned.map(c => {
              const streamingHere = chatRuns.isRunning(c.id);
              return (
                <button key={c.id} className={`chat-list-item simple ${chatId===c.id?"active":""}`} onClick={() => openChat(c.id)} title={streamingHere ? "Идёт стрим в этом чате" : undefined}>
                  <span className="chat-list-title truncate">{c.title||"Новый чат"}</span>
                  {streamingHere && <span className="chat-streaming-dot" aria-label="streaming"/>}
                </button>
              );
            })}
            {regular.length > 0 && <div className="sidebar-section-title">Чаты</div>}
            {regular.map(c => {
              const streamingHere = chatRuns.isRunning(c.id);
              return (
                <button key={c.id} className={`chat-list-item simple ${chatId===c.id?"active":""}`} onClick={() => openChat(c.id)} title={streamingHere ? "Идёт стрим в этом чате" : undefined}>
                  <span className="chat-list-title truncate">{c.title||"Новый чат"}</span>
                  {streamingHere && <span className="chat-streaming-dot" aria-label="streaming"/>}
                </button>
              );
            })}
            {!fChats.length && <div className="sidebar-empty">Пусто</div>}
          </div>
        )}
        {sideTab === "memory" && <div className="chat-list" style={{flex:1}}>{memChats.length ? memChats.map(c => <button key={c.id} className={`chat-list-item simple ${chatId===c.id?"active":""}`} onClick={() => openChat(c.id)}><span className="chat-list-title truncate">{c.title||"Чат"}</span></button>) : <div className="sidebar-empty">Нет</div>}</div>}
        {sideTab === "settings" && <div className="sidebar-empty">→ Центральное окно</div>}
        {sideTab === "library" && <div className="sidebar-empty">→ Центральное окно</div>}
        {sideTab === "project" && <div className="sidebar-empty">→ Центральное окно</div>}
        <div style={{padding:"8px 12px",borderTop:"1px solid var(--border)",display:"flex",alignItems:"center",justifyContent:"space-between"}}>
          <button onClick={()=>setTheme(t=>{ const order = ["dark","light","cursor","cyber","glass","minimal"]; const i = order.indexOf(t); return order[(i + 1) % order.length]; })} style={{background:"none",border:"1px solid var(--border)",borderRadius:6,padding:"3px 8px",cursor:"pointer",color:"var(--text-muted)",fontSize:11,display:"inline-flex",alignItems:"center",gap:6}} title={`Тема: ${theme}. Ctrl+Shift+T — следующая.`}>
            <UiIcon icon={theme==="light"||theme==="minimal" ? Sun : Moon} size={13} />
            <span style={{textTransform:"capitalize"}}>{theme}</span>
          </button>
          <span style={{fontSize:9,color:"var(--text-muted)",opacity:0.5}}>Ctrl+N чат</span>
        </div>
      </aside>

      <main className="elira-main">
        <div className="elira-topbar slim">
          <button className="mobile-burger" onClick={()=>setMobileSidebar(v=>!v)}><UiIcon icon={Menu} size={16} /></button>
          <div className="elira-brand"><svg width="22" height="22" viewBox="0 0 64 64" fill="none" style={{marginRight:7,verticalAlign:"middle",marginTop:-2}}><defs><linearGradient id="jg" x1="12" y1="10" x2="52" y2="54" gradientUnits="userSpaceOnUse"><stop stopColor="#7C3AED"/><stop offset="1" stopColor="#06B6D4"/></linearGradient></defs><rect x="5" y="5" width="54" height="54" rx="14" fill="#0B1020"/><circle cx="32" cy="32" r="14" stroke="url(#jg)" strokeWidth="3"/><circle cx="32" cy="32" r="6" fill="url(#jg)"/></svg>Elira AI</div>
          <div className="topbar-tabs">
            <button className={`soft-btn ${mainTab==="chat"?"active":""}`} onClick={() => setMainTab("chat")}>Чат</button>
            <button className={`soft-btn ${mainTab==="code"?"active":""}`} onClick={() => setMainTab("code")}>Код</button>
            <button className={`soft-btn ${showPanel?"active":""}`} onClick={() => setShowPanel(p => !p)} title="Панель кода"><UiIcon icon={Code2} size={13} /></button>
          </div>
        </div>

        <div className="chat-page">
          <div className="chat-header-row">
            <div className="chat-page-title">{sideTab==="chats"&&"Чат"}{sideTab==="memory"&&"Память"}{sideTab==="settings"&&"Настройки"}{sideTab==="library"&&"Библиотека"}{sideTab==="project"&&"Проект"}{sideTab==="dashboard"&&"Панель"}{sideTab==="pipelines"&&"Пайплайны"}</div>
            {sideTab === "chats" && chatId && (
              <div className="chat-header-actions icon-actions" style={{display:"flex"}}>
                <div className="export-dropdown-wrap" style={{position:"relative"}}>
                  <button className="soft-btn icon-btn" title="Экспорт чата" onClick={()=>setShowExportMenu(v=>!v)}><UiIcon icon={Download} size={14} /></button>
                  {showExportMenu && <div className="export-dropdown" style={{position:"absolute",top:"100%",right:0,zIndex:99,background:"var(--bg-card)",border:"1px solid var(--border)",borderRadius:8,padding:"4px 0",minWidth:140,boxShadow:"0 4px 16px rgba(0,0,0,.18)"}}>
                    <button className="export-item" onClick={()=>{exportChat("md");setShowExportMenu(false)}}><IconText icon={FileText}>Markdown</IconText></button>
                    <button className="export-item" onClick={()=>{exportChat("html");setShowExportMenu(false)}}><IconText icon={Globe}>HTML-страница</IconText></button>
                    <button className="export-item" onClick={()=>{exportChat("json");setShowExportMenu(false)}}><IconText icon={Braces}>JSON</IconText></button>
                    <button className="export-item" onClick={()=>{exportChat("txt");setShowExportMenu(false)}}><IconText icon={ScrollText}>Текстовый файл</IconText></button>
                  </div>}
                </div>
                <button className={`soft-btn icon-btn ${chats.find(c=>c.id===chatId)?.memory_saved ? "on" : ""}`} title={chats.find(c=>c.id===chatId)?.memory_saved ? "В памяти — нажми, чтобы убрать" : "Сохранить в память"} onClick={() => saveToMemory(chatId, chats.find(c=>c.id===chatId)?.memory_saved)}><UiIcon icon={BrainCircuit} size={14} /></button>
                <button className={`soft-btn icon-btn ${chats.find(c=>c.id===chatId)?.pinned ? "on" : ""}`} title={chats.find(c=>c.id===chatId)?.pinned ? "Закреплён — нажми, чтобы открепить" : "Закрепить чат"} onClick={() => pinChat(chatId, chats.find(c=>c.id===chatId)?.pinned)}><UiIcon icon={Pin} size={14} /></button>
                <button className="soft-btn icon-btn" title="Переименовать чат" onClick={() => { setRenaming(true); setRenameVal(chats.find(c=>c.id===chatId)?.title||""); }}><UiIcon icon={Pencil} size={14} /></button>
                <button className="soft-btn icon-btn" title="Удалить чат" onClick={() => deleteChat(chatId)}><UiIcon icon={Trash2} size={14} /></button>
              </div>
            )}
          </div>

          {renaming && sideTab==="chats" && <div className="rename-bar"><input value={renameVal} onChange={e=>setRenameVal(e.target.value)} className="rename-input wide" placeholder="Название"/><button className="mini-btn" onClick={renameActive}>Сохранить</button></div>}

          {sideTab === "tasks" ? (
            <div className="settings-main-card" style={{overflow:"auto"}}>
              <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:12}}>
                <div style={{fontSize:15,fontWeight:700,color:"var(--text)"}}><IconText icon={ListTodo} size={15}>Задачи</IconText></div>
                <button className="soft-btn" style={{fontSize:10,padding:"3px 10px",border:"1px solid var(--border)"}} onClick={() => loadTasks()} title="Обновить"><UiIcon icon={RefreshCw} size={13} /></button>
              </div>
              <PanelNotice title="Раздел задач временно недоступен" message={tasksError} onRetry={() => loadTasks()} />
              {taskStats && (
                <div style={{display:"flex",gap:8,marginBottom:12,flexWrap:"wrap"}}>
                  {[
                    {l:"Всего",v:(taskStats.total as number),c:"var(--text)"},
                    {l:"К выполнению",v:((taskStats.by_status as Record<string,number>)?.todo||0),c:"#5b9bd5"},
                    {l:"В работе",v:((taskStats.by_status as Record<string,number>)?.in_progress||0),c:"#f5a623"},
                    {l:"Готово",v:((taskStats.by_status as Record<string,number>)?.done||0),c:"#4caf50"},
                    {l:"Просрочено",v:((taskStats.overdue as number)||0),c:"#f44336"},
                  ].map(s=>(
                    <div key={s.l} style={{padding:"6px 10px",borderRadius:8,border:"1px solid var(--border)",background:"var(--bg-surface)",textAlign:"center",minWidth:50}}>
                      <div style={{fontSize:16,fontWeight:700,color:s.c}}>{s.v}</div>
                      <div style={{fontSize:9,color:"var(--text-muted)"}}>{s.l}</div>
                    </div>
                  ))}
                </div>
              )}
              <div style={{display:"flex",gap:4,marginBottom:12}}>
                {([["active","Активные"],["todo","К выполнению"],["in_progress","В работе"],["done","Готовые"],["all","Все"]] as [string,string][]).map(([k,l])=>(
                  <button key={k} className="soft-btn" style={{fontSize:10,padding:"3px 10px",background:taskFilter===k?"var(--accent)":"transparent",color:taskFilter===k?"#fff":"var(--text)",border:"1px solid var(--border)",borderRadius:6}} onClick={()=>{setTaskFilter(k);loadTasks(k);}}>{l}</button>
                ))}
              </div>
              <div style={{padding:12,borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:14}}>
                <div style={{fontSize:12,fontWeight:600,color:"var(--text)",marginBottom:8}}>{editingTask ? "Редактирование задачи" : "Новая задача"}</div>
                <input placeholder="Название задачи" value={taskForm.title} onChange={e=>setTaskForm({...taskForm,title:e.target.value})} className="rename-input" style={{width:"100%",fontSize:11,padding:"5px 8px",marginBottom:6}}/>
                <textarea placeholder="Описание (необязательно)" value={taskForm.description} onChange={e=>setTaskForm({...taskForm,description:e.target.value})} className="rename-input" style={{width:"100%",fontSize:11,padding:"5px 8px",marginBottom:6,minHeight:40,resize:"vertical",fontFamily:"inherit"}} rows={2}/>
                <div style={{display:"flex",gap:6,flexWrap:"wrap",marginBottom:6}}>
                  <select value={taskForm.priority} onChange={e=>setTaskForm({...taskForm,priority:e.target.value})} className="topbar-select dark-select" style={{fontSize:11}}>
                    <option value="low">Низкий</option><option value="medium">Средний</option><option value="high">Высокий</option><option value="urgent">Срочный</option>
                  </select>
                  <select value={taskForm.category} onChange={e=>setTaskForm({...taskForm,category:e.target.value})} className="topbar-select dark-select" style={{fontSize:11}}>
                    <option value="general">Общее</option><option value="work">Работа</option><option value="personal">Личное</option><option value="study">Учёба</option><option value="project">Проект</option><option value="idea">Идея</option>
                  </select>
                  <input type="date" value={taskForm.due_date||""} onChange={e=>setTaskForm({...taskForm,due_date:e.target.value})} className="rename-input" style={{fontSize:11,padding:"4px 8px"}}/>
                </div>
                <div style={{display:"flex",gap:6}}>
                  <button className="soft-btn" style={{fontSize:11,padding:"4px 14px",background:"var(--accent)",color:"#fff",border:"none",borderRadius:6}} onClick={async()=>{
                    if(!taskForm.title) return;
                    try {
                      if(editingTask) { await api.updateTask(editingTask, taskForm); setEditingTask(null); }
                      else { await api.createTask(taskForm); }
                      setTaskForm({title:"",description:"",category:"general",priority:"medium",due_date:""});
                      await loadTasks(); setError("");
                    } catch(e){setError(`Tasks: ${normalizeErrorMessage(e)}`)}
                  }}>{editingTask ? "Сохранить" : "Создать"}</button>
                  {editingTask && <button className="soft-btn" style={{fontSize:11,padding:"4px 10px",border:"1px solid var(--border)",borderRadius:6}} onClick={()=>{setEditingTask(null);setTaskForm({title:"",description:"",category:"general",priority:"medium",due_date:""});}}>Отмена</button>}
                </div>
              </div>
              {tasksList.length===0 && !tasksError && <div style={{fontSize:11,color:"var(--text-muted)",padding:"12px 0",textAlign:"center"}}>Нет задач</div>}
              {tasksList.map(t=>{
                const prioColor = ({urgent:"#f44336",high:"#ff9800",medium:"#f5a623",low:"#4caf50"} as Record<string,string>)[t.priority||""]||"var(--text-muted)";
                const isOverdue = t.due_date && t.status!=="done" && t.status!=="cancelled" && new Date(t.due_date) < new Date();
                return (
                  <div key={t.id} style={{padding:"10px 12px",borderRadius:10,border:`1px solid ${isOverdue?"#f44336":"var(--border)"}`,background:"var(--bg-surface)",marginBottom:6,opacity:t.status==="done"||t.status==="cancelled"?0.6:1}}>
                    <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:4}}>
                      <div style={{display:"flex",alignItems:"center",gap:6,flex:1,minWidth:0}}>
                        <span style={{cursor:"pointer",fontSize:16}} title={t.status==="done"?"Вернуть":"Выполнено"} onClick={async()=>{await updateTaskStatus(t.id, t.status==="done"?"todo":"done");}}>{t.status==="done"?"↺":"✓"}</span>
                        <div style={{flex:1,minWidth:0}}>
                          <div style={{fontWeight:600,fontSize:12,color:"var(--text)",textDecoration:t.status==="done"?"line-through":"none",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{t.title}</div>
                          {t.description && <div style={{fontSize:10,color:"var(--text-muted)",marginTop:2,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{t.description}</div>}
                        </div>
                      </div>
                      <div style={{display:"flex",gap:3,flexShrink:0}}>
                        {t.status!=="done" && t.status!=="cancelled" && (
                          <button className="soft-btn" style={{fontSize:9,padding:"2px 6px"}} title="В работу" onClick={async()=>{await updateTaskStatus(t.id, t.status==="in_progress"?"todo":"in_progress");}}><UiIcon icon={t.status==="in_progress" ? Pause : Play} size={12} /></button>
                        )}
                        <button className="soft-btn" style={{fontSize:9,padding:"2px 6px"}} title="Редактировать" onClick={()=>{setEditingTask(t.id);setTaskForm({title:t.title,description:t.description||"",category:t.category||"general",priority:t.priority||"medium",due_date:t.due_date||""});}}><UiIcon icon={Pencil} size={12} /></button>
                        <button className="soft-btn" style={{fontSize:9,padding:"2px 6px",color:"#f44336"}} title="Удалить" onClick={() => deleteTaskItem(t.id)}><UiIcon icon={Trash2} size={12} /></button>
                      </div>
                    </div>
                    <div style={{display:"flex",gap:8,alignItems:"center",fontSize:10,color:"var(--text-muted)",marginTop:2}}>
                      <span style={{color:prioColor}}>{({urgent:"Срочный",high:"Высокий",medium:"Средний",low:"Низкий"} as Record<string,string>)[t.priority||""]||t.priority}</span>
                      <span>{({general:"Общее",work:"Работа",personal:"Личное",study:"Учёба",project:"Проект",idea:"Идея"} as Record<string,string>)[t.category||""]||t.category}</span>
                      {t.due_date && <span style={{color:isOverdue?"#f44336":"var(--text-muted)",display:"inline-flex",alignItems:"center",gap:4}}><UiIcon icon={CalendarDays} size={12} />{new Date(t.due_date).toLocaleDateString("ru-RU")}{isOverdue?" просрочено":""}</span>}
                      {t.status==="in_progress" && <span style={{color:"#f5a623",display:"inline-flex",alignItems:"center",gap:4}}><UiIcon icon={RefreshCw} size={11} />в работе</span>}
                      {t.status==="done" && t.completed_at && <span style={{color:"#4caf50"}}>Готово {new Date(t.completed_at).toLocaleDateString("ru-RU")}</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : sideTab === "telegram" ? (
            <div className="settings-main-card" style={{overflow:"auto"}}>
              <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:12}}>
                <div style={{fontSize:15,fontWeight:700,color:"var(--text)"}}><IconText icon={Bot} size={15}>Telegram-бот</IconText></div>
                <button className="soft-btn" style={{fontSize:10,padding:"3px 10px",border:"1px solid var(--border)"}} onClick={loadTelegram} title="Обновить"><UiIcon icon={RefreshCw} size={13} /></button>
              </div>
              <PanelNotice title="Панель Telegram временно недоступна" message={telegramError} onRetry={loadTelegram} />
              <div style={{display:"flex",gap:4,marginBottom:14}}>
                {([["setup","Настройка",Settings],["users","Пользователи",Users],["log","Лог",ScrollText],["guide","Инструкция",BookOpen]] as [string,string,LucideIcon][]).map(([k,l,Icon])=>(
                  <button key={k} className="soft-btn" style={{fontSize:10,padding:"3px 10px",background:tgTab===k?"var(--accent)":"transparent",color:tgTab===k?"#fff":"var(--text)",border:"1px solid var(--border)",borderRadius:6}} onClick={()=>setTgTab(k)}><IconText icon={Icon} size={12} gap={5}>{l}</IconText></button>
                ))}
              </div>
              {tgTab === "guide" && (
                <div style={{fontSize:11,color:"var(--text)",lineHeight:1.7}}>
                  <div style={{fontSize:13,fontWeight:700,marginBottom:8,color:"var(--accent)"}}><IconText icon={BookOpen} size={14}>Как подключить Telegram-бота</IconText></div>
                  <div style={{padding:12,borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:10}}>
                    <div style={{fontWeight:700,marginBottom:6}}>Шаг 1: Создай бота</div>
                    <div>1. Открой Telegram и найди <b>@BotFather</b></div>
                    <div>2. Отправь команду <code style={{background:"var(--bg-code)",padding:"1px 5px",borderRadius:4}}>/newbot</code></div>
                    <div>3. BotFather даст тебе <b>токен</b> — вставь его ниже</div>
                  </div>
                  <div style={{padding:12,borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:10}}>
                    <div style={{fontWeight:700,marginBottom:6}}>Шаг 2: Вставь токен → Шаг 3: Запусти бота</div>
                    <div>Перейди на вкладку <b>Настройка</b>, вставь токен и нажми <b>Запустить</b></div>
                  </div>
                  <div style={{padding:12,borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)"}}>
                    <div style={{fontWeight:700,marginBottom:6}}>Команды бота</div>
                    <div><code>/start</code> — Приветствие · <code>/help</code> — Справка · <code>/status</code> — Настройки</div>
                    <div style={{marginTop:4}}><code>/web on|off</code> — Веб-поиск · <code>/memory on|off</code> — Память</div>
                  </div>
                </div>
              )}
              {tgTab === "setup" && (
                <div>
                  <div style={{padding:10,borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:12,display:"flex",alignItems:"center",justifyContent:"space-between"}}>
                    <div><span style={{fontSize:12,fontWeight:600}}>Статус: </span><span style={{fontSize:12,color:tgConfig?.running?"#4caf50":"var(--text-muted)",fontWeight:600}}>{tgConfig?.running?"● Работает":"○ Остановлен"}</span></div>
                    <div style={{display:"flex",gap:4}}>
                      {!tgConfig?.running ? (
                        <button className="soft-btn" style={{fontSize:10,padding:"4px 12px",background:"#4caf50",color:"#fff",border:"none",borderRadius:6,display:"inline-flex",alignItems:"center",gap:6}} onClick={startTelegramBot}><UiIcon icon={Play} size={12} />Запустить</button>
                      ) : (
                        <button className="soft-btn" style={{fontSize:10,padding:"4px 12px",background:"#f44336",color:"#fff",border:"none",borderRadius:6,display:"inline-flex",alignItems:"center",gap:6}} onClick={stopTelegramBot}><UiIcon icon={Square} size={12} />Остановить</button>
                      )}
                      <button className="soft-btn" style={{fontSize:10,padding:"4px 10px",border:"1px solid var(--border)"}} onClick={testTelegramBot}>Тест</button>
                    </div>
                  </div>
                  <div style={{padding:12,borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:12}}>
                    <div style={{fontSize:12,fontWeight:600,marginBottom:6}}>Токен бота</div>
                    {Boolean(tgConfig?.has_token) && <div style={{fontSize:10,color:"var(--text-muted)",marginBottom:4}}>Текущий: {tgConfig!.bot_token as string}</div>}
                    <div style={{display:"flex",gap:6}}>
                      <input type="password" placeholder="Вставь токен от @BotFather" value={tgTokenInput} onChange={e=>setTgTokenInput(e.target.value)} className="rename-input" style={{flex:1,fontSize:11,padding:"5px 8px"}}/>
                      <button className="soft-btn" style={{fontSize:10,padding:"4px 12px",background:"var(--accent)",color:"#fff",border:"none",borderRadius:6}} onClick={saveTelegramToken}>Сохранить</button>
                    </div>
                  </div>
                  <div style={{padding:12,borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:12}}>
                    <div style={{fontSize:12,fontWeight:600,marginBottom:8}}><IconText icon={Settings} size={13}>Параметры</IconText></div>
                    <div style={{display:"flex",gap:8,flexWrap:"wrap",marginBottom:8}}>
                      <div>
                        <div style={{fontSize:10,color:"var(--text-muted)",marginBottom:2}}>Модель</div>
                        <input placeholder="auto (текущая)" value={(tgConfig?.model as string)||""} onChange={e=>{setTgConfig({...tgConfig,model:e.target.value})}} className="rename-input" style={{fontSize:11,padding:"4px 8px",width:140}}/>
                      </div>
                      <div>
                        <div style={{fontSize:10,color:"var(--text-muted)",marginBottom:2}}>Профиль</div>
                        <select value={(tgConfig?.profile as string)||"Универсальный"} onChange={e=>{setTgConfig({...tgConfig,profile:e.target.value})}} className="topbar-select dark-select" style={{fontSize:11}}>
                          <option>Универсальный</option><option>Исследователь</option><option>Программист</option><option>Аналитик</option><option>Сократ</option>
                        </select>
                      </div>
                    </div>
                    <div style={{display:"flex",gap:12,marginBottom:8}}>
                      <label style={{fontSize:11,display:"flex",alignItems:"center",gap:4,cursor:"pointer"}}><input type="checkbox" checked={Boolean(tgConfig?.use_memory)} onChange={e=>{setTgConfig({...tgConfig,use_memory:e.target.checked})}}/>Память</label>
                      <label style={{fontSize:11,display:"flex",alignItems:"center",gap:4,cursor:"pointer"}}><input type="checkbox" checked={Boolean(tgConfig?.use_web_search)} onChange={e=>{setTgConfig({...tgConfig,use_web_search:e.target.checked})}}/>Веб-поиск</label>
                    </div>
                    <div style={{marginBottom:8}}>
                      <div style={{fontSize:10,color:"var(--text-muted)",marginBottom:2}}>Приветствие (/start)</div>
                      <textarea value={(tgConfig?.welcome_message as string)||""} onChange={e=>{setTgConfig({...tgConfig,welcome_message:e.target.value})}} className="rename-input" style={{width:"100%",fontSize:11,padding:"5px 8px",minHeight:50,resize:"vertical",fontFamily:"inherit"}} rows={2}/>
                    </div>
                    <button className="soft-btn" style={{fontSize:11,padding:"4px 14px",background:"var(--accent)",color:"#fff",border:"none",borderRadius:6}} onClick={async()=>{
                      try{
                        const upd: Record<string, unknown> = {};
                        if(tgConfig?.model !== undefined) upd.model = tgConfig.model;
                        if(tgConfig?.profile) upd.profile = tgConfig.profile;
                        if(tgConfig?.use_memory !== undefined) upd.use_memory = tgConfig.use_memory;
                        if(tgConfig?.use_web_search !== undefined) upd.use_web_search = tgConfig.use_web_search;
                        if(tgConfig?.welcome_message) upd.welcome_message = tgConfig.welcome_message;
                        await api.updateTelegramConfig(upd); await loadTelegram(); setError("");
                      }catch(e){setError(`Telegram: ${normalizeErrorMessage(e)}`)}
                    }}>Сохранить настройки</button>
                  </div>
                </div>
              )}
              {tgTab === "users" && (
                <div>
                  <div style={{fontSize:11,color:"var(--text-muted)",marginBottom:8}}>Пользователи, написавшие боту.</div>
                  <div style={{marginBottom:10}}>
                    <label style={{fontSize:11,display:"flex",alignItems:"center",gap:4,cursor:"pointer"}}>
                      <input type="checkbox" checked={tgConfig?.allowed_users==="all"} onChange={async e=>{await updateTelegramAllowedUsers(e.target.checked ? "all" : "whitelist");}}/>
                      Разрешить всем
                    </label>
                  </div>
                  {tgUsers.length===0 && <div style={{fontSize:11,color:"var(--text-muted)",padding:"12px 0",textAlign:"center"}}>Пока нет пользователей</div>}
                  {tgUsers.map(u=>(
                    <div key={String(u.chat_id)} style={{padding:"8px 12px",borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:4,display:"flex",alignItems:"center",justifyContent:"space-between"}}>
                      <div>
                        <span style={{fontWeight:600,fontSize:12}}>{u.first_name||""} {u.last_name||""}</span>
                        {u.username && <span style={{fontSize:10,color:"var(--text-muted)",marginLeft:6}}>@{u.username}</span>}
                        <span style={{fontSize:9,color:"var(--text-muted)",marginLeft:6}}>ID: {u.chat_id}</span>
                      </div>
                      <div style={{display:"flex",alignItems:"center",gap:6}}>
                        <span style={{fontSize:10,color:u.allowed?"#4caf50":"#f44336"}}>{u.allowed?"Разрешён":"Заблокирован"}</span>
                        <button className="soft-btn" style={{fontSize:9,padding:"2px 8px"}} onClick={() => toggleTelegramUserAccess(u)}>{u.allowed ? "Запретить" : "Разрешить"}</button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {tgTab === "log" && (
                <div>
                  <div style={{fontSize:11,color:"var(--text-muted)",marginBottom:8}}>Последние сообщения через бота</div>
                  {tgLog.length===0 && <div style={{fontSize:11,color:"var(--text-muted)",padding:"12px 0",textAlign:"center"}}>Пока нет сообщений</div>}
                  <div style={{maxHeight:400,overflow:"auto"}}>
                    {tgLog.map((l,i)=>(
                      <div key={i} style={{padding:"6px 10px",borderRadius:8,marginBottom:3,background:l.direction==="in"?"rgba(99,102,241,0.08)":"rgba(76,175,80,0.08)",borderLeft:`3px solid ${l.direction==="in"?"var(--accent)":"#4caf50"}`}}>
                        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:2}}>
                          <span style={{fontSize:9,fontWeight:600,color:l.direction==="in"?"var(--accent)":"#4caf50"}}>{l.direction==="in"?"→ Входящее":"← Ответ"}</span>
                          <span style={{fontSize:9,color:"var(--text-muted)"}}>{l.created_at?new Date(l.created_at).toLocaleString("ru-RU"):""}</span>
                        </div>
                        <div style={{fontSize:11,color:"var(--text)",wordBreak:"break-word",maxHeight:60,overflow:"hidden"}}>{l.text}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : sideTab === "pipelines" ? (
            <div className="settings-main-card" style={{overflow:"auto"}}>
              <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:12}}>
                <div style={{fontSize:15,fontWeight:700,color:"var(--text)"}}><IconText icon={Workflow} size={15}>Пайплайны</IconText></div>
                <button className="soft-btn" style={{fontSize:10,padding:"3px 10px",border:"1px solid var(--border)",display:"inline-flex",alignItems:"center",gap:6}} onClick={loadPipelines}><UiIcon icon={RefreshCw} size={13} />Обновить</button>
              </div>
              <PanelNotice title="Пайплайны временно недоступны" message={pipelinesError} onRetry={loadPipelines} />
              <div className="settings-desc" style={{marginBottom:12}}>Автоматические задачи по расписанию</div>
              <div style={{padding:12,borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:14}}>
                <div style={{fontSize:12,fontWeight:600,color:"var(--text)",marginBottom:8}}>＋ Новый пайплайн</div>
                <div style={{display:"flex",gap:6,flexWrap:"wrap",marginBottom:6}}>
                  <input placeholder="Название" value={pipeForm.name} onChange={e=>setPipeForm({...pipeForm,name:e.target.value})} className="rename-input" style={{flex:1,minWidth:120,fontSize:11,padding:"4px 8px"}}/>
                  <select value={pipeForm.task_type} onChange={e=>setPipeForm({...pipeForm,task_type:e.target.value})} className="topbar-select dark-select" style={{fontSize:11}}>
                    <option value="prompt">Промпт</option><option value="web_search">Веб-поиск</option><option value="plugin">Плагин</option><option value="workflow">Workflow</option><option value="http">HTTP</option>
                  </select>
                  <select value={pipeForm.interval_minutes} onChange={e=>setPipeForm({...pipeForm,interval_minutes:+e.target.value})} className="topbar-select dark-select" style={{fontSize:11}}>
                    <option value={5}>5 мин</option><option value={15}>15 мин</option><option value={30}>30 мин</option><option value={60}>1 час</option><option value={180}>3 часа</option><option value={360}>6 часов</option><option value={720}>12 часов</option><option value={1440}>24 часа</option>
                  </select>
                </div>
                {/* Description of the selected task type — changes with the dropdown. */}
                <div style={{fontSize:10,color:"var(--text-muted)",marginBottom:6,lineHeight:1.4}}>{PIPELINE_TYPE_DESCRIPTIONS[pipeForm.task_type] || ""}</div>
                <input placeholder={PIPELINE_TASK_PLACEHOLDERS[pipeForm.task_type] || "Параметр"} value={getPipelineTaskInputValue(pipeForm.task_data)} onChange={e=>{const key=getPipelineTaskDataKey(pipeForm.task_type);setPipeForm({...pipeForm,task_data:{...pipeForm.task_data,[key]:e.target.value}})}} className="rename-input" style={{width:"100%",fontSize:11,padding:"4px 8px",marginBottom:6}}/>
                {pipeForm.task_type==="web_search" && (
                  <>
                    <select value={pipeForm.task_data.mode || ""} onChange={e=>setPipeForm({...pipeForm,task_data:{...pipeForm.task_data,mode:e.target.value}})} className="topbar-select dark-select" style={{fontSize:11,marginBottom:4,width:"100%"}}>
                      <option value="">Обычный поиск</option>
                      <option value="news">Новости</option>
                      <option value="local_news">Локальные новости</option>
                    </select>
                    <div style={{fontSize:10,color:"var(--text-muted)",marginBottom:6,lineHeight:1.4}}>{PIPELINE_WEB_MODE_DESCRIPTIONS[pipeForm.task_data.mode || ""]}</div>
                    <div style={{display:"flex",alignItems:"center",gap:6,marginBottom:6}}>
                      <span style={{fontSize:10,color:"var(--text-muted)"}}>Кол-во результатов</span>
                      <input type="number" min={1} max={20} value={pipeForm.task_data.max_results || "5"} onChange={e=>{const v=String(Math.max(1,Math.min(20,Number(e.target.value)||5)));setPipeForm({...pipeForm,task_data:{...pipeForm.task_data,max_results:v}})}} className="rename-input" style={{width:64,fontSize:11,padding:"4px 6px"}}/>
                    </div>
                  </>
                )}
                <button className="soft-btn" style={{fontSize:11,padding:"4px 14px",background:"var(--accent)",color:"#fff",border:"none",borderRadius:6}} onClick={createPipeline}>Создать</button>
              </div>
              {pipelinesList.length===0 && !pipelinesError && <div style={{fontSize:11,color:"var(--text-muted)",padding:"12px 0",textAlign:"center"}}>Пайплайнов пока нет</div>}
              {pipelinesList.map(p=>{
                const outputLogs = getPipelineOutputLogs(p, pipelineLogsById[p.id] || []);
                return (
                <div key={p.id} style={{padding:"10px 12px",borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:6}}>
                  <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:4}}>
                    <div>
                      <span style={{fontWeight:600,fontSize:12,color:"var(--text)"}}>{p.name}</span>
                      <span style={{fontSize:10,color:"var(--text-muted)",marginLeft:8}}>{PIPELINE_TYPE_LABELS[p.task_type || ""] || p.task_type} • каждые {p.interval_minutes} мин</span>
                      <span style={{fontSize:9,color:p.enabled?"#4caf50":"#f44336",marginLeft:6}}>{p.enabled?"● вкл":"○ выкл"}</span>
                    </div>
                    <div style={{display:"flex",gap:4}}>
                      <button className="soft-btn" style={{fontSize:9,padding:"2px 8px"}} title="Запустить сейчас" onClick={() => runPipelineNow(p.id)}><UiIcon icon={Play} size={12} /></button>
                      <button className="soft-btn" style={{fontSize:9,padding:"2px 8px"}} title={p.enabled?"Выключить":"Включить"} onClick={() => togglePipelineEnabled(p)}><UiIcon icon={p.enabled ? Pause : Play} size={12} /></button>
                      <button className="soft-btn" style={{fontSize:9,padding:"2px 8px",color:"#f44336"}} title="Удалить" onClick={() => deletePipeline(p.id)}><UiIcon icon={Trash2} size={12} /></button>
                    </div>
                  </div>
                  <div style={{fontSize:10,color:"var(--text-muted)"}}>
                    {(p.run_count||0)>0 && <span>Запусков: {p.run_count} • </span>}
                    {p.last_run && <span>Посл.: {formatPipelineTimestamp(p.last_run)} • </span>}
                    {p.next_run && <span>След.: {formatPipelineTimestamp(p.next_run)}</span>}
                  </div>
                  {outputLogs.length > 0 && (
                    <details open style={{marginTop:6,fontSize:10,color:"var(--text)"}}>
                      <summary style={{cursor:"pointer",color:"var(--text-muted)"}}>Выводы ({outputLogs.length})</summary>
                      <div style={{marginTop:6,display:"grid",gap:6}}>
                        {outputLogs.map((log, index) => (
                          <details key={`${log.id || index}-${log.started_at || ""}`} style={{padding:8,borderRadius:6,border:"1px solid var(--border)",background:"var(--bg)"}}>
                            <summary style={{cursor:"pointer",color:"var(--text-muted)"}}>
                              Вывод: {formatPipelineLogDate(log)}
                              {!isPipelineLogOk(log) && <span style={{marginLeft:6,color:"#f44336"}}>ошибка</span>}
                            </summary>
                            <div style={{marginTop:8,maxHeight:260,overflow:"auto",fontSize:10,lineHeight:1.45}}>
                              <PipelineResultView value={log.result} />
                              {log.error && <div style={{marginTop:6,color:"#f44336",whiteSpace:"pre-wrap",wordBreak:"break-word"}}>Ошибка: {log.error}</div>}
                            </div>
                          </details>
                        ))}
                      </div>
                    </details>
                  )}
                  {p.last_error && <div style={{fontSize:10,color:"#f44336",marginTop:2}}>Ошибка: {p.last_error}</div>}
                </div>
                );
              })}
            </div>
          ) : sideTab === "dashboard" ? (
            <div className="settings-main-card" style={{overflow:"auto"}}>
              <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:16}}>
                <div style={{fontSize:15,fontWeight:700,color:"var(--text)"}}><IconText icon={LayoutDashboard} size={15}>Панель</IconText></div>
                <button className="soft-btn" style={{fontSize:10,padding:"3px 10px",border:"1px solid var(--border)",display:"inline-flex",alignItems:"center",gap:6}} onClick={loadDashboard}><UiIcon icon={RefreshCw} size={13} />Обновить</button>
              </div>
              <PanelNotice title="Проблема синхронизации панели" message={dashboardError} onRetry={loadDashboard} tone={dashData || projectBrainStatus ? "warning" : "error"} />
              <RuntimeStatusSection status={runtimeStatus} />
              <CapabilityStatusSection status={projectBrainStatus as { capabilities?: Record<string, { available?: boolean; reason?: string; mode?: string; missing_packages?: string[]; hint?: string }> } | null} />
              <PersonaStatusSection status={personaStatus} busy={personaBusy} onRollback={handlePersonaRollback} />
              <AgentOsStatusSection health={agentOsHealth} dashboard={agentOsDashboard} limits={agentOsLimits} />
              {!dashData && !dashboardError ? <div style={{color:"var(--text-muted)",fontSize:12}}>Загрузка...</div> : !dashData ? null : (
                <>
                  <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(130px,1fr))",gap:8,marginBottom:16}}>
                    {[
                      {label:"Запросов",value:(dashData.total_runs as number)||0,icon:<UiIcon icon={MessageSquare} size={18} />},
                      {label:"Сегодня",value:(dashData.today as number)||0,icon:<UiIcon icon={CalendarDays} size={18} />},
                      {label:"За неделю",value:(dashData.this_week as number)||0,icon:<UiIcon icon={CalendarDays} size={18} style={{opacity:0.75}} />},
                      {label:"Успешность",value:`${(dashData.success_rate as number)||0}%`,icon:<UiIcon icon={BarChart3} size={18} />},
                      {label:"Чатов",value:(dashData.chats as number)||0,icon:<UiIcon icon={MessageSquare} size={18} />},
                      {label:"Сообщений",value:(dashData.messages as number)||0,icon:<UiIcon icon={ScrollText} size={18} />},
                      {label:"Ср. длина",value:(dashData.avg_answer_length as number)||0,icon:<UiIcon icon={FileText} size={18} />},
                      {label:"Плагинов",value:(dashData.plugins as number)||0,icon:<UiIcon icon={Settings} size={18} />},
                    ].map(s=>(
                      <div key={s.label} style={{padding:"12px",borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",textAlign:"center"}}>
                        <div style={{fontSize:20,marginBottom:4,display:"flex",justifyContent:"center"}}>{s.icon}</div>
                        <div style={{fontSize:18,fontWeight:700,color:"var(--text)"}}>{s.value}</div>
                        <div style={{fontSize:10,color:"var(--text-muted)",marginTop:2}}>{s.label}</div>
                      </div>
                    ))}
                  </div>
                  {dashData.daily_activity && (
                    <div style={{marginBottom:16}}>
                      <div style={{fontSize:12,fontWeight:600,color:"var(--text)",marginBottom:8}}>Активность (14 дней)</div>
                      <div style={{display:"flex",alignItems:"flex-end",gap:3,height:80,padding:"0 4px"}}>
                        {(dashData.daily_activity as Array<{count:number;date:string}>).map((d,i)=>{
                          const max = Math.max(...(dashData.daily_activity as Array<{count:number}>).map(x=>x.count),1);
                          const h = Math.max(4, (d.count/max)*70);
                          return <div key={i} style={{flex:1,display:"flex",flexDirection:"column",alignItems:"center",gap:2}}>
                            <div style={{fontSize:8,color:"var(--text-muted)"}}>{d.count||""}</div>
                            <div style={{width:"100%",height:h,borderRadius:3,background:d.count?"var(--accent)":"var(--border)",opacity:d.count?1:0.3,transition:"height .3s"}}/>
                            <div style={{fontSize:7,color:"var(--text-muted)",whiteSpace:"nowrap"}}>{d.date}</div>
                          </div>
                        })}
                      </div>
                    </div>
                  )}
                  {(dashData.top_models as Array<{model:string;count:number}>)?.length > 0 && (
                    <div style={{marginBottom:16}}>
                      <div style={{fontSize:12,fontWeight:600,color:"var(--text)",marginBottom:6}}>Модели</div>
                      {(dashData.top_models as Array<{model:string;count:number}>).map(m=>{
                        const pct = (dashData.total_runs as number) ? Math.round(m.count/(dashData.total_runs as number)*100) : 0;
                        return <div key={m.model} style={{display:"flex",alignItems:"center",gap:8,marginBottom:4}}>
                          <div style={{fontSize:11,color:"var(--text)",minWidth:140,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{m.model}</div>
                          <div style={{flex:1,height:6,borderRadius:3,background:"var(--border)",overflow:"hidden"}}><div style={{width:`${pct}%`,height:"100%",borderRadius:3,background:"var(--accent)"}}/></div>
                          <div style={{fontSize:10,color:"var(--text-muted)",minWidth:40,textAlign:"right"}}>{m.count} ({pct}%)</div>
                        </div>
                      })}
                    </div>
                  )}
                  {(dashData.top_routes as Array<{route:string;count:number}>)?.length > 0 && (
                    <div style={{marginBottom:16}}>
                      <div style={{fontSize:12,fontWeight:600,color:"var(--text)",marginBottom:6}}>Типы задач</div>
                      {(dashData.top_routes as Array<{route:string;count:number}>).map(r=>(
                        <div key={r.route} style={{display:"flex",justifyContent:"space-between",fontSize:11,padding:"3px 0",borderBottom:"1px solid var(--border)"}}>
                          <span style={{color:"var(--text)"}}>{r.route || "—"}</span>
                          <span style={{color:"var(--text-muted)"}}>{r.count}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  {dashData.memory && typeof dashData.memory === "object" && (
                    <div>
                      <div style={{fontSize:12,fontWeight:600,color:"var(--text)",marginBottom:6}}>Память</div>
                      <div style={{fontSize:11,color:"var(--text-muted)"}}>Всего: {((dashData.memory as Record<string,number>).total || (dashData.memory as Record<string,number>).count || 0)} записей</div>
                    </div>
                  )}
                </>
              )}
            </div>
          ) : sideTab === "settings" ? (
            <div className="settings-main-card">
              <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:12}}>
                <div style={{fontSize:13,fontWeight:600,color:"var(--text)"}}>Настройки по умолчанию</div>
                <button className="soft-btn" style={{fontSize:10,padding:"3px 10px",border:"1px solid var(--border)"}} onClick={async()=>{const ml=await refreshModels();setError(ml.length?"":`Ollama недоступна`);}}>↻ Обновить модели ({modelOpts.length})</button>
              </div>
              <div className="settings-desc" style={{marginBottom:14,fontSize:11}}>Сохранённые значения загружаются при каждом запуске Elira</div>
              <div className="settings-tile-grid">
                <div className="settings-tile">
                  <div className="settings-title">Модель по умолчанию</div>
                  <select value={settingsModel} onChange={e=>{setSettingsModel(e.target.value);setSettingsSaved(false);}} className="topbar-select full dark-select">
                    {(modelOpts?.length?modelOpts:[{name:settingsModel}]).map((i,idx)=>{const n=getName(i);return <option key={n+idx} value={n}>{n}</option>})}
                  </select>
                  <div className="settings-desc" style={{marginTop:4,fontSize:10}}>Прямой режим: вопрос уходит в эту модель без маршрутизации и многошаговой оркестрации.</div>
                </div>
                <div className="settings-tile">
                  <div className="settings-title">Контекст Ollama</div>
                  <div style={{display:"flex",alignItems:"center",gap:10}}>
                    <input type="range" min={4096} max={262144} step={1024} value={settingsContext} onChange={e=>{setSettingsContext(Number(e.target.value));setSettingsSaved(false);}} style={{flex:1,accentColor:"var(--accent)"}}/>
                    <span style={{fontSize:12,color:"var(--text-muted)",minWidth:50,textAlign:"right"}}>{settingsContext >= 1024 ? Math.round(settingsContext/1024)+"K" : settingsContext}</span>
                  </div>
                  <div className="settings-desc" style={{marginTop:4}}>Чем больше контекст — тем больше информации помещается, но медленнее генерация</div>
                </div>
                <div className="settings-tile">
                  <div className="settings-title">Профиль по умолчанию</div>
                  <select value={settingsProfile} onChange={e=>{setSettingsProfile(e.target.value);setSettingsSaved(false);}} className="topbar-select full dark-select">
                    {Object.keys(PROFILE_DESCRIPTIONS).map(n=><option key={n} value={n}>{n}</option>)}
                  </select>
                  <div className="settings-desc">{PROFILE_DESCRIPTIONS[settingsProfile]}</div>
                </div>
                <div className="settings-tile" style={{gridColumn:"1 / -1"}}>
                  <div className="settings-title">Тема оформления</div>
                  <div className="settings-desc" style={{marginBottom:8,fontSize:11}}>Клик — мгновенный preview. Ctrl+Shift+T — следующая тема.</div>
                  <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit, minmax(140px, 1fr))",gap:8}}>
                    {[
                      {id:"dark",label:"Тёмная",sub:"Claude-style",bg:"#1a1a1e",accent:"#7c9fff",text:"#e8e8ec"},
                      {id:"light",label:"Светлая",sub:"чистая",bg:"#f5f5f7",accent:"#4a7aff",text:"#1d1d1f"},
                      {id:"cursor",label:"Cursor",sub:"SaaS, оранж",bg:"#0a0a0f",accent:"#ff5e1f",text:"#ededf3"},
                      {id:"cyber",label:"Cyber",sub:"терминал, неон",bg:"#06080a",accent:"#00ff9f",text:"#d4ffe0"},
                      {id:"glass",label:"Glass",sub:"frost, blur",bg:"#14081e",accent:"#c084fc",text:"#f5f3ff"},
                      {id:"minimal",label:"Minimal",sub:"Notion-ghost",bg:"#fafafa",accent:"#18181b",text:"#18181b"},
                    ].map(t => (
                      <button key={t.id} onClick={()=>setTheme(t.id)} style={{
                        padding:"8px 10px",
                        borderRadius:10,
                        border:"1.5px solid "+(theme===t.id?"var(--accent)":"var(--border)"),
                        background:theme===t.id?"var(--accent-dim)":"transparent",
                        color:"var(--text-primary)",
                        cursor:"pointer",
                        display:"flex",
                        flexDirection:"column",
                        alignItems:"flex-start",
                        gap:6,
                        textAlign:"left",
                        transition:"all 0.15s",
                      }}>
                        {/* mini preview swatch */}
                        <div style={{
                          width:"100%",
                          height:42,
                          borderRadius:6,
                          background:t.bg,
                          border:"1px solid rgba(127,127,127,0.2)",
                          display:"flex",
                          alignItems:"center",
                          padding:"0 8px",
                          gap:6,
                          overflow:"hidden",
                        }}>
                          <span style={{width:8,height:8,borderRadius:"50%",background:t.accent,flexShrink:0}}/>
                          <span style={{color:t.text,fontSize:11,fontWeight:500,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{t.label}</span>
                          <span style={{flex:1,height:1,background:t.text,opacity:0.15}}/>
                        </div>
                        <div style={{display:"flex",flexDirection:"column",gap:1}}>
                          <span style={{fontSize:12,fontWeight:600}}>{t.label}</span>
                          <span style={{fontSize:10,color:"var(--text-muted)"}}>{t.sub}</span>
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
                <div className="settings-tile" style={{gridColumn:"1 / -1"}}>
                  <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:10,marginBottom:4}}>
                    <div className="settings-title">Оркестрация моделей</div>
                    <button
                      onClick={()=>toggleOrchestration(!orchestrationEnabled)}
                      style={{padding:"3px 10px",borderRadius:99,fontSize:10,border:"1px solid " + (orchestrationEnabled ? "rgba(34,197,94,0.45)" : "var(--border)"),background:orchestrationEnabled ? "rgba(34,197,94,0.12)" : "transparent",color:orchestrationEnabled ? "#22c55e" : "var(--text-muted)",cursor:"pointer"}}
                    >{orchestrationEnabled ? "Включена" : "Выключена"}</button>
                  </div>
                  <div className="settings-desc" style={{marginBottom:8}}>Отдельный режим: при включении чат использует таблицу ниже; модель по умолчанию не участвует.</div>
                  {(["code","project","research","chat"] as const).map(route => {
                    const routeLabels: Record<string,string> = {code:"Код",project:"Проект",research:"Исследование",chat:"Чат"};
                    const routeDescs: Record<string,string> = {code:"Написание, ревью и отладка кода",project:"Работа с файлами проекта",research:"Поиск, анализ, факты",chat:"Обычные вопросы и диалог"};
                    const current = routeMap[route] || [];
                    const allModels = (modelOpts?.length ? modelOpts : []).map(getName);
                    return (
                      <div key={route} style={{padding:"8px 10px",borderRadius:8,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:6}}>
                        <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:4}}>
                          <div><span style={{fontWeight:600,fontSize:12}}>{routeLabels[route]}</span><span style={{fontSize:10,color:"var(--text-muted)",marginLeft:8}}>{routeDescs[route]}</span></div>
                        </div>
                        <div style={{display:"flex",gap:6,flexWrap:"wrap",alignItems:"center"}}>
                          <select value={current[0] || ""} onChange={e=>{const val = e.target.value;const rest = current.filter(m => m !== val).slice(0, 2);const updated = {...routeMap, [route]: val ? [val, ...rest] : current};setRouteMap(updated);setSettingsSaved(false);}} className="topbar-select dark-select" style={{fontSize:11,padding:"3px 6px"}}>
                            <option value="">— не задана —</option>
                            {allModels.map(n=><option key={n} value={n}>{n}</option>)}
                          </select>
                          {current.length > 1 && <span style={{fontSize:10,color:"var(--text-muted)"}}>фоллбэк: {current.slice(1).join(" → ")}</span>}
                          {current.length > 1 && <button className="soft-btn" style={{fontSize:9,padding:"1px 6px",marginLeft:4}} onClick={()=>{setRouteMap({...routeMap,[route]:[current[0]]});setSettingsSaved(false)}} title="Очистить фоллбэк">✕</button>}
                        </div>
                      </div>
                    );
                  })}
                  <button
                    className="soft-btn"
                    style={{fontSize:11,padding:"4px 12px",border:"1px solid var(--border)",marginTop:2}}
                    onClick={async()=>{try{await saveSettings({ route_model_map: routeMap, orchestration_enabled: orchestrationEnabled }, false);}catch(e){setError(normalizeErrorMessage(e));}}}
                  >Сохранить оркестрацию</button>
                </div>
              </div>
              <div className="settings-desc" style={{marginTop:12,fontSize:10,color:"var(--text-muted)"}}>Горячие клавиши: Ctrl+N новый чат · Escape стоп · Ctrl+Shift+T тема</div>

              {/* Planner keywords editor — controls which route each query goes to */}
              <div style={{marginTop:18,paddingTop:14,borderTop:"1px solid var(--border)"}}>
                <PlannerKeywordsPanel />
              </div>

              <button
                style={{marginTop:14,padding:"8px 24px",borderRadius:8,border:"1px solid var(--accent)",background:settingsSaved?"rgba(16,185,129,0.15)":"var(--accent)",color:settingsSaved?"#10b981":"#fff",cursor:"pointer",fontSize:13,fontWeight:600,transition:"all 0.2s"}}
                onClick={async()=>{
                  try {
                    await saveSettings();
                  } catch(e){setError(normalizeErrorMessage(e));}
                }}
              >{settingsSaved?"✓ Сохранено":"Сохранить"}</button>
              <div style={{marginTop:18}}>
                <div className="settings-title" style={{marginBottom:8}}>Навыки</div>
                <div className="settings-desc" style={{marginBottom:10}}>Включи / выключи возможности</div>
                <div className="skills-grid">{SKILLS.map(s=><button key={s.id} className={`skill-chip ${skills.includes(s.id)?"active":""}`} onClick={()=>toggleSkill(s.id)} title={s.desc}>{s.label}</button>)}</div>
              </div>
              <div style={{marginTop:18}}>
                <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:8}}>
                  <div className="settings-title">Плагины</div>
                  <button className="soft-btn" style={{fontSize:10,padding:"3px 10px",border:"1px solid var(--border)",display:"inline-flex",alignItems:"center",gap:6}} onClick={reloadPlugins}><UiIcon icon={RefreshCw} size={12} />Перезагрузить</button>
                </div>
                <div className="settings-desc" style={{marginBottom:10}}>Пользовательские .py скрипты в data/plugins/</div>
                {pluginList.length===0 && <div style={{fontSize:11,color:"var(--text-muted)",padding:"8px 0"}}>Плагинов нет. Положи .py файлы в data/plugins/</div>}
                {pluginList.map(p=>(
                  <div key={p.name} style={{padding:"8px 10px",borderRadius:8,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:6,display:"flex",alignItems:"center",justifyContent:"space-between"}}>
                    <div>
                      <span style={{fontSize:14,marginRight:6}}>{p.icon||"PLG"}</span>
                      <span style={{fontWeight:600,fontSize:12}}>{p.name}</span>
                      <span style={{fontSize:10,color:"var(--text-muted)",marginLeft:8}}>{p.description||""}</span>
                      {p.version && <span style={{fontSize:9,color:"var(--text-muted)",marginLeft:6}}>v{p.version}</span>}
                    </div>
                    <button className={`skill-chip ${p.enabled?"active":""}`} style={{fontSize:10,padding:"2px 10px"}} onClick={() => togglePluginState(p)}>{p.enabled?"Вкл":"Выкл"}</button>
                  </div>
                ))}
              </div>
            </div>
          ) : sideTab === "library" ? (
            <div className="library-table-view">
              <div className={`upload-dropzone ${drag?"active":""}`} onDragOver={onDragOver} onDragLeave={onDragLeave} onDrop={onDrop} onClick={()=>fileRef.current?.click()}>Перетащи файлы (PDF, код, текст)</div>
              <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
                <div className="library-search-row" style={{flex:1}}><span className="library-search-icon"><UiIcon icon={Search} size={12} /></span><input value={libSearch} onChange={e=>setLibSearch(e.target.value)} placeholder="Поиск файлов" className="library-search-input"/></div>
                <button className="soft-btn" style={{fontSize:11,padding:"4px 10px",border:"1px solid var(--border)"}} onClick={()=>selectAllLib(true)}>✓ Все в контекст</button>
                <button className="soft-btn" style={{fontSize:11,padding:"4px 10px",border:"1px solid var(--border)"}} onClick={()=>selectAllLib(false)}>✕ Убрать все</button>
                <span style={{fontSize:10,color:"var(--text-muted)"}}>{ctxF.length} из {libraryFiles.length} в контексте</span>
              </div>
              <div className="library-table">
                <div className="library-table-row header"><div>Имя</div><div>Тип</div><div>Размер</div><div>Контекст</div><div></div></div>
                {fLib.length ? fLib.map(i => <div key={i.id} className={`library-table-row ${selLibId===i.id?"active":""}`} onClick={()=>setSelLibId(i.id)}><div className="table-name">{i.name}</div><div>{(i.type||"").split("/").pop()}</div><div>{Math.round(i.size/1024)||0}K</div><div><input type="checkbox" checked={chatId ? ctxF.some(f => f.id === i.id) : (i.use_in_context !== false)} onChange={e=>{e.stopPropagation();toggleCtx(i.id,e.target.checked);}}/></div><div><button className="mini-icon-btn" onClick={e=>{e.stopPropagation();removeLib(i.id);}}>✕</button></div></div>) : <div className="sidebar-empty" style={{padding:10}}>Нет файлов</div>}
              </div>
              {selLib && <div className="content-card"><div className="content-card-title">{selLib.name}</div><div className="content-card-text">{selLib.type} · {Math.round(selLib.size/1024)||0} KB</div>{selLib.preview ? <pre className="library-preview">{selLib.preview}</pre> : <div className="content-card-text" style={{marginTop:6}}>Превью недоступно</div>}</div>}
            </div>
          ) : sideTab === "memory" ? (
            <MemoryPanel />
          ) : sideTab === "project" ? (
            <ProjectPanel />
          ) : (
            <>
              {ctxF.length > 0 && <div className="context-bar"><div className="context-bar-title"><IconText icon={Paperclip} size={13}>{ctxF.length} файлов доступно (упомяни «файл» или «документ»)</IconText></div><div className="context-tags">{ctxF.map(f=><span key={f.id} className="context-tag">{f.name}<button className="context-tag-remove" onClick={()=>toggleCtx(f.id,false)} title="Убрать из контекста">✕</button></span>)}</div></div>}
              {messages.length === 0 && !streaming && <div style={{flex:1,display:"flex",alignItems:"center",justifyContent:"center"}}><div style={{textAlign:"center",color:"var(--text-muted)"}}><svg width="48" height="48" viewBox="0 0 64 64" fill="none" style={{marginBottom:12,opacity:0.4}}><defs><linearGradient id="jgw" x1="12" y1="10" x2="52" y2="54" gradientUnits="userSpaceOnUse"><stop stopColor="#7C3AED"/><stop offset="1" stopColor="#06B6D4"/></linearGradient></defs><rect x="5" y="5" width="54" height="54" rx="14" fill="#0B1020"/><circle cx="32" cy="32" r="14" stroke="url(#jgw)" strokeWidth="3"/><circle cx="32" cy="32" r="6" fill="url(#jgw)"/></svg><div style={{fontSize:14}}>Чем могу помочь?</div></div></div>}
              <div className="message-stream compact-stream" ref={msgRef}>
                {messages.map(msg => <MessageItem key={msg.id} msg={msg} />)}
                {streaming && streamText && <div className="message-row assistant"><div className="message-bubble smaller-text assistant-bubble streaming-active"><MarkdownRenderer content={streamText}/><span className="typing-cursor"/></div></div>}
                {streaming && !streamText && (
                  <div className="message-row assistant">
                    <div className="message-bubble smaller-text assistant-bubble thinking-bubble">
                      <div className="thinking-indicator">
                        <div className="thinking-dots"><span/><span/><span/></div>
                        <span className="thinking-text">{phase || "Думаю..."}</span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
              {error && <div className="error-banner smaller-text">{error}</div>}
              {chartData && chartData.values.length > 0 && !working && (() => {
                const cd = chartData;
                return (
                  <div style={{background:"var(--bg-surface)",border:"1px solid var(--border)",borderRadius:8,padding:"10px 14px",marginTop:4}}>
                    <div style={{fontSize:11,color:"var(--text-muted)",marginBottom:6,display:"flex",justifyContent:"space-between"}}>
                      <IconText icon={BarChart3} size={13}>{cd.valueLabel}</IconText>
                      <button className="soft-btn" style={{fontSize:10,padding:"1px 6px"}} onClick={()=>setChartData(null)}>✕</button>
                    </div>
                    <div style={{display:"flex",gap:3,alignItems:"flex-end",height:72}}>
                      {cd.values.map((v,i)=>{const mx=Math.max(...cd.values)||1;return <div key={i} title={`${cd.labels[i]}: ${v}`} style={{flex:1,minWidth:6,maxWidth:36,background:"var(--accent)",opacity:0.75,height:(v/mx*68)+"px",borderRadius:"3px 3px 0 0"}}></div>;})}
                    </div>
                    <div style={{display:"flex",gap:3,marginTop:2,overflow:"hidden"}}>
                      {cd.labels.map((l,i)=><div key={i} style={{flex:1,minWidth:6,maxWidth:36,fontSize:9,color:"var(--text-muted)",textAlign:"center",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{l}</div>)}
                    </div>
                  </div>
                );
              })()}
              <div className="composer-wrap" onDragOver={onDragOver} onDragLeave={onDragLeave} onDrop={onDrop}>
                <div className={`chat-input-shell ${drag?"drag-active":""}`}>
                  <button className="input-plus-btn" onClick={()=>fileRef.current?.click()}>+</button>
                  <textarea ref={taRef} value={input} onChange={e=>setInput(e.target.value)} onKeyDown={handleKeyDown} placeholder="Напиши сообщение..." className="chat-textarea"/>
                  <button className="send-btn" onClick={working ? handleStop : handleSend} style={working ? {background:"rgba(255,70,70,0.15)",borderColor:"rgba(255,70,70,0.3)",color:"#ff9090"} : undefined}>{working?"Стоп":"Отправить"}</button>
                  <input ref={fileRef} type="file" multiple hidden onChange={e=>handleFiles(e.target.files)}/>
                </div>
                <div className="composer-selectors" style={{justifyContent:"center"}}>
                  <select value={isAutoModel(model) ? (settingsModel || "gemma3:4b") : model} onChange={e=>setModel(e.target.value)} className="composer-select" disabled={orchestrationEnabled} title={orchestrationEnabled?"Оркестрация включена: модель выбирается из таблицы в настройках":"Текущая модель: "+model}>
                    {(modelOpts?.length?modelOpts:[{name:model}]).map((i,idx)=>{const n=getName(i);return <option key={n+idx} value={n}>{shortModelName(n)}</option>})}
                  </select>
                  <select value={profile} onChange={e=>setProfile(e.target.value)} className="composer-select">{Object.keys(PROFILE_DESCRIPTIONS).map(n=><option key={n} value={n}>{n}</option>)}</select>
                  <button onClick={() => toggleOrchestration(!orchestrationEnabled)} style={{padding:"2px 10px",borderRadius:99,fontSize:10,border:"1px solid " + (orchestrationEnabled ? "rgba(34,197,94,0.45)" : "var(--border)"),background:orchestrationEnabled ? "rgba(34,197,94,0.12)" : "transparent",color:orchestrationEnabled ? "#22c55e" : "var(--text-muted)",cursor:"pointer"}}>{orchestrationEnabled ? "Орк ON" : "Орк"}</button>
                  <button onClick={() => { const next = !multiAgent; setMultiAgent(next); if (next && orchestrationEnabled) toggleOrchestration(false); }} style={{padding:"2px 10px",borderRadius:99,fontSize:10,border:"1px solid " + (multiAgent ? "rgba(244,114,182,0.4)" : "var(--border)"),background:multiAgent ? "rgba(244,114,182,0.12)" : "transparent",color:multiAgent ? "#f472b6" : "var(--text-muted)",cursor:"pointer"}}>{multiAgent ? "Multi ON" : "Multi"}</button>
                </div>
              </div>
            </>
          )}
        </div>
      </main>

      {showPanel && sideTab === "chats" && (
        <ArtifactPanel
          messages={messages}
          streamingCode={streamText}
          onClose={() => setShowPanel(false)}
        />
      )}

      <SpotlightOverlay
        open={spotlightOpen}
        onClose={() => setSpotlightOpen(false)}
        onPick={handleSpotlightPick}
      />
    </div>
  );
}
