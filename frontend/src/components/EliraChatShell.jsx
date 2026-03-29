/**
 * EliraChatShell.jsx — v3
 *
 * Фиксы:
 *   • Индикатор прогресса: "Поиск...", "Генерация...", "Проверка..."
 *   • Быстрее ощущается — фазы видны до первого токена
 *   • Code tab работает как артефакты Claude
 */

import React, { useEffect, useMemo, useRef, useState, useCallback } from "react";
import {
  BarChart3,
  BookOpen,
  Bot,
  BrainCircuit,
  Braces,
  CalendarDays,
  Code2,
  Download,
  FileText,
  Files,
  FolderOpen,
  Globe,
  LayoutDashboard,
  ListTodo,
  Menu,
  MessageSquare,
  Moon,
  Paperclip,
  Pause,
  Pencil,
  Pin,
  Play,
  RefreshCw,
  ScrollText,
  Search,
  Send,
  Settings,
  Square,
  Sun,
  Trash2,
  Users,
  Workflow,
} from "lucide-react";
import { api, executeStream } from "../api/ide";
import IdeWorkspaceShell from "./IdeWorkspaceShell";
import MarkdownRenderer from "./MarkdownRenderer";
import ArtifactPanel from "./ArtifactPanel";
import MemoryPanel from "./MemoryPanel";
import ProjectPanel from "./ProjectPanel";
import "../styles/markdown.css";

const LIBRARY_KEY = "elira_library_files_v7";
const CHAT_CONTEXT_KEY = "elira_chat_context_map_v7";

const MAX_HISTORY_PAIRS = 10;

const PROFILE_DESCRIPTIONS = {
  "Универсальный": "Ясный, структурированный и профессиональный тон.",
  "Программист": "Код, исправления, архитектура, рефакторинг.",
  "Исследователь": "Факты, источники, web-поиск.",
  "Аналитик": "Выводы, риски, декомпозиция.",
  "Сократ": "Обучение через наводящие вопросы.",
};

const SKILLS = [
  { id: "web_search", label: "Веб-поиск", desc: "Поиск в интернете" },
  { id: "code_analysis", label: "Анализ кода", desc: "Разбор структуры кода" },
  { id: "file_context", label: "Контекст файлов", desc: "Загруженные файлы в ответах" },
  { id: "memory", label: "Память", desc: "Запоминание между чатами" },
  { id: "python_exec", label: "Python", desc: "Выполнение скриптов" },
  { id: "project_patch", label: "Патчинг", desc: "Изменение файлов проекта" },
  { id: "pdf_reader", label: "PDF", desc: "Извлечение текста из PDF" },
  { id: "reflection", label: "Рефлексия", desc: "Двойная проверка ответов" },
  { id: "http_api", label: "HTTP/API", desc: "GET/POST запросы к API" },
  { id: "sql_query", label: "SQL", desc: "Запросы к базе данных" },
  { id: "file_gen", label: "Word/Excel", desc: "Генерация документов" },
  { id: "screenshot", label: "Скриншот", desc: "Снимок веб-страницы" },
  { id: "encrypt", label: "Шифрование", desc: "AES шифрование заметок" },
  { id: "archiver", label: "Архиватор", desc: "ZIP создание/распаковка" },
  { id: "converter", label: "Конвертер", desc: "CSV→XLSX, MD→DOCX, JSON→CSV" },
  { id: "regex", label: "Regex", desc: "Тестирование регулярок" },
  { id: "translator", label: "Переводчик", desc: "Перевод через LLM" },
  { id: "csv_analysis", label: "CSV анализ", desc: "Статистика и агрегации" },
  { id: "webhook", label: "Webhook", desc: "Приём входящих вебхуков" },
  { id: "plugins", label: "Плагины", desc: "Пользовательские .py скрипты" },
  { id: "image_gen", label: "Картинки", desc: "FLUX.1 генерация изображений" },
  { id: "git", label: "Git", desc: "Статус, log, diff репозитория" },
];

// Tauri window controls
function loadJson(k, f) { try { return JSON.parse(localStorage.getItem(k) || JSON.stringify(f)); } catch { return f; } }
function saveJson(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { console.warn("localStorage quota exceeded:", e); } }
function loadLibraryFiles() { return loadJson(LIBRARY_KEY, []); }
function saveLibraryFiles(i) { saveJson(LIBRARY_KEY, i); }
function loadChatContextMap() { return loadJson(CHAT_CONTEXT_KEY, {}); }
function saveChatContextMap(v) { saveJson(CHAT_CONTEXT_KEY, v); }
function makeId(p = "id") { return `${p}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`; }
function deriveChatTitle(t) { const c = String(t || "").trim().replace(/\s+/g, " "); return !c ? "Новый чат" : c.length > 28 ? `${c.slice(0, 28)}…` : c; }

function shortModelName(name) {
  if (!name) return "model";
  // YandexGPT-5-Lite-8B-instruct-GGUF в†' YandexGPT
  if (name.toLowerCase().includes("yandex")) return "YandexGPT";
  // nemotron-mini в†' Nemotron Mini, etc.
  return name;
}

function normalizeErrorMessage(e, fb = "Ошибка") {
  const v = e?.message ?? e?.detail ?? e;
  if (!v) return fb;
  if (typeof v === "string") return v;
  if (Array.isArray(v)) return v.map(i => normalizeErrorMessage(i, "")).filter(Boolean).join(" | ") || fb;
  if (typeof v === "object") return v.message || v.msg || JSON.stringify(v);
  return String(v);
}

function humanizeValue(value) {
  return String(value || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function capabilityLabel(key) {
  return {
    vector_memory: "Векторная память",
    screenshot: "Скриншоты",
  }[key] || humanizeValue(key);
}

function capabilityStateText(capability = {}) {
  if (capability.available) return "Доступно";
  if (capability.reason === "optional_dependency_missing") return "Не хватает модулей";
  if (capability.reason) return humanizeValue(capability.reason);
  return "Недоступно";
}

function capabilityModeText(mode) {
  return {
    keyword_fallback: "Резервный поиск по ключевым словам",
  }[mode] || humanizeValue(mode);
}

function UiIcon({ icon: Icon, size = 14, strokeWidth = 2, style }) {
  return <Icon size={size} strokeWidth={strokeWidth} style={{ display: "block", flexShrink: 0, ...style }} aria-hidden="true" />;
}

function IconText({ icon, children, size = 14, gap = 6, style, textStyle }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap, ...style }}>
      <UiIcon icon={icon} size={size} />
      <span style={textStyle}>{children}</span>
    </span>
  );
}

function PanelNotice({ title, message, onRetry, tone = "error" }) {
  if (!message) return null;

  const palette = {
    error: {
      border: "rgba(244,67,54,0.45)",
      background: "rgba(244,67,54,0.08)",
      title: "#f44336",
    },
    warning: {
      border: "rgba(245,166,35,0.45)",
      background: "rgba(245,166,35,0.08)",
      title: "#f5a623",
    },
    info: {
      border: "rgba(99,102,241,0.35)",
      background: "rgba(99,102,241,0.08)",
      title: "var(--accent)",
    },
  }[tone] || {
    border: "rgba(244,67,54,0.45)",
    background: "rgba(244,67,54,0.08)",
    title: "#f44336",
  };

  return (
    <div style={{ marginBottom: 12, padding: "10px 12px", borderRadius: 10, border: `1px solid ${palette.border}`, background: palette.background }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: palette.title, marginBottom: 4 }}>{title}</div>
          <div style={{ fontSize: 11, color: "var(--text)", wordBreak: "break-word", whiteSpace: "pre-wrap" }}>{message}</div>
        </div>
        {onRetry && (
          <button className="soft-btn" style={{ fontSize: 10, padding: "3px 10px", border: "1px solid var(--border)", borderRadius: 6, flexShrink: 0 }} onClick={onRetry}>
            Повторить
          </button>
        )}
      </div>
    </div>
  );
}

function CapabilityStatusSection({ status }) {
  const entries = Object.entries(status?.capabilities || {});
  if (!entries.length) return null;

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text)", marginBottom: 8 }}>Возможности Project Brain</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))", gap: 8 }}>
        {entries.map(([key, capability]) => {
          const packages = Array.isArray(capability?.missing_packages) ? capability.missing_packages.filter(Boolean) : [];
          const available = Boolean(capability?.available);
          const tone = available ? "#4caf50" : "#f5a623";
          return (
            <div key={key} style={{ padding: 12, borderRadius: 10, border: `1px solid ${available ? "rgba(76,175,80,0.28)" : "rgba(245,166,35,0.32)"}`, background: "var(--bg-surface)" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 6 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text)" }}>{capabilityLabel(key)}</div>
                <div style={{ fontSize: 10, fontWeight: 700, color: tone }}>{capabilityStateText(capability)}</div>
              </div>
              {capability?.mode && (
                <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>
                  Режим: {capabilityModeText(capability.mode)}
                </div>
              )}
              {!available && capability?.reason && (
                <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: packages.length || capability?.hint ? 4 : 0 }}>
                  Причина: {capabilityStateText(capability)}
                </div>
              )}
              {packages.length > 0 && (
                <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: capability?.hint ? 4 : 0 }}>
                  Не хватает: <code style={{ fontSize: 10 }}>{packages.join(", ")}</code>
                </div>
              )}
              {capability?.hint && (
                <div style={{ fontSize: 10, color: "var(--text-muted)", wordBreak: "break-word" }}>
                  Подсказка: <code style={{ fontSize: 10 }}>{capability.hint}</code>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function PersonaStatusSection({ status, busy = false, onRollback }) {
  if (!status?.active_version) return null;

  const traits = Array.isArray(status?.latest_traits) ? status.latest_traits : [];
  const models = Array.isArray(status?.model_consistency) ? status.model_consistency : [];

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text)", marginBottom: 8 }}>Личность Elira</div>
      <div style={{ padding: 12, borderRadius: 10, border: "1px solid rgba(99,102,241,0.28)", background: "var(--bg-surface)" }}>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, marginBottom: 10 }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text)", marginBottom: 4 }}>
              Версия v{status.active_version}
            </div>
            <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
              Последняя эволюция: {status.last_evolution_at ? new Date(status.last_evolution_at).toLocaleString("ru-RU") : "—"}
            </div>
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
              Кандидатов в карантине: {status.quarantine_candidates ?? 0}
            </div>
          </div>
          {status.previous_version ? (
            <button
              className="soft-btn"
              style={{ fontSize: 10, padding: "4px 10px", border: "1px solid var(--border)", borderRadius: 6, flexShrink: 0 }}
              onClick={() => onRollback?.(status.previous_version)}
              disabled={busy}
            >
              {busy ? "Откат..." : `Откат к v${status.previous_version}`}
            </button>
          ) : null}
        </div>

        {traits.length ? (
          <div style={{ marginBottom: models.length ? 10 : 0 }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text)", marginBottom: 4 }}>Последние принятые черты</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {traits.map((trait) => (
                <span key={`${trait.trait_key}-${trait.promoted_version || trait.last_seen}`} style={{ fontSize: 10, color: "var(--text)", padding: "4px 8px", borderRadius: 999, border: "1px solid var(--border)", background: "rgba(99,102,241,0.08)" }}>
                  {trait.summary || trait.trait_key}
                </span>
              ))}
            </div>
          </div>
        ) : null}

        {models.length ? (
          <div>
            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text)", marginBottom: 4 }}>Согласованность по моделям</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))", gap: 8 }}>
              {models.map((item) => (
                <div key={`${item.model}-${item.version_id}`} style={{ padding: 10, borderRadius: 8, border: "1px solid var(--border)", background: "rgba(255,255,255,0.01)" }}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text)", marginBottom: 4 }}>{item.model}</div>
                  <div style={{ fontSize: 10, color: "var(--text-muted)" }}>Consistency: {item.consistency_score}</div>
                  <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
                    Обновлено: {item.updated_at ? new Date(item.updated_at).toLocaleString("ru-RU") : "—"}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

async function fileToLibraryRecord(file) {
  let preview = "";
  const name = file.name || "";
  const ext = name.split(".").pop().toLowerCase();

  // Текстовые файлы — читаем на клиенте
  // UTF-8 файлы — читаем на клиенте
  const textExts = ["txt","md","json","js","jsx","ts","tsx","py","css","html","htm","yml","yaml","xml","csv","log","ini","toml","bat","cmd","ps1","sh","sql","rb","php","java","c","cpp","h","hpp","cs","go","rs","swift","kt","r","m","lua","pl","tcl","asm","cfg","conf","env"];
  const isText = file.type.startsWith("text/") || textExts.includes(ext);
  if (isText) try { preview = (await file.text()).slice(0, 12000); } catch {}

  // Бинарные + файлы с другими кодировками → на бекенд
  const serverExts = ["pdf","docx","doc","xlsx","xls","xlsm","zip","bas","vbs","vba","cls","frm","rsc"];
  if (serverExts.includes(ext)) try {
    const d = await api.extractUploadedFileText(file);
    preview = (d.text || "").slice(0, 12000);
  } catch {}

  return { id: makeId("lib"), name: file.name, size: file.size, type: file.type || ext || "unknown", uploaded_at: new Date().toISOString(), preview, use_in_context: true, source: "upload" };
}

/** Files included in context only for the current chat */
function getChatContextFiles(lib, chatId) {
  if (!chatId) return [];
  const map = loadChatContextMap();
  const ids = new Set(map[chatId] || []);
  return lib.filter(i => ids.has(i.id) && i.preview);
}
function buildHistory(msgs) { if (!msgs?.length) return []; const p = msgs.filter(m => m.role === "user" || m.role === "assistant").map(m => ({ role: m.role, content: m.content || "" })); return p.length > MAX_HISTORY_PAIRS * 2 ? p.slice(-MAX_HISTORY_PAIRS * 2) : p; }


// Мемоизированный компонент сообщения — не пере-рендерится при стриминге нового
const MessageItem = React.memo(function MessageItem({ msg }) {
  return (
    <div className={`message-row ${msg.role}`}>
      <div className="message-bubble smaller-text">
        {msg.role === "assistant" ? <MarkdownRenderer content={msg.content}/> : msg.content}
      </div>
    </div>
  );
});

export default function EliraChatShell() {
  const fileRef = useRef(null);
  const msgRef = useRef(null);
  const taRef = useRef(null);
  const streamRef = useRef(null);
  const stoppedRef = useRef(false);
  const initRef = useRef(false);

  const [mainTab, setMainTab] = useState("chat");
  const [sideTab, setSideTab] = useState("chats");
  const [model, setModel] = useState("gemma3:4b");
  const [modelOpts, setModelOpts] = useState([]);
  const [profile, setProfile] = useState("Универсальный");
  const [skills, setSkills] = useState(["web_search", "file_context", "memory", "pdf_reader", "python_exec", "code_analysis", "file_gen", "translator", "converter", "archiver", "http_api", "screenshot", "image_gen"]);
  const [chats, setChats] = useState([]);
  const [chatId, setChatId] = useState("");
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [sideSearch, setSideSearch] = useState("");
  const [libSearch, setLibSearch] = useState("");
  const [error, setError] = useState("");
  const [drag, setDrag] = useState(false);
  const [working, setWorking] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [streamText, setStreamText] = useState("");
  const [phase, setPhase] = useState(""); // "searching" | "thinking" | "reflecting" | ""
  const [libraryFiles, setLibraryFiles] = useState(loadLibraryFiles());
  const [selLibId, setSelLibId] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [renameVal, setRenameVal] = useState("");
  const [showPanel, setShowPanel] = useState(false);
  const [showExportMenu, setShowExportMenu] = useState(false);
  const [mobileSidebar, setMobileSidebar] = useState(false);
  const [pluginList, setPluginList] = useState([]);
  const [dashData, setDashData] = useState(null);
  const [projectBrainStatus, setProjectBrainStatus] = useState(null);
  const [personaStatus, setPersonaStatus] = useState(null);
  const [personaBusy, setPersonaBusy] = useState(false);
  const [dashboardError, setDashboardError] = useState("");
  const [pipelinesList, setPipelinesList] = useState([]);
  const [pipelinesError, setPipelinesError] = useState("");
  const [pipeForm, setPipeForm] = useState({name:"",task_type:"prompt",interval_minutes:60,task_data:{prompt:""}});
  const [tasksList, setTasksList] = useState([]);
  const [tasksError, setTasksError] = useState("");
  const [taskFilter, setTaskFilter] = useState("active");
  const [taskForm, setTaskForm] = useState({title:"",description:"",category:"general",priority:"medium",due_date:""});
  const [taskStats, setTaskStats] = useState(null);
  const [editingTask, setEditingTask] = useState(null);
  const [tgConfig, setTgConfig] = useState(null);
  const [tgUsers, setTgUsers] = useState([]);
  const [tgLog, setTgLog] = useState([]);
  const [telegramError, setTelegramError] = useState("");
  const [tgTokenInput, setTgTokenInput] = useState("");
  const [tgTab, setTgTab] = useState("setup");
  const [multiAgent, setMultiAgent] = useState(false);
  const [lastInput, setLastInput] = useState("");
  const [lastModel, setLastModel] = useState("");
  const [chartData, setChartData] = useState(null);
  const [ollamaContext, setOllamaContext] = useState(8192);
  const [settingsModel, setSettingsModel] = useState("gemma3:4b");
  const [settingsProfile, setSettingsProfile] = useState("Универсальный");
  const [settingsContext, setSettingsContext] = useState(8192);
  const [settingsSaved, setSettingsSaved] = useState(false);
  const [routeMap, setRouteMap] = useState({ code: [], project: [], research: [], chat: [] });
  const [theme, setTheme] = useState(() => localStorage.getItem("elira_theme") || "dark");


  useEffect(() => { init(); return () => { if (streamRef.current) { streamRef.current.abort(); streamRef.current = null; } }; }, []);
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

  // Закрытие export dropdown при клике снаружи
  useEffect(() => {
    if (!showExportMenu) return;
    const h = (e) => { if (!e.target.closest(".export-dropdown-wrap")) setShowExportMenu(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [showExportMenu]);

  // Тема: применяем к document
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("elira_theme", theme);
  }, [theme]);

  // Глобальные горячие клавиши
  const workingRef = useRef(false);
  workingRef.current = working;
  useEffect(() => {
    function onGlobalKey(e) {
      // Ctrl+N — новый чат
      if ((e.ctrlKey || e.metaKey) && e.key === "n") { e.preventDefault(); newChat(false); }
      // Escape — остановить стриминг
      if (e.key === "Escape" && workingRef.current) {
        e.preventDefault();
        stoppedRef.current = true;
        if (streamRef.current) { streamRef.current.abort(); streamRef.current = null; }
        setStreamText(""); setStreaming(false); setWorking(false); setPhase("");
      }
      // Ctrl+Shift+T — переключить тему
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === "T") { e.preventDefault(); setTheme(t => t === "dark" ? "light" : "dark"); }
    }
    window.addEventListener("keydown", onGlobalKey);
    return () => window.removeEventListener("keydown", onGlobalKey);
  }, []);

  // Sync library from SQLite backend on mount (optional)
  useEffect(() => {
    api.listLibraryFiles().then(d => {
      if (d?.ok && d.items?.length) {
        const ctxMap = loadChatContextMap();
        const activeIds = new Set(Object.values(ctxMap).flat());
        setLibraryFiles(prev => {
          const merged = [...d.items.map(i => ({...i, id: `db-${i.id}`, source: "sqlite", use_in_context: activeIds.has(`db-${i.id}`)})), ...prev.filter(f => f.source !== "sqlite")];
          const seen = new Set();
          const unique = merged.filter(f => { const k = f.name + f.size; if (seen.has(k)) return false; seen.add(k); return true; });
          saveLibraryFiles(unique);
          return unique;
        });
      }
    }).catch(() => {});
  }, []);

  // Auto-open right panel when code blocks appear
  useEffect(() => {
    const lastMsg = messages[messages.length - 1];
    if (lastMsg?.role === "assistant" && /```\w*\n[\s\S]{20,}?```/.test(lastMsg.content || "")) {
      setShowPanel(true);
    }
  }, [messages]);

  async function init() {
    if (initRef.current) return;
    initRef.current = true;
    try {
      const [m, c, settings] = await Promise.all([api.listOllamaModels(), api.listChats(), api.getSettings()]);
      const ml = Array.isArray(m?.models) ? m.models : Array.isArray(m) ? m : [];
      setModelOpts(ml);

      // Загружаем сохранённые настройки из backend
      const savedModel = settings?.default_model || "gemma3:4b";
      const savedProfile = settings?.agent_profile || "Универсальный";
      const savedCtx = settings?.ollama_context || 8192;

      // Устанавливаем модель из настроек (если доступна в Ollama)
      const getName = i => typeof i === "string" ? i : (i.name || i.model || "");
      const pref = ml.find(i => getName(i) === savedModel);
      const chosenModel = pref ? getName(pref) : ml.length ? getName(ml[0]) : "gemma3:4b";
      setModel(chosenModel);
      setProfile(savedProfile);
      setOllamaContext(savedCtx);

      // Синхронизируем панель настроек
      setSettingsModel(savedModel);
      setSettingsProfile(savedProfile);
      setSettingsContext(savedCtx);
      if (settings?.route_model_map) setRouteMap(settings.route_model_map);

      if (c?.length) { setChats(c); }
      // Всегда новый чат при запуске
      const n = await newChat(true); if (n?.id) setMessages([]);
    } catch (e) { setError(normalizeErrorMessage(e)); }
  }

  async function refreshModels() {
    try {
      const m = await api.listOllamaModels();
      const ml = Array.isArray(m?.models) ? m.models : Array.isArray(m) ? m : [];
      setModelOpts(ml);
      return ml;
    } catch { return []; }
  }

  async function loadPipelines() {
    setPipelinesError("");
    try {
      setPipelinesList(await api.listPipelines());
    } catch (e) {
      const message = normalizeErrorMessage(e);
      setPipelinesList([]);
      setPipelinesError(message);
      setError(`Pipelines: ${message}`);
    }
  }

  async function loadTelegram() {
    setTelegramError("");
    try {
      const data = await api.getTelegramOverview(30);
      setTgConfig(data.config);
      setTgUsers(data.users);
      setTgLog(data.log);
    } catch (e) {
      const message = normalizeErrorMessage(e);
      setTgConfig(null); setTgUsers([]); setTgLog([]);
      setTelegramError(message);
      setError(`Telegram: ${message}`);
    }
  }

  async function loadTasks(filter) {
    const f = filter || taskFilter;
    setTasksError("");
    try {
      const data = await api.getTasksOverview(f);
      setTasksList(data.tasks);
      setTaskStats(data.stats);
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
      const data = await api.getDashboardOverview();
      setDashData(data.stats || null);
      setProjectBrainStatus(data.projectBrainStatus || null);
      setPersonaStatus(data.personaStatus || null);
      const message = Array.isArray(data.errors) ? data.errors.filter(Boolean).join(" | ") : "";
      setDashboardError(message);
      setError(message ? `Dashboard: ${message}` : "");
    } catch (e) {
      const message = normalizeErrorMessage(e);
      setDashData(null);
      setProjectBrainStatus(null);
      setPersonaStatus(null);
      setDashboardError(message);
      setError(`Dashboard: ${message}`);
    }
  }

  async function handlePersonaRollback(version) {
    if (!version) return;
    setPersonaBusy(true);
    try {
      await api.rollbackPersona(version);
      await loadDashboard();
    } catch (e) {
      setDashboardError(normalizeErrorMessage(e));
      setError(`Dashboard: ${normalizeErrorMessage(e)}`);
    } finally {
      setPersonaBusy(false);
    }
  }

  async function loadPluginList() {
    try {
      setPluginList(await api.listPlugins());
    } catch (e) {
      setPluginList([]);
      setError(`Plugins: ${normalizeErrorMessage(e)}`);
    }
  }

  async function loadChats(sel = "") { setChats(await api.listChats() || []); if (sel) setChatId(sel); }
  async function newChat(silent = false) {
    try { setMessages([]); setInput(""); setRenaming(false); setStreamText(""); setStreaming(false); setPhase("");
      const c = await api.createChat({ title: "Новый чат", clean: true }); await loadChats(c.id); setChatId(c.id); setSideTab("chats"); if (!silent) setError(""); return c;
    } catch (e) { setError(normalizeErrorMessage(e)); return null; }
  }
  async function openChat(id) {
    try { streamRef.current?.abort(); setStreamText(""); setStreaming(false); setPhase(""); setChatId(id); setMessages(await api.getMessages({ chatId: id }) || []); setSideTab("chats"); setMainTab("chat"); setRenaming(false); setMobileSidebar(false);
    } catch (e) { setError(normalizeErrorMessage(e)); }
  }
  async function renameActive() { const t = renameVal.trim(); if (!t || !chatId) return; try { await api.renameChat({ id: chatId, title: t }); await loadChats(chatId); setRenaming(false); } catch (e) { setError(normalizeErrorMessage(e)); } }
  async function autoRename(text) { const a = chats.find(c => c.id === chatId); if (!chatId || !a || (a.title && a.title !== "Новый чат")) return; try { await api.renameChat({ id: chatId, title: deriveChatTitle(text) }); await loadChats(chatId); } catch {} }


  function exportChat(fmt) {
    if (!messages.length) return;
    const title = chats.find(c => c.id === chatId)?.title || "Чат Elira AI";
    const safe = title.slice(0,40).replace(/[^\w\u0400-\u04FF]/g,"_");
    const ts = new Date().toLocaleString("ru-RU");
    let blob, ext;
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
      const html = `<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"><title>${title}</title><style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:800px;margin:0 auto;padding:24px;background:#fff;color:#333}h1{font-size:22px;border-bottom:2px solid #1976d2;padding-bottom:8px}.meta{color:#888;font-size:13px;margin-bottom:24px}</style></head><body><h1>${title}</h1><div class="meta">${ts} | ${messages.length} сообщений</div>${msgs}</body></html>`;
      blob = new Blob([html], {type:"text/html;charset=utf-8"});
      ext = ".html";
    } else {
      const body = messages
        .map((m) => `${m.role === "user" ? "Вы" : "Elira"}:\n${m.content}`)
        .join("\n\n" + "═".repeat(40) + "\n\n");
      blob = new Blob([`${title}\nЭкспорт: ${ts} | Сообщений: ${messages.length}\n${"═".repeat(40)}\n\n${body}`], {type:"text/plain;charset=utf-8"});
      ext = ".txt";
    }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = safe + ext; a.click();
    URL.revokeObjectURL(a.href);
  }

  function handleResend(withModel) {
    if (!lastInput || working) return;
    if (withModel) setModel(withModel);
    setInput(lastInput);
    setTimeout(() => { taRef.current?.focus(); }, 80);
  }

  function detectTableInText(text) {
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
    if (!text || !chatId || working) return;
    try {
      setWorking(true); setStreaming(true); setStreamText(""); setError(""); setPhase(""); stoppedRef.current = false;
      setLastInput(text); setLastModel(model);
      const userMsg = await api.addMessage({ chatId, role: "user", content: text });
      const nextMessages = [...messages, userMsg];
      setMessages(nextMessages); setInput(""); await autoRename(text);
      const history = buildHistory(nextMessages);

      // Файлы библиотеки
      const cf = getChatContextFiles(libraryFiles, chatId);
      const tl = text.toLowerCase();
      const wantsFiles = cf.length > 0 && (
        tl.includes("файл") || tl.includes("документ") || tl.includes("библиотек") ||
        tl.includes("загруженн") || tl.includes("прочитай") || tl.includes("опиши") ||
        tl.includes("file") || tl.includes("document") || tl.includes("pdf") ||
        tl.includes("резюме") || tl.includes("отчёт") || tl.includes("отчет") ||
        tl.includes("что в ") || tl.includes("покажи содержимое") || tl.includes("проанализируй")
      );
      let cp = wantsFiles ? "\n\nФайлы пользователя:\n" + cf.map(f => `=== ${f.name} ===\n${f.preview.slice(0, 1500)}`).join("\n\n") : "";

      const wantsProjectContext = (
        tl.includes("проект") || tl.includes("project") ||
        tl.includes("repo") || tl.includes("repository") || tl.includes("репозитор") ||
        tl.includes("РєРѕРґ") || tl.includes("codebase") ||
        tl.includes("backend") || tl.includes("frontend") ||
        tl.includes("структур") || tl.includes("tree") ||
        tl.includes("директор") || tl.includes("каталог") || tl.includes("папк") ||
        tl.includes("readme") || tl.includes("модул") || tl.includes("компонент")
      );

      // Контекст проекта — только для запросов про код/репозиторий
      if (wantsProjectContext) {
        try {
          const projInfo = await api.getAdvancedProjectInfo();
          if (projInfo.ok) {
            const projTree = await api.getAdvancedProjectTree({ maxDepth: 2, maxItems: 50 });
            if (projTree.ok && projTree.items?.length) {
              const fileList = projTree.items.filter(i => i.type === "file").map(i => i.path).join(", ");
              cp += `\n\nОткрыт проект: ${projInfo.name} (${projTree.count} файлов)\nФайлы: ${fileList.slice(0, 800)}`;
            }
          }
        } catch {}
      }

      // Multi-agent режим
      if (multiAgent) {
        const useOrch = profile === "Оркестратор";
        const useRefl = skills.includes("reflection");
        const modeLabel = [useOrch && "Оркестратор", "Агенты", useRefl && "Рефлексия"].filter(Boolean).join(" → ");
        setPhase(`✨ ${modeLabel}...`);
        try {
          const data = await api.runAdvancedMultiAgent({ query: `${text}${cp}`, model_name: model, context: "", agents: ["researcher","programmer","analyst"], use_reflection: useRefl, use_orchestrator: useOrch });
          if (data?.ok === false) throw new Error(normalizeErrorMessage(data?.error || data?.detail || "HTTP error"));
          const final = (data?.report || "").trim() || "Multi-agent не вернул результат";
          try { await api.addMessage({ chatId, role: "assistant", content: final }); } catch {}
          setMessages(prev => [...prev, { id: `a-${Date.now()}`, role: "assistant", content: final }]);
          setError("");
          setStreamText(""); setStreaming(false); setWorking(false); setPhase("");
          return;
        } catch (e) {
          const msg = e?.message === "Failed to fetch"
            ? "Multi-agent: backend недоступен или процесс упал во время выполнения. Проверь, жив ли FastAPI/Ollama."
            : normalizeErrorMessage(e);
          setError(msg); setStreamText(""); setStreaming(false); setWorking(false); setPhase(""); return;
        }
      }

      // Обычный стриминг
      let fullText = "";
      const ctrl = executeStream(
        { model_name: model, profile_name: profile, user_input: `${text}${cp}`, session_id: chatId || null, history, num_ctx: ollamaContext, use_memory: skills.includes("memory"), use_library: skills.includes("file_context"), use_reflection: skills.includes("reflection"), use_web_search: skills.includes("web_search"), use_python_exec: skills.includes("python_exec"), use_image_gen: skills.includes("image_gen"), use_file_gen: skills.includes("file_gen"), use_http_api: skills.includes("http_api"), use_sql: skills.includes("sql_query"), use_screenshot: skills.includes("screenshot"), use_encrypt: skills.includes("encrypt"), use_archiver: skills.includes("archiver"), use_converter: skills.includes("converter"), use_regex: skills.includes("regex"), use_translator: skills.includes("translator"), use_csv: skills.includes("csv_analysis"), use_webhook: skills.includes("webhook"), use_plugins: skills.includes("plugins") },
        {
          onToken(t) { fullText += t; setStreamText(fullText); setPhase(""); },
          onPhase(ev) {
            if (ev.phase === "reflection_replace" && ev.full_text) { fullText = ev.full_text; setStreamText(fullText); }
            else if (ev.message) { setPhase(ev.message); }
          },
          onDone({ full_text }) {
            if (stoppedRef.current) return;
            const final = full_text || fullText;
            // Оптимистичное обновление — показываем сразу, сохраняем в фоне
            const tempId = `a-${Date.now()}`;
            setMessages(prev => [...prev, { id: tempId, role: "assistant", content: final }]);
            const _cd = detectTableInText(final); _cd ? setChartData(_cd) : setChartData(null);
            setStreamText(""); setStreaming(false); setWorking(false); setPhase(""); streamRef.current = null;
            // Фоновое сохранение в БД (не блокирует UI)
            api.addMessage({ chatId, role: "assistant", content: final }).catch(() => {});
          },
          onError(msg) { setError(msg); setStreamText(""); setStreaming(false); setWorking(false); setPhase(""); streamRef.current = null; },
        }
      );
      streamRef.current = ctrl;
    } catch (e) { setError(normalizeErrorMessage(e)); setStreamText(""); setStreaming(false); setWorking(false); setPhase(""); }
  }

  async function deleteChat(id) { try { await api.deleteChat({ id }); const next = chats.filter(c => c.id !== id); setChats(next); if (chatId === id) { if (next.length) await openChat(next[0].id); else { const c = await newChat(true); if (c?.id) await openChat(c.id); } } } catch (e) { setError(normalizeErrorMessage(e)); } }
  async function pinChat(id, p) { try { await api.pinChat({ id, pinned: !p }); await loadChats(chatId); } catch (e) { setError(normalizeErrorMessage(e)); } }
  async function saveToMemory(id, s) { try { await api.saveChatToMemory({ id, saved: !s }); await loadChats(chatId); } catch (e) { setError(normalizeErrorMessage(e)); } }

  async function handleFiles(fl) {
    const files = Array.from(fl || []); if (!files.length) return;
    const recs = []; for (const f of files) {
      recs.push(await fileToLibraryRecord(f));
      // Сохраняем в SQLite бекенд
      try { await api.uploadLibraryFile(f, { useInContext: false }); } catch {}
    }
    const next = [...recs, ...libraryFiles]; setLibraryFiles(next); saveLibraryFiles(next); setSideTab("library"); setSelLibId(recs[0]?.id || "");
    if (chatId) { const map = loadChatContextMap(); map[chatId] = Array.from(new Set([...recs.map(r => r.id), ...(map[chatId] || [])])); saveChatContextMap(map); }
  }
  function onDrop(e) { e.preventDefault(); e.stopPropagation(); setDrag(false); handleFiles(e.dataTransfer.files); }
  function onDragOver(e) { e.preventDefault(); e.stopPropagation(); setDrag(true); }
  function onDragLeave(e) { e.preventDefault(); e.stopPropagation(); setDrag(false); }
  async function removeLib(id) {
    try {
      if (String(id).startsWith("db-")) {
        const dbId = String(id).slice(3);
        await api.deleteLibraryFile(dbId);
      }
    } catch {}
    const n = libraryFiles.filter(i => i.id !== id);
    setLibraryFiles(n);
    saveLibraryFiles(n);
    const m = loadChatContextMap();
    saveChatContextMap(Object.fromEntries(Object.entries(m).map(([k,v]) => [k,(v||[]).filter(x=>x!==id)])));
    if (selLibId === id) setSelLibId(n[0]?.id || "");
  }
  function toggleCtx(id, on) {
    const n = libraryFiles.map(i => i.id === id ? {...i, use_in_context: on} : i);
    setLibraryFiles(n);
    saveLibraryFiles(n);
    if (!chatId) return;
    const m = loadChatContextMap();
    const s = new Set(m[chatId]||[]);
    on ? s.add(id) : s.delete(id);
    m[chatId] = Array.from(s);
    saveChatContextMap(m);
  }
  function toggleSkill(id) { setSkills(p => p.includes(id) ? p.filter(s => s !== id) : [...p, id]); }
  function handleStop() {
    stoppedRef.current = true;
    if (streamRef.current) { streamRef.current.abort(); streamRef.current = null; }
    if (streamText) {
      setMessages(prev => [...prev, { id: `a-${Date.now()}`, role: "assistant", content: streamText + "\n\n*[остановлено]*" }]);
      api.addMessage({ chatId, role: "assistant", content: streamText + "\n\n*[остановлено]*" }).catch(() => {});
    }
    setStreamText(""); setStreaming(false); setWorking(false); setPhase("");
  }

  function selectAllLib(on) {
    const next = libraryFiles.map(i => ({ ...i, use_in_context: on }));
    setLibraryFiles(next);
    saveLibraryFiles(next);
    if (!chatId) return;
    const m = loadChatContextMap();
    m[chatId] = on ? libraryFiles.map(i => i.id) : [];
    saveChatContextMap(m);
  }

  async function submitTaskForm() {
    if (!taskForm.title) return;
    try {
      if (editingTask) {
        await api.updateTask(editingTask, taskForm);
        setEditingTask(null);
      } else {
        await api.createTask(taskForm);
      }
      setTaskForm({ title:"", description:"", category:"general", priority:"medium", due_date:"" });
      setTasksError("");
      setError("");
      await loadTasks();
    } catch (e) {
      const message = normalizeErrorMessage(e);
      setTasksError(message);
      setError(`Tasks: ${message}`);
    }
  }

  async function updateTaskStatus(taskId, status) {
    try {
      await api.updateTask(taskId, { status });
      setTasksError("");
      setError("");
      await loadTasks();
    } catch (e) {
      const message = normalizeErrorMessage(e);
      setTasksError(message);
      setError(`Tasks: ${message}`);
    }
  }

  async function deleteTaskItem(taskId) {
    if (!confirm("Удалить задачу?")) return;
    try {
      await api.deleteTask(taskId);
      setTasksError("");
      setError("");
      await loadTasks();
    } catch (e) {
      const message = normalizeErrorMessage(e);
      setTasksError(message);
      setError(`Tasks: ${message}`);
    }
  }

  async function startTelegramBot() {
    try {
      const data = await api.startTelegramBot();
      setTelegramError("");
      if (data?.ok === false) throw new Error(data.error || "Ошибка запуска");
      setError("");
      await loadTelegram();
    } catch (e) {
      const message = normalizeErrorMessage(e);
      setTelegramError(message);
      setError(`Telegram: ${message}`);
    }
  }

  async function stopTelegramBot() {
    try {
      await api.stopTelegramBot();
      setTelegramError("");
      setError("");
      await loadTelegram();
    } catch (e) {
      const message = normalizeErrorMessage(e);
      setTelegramError(message);
      setError(`Telegram: ${message}`);
    }
  }

  async function testTelegramBot() {
    try {
      const data = await api.testTelegramBot();
      setTelegramError("");
      setError("");
      if (data?.ok) alert(`Бот: @${data.bot_username} (${data.bot_name})`);
      else alert(`❌ ${data?.error || "Ошибка"}`);
    } catch (e) {
      const message = normalizeErrorMessage(e);
      setTelegramError(message);
      setError(`Telegram: ${message}`);
      alert("Ошибка соединения");
    }
  }

  async function saveTelegramToken() {
    if (!tgTokenInput.trim()) return;
    try {
      await api.updateTelegramConfig({ bot_token: tgTokenInput.trim() });
      setTelegramError("");
      setTgTokenInput("");
      setError("");
      await loadTelegram();
    } catch (e) {
      const message = normalizeErrorMessage(e);
      setTelegramError(message);
      setError(`Telegram: ${message}`);
    }
  }

  async function saveTelegramSettings() {
    try {
      const upd = {};
      if (tgConfig?.model !== undefined) upd.model = tgConfig.model;
      if (tgConfig?.profile) upd.profile = tgConfig.profile;
      if (tgConfig?.use_memory !== undefined) upd.use_memory = tgConfig.use_memory;
      if (tgConfig?.use_web_search !== undefined) upd.use_web_search = tgConfig.use_web_search;
      if (tgConfig?.welcome_message) upd.welcome_message = tgConfig.welcome_message;
      await api.updateTelegramConfig(upd);
      setTelegramError("");
      setError("");
      await loadTelegram();
    } catch (e) {
      const message = normalizeErrorMessage(e);
      setTelegramError(message);
      setError(`Telegram: ${message}`);
    }
  }

  async function updateTelegramAllowedUsers(val) {
    try {
      await api.updateTelegramConfig({ allowed_users: val });
      setTelegramError("");
      setError("");
      await loadTelegram();
    } catch (e) {
      const message = normalizeErrorMessage(e);
      setTelegramError(message);
      setError(`Telegram: ${message}`);
    }
  }

  async function toggleTelegramUserAccess(user) {
    try {
      await api.toggleTelegramUser({ chat_id: user.chat_id, allowed: !user.allowed });
      setTelegramError("");
      setError("");
      await loadTelegram();
    } catch (e) {
      const message = normalizeErrorMessage(e);
      setTelegramError(message);
      setError(`Telegram: ${message}`);
    }
  }

  async function createPipeline() {
    if (!pipeForm.name) return;
    try {
      await api.createPipeline(pipeForm);
      setPipelinesError("");
      setPipeForm({ name:"", task_type:"prompt", interval_minutes:60, task_data:{prompt:""} });
      setError("");
      await loadPipelines();
    } catch (e) {
      const message = normalizeErrorMessage(e);
      setPipelinesError(message);
      setError(`Pipelines: ${message}`);
    }
  }

  async function runPipelineNow(pipelineId) {
    try {
      await api.runPipeline(pipelineId);
      setPipelinesError("");
      setError("");
      await loadPipelines();
    } catch (e) {
      const message = normalizeErrorMessage(e);
      setPipelinesError(message);
      setError(`Pipelines: ${message}`);
    }
  }

  async function togglePipelineEnabled(pipeline) {
    try {
      await api.updatePipeline(pipeline.id, { enabled: !pipeline.enabled });
      setPipelinesError("");
      setError("");
      await loadPipelines();
    } catch (e) {
      const message = normalizeErrorMessage(e);
      setPipelinesError(message);
      setError(`Pipelines: ${message}`);
    }
  }

  async function deletePipeline(pipelineId) {
    if (!confirm("Удалить?")) return;
    try {
      await api.deletePipeline(pipelineId);
      setPipelinesError("");
      setError("");
      await loadPipelines();
    } catch (e) {
      const message = normalizeErrorMessage(e);
      setPipelinesError(message);
      setError(`Pipelines: ${message}`);
    }
  }

  async function reloadPlugins() {
    try {
      const data = await api.reloadPlugins();
      setPluginList(data.loaded?.map(n => ({ name:n, enabled:true })) || []);
      setError("");
      await loadPluginList();
    } catch (e) {
      setError(`Plugins: ${normalizeErrorMessage(e)}`);
    }
  }

  async function togglePluginState(plugin) {
    try {
      await api.setPluginEnabled(plugin.name, !plugin.enabled);
      setError("");
      await loadPluginList();
    } catch (e) {
      setError(`Plugins: ${normalizeErrorMessage(e)}`);
    }
  }

  function handleKeyDown(e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); } }

  const fChats = useMemo(() => { const q = sideSearch.trim().toLowerCase(); return q ? chats.filter(c => (c.title||"").toLowerCase().includes(q)) : chats; }, [sideSearch, chats]);
  const pinned = useMemo(() => fChats.filter(c => c.pinned), [fChats]);
  const regular = useMemo(() => fChats.filter(c => !c.pinned), [fChats]);
  const memChats = useMemo(() => chats.filter(c => c.memory_saved), [chats]);
  const fLib = useMemo(() => { const q = libSearch.trim().toLowerCase(); return q ? libraryFiles.filter(i => `${i.name} ${i.preview||""}`.toLowerCase().includes(q)) : libraryFiles; }, [libSearch, libraryFiles]);
  const selLib = useMemo(() => libraryFiles.find(i => i.id === selLibId) || libraryFiles[0] || null, [libraryFiles, selLibId]);
  const ctxF = useMemo(() => getChatContextFiles(libraryFiles, chatId), [libraryFiles, chatId]);

  if (mainTab === "code") return <IdeWorkspaceShell messages={messages} libraryFiles={libraryFiles} setLibraryFiles={setLibraryFiles} onBackToChat={() => setMainTab("chat")} onSendToChat={(txt) => { setMainTab("chat"); setTimeout(() => setInput(txt), 100); }} />;

  return (
    <div className="elira-shell" style={showPanel && sideTab === "chats" ? {gridTemplateColumns: "200px 1fr auto"} : undefined}>
      {mobileSidebar && <div className="mobile-overlay" onClick={()=>setMobileSidebar(false)}/>}
      <aside className={`elira-sidebar ${mobileSidebar?"mobile-open":""}`}>
        <button className="sidebar-newchat-btn" onClick={() => newChat(false)}>+ Новый чат</button>
        <div className="sidebar-nav">
          {[
            ["chats", "Чаты", MessageSquare],
            ["project", "Проекты", FolderOpen],
            ["library", "Файлы", Files],
            ["memory", "Память", BrainCircuit],
            ["tasks", "Задачи", ListTodo],
            ["dashboard", "Панель", LayoutDashboard],
            ["pipelines", "Пайплайны", Workflow],
            ["telegram", "Telegram", Send],
            ["settings", "Настройки", Settings],
          ].map(([k, l, Icon]) => (
            <button key={k} className={`sidebar-nav-item ${sideTab === k ? "active" : ""}`} onClick={() => { setSideTab(k); setMobileSidebar(false); if(k==="settings"){setSettingsModel(model);setSettingsProfile(profile);setSettingsContext(ollamaContext);setSettingsSaved(false);refreshModels();loadPluginList();}if(k==="dashboard"){loadDashboard();}if(k==="pipelines"){loadPipelines();}if(k==="tasks"){loadTasks();}if(k==="telegram"){loadTelegram();} }}>
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
            {pinned.map(c => <button key={c.id} className={`chat-list-item simple ${chatId===c.id?"active":""}`} onClick={() => openChat(c.id)}><span className="chat-list-title truncate">{c.title||"Новый чат"}</span></button>)}
            {regular.length > 0 && <div className="sidebar-section-title">Чаты</div>}
            {regular.map(c => <button key={c.id} className={`chat-list-item simple ${chatId===c.id?"active":""}`} onClick={() => openChat(c.id)}><span className="chat-list-title truncate">{c.title||"Новый чат"}</span></button>)}
            {!fChats.length && <div className="sidebar-empty">Пусто</div>}
          </div>
        )}
        {sideTab === "memory" && <div className="chat-list" style={{flex:1}}>{memChats.length ? memChats.map(c => <button key={c.id} className={`chat-list-item simple ${chatId===c.id?"active":""}`} onClick={() => openChat(c.id)}><span className="chat-list-title truncate">{c.title||"Чат"}</span></button>) : <div className="sidebar-empty">Нет</div>}</div>}
        {sideTab === "settings" && <div className="sidebar-empty">→ Центральное окно</div>}
        {sideTab === "library" && <div className="sidebar-empty">→ Центральное окно</div>}
        {sideTab === "project" && <div className="sidebar-empty">→ Центральное окно</div>}
        <div style={{padding:"8px 12px",borderTop:"1px solid var(--border)",display:"flex",alignItems:"center",justifyContent:"space-between"}}>
          <button onClick={()=>setTheme(t=>t==="dark"?"light":"dark")} style={{background:"none",border:"1px solid var(--border)",borderRadius:6,padding:"3px 8px",cursor:"pointer",color:"var(--text-muted)",fontSize:11,display:"inline-flex",alignItems:"center",gap:6}} title="Ctrl+Shift+T">
            <UiIcon icon={theme==="dark" ? Sun : Moon} size={13} />
            <span>{theme==="dark"?"Светлая":"Тёмная"}</span>
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
                <button className="soft-btn icon-btn" title="Сохранить в память" onClick={() => saveToMemory(chatId, chats.find(c=>c.id===chatId)?.memory_saved)}><UiIcon icon={BrainCircuit} size={14} /></button>
                <button className="soft-btn icon-btn" title="Закрепить чат" onClick={() => pinChat(chatId, chats.find(c=>c.id===chatId)?.pinned)}><UiIcon icon={Pin} size={14} /></button>
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
                <button className="soft-btn" style={{fontSize:10,padding:"3px 10px",border:"1px solid var(--border)"}} onClick={loadTasks} title="Обновить"><UiIcon icon={RefreshCw} size={13} /></button>
              </div>
              <PanelNotice title="Раздел задач временно недоступен" message={tasksError} onRetry={() => loadTasks()} />

              {/* Статистика */}
              {taskStats && (
                <div style={{display:"flex",gap:8,marginBottom:12,flexWrap:"wrap"}}>
                  {[
                    {l:"Всего",v:taskStats.total,c:"var(--text)"},
                    {l:"К выполнению",v:taskStats.by_status?.todo||0,c:"#5b9bd5"},
                    {l:"В работе",v:taskStats.by_status?.in_progress||0,c:"#f5a623"},
                    {l:"Готово",v:taskStats.by_status?.done||0,c:"#4caf50"},
                    {l:"Просрочено",v:taskStats.overdue||0,c:"#f44336"},
                  ].map(s=>(
                    <div key={s.l} style={{padding:"6px 10px",borderRadius:8,border:"1px solid var(--border)",background:"var(--bg-surface)",textAlign:"center",minWidth:50}}>
                      <div style={{fontSize:16,fontWeight:700,color:s.c}}>{s.v}</div>
                      <div style={{fontSize:9,color:"var(--text-muted)"}}>{s.l}</div>
                    </div>
                  ))}
                </div>
              )}

              {/* Фильтр */}
              <div style={{display:"flex",gap:4,marginBottom:12}}>
                {[["active","Активные"],["todo","К выполнению"],["in_progress","В работе"],["done","Готовые"],["all","Все"]].map(([k,l])=>(
                  <button key={k} className="soft-btn" style={{fontSize:10,padding:"3px 10px",background:taskFilter===k?"var(--accent)":"transparent",color:taskFilter===k?"#fff":"var(--text)",border:"1px solid var(--border)",borderRadius:6}} onClick={()=>{setTaskFilter(k);loadTasks(k);}}>{l}</button>
                ))}
              </div>

              {/* Форма создания / редактирования */}
              <div style={{padding:12,borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:14}}>
                <div style={{fontSize:12,fontWeight:600,color:"var(--text)",marginBottom:8}}>{editingTask ? "Редактирование задачи" : "Новая задача"}</div>
                <input placeholder="Название задачи" value={taskForm.title} onChange={e=>setTaskForm({...taskForm,title:e.target.value})} className="rename-input" style={{width:"100%",fontSize:11,padding:"5px 8px",marginBottom:6}}/>
                <textarea placeholder="Описание (необязательно)" value={taskForm.description} onChange={e=>setTaskForm({...taskForm,description:e.target.value})} className="rename-input" style={{width:"100%",fontSize:11,padding:"5px 8px",marginBottom:6,minHeight:40,resize:"vertical",fontFamily:"inherit"}} rows={2}/>
                <div style={{display:"flex",gap:6,flexWrap:"wrap",marginBottom:6}}>
                  <select value={taskForm.priority} onChange={e=>setTaskForm({...taskForm,priority:e.target.value})} className="topbar-select dark-select" style={{fontSize:11}}>
                    <option value="low">Низкий</option>
                    <option value="medium">Средний</option>
                    <option value="high">Высокий</option>
                    <option value="urgent">Срочный</option>
                  </select>
                  <select value={taskForm.category} onChange={e=>setTaskForm({...taskForm,category:e.target.value})} className="topbar-select dark-select" style={{fontSize:11}}>
                    <option value="general">Общее</option>
                    <option value="work">Работа</option>
                    <option value="personal">Личное</option>
                    <option value="study">Учёба</option>
                    <option value="project">Проект</option>
                    <option value="idea">Идея</option>
                  </select>
                  <input type="date" value={taskForm.due_date||""} onChange={e=>setTaskForm({...taskForm,due_date:e.target.value})} className="rename-input" style={{fontSize:11,padding:"4px 8px"}}/>
                </div>
                <div style={{display:"flex",gap:6}}>
                  <button className="soft-btn" style={{fontSize:11,padding:"4px 14px",background:"var(--accent)",color:"#fff",border:"none",borderRadius:6}} onClick={async()=>{
                    if(!taskForm.title) return;
                    try {
                      if(editingTask) {
                        await api.updateTask(editingTask, taskForm);
                        setEditingTask(null);
                      } else {
                        await api.createTask(taskForm);
                      }
                      setTaskForm({title:"",description:"",category:"general",priority:"medium",due_date:""});
                      await loadTasks();
                      setError("");
                    } catch(e){setError(`Tasks: ${normalizeErrorMessage(e)}`)}
                  }}>{editingTask ? "Сохранить" : "Создать"}</button>
                  {editingTask && <button className="soft-btn" style={{fontSize:11,padding:"4px 10px",border:"1px solid var(--border)",borderRadius:6}} onClick={()=>{setEditingTask(null);setTaskForm({title:"",description:"",category:"general",priority:"medium",due_date:""});}}>Отмена</button>}
                </div>
              </div>

              {/* Список задач */}
              {tasksList.length===0 && !tasksError && <div style={{fontSize:11,color:"var(--text-muted)",padding:"12px 0",textAlign:"center"}}>Нет задач</div>}
              {tasksList.map(t=>{
                const prioColor = {urgent:"#f44336",high:"#ff9800",medium:"#f5a623",low:"#4caf50"}[t.priority]||"var(--text-muted)";
                const isOverdue = t.due_date && t.status!=="done" && t.status!=="cancelled" && new Date(t.due_date) < new Date();
                return (
                  <div key={t.id} style={{padding:"10px 12px",borderRadius:10,border:`1px solid ${isOverdue?"#f44336":"var(--border)"}`,background:"var(--bg-surface)",marginBottom:6,opacity:t.status==="done"||t.status==="cancelled"?0.6:1}}>
                    <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:4}}>
                      <div style={{display:"flex",alignItems:"center",gap:6,flex:1,minWidth:0}}>
                        <span style={{cursor:"pointer",fontSize:16}} title={t.status==="done"?"Вернуть":"Выполнено"} onClick={async()=>{
                          const newStatus = t.status==="done" ? "todo" : "done";
                          await updateTaskStatus(t.id, newStatus);
                          }}>{t.status==="done"?"↺":"✓"}</span>
                        <div style={{flex:1,minWidth:0}}>
                          <div style={{fontWeight:600,fontSize:12,color:"var(--text)",textDecoration:t.status==="done"?"line-through":"none",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{t.title}</div>
                          {t.description && <div style={{fontSize:10,color:"var(--text-muted)",marginTop:2,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{t.description}</div>}
                        </div>
                      </div>
                      <div style={{display:"flex",gap:3,flexShrink:0}}>
                        {t.status!=="done" && t.status!=="cancelled" && (
                          <button className="soft-btn" style={{fontSize:9,padding:"2px 6px"}} title="В работу" onClick={async()=>{
                            const newS = t.status==="in_progress"?"todo":"in_progress";
                            await updateTaskStatus(t.id, newS);
                          }}><UiIcon icon={t.status==="in_progress" ? Pause : Play} size={12} /></button>
                        )}
                        <button className="soft-btn" style={{fontSize:9,padding:"2px 6px"}} title="Редактировать" onClick={()=>{setEditingTask(t.id);setTaskForm({title:t.title,description:t.description||"",category:t.category||"general",priority:t.priority||"medium",due_date:t.due_date||""});}}><UiIcon icon={Pencil} size={12} /></button>
                        <button className="soft-btn" style={{fontSize:9,padding:"2px 6px",color:"#f44336"}} title="Удалить" onClick={() => deleteTaskItem(t.id)}><UiIcon icon={Trash2} size={12} /></button>
                      </div>
                    </div>
                    <div style={{display:"flex",gap:8,alignItems:"center",fontSize:10,color:"var(--text-muted)",marginTop:2}}>
                      <span style={{color:prioColor}}>{({ urgent:"Срочный", high:"Высокий", medium:"Средний", low:"Низкий" }[t.priority] || t.priority)}</span>
                      <span>{({ general:"Общее", work:"Работа", personal:"Личное", study:"Учёба", project:"Проект", idea:"Идея" }[t.category] || t.category)}</span>
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

              {/* Внутренние табы */}
              <div style={{display:"flex",gap:4,marginBottom:14}}>
                {[["setup","Настройка", Settings],["users","Пользователи", Users],["log","Лог", ScrollText],["guide","Инструкция", BookOpen]].map(([k,l,Icon])=>(
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
                    <div>3. Введи имя бота (например: <i>Elira AI</i>)</div>
                    <div>4. Введи username бота (например: <i>elira_ai_bot</i>)</div>
                    <div>5. BotFather даст тебе <b>токен</b> — строка вида:</div>
                    <div style={{background:"var(--bg-code)",padding:"6px 10px",borderRadius:6,fontFamily:"monospace",fontSize:10,margin:"6px 0",wordBreak:"break-all"}}>7123456789:AAHfGx0X...</div>
                  </div>

                  <div style={{padding:12,borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:10}}>
                    <div style={{fontWeight:700,marginBottom:6}}>Шаг 2: Вставь токен</div>
                    <div>1. Перейди на вкладку <b>Настройка</b> выше</div>
                    <div>2. Вставь токен в поле «Токен бота»</div>
                    <div>3. Нажми <b>Сохранить</b></div>
                    <div>4. Нажми <b>Тест</b> — должно показать имя бота</div>
                  </div>

                  <div style={{padding:12,borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:10}}>
                    <div style={{fontWeight:700,marginBottom:6}}>Шаг 3: Запусти бота</div>
                    <div>1. Нажми <b>Запустить бота</b></div>
                    <div>2. Открой своего бота в Telegram</div>
                    <div>3. Нажми <b>/start</b> — бот ответит приветствием</div>
                    <div>4. Пиши любые сообщения — Elira будет отвечать!</div>
                  </div>

                  <div style={{padding:12,borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:10}}>
                    <div style={{fontWeight:700,marginBottom:6}}>Команды бота</div>
                    <div><code>/start</code> — Приветствие</div>
                    <div><code>/help</code> — Справка</div>
                    <div><code>/status</code> — Текущие настройки</div>
                    <div><code>/web on|off</code> — Включить/выключить веб-поиск</div>
                    <div><code>/memory on|off</code> — Включить/выключить память</div>
                  </div>

                  <div style={{padding:12,borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:10}}>
                    <div style={{fontWeight:700,marginBottom:6}}>Дополнительно</div>
                    <div>• <b>Доступ:</b> по умолчанию «все» — любой пользователь может писать боту. Переключи на «только разрешённые» во вкладке Пользователи.</div>
                    <div>• <b>Модель:</b> бот использует ту же модель что и в чате Elira. Можно изменить в настройках.</div>
                    <div>• <b>Память и веб-поиск:</b> можно включить для более умных ответов.</div>
                    <div>• <b>Бот работает пока запущен backend</b> (Elira). При перезапуске нужно снова нажать «Запустить».</div>
                  </div>

                  <div style={{padding:10,borderRadius:10,background:"rgba(99,102,241,0.1)",border:"1px solid var(--accent)",fontSize:10}}>
                    <b>Совет от @BotFather:</b> после создания бота отправь <code>/setdescription</code> и <code>/setuserpic</code> чтобы задать описание и аватарку.
                  </div>
                </div>
              )}

              {tgTab === "setup" && (
                <div>
                  {/* Статус */}
                  <div style={{padding:10,borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:12,display:"flex",alignItems:"center",justifyContent:"space-between"}}>
                    <div>
                      <span style={{fontSize:12,fontWeight:600}}>Статус: </span>
                      <span style={{fontSize:12,color:tgConfig?.running?"#4caf50":"var(--text-muted)",fontWeight:600}}>{tgConfig?.running?"● Работает":"○ Остановлен"}</span>
                    </div>
                    <div style={{display:"flex",gap:4}}>
                      {!tgConfig?.running ? (
<button className="soft-btn" style={{fontSize:10,padding:"4px 12px",background:"#4caf50",color:"#fff",border:"none",borderRadius:6,display:"inline-flex",alignItems:"center",gap:6}} onClick={startTelegramBot}><UiIcon icon={Play} size={12} />Запустить</button>
                      ) : (
<button className="soft-btn" style={{fontSize:10,padding:"4px 12px",background:"#f44336",color:"#fff",border:"none",borderRadius:6,display:"inline-flex",alignItems:"center",gap:6}} onClick={stopTelegramBot}><UiIcon icon={Square} size={12} />Остановить</button>
                      )}
<button className="soft-btn" style={{fontSize:10,padding:"4px 10px",border:"1px solid var(--border)"}} onClick={testTelegramBot}>Тест</button>
                    </div>
                  </div>

                  {/* Токен */}
                  <div style={{padding:12,borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:12}}>
                    <div style={{fontSize:12,fontWeight:600,marginBottom:6}}>Токен бота</div>
                    {tgConfig?.has_token && <div style={{fontSize:10,color:"var(--text-muted)",marginBottom:4}}>Текущий: {tgConfig.bot_token}</div>}
                    <div style={{display:"flex",gap:6}}>
                      <input type="password" placeholder="Вставь токен от @BotFather" value={tgTokenInput} onChange={e=>setTgTokenInput(e.target.value)} className="rename-input" style={{flex:1,fontSize:11,padding:"5px 8px"}}/>
<button className="soft-btn" style={{fontSize:10,padding:"4px 12px",background:"var(--accent)",color:"#fff",border:"none",borderRadius:6}} onClick={saveTelegramToken}>Сохранить</button>
                    </div>
                  </div>

                  {/* Настройки бота */}
                  <div style={{padding:12,borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:12}}>
                    <div style={{fontSize:12,fontWeight:600,marginBottom:8}}><IconText icon={Settings} size={13}>Параметры</IconText></div>
                    <div style={{display:"flex",gap:8,flexWrap:"wrap",marginBottom:8}}>
                      <div>
                        <div style={{fontSize:10,color:"var(--text-muted)",marginBottom:2}}>Модель</div>
                        <input placeholder="auto (текущая)" value={tgConfig?.model||""} onChange={e=>{setTgConfig({...tgConfig,model:e.target.value})}} className="rename-input" style={{fontSize:11,padding:"4px 8px",width:140}}/>
                      </div>
                      <div>
                        <div style={{fontSize:10,color:"var(--text-muted)",marginBottom:2}}>Профиль</div>
                        <select value={tgConfig?.profile||"Универсальный"} onChange={e=>{setTgConfig({...tgConfig,profile:e.target.value})}} className="topbar-select dark-select" style={{fontSize:11}}>
                          <option>Универсальный</option>
                          <option>Исследователь</option>
                          <option>Программист</option>
                          <option>Аналитик</option>
                          <option>Сократ</option>
                        </select>
                      </div>
                    </div>
                    <div style={{display:"flex",gap:12,marginBottom:8}}>
                      <label style={{fontSize:11,display:"flex",alignItems:"center",gap:4,cursor:"pointer"}}>
                        <input type="checkbox" checked={tgConfig?.use_memory||false} onChange={e=>{setTgConfig({...tgConfig,use_memory:e.target.checked})}}/>
                        Память
                      </label>
                      <label style={{fontSize:11,display:"flex",alignItems:"center",gap:4,cursor:"pointer"}}>
                        <input type="checkbox" checked={tgConfig?.use_web_search||false} onChange={e=>{setTgConfig({...tgConfig,use_web_search:e.target.checked})}}/>
                        Веб-поиск
                      </label>
                    </div>
                    <div style={{marginBottom:8}}>
                      <div style={{fontSize:10,color:"var(--text-muted)",marginBottom:2}}>Приветствие (/start)</div>
                      <textarea value={tgConfig?.welcome_message||""} onChange={e=>{setTgConfig({...tgConfig,welcome_message:e.target.value})}} className="rename-input" style={{width:"100%",fontSize:11,padding:"5px 8px",minHeight:50,resize:"vertical",fontFamily:"inherit"}} rows={2}/>
                    </div>
                    <button className="soft-btn" style={{fontSize:11,padding:"4px 14px",background:"var(--accent)",color:"#fff",border:"none",borderRadius:6}} onClick={async()=>{
                      try{
                        const upd = {};
                        if(tgConfig?.model !== undefined) upd.model = tgConfig.model;
                        if(tgConfig?.profile) upd.profile = tgConfig.profile;
                        if(tgConfig?.use_memory !== undefined) upd.use_memory = tgConfig.use_memory;
                        if(tgConfig?.use_web_search !== undefined) upd.use_web_search = tgConfig.use_web_search;
                        if(tgConfig?.welcome_message) upd.welcome_message = tgConfig.welcome_message;
                        await api.updateTelegramConfig(upd);
                        await loadTelegram(); setError("");
                      }catch(e){setError(`Telegram: ${normalizeErrorMessage(e)}`)}
                    }}>Сохранить настройки</button>
                  </div>
                </div>
              )}

              {tgTab === "users" && (
                <div>
                  <div style={{fontSize:11,color:"var(--text-muted)",marginBottom:8}}>Пользователи, написавшие боту. Можно ограничить доступ.</div>
                  <div style={{marginBottom:10}}>
                    <label style={{fontSize:11,display:"flex",alignItems:"center",gap:4,cursor:"pointer"}}>
                      <input type="checkbox" checked={tgConfig?.allowed_users==="all"} onChange={async e=>{
                        const val = e.target.checked ? "all" : "whitelist";
                        await updateTelegramAllowedUsers(val);
                      }}/>
                      Разрешить всем (иначе — только отмеченным)
                    </label>
                  </div>
                  {tgUsers.length===0 && <div style={{fontSize:11,color:"var(--text-muted)",padding:"12px 0",textAlign:"center"}}>Пока нет пользователей</div>}
                  {tgUsers.map(u=>(
                    <div key={u.chat_id} style={{padding:"8px 12px",borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:4,display:"flex",alignItems:"center",justifyContent:"space-between"}}>
                      <div>
                        <span style={{fontWeight:600,fontSize:12}}>{u.first_name||""} {u.last_name||""}</span>
                        {u.username && <span style={{fontSize:10,color:"var(--text-muted)",marginLeft:6}}>@{u.username}</span>}
                        <span style={{fontSize:9,color:"var(--text-muted)",marginLeft:6}}>ID: {u.chat_id}</span>
                      </div>
                      <div style={{display:"flex",alignItems:"center",gap:6}}>
                        <span style={{fontSize:10,color:u.allowed?"#4caf50":"#f44336"}}>{u.allowed?"Разрешён":"Заблокирован"}</span>
                        <button className="soft-btn" style={{fontSize:9,padding:"2px 8px"}} onClick={() => toggleTelegramUserAccess(u)}>
                          {u.allowed ? "Запретить" : "Разрешить"}
                        </button>
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
                          <span style={{fontSize:9,fontWeight:600,color:l.direction==="in"?"var(--accent)":"#4caf50"}}>{l.direction==="in"?"→ Входящее":"← Ответ"}{l.direction==="cmd"?" (команда)":""}</span>
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

              {/* Форма создания */}
              <div style={{padding:12,borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:14}}>
                <div style={{fontSize:12,fontWeight:600,color:"var(--text)",marginBottom:8}}>＋ Новый пайплайн</div>
                <div style={{display:"flex",gap:6,flexWrap:"wrap",marginBottom:6}}>
                  <input placeholder="Название" value={pipeForm.name} onChange={e=>setPipeForm({...pipeForm,name:e.target.value})} className="rename-input" style={{flex:1,minWidth:120,fontSize:11,padding:"4px 8px"}}/>
                  <select value={pipeForm.task_type} onChange={e=>setPipeForm({...pipeForm,task_type:e.target.value})} className="topbar-select dark-select" style={{fontSize:11}}>
                    <option value="prompt">Промпт</option>
                    <option value="web_search">Веб-поиск</option>
                    <option value="plugin">Плагин</option>
                    <option value="http">HTTP</option>
                  </select>
                  <select value={pipeForm.interval_minutes} onChange={e=>setPipeForm({...pipeForm,interval_minutes:+e.target.value})} className="topbar-select dark-select" style={{fontSize:11}}>
                    <option value={5}>5 мин</option>
                    <option value={15}>15 мин</option>
                    <option value={30}>30 мин</option>
                    <option value={60}>1 час</option>
                    <option value={180}>3 часа</option>
                    <option value={360}>6 часов</option>
                    <option value={720}>12 часов</option>
                    <option value={1440}>24 часа</option>
                  </select>
                </div>
                <input placeholder={pipeForm.task_type==="prompt"?"Промпт для LLM":pipeForm.task_type==="web_search"?"Поисковый запрос":pipeForm.task_type==="plugin"?"Имя плагина":"URL"} value={pipeForm.task_data.prompt||pipeForm.task_data.query||pipeForm.task_data.plugin_name||pipeForm.task_data.url||""} onChange={e=>{const key={prompt:"prompt",web_search:"query",plugin:"plugin_name",http:"url"}[pipeForm.task_type]||"prompt";setPipeForm({...pipeForm,task_data:{[key]:e.target.value}})}} className="rename-input" style={{width:"100%",fontSize:11,padding:"4px 8px",marginBottom:6}}/>
<button className="soft-btn" style={{fontSize:11,padding:"4px 14px",background:"var(--accent)",color:"#fff",border:"none",borderRadius:6}} onClick={createPipeline}>Создать</button>
              </div>

              {/* РЎРїРёСЃРѕРє */}
              {pipelinesList.length===0 && !pipelinesError && <div style={{fontSize:11,color:"var(--text-muted)",padding:"12px 0",textAlign:"center"}}>Пайплайнов пока нет</div>}
              {pipelinesList.map(p=>(
                <div key={p.id} style={{padding:"10px 12px",borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:6}}>
                  <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:4}}>
                    <div>
                      <span style={{fontWeight:600,fontSize:12,color:"var(--text)"}}>{p.name}</span>
                      <span style={{fontSize:10,color:"var(--text-muted)",marginLeft:8}}>{({ prompt:"Промпт", web_search:"Веб-поиск", plugin:"Плагин", http:"HTTP" }[p.task_type] || p.task_type)} • каждые {p.interval_minutes} мин</span>
                      <span style={{fontSize:9,color:p.enabled?"#4caf50":"#f44336",marginLeft:6}}>{p.enabled?"● вкл":"○ выкл"}</span>
                    </div>
                    <div style={{display:"flex",gap:4}}>
                      <button className="soft-btn" style={{fontSize:9,padding:"2px 8px"}} title="Запустить сейчас" onClick={() => runPipelineNow(p.id)}><UiIcon icon={Play} size={12} /></button>
                      <button className="soft-btn" style={{fontSize:9,padding:"2px 8px"}} title={p.enabled?"Выключить":"Включить"} onClick={() => togglePipelineEnabled(p)}><UiIcon icon={p.enabled ? Pause : Play} size={12} /></button>
                      <button className="soft-btn" style={{fontSize:9,padding:"2px 8px",color:"#f44336"}} title="Удалить" onClick={() => deletePipeline(p.id)}><UiIcon icon={Trash2} size={12} /></button>
                    </div>
                  </div>
                  <div style={{fontSize:10,color:"var(--text-muted)"}}>
                    {p.run_count>0 && <span>Запусков: {p.run_count} • </span>}
                    {p.last_run && <span>Посл.: {new Date(p.last_run).toLocaleString("ru-RU")} • </span>}
                    {p.next_run && <span>След.: {new Date(p.next_run).toLocaleString("ru-RU")}</span>}
                  </div>
                  {p.last_error && <div style={{fontSize:10,color:"#f44336",marginTop:2}}>Ошибка: {p.last_error}</div>}
                </div>
              ))}
            </div>
          ) : sideTab === "dashboard" ? (
            <div className="settings-main-card" style={{overflow:"auto"}}>
              <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:16}}>
                <div style={{fontSize:15,fontWeight:700,color:"var(--text)"}}><IconText icon={LayoutDashboard} size={15}>Панель</IconText></div>
                <button className="soft-btn" style={{fontSize:10,padding:"3px 10px",border:"1px solid var(--border)",display:"inline-flex",alignItems:"center",gap:6}} onClick={loadDashboard}><UiIcon icon={RefreshCw} size={13} />Обновить</button>
              </div>
              <PanelNotice title="Проблема синхронизации панели" message={dashboardError} onRetry={loadDashboard} tone={dashData || projectBrainStatus ? "warning" : "error"} />
              <CapabilityStatusSection status={projectBrainStatus} />
              <PersonaStatusSection status={personaStatus} busy={personaBusy} onRollback={handlePersonaRollback} />
              {!dashData && !dashboardError ? <div style={{color:"var(--text-muted)",fontSize:12}}>Загрузка...</div> : !dashData ? null : (
                <>
                  {/* Карточки статистики */}
                  <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(130px,1fr))",gap:8,marginBottom:16}}>
                    {[
                      {label:"Запросов",value:dashData.total_runs||0,icon:<UiIcon icon={MessageSquare} size={18} />},
                      {label:"Сегодня",value:dashData.today||0,icon:<UiIcon icon={CalendarDays} size={18} />},
                      {label:"За неделю",value:dashData.this_week||0,icon:<UiIcon icon={CalendarDays} size={18} style={{opacity:0.75}} />},
                      {label:"Успешность",value:`${dashData.success_rate||0}%`,icon:<UiIcon icon={BarChart3} size={18} />},
                      {label:"Чатов",value:dashData.chats||0,icon:<UiIcon icon={MessageSquare} size={18} />},
                      {label:"Сообщений",value:dashData.messages||0,icon:<UiIcon icon={ScrollText} size={18} />},
                      {label:"Ср. длина",value:dashData.avg_answer_length||0,icon:<UiIcon icon={FileText} size={18} />},
                      {label:"Плагинов",value:dashData.plugins||0,icon:<UiIcon icon={Settings} size={18} />},
                    ].map(s=>(
                      <div key={s.label} style={{padding:"12px",borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",textAlign:"center"}}>
                        <div style={{fontSize:20,marginBottom:4,display:"flex",justifyContent:"center"}}>{s.icon}</div>
                        <div style={{fontSize:18,fontWeight:700,color:"var(--text)"}}>{s.value}</div>
                        <div style={{fontSize:10,color:"var(--text-muted)",marginTop:2}}>{s.label}</div>
                      </div>
                    ))}
                  </div>

                  {/* Активность по дням — мини-график */}
                  {dashData.daily_activity && (
                    <div style={{marginBottom:16}}>
                      <div style={{fontSize:12,fontWeight:600,color:"var(--text)",marginBottom:8}}>Активность (14 дней)</div>
                      <div style={{display:"flex",alignItems:"flex-end",gap:3,height:80,padding:"0 4px"}}>
                        {dashData.daily_activity.map((d,i)=>{
                          const max = Math.max(...dashData.daily_activity.map(x=>x.count),1);
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

                  {/* Топ моделей */}
                  {dashData.top_models?.length > 0 && (
                    <div style={{marginBottom:16}}>
                      <div style={{fontSize:12,fontWeight:600,color:"var(--text)",marginBottom:6}}>Модели</div>
                      {dashData.top_models.map(m=>{
                        const pct = dashData.total_runs ? Math.round(m.count/dashData.total_runs*100) : 0;
                        return <div key={m.model} style={{display:"flex",alignItems:"center",gap:8,marginBottom:4}}>
                          <div style={{fontSize:11,color:"var(--text)",minWidth:140,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{m.model}</div>
                          <div style={{flex:1,height:6,borderRadius:3,background:"var(--border)",overflow:"hidden"}}><div style={{width:`${pct}%`,height:"100%",borderRadius:3,background:"var(--accent)"}}/></div>
                          <div style={{fontSize:10,color:"var(--text-muted)",minWidth:40,textAlign:"right"}}>{m.count} ({pct}%)</div>
                        </div>
                      })}
                    </div>
                  )}

                  {/* Топ роутов */}
                  {dashData.top_routes?.length > 0 && (
                    <div style={{marginBottom:16}}>
                      <div style={{fontSize:12,fontWeight:600,color:"var(--text)",marginBottom:6}}>Типы задач</div>
                      {dashData.top_routes.map(r=>(
                        <div key={r.route} style={{display:"flex",justifyContent:"space-between",fontSize:11,padding:"3px 0",borderBottom:"1px solid var(--border)"}}>
                          <span style={{color:"var(--text)"}}>{r.route || "—"}</span>
                          <span style={{color:"var(--text-muted)"}}>{r.count}</span>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Память */}
                  {dashData.memory && typeof dashData.memory === "object" && (
                    <div>
                      <div style={{fontSize:12,fontWeight:600,color:"var(--text)",marginBottom:6}}>Память</div>
                      <div style={{fontSize:11,color:"var(--text-muted)"}}>
                        Всего: {dashData.memory.total || dashData.memory.count || 0} записей
                      </div>
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
                    {(modelOpts?.length?modelOpts:[{name:settingsModel}]).map((i,idx)=>{const n=typeof i==="string"?i:(i.name||i.model||"model");return <option key={n+idx} value={n}>{n}</option>})}
                  </select>
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
                <div className="settings-tile">
                  <div className="settings-title">Тема оформления</div>
                  <div style={{display:"flex",gap:8}}>
                    <button onClick={()=>setTheme("dark")} style={{flex:1,padding:"6px 12px",borderRadius:6,border:"1px solid "+(theme==="dark"?"var(--accent)":"var(--border)"),background:theme==="dark"?"var(--accent-dim)":"transparent",color:"var(--text-primary)",cursor:"pointer",fontSize:12,display:"inline-flex",alignItems:"center",justifyContent:"center",gap:6}}><UiIcon icon={Moon} size={14} />Тёмная</button>
                    <button onClick={()=>setTheme("light")} style={{flex:1,padding:"6px 12px",borderRadius:6,border:"1px solid "+(theme==="light"?"var(--accent)":"var(--border)"),background:theme==="light"?"var(--accent-dim)":"transparent",color:"var(--text-primary)",cursor:"pointer",fontSize:12,display:"inline-flex",alignItems:"center",justifyContent:"center",gap:6}}><UiIcon icon={Sun} size={14} />Светлая</button>
                  </div>
                </div>
                <div className="settings-tile" style={{gridColumn:"1 / -1"}}>
                  <div className="settings-title">Оркестрация моделей</div>
                  <div className="settings-desc" style={{marginBottom:8}}>Какая модель отвечает за какой тип задачи. Первая в списке — приоритетная.</div>
                  {["code","project","research","chat"].map(route => {
                    const routeLabels = {code:"Код",project:"Проект",research:"Исследование",chat:"Чат"};
                    const routeDescs = {code:"Написание, ревью и отладка кода",project:"Работа с файлами проекта",research:"Поиск, анализ, факты",chat:"Обычные вопросы и диалог"};
                    const current = routeMap[route] || [];
                    const getName = i => typeof i === "string" ? i : (i.name || i.model || "");
                    const allModels = (modelOpts?.length ? modelOpts : []).map(getName);
                    return (
                      <div key={route} style={{padding:"8px 10px",borderRadius:8,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:6}}>
                        <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:4}}>
                          <div><span style={{fontWeight:600,fontSize:12}}>{routeLabels[route]}</span><span style={{fontSize:10,color:"var(--text-muted)",marginLeft:8}}>{routeDescs[route]}</span></div>
                        </div>
                        <div style={{display:"flex",gap:6,flexWrap:"wrap",alignItems:"center"}}>
                          <select
                            value={current[0] || ""}
                            onChange={e=>{
                              const val = e.target.value;
                              const rest = current.filter(m => m !== val).slice(0, 2);
                              const updated = {...routeMap, [route]: val ? [val, ...rest] : current};
                              setRouteMap(updated);
                              setSettingsSaved(false);
                            }}
                            className="topbar-select dark-select"
                            style={{fontSize:11,padding:"3px 6px"}}
                          >
                            <option value="">— не задана —</option>
                            {allModels.map(n=><option key={n} value={n}>{n}</option>)}
                          </select>
                          {current.length > 1 && <span style={{fontSize:10,color:"var(--text-muted)"}}>фоллбэк: {current.slice(1).join(" → ")}</span>}
                          {current.length > 1 && <button className="soft-btn" style={{fontSize:9,padding:"1px 6px",marginLeft:4}} onClick={()=>{setRouteMap({...routeMap,[route]:[current[0]]});setSettingsSaved(false)}} title="Очистить фоллбэк">✕</button>}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
              <div className="settings-desc" style={{marginTop:12,fontSize:10,color:"var(--text-muted)"}}>
                Горячие клавиши: Ctrl+N новый чат · Escape стоп · Ctrl+Shift+T тема
              </div>
              <button
                style={{marginTop:14,padding:"8px 24px",borderRadius:8,border:"1px solid var(--accent)",background:settingsSaved?"rgba(16,185,129,0.15)":"var(--accent)",color:settingsSaved?"#10b981":"#fff",cursor:"pointer",fontSize:13,fontWeight:600,transition:"all 0.2s"}}
                onClick={async()=>{
                  try {
                    await api.updateSettings({ollama_context:settingsContext,default_model:settingsModel,agent_profile:settingsProfile,route_model_map:routeMap});
                    setModel(settingsModel);setProfile(settingsProfile);setOllamaContext(settingsContext);
                    setSettingsSaved(true);setTimeout(()=>setSettingsSaved(false),2000);
                  } catch(e){setError(normalizeErrorMessage(e));}
                }}
              >{settingsSaved?"✓ Сохранено":"Сохранить"}</button>
              <div style={{marginTop:18}}><div className="settings-title" style={{marginBottom:8}}>Навыки</div><div className="settings-desc" style={{marginBottom:10}}>Включи / выключи возможности</div>
                <div className="skills-grid">{SKILLS.map(s=><button key={s.id} className={`skill-chip ${skills.includes(s.id)?"active":""}`} onClick={()=>toggleSkill(s.id)} title={s.desc}>{s.label}</button>)}</div>
              </div>
              <div style={{marginTop:18}}>
                <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:8}}>
                  <div className="settings-title">Плагины</div>
<button className="soft-btn" style={{fontSize:10,padding:"3px 10px",border:"1px solid var(--border)",display:"inline-flex",alignItems:"center",gap:6}} onClick={reloadPlugins}><UiIcon icon={RefreshCw} size={12} />Перезагрузить</button>
                </div>
                <div className="settings-desc" style={{marginBottom:10}}>Пользовательские .py скрипты в data/plugins/</div>
                {pluginList.length===0 && <div style={{fontSize:11,color:"var(--text-muted)",padding:"8px 0"}}>Плагинов нет. Положи .py файлы в backend/data/plugins/</div>}
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
                {fLib.length ? fLib.map(i => <div key={i.id} className={`library-table-row ${selLibId===i.id?"active":""}`} onClick={()=>setSelLibId(i.id)}><div className="table-name">{i.name}</div><div>{i.type.split("/").pop()}</div><div>{Math.round(i.size/1024)||0}K</div><div><input type="checkbox" checked={chatId ? ctxF.some(f => f.id === i.id) : (i.use_in_context !== false)} onChange={e=>{e.stopPropagation();toggleCtx(i.id,e.target.checked);}}/></div><div><button className="mini-icon-btn" onClick={e=>{e.stopPropagation();removeLib(i.id);}}>✕</button></div></div>) : <div className="sidebar-empty" style={{padding:10}}>Нет файлов</div>}
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
                {streaming && streamText && <div className="message-row assistant"><div className="message-bubble smaller-text streaming-active"><MarkdownRenderer content={streamText}/><span className="typing-cursor"/></div></div>}
                {streaming && !streamText && (
                  <div className="message-row assistant">
                    <div className="message-bubble smaller-text thinking-bubble">
                      <div className="thinking-indicator">
                        <div className="thinking-dots"><span/><span/><span/></div>
                        <span className="thinking-text">{phase || "Думаю..."}</span>
                      </div>
                    </div>
                  </div>
                )}
              </div>

              {error && <div className="error-banner smaller-text">{error}</div>}
              {chartData?.values?.length > 0 && !working && (
                <div style={{background:"var(--bg-surface)",border:"1px solid var(--border)",borderRadius:8,padding:"10px 14px",marginTop:4}}>
                  <div style={{fontSize:11,color:"var(--text-muted)",marginBottom:6,display:"flex",justifyContent:"space-between"}}>
                    <IconText icon={BarChart3} size={13}>{chartData.valueLabel}</IconText>
                    <button className="soft-btn" style={{fontSize:10,padding:"1px 6px"}} onClick={()=>setChartData(null)}>✕</button>
                  </div>
                  <div style={{display:"flex",gap:3,alignItems:"flex-end",height:72}}>
                    {chartData.values.map((v,i)=>{const mx=Math.max(...chartData.values)||1;return <div key={i} title={chartData.labels[i]+": "+v} style={{flex:1,minWidth:6,maxWidth:36,background:"var(--accent)",opacity:0.75,height:(v/mx*68)+"px",borderRadius:"3px 3px 0 0"}}></div>;})}
                  </div>
                  <div style={{display:"flex",gap:3,marginTop:2,overflow:"hidden"}}>
                    {chartData.labels.map((l,i)=><div key={i} style={{flex:1,minWidth:6,maxWidth:36,fontSize:9,color:"var(--text-muted)",textAlign:"center",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{l}</div>)}
                  </div>
                </div>
              )}

              <div className="composer-wrap" onDragOver={onDragOver} onDragLeave={onDragLeave} onDrop={onDrop}>
                <div className={`chat-input-shell ${drag?"drag-active":""}`}>
                  <button className="input-plus-btn" onClick={()=>fileRef.current?.click()}>+</button>
                  <textarea ref={taRef} value={input} onChange={e=>setInput(e.target.value)} onKeyDown={handleKeyDown} placeholder="Напиши сообщение..." className="chat-textarea"/>
                  <button className="send-btn" onClick={working ? handleStop : handleSend} style={working ? {background:"rgba(255,70,70,0.15)",borderColor:"rgba(255,70,70,0.3)",color:"#ff9090"} : undefined}>{working?"Стоп":"Отправить"}</button>
                  <input ref={fileRef} type="file" multiple hidden onChange={e=>handleFiles(e.target.files)}/>
                </div>
                <div className="composer-selectors" style={{justifyContent:"center"}}>
                  <select value={model} onChange={e=>setModel(e.target.value)} className="composer-select">{(modelOpts?.length?modelOpts:[{name:model}]).map((i,idx)=>{const n=typeof i==="string"?i:(i.name||i.model||"model");return <option key={n+idx} value={n}>{shortModelName(n)}</option>})}</select>
                  <select value={profile} onChange={e=>setProfile(e.target.value)} className="composer-select">{Object.keys(PROFILE_DESCRIPTIONS).map(n=><option key={n} value={n}>{n}</option>)}</select>
                  <button onClick={() => setMultiAgent(p => !p)} style={{padding:"2px 10px",borderRadius:99,fontSize:10,border:"1px solid " + (multiAgent ? "rgba(244,114,182,0.4)" : "var(--border)"),background:multiAgent ? "rgba(244,114,182,0.12)" : "transparent",color:multiAgent ? "#f472b6" : "var(--text-muted)",cursor:"pointer"}}>{multiAgent ? "Multi ON" : "Multi"}</button>
                </div>
              </div>
            </>
          )}
        </div>
      </main>

      {/* Right panel - artifacts / code viewer */}
      {showPanel && sideTab === "chats" && (
        <ArtifactPanel
          messages={messages}
          streamingCode={streamText}
          onClose={() => setShowPanel(false)}
        />
      )}
    </div>
  );
}
