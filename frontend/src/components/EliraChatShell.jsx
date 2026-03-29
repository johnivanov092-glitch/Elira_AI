/**
 * EliraChatShell.jsx — v3
 *
 * Фиксы:
 *   • Индикатор прогресса: "Поиск...", "Генерация...", "Проверка..."
 *   • Быстрее ощущается — фазы видны до первого токена
 *   • Code tab работает как артефакты Claude
 */

import React, { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { api, executeStream } from "../api/ide";
import IdeWorkspaceShell from "./IdeWorkspaceShell";
import MarkdownRenderer from "./MarkdownRenderer";
import ArtifactPanel from "./ArtifactPanel";
import MemoryPanel from "./MemoryPanel";
import ProjectPanel from "./ProjectPanel";
import "../styles/markdown.css";

const LIBRARY_KEY = "elira_library_files_v7";
const CHAT_CONTEXT_KEY = "elira_chat_context_map_v7";

// Миграция старых localStorage ключей
["jarvis_library_files_v7", "jarvis_chat_context_map_v7", "jarvis_theme"].forEach(oldKey => {
  const val = localStorage.getItem(oldKey);
  if (val !== null) {
    const newKey = oldKey.replace("jarvis_", "elira_");
    if (!localStorage.getItem(newKey)) localStorage.setItem(newKey, val);
    localStorage.removeItem(oldKey);
  }
});
const MAX_HISTORY_PAIRS = 10;

const PROFILE_DESCRIPTIONS = {
  "Универсальный": "Ясный, структурированный и профессиональный тон.",
  "Программист": "Код, исправления, архитектура, рефакторинг.",
  "Исследователь": "Факты, источники, web-поиск.",
  "Аналитик": "Выводы, риски, декомпозиция.",
  "Сократ": "Обучение через наводящие вопросы.",
};

const SKILLS = [
  { id: "web_search", label: "🌐 Веб-поиск", desc: "Поиск в интернете" },
  { id: "code_analysis", label: "🔍 Анализ кода", desc: "Разбор структуры кода" },
  { id: "file_context", label: "📄 Контекст файлов", desc: "Загруженные файлы в ответах" },
  { id: "memory", label: "🧠 Память", desc: "Запоминание между чатами" },
  { id: "python_exec", label: "🐍 Python", desc: "Выполнение скриптов" },
  { id: "project_patch", label: "🔧 Патчинг", desc: "Изменение файлов проекта" },
  { id: "pdf_reader", label: "📑 PDF", desc: "Извлечение текста из PDF" },
  { id: "reflection", label: "🪞 Рефлексия", desc: "Двойная проверка ответов" },
  { id: "http_api", label: "🌐 HTTP/API", desc: "GET/POST запросы к API" },
  { id: "sql_query", label: "🗄 SQL", desc: "Запросы к базе данных" },
  { id: "file_gen", label: "📝 Word/Excel", desc: "Генерация документов" },
  { id: "screenshot", label: "🖼 Скриншот", desc: "Снимок веб-страницы" },
  { id: "encrypt", label: "🔐 Шифрование", desc: "AES шифрование заметок" },
  { id: "archiver", label: "📦 Архиватор", desc: "ZIP создание/распаковка" },
  { id: "converter", label: "🔄 Конвертер", desc: "CSV→XLSX, MD→DOCX, JSON→CSV" },
  { id: "regex", label: "📐 Regex", desc: "Тестирование регулярок" },
  { id: "translator", label: "🌍 Переводчик", desc: "Перевод через LLM" },
  { id: "csv_analysis", label: "📈 CSV анализ", desc: "Статистика и агрегации" },
  { id: "webhook", label: "📡 Webhook", desc: "Приём входящих вебхуков" },
  { id: "plugins", label: "🔌 Плагины", desc: "Пользовательские .py скрипты" },
  { id: "image_gen", label: "🎨 Картинки", desc: "FLUX.1 генерация изображений" },
  { id: "git", label: "🔀 Git", desc: "Статус, log, diff репозитория" },
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
  // YandexGPT-5-Lite-8B-instruct-GGUF → YandexGPT
  if (name.toLowerCase().includes("yandex")) return "YandexGPT";
  // nemotron-mini → Nemotron Mini, etc.
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

async function parseJsonSafe(resp) {
  const raw = await resp.text();
  try {
    return raw ? JSON.parse(raw) : {};
  } catch {
    if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${raw || resp.statusText || "Ошибка backend"}`);
    throw new Error(raw || "Backend вернул некорректный JSON");
  }
}

async function fileToLibraryRecord(file) {
  let preview = "";
  const name = file.name || "";
  const ext = name.split(".").pop().toLowerCase();
  const API_URL = import.meta.env.VITE_API_BASE_URL || `http://${window.location.hostname}:8000`;

  // Текстовые файлы — читаем на клиенте
  // UTF-8 файлы — читаем на клиенте
  const textExts = ["txt","md","json","js","jsx","ts","tsx","py","css","html","htm","yml","yaml","xml","csv","log","ini","toml","bat","cmd","ps1","sh","sql","rb","php","java","c","cpp","h","hpp","cs","go","rs","swift","kt","r","m","lua","pl","tcl","asm","cfg","conf","env"];
  const isText = file.type.startsWith("text/") || textExts.includes(ext);
  if (isText) try { preview = (await file.text()).slice(0, 12000); } catch {}

  // Бинарные + файлы с другими кодировками → на бекенд
  const serverExts = ["pdf","docx","doc","xlsx","xls","xlsm","zip","bas","vbs","vba","cls","frm","rsc"];
  if (serverExts.includes(ext)) try {
    const fd = new FormData(); fd.append("file", file);
    const r = await fetch(`${API_URL}/api/files/extract-text`, { method: "POST", body: fd });
    if (r.ok) { const d = await r.json(); preview = (d.text || "").slice(0, 12000); }
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
  const [pipelinesList, setPipelinesList] = useState([]);
  const [pipeForm, setPipeForm] = useState({name:"",task_type:"prompt",interval_minutes:60,task_data:{prompt:""}});
  const [tasksList, setTasksList] = useState([]);
  const [taskFilter, setTaskFilter] = useState("active");
  const [taskForm, setTaskForm] = useState({title:"",description:"",category:"general",priority:"medium",due_date:""});
  const [taskStats, setTaskStats] = useState(null);
  const [editingTask, setEditingTask] = useState(null);
  const [tgConfig, setTgConfig] = useState(null);
  const [tgUsers, setTgUsers] = useState([]);
  const [tgLog, setTgLog] = useState([]);
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

  const API_URL = import.meta.env.VITE_API_BASE_URL || `http://${window.location.hostname}:8000`;

  useEffect(() => { init(); return () => { if (streamRef.current) { streamRef.current.abort(); streamRef.current = null; } }; }, []);
  useEffect(() => { if (msgRef.current) msgRef.current.scrollTop = msgRef.current.scrollHeight; }, [messages, chatId]);
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
    fetch(`${API_URL}/api/lib/list`).then(r => { if (!r.ok) return null; return r.json(); }).then(d => {
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
    try { const r = await fetch("/api/pipelines/list"); const d = await r.json(); setPipelinesList(d.pipelines || []); } catch {}
  }

  async function loadTelegram() {
    try {
      const [rc, ru, rl] = await Promise.all([
        fetch("/api/telegram/config"), fetch("/api/telegram/users"), fetch("/api/telegram/log?limit=30"),
      ]);
      const [dc, du, dl] = await Promise.all([rc.json(), ru.json(), rl.json()]);
      setTgConfig(dc); setTgUsers(du.users || []); setTgLog(dl.log || []);
    } catch {}
  }

  async function loadTasks(filter) {
    const f = filter || taskFilter;
    try {
      let items = [];
      if (f === "active") {
        const [r1, r2] = await Promise.all([fetch("/api/tasks/list?status=todo"), fetch("/api/tasks/list?status=in_progress")]);
        const [d1, d2] = await Promise.all([r1.json(), r2.json()]);
        items = [...(d1.tasks || []), ...(d2.tasks || [])];
      } else if (f === "all") {
        const r = await fetch("/api/tasks/list"); const d = await r.json(); items = d.tasks || [];
      } else {
        const r = await fetch(`/api/tasks/list?status=${f}`); const d = await r.json(); items = d.tasks || [];
      }
      setTasksList(items);
      const sr = await fetch("/api/tasks/stats"); setTaskStats(await sr.json());
    } catch {}
  }

  async function loadDashboard() {
    try { const r = await fetch("/api/dashboard/stats"); setDashData(await r.json()); } catch {}
  }

  async function loadPluginList() {
    try { const r = await fetch("/api/extra/plugins/list"); const d = await r.json(); setPluginList(d.plugins || []); } catch {}
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
    const title = chats.find(c => c.id === chatId)?.title || "Elira Chat";
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
      const body = messages.map(m => `${m.role==="user"?"Вы":"Elira"}:\n${m.content}`).join("\n\n" + "─".repeat(40) + "\n\n");
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
        tl.includes("код") || tl.includes("codebase") ||
        tl.includes("backend") || tl.includes("frontend") ||
        tl.includes("структур") || tl.includes("tree") ||
        tl.includes("директор") || tl.includes("каталог") || tl.includes("папк") ||
        tl.includes("readme") || tl.includes("модул") || tl.includes("компонент")
      );

      // Контекст проекта — только для запросов про код/репозиторий
      if (wantsProjectContext) {
        try {
          const projInfo = await fetch(`${API_URL}/api/advanced/project/info`).then(r => r.json());
          if (projInfo.ok) {
            const projTree = await fetch(`${API_URL}/api/advanced/project/tree?max_depth=2&max_items=50`).then(r => r.json());
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
        const modeLabel = [useOrch && "🎯 Оркестратор", "🔎→💻→📊 Агенты", useRefl && "🪞 Рефлексия"].filter(Boolean).join(" → ");
        setPhase(`🤖 ${modeLabel}...`);
        try {
          const resp = await fetch(`${API_URL}/api/advanced/multi-agent`, {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ query: `${text}${cp}`, model_name: model, context: "", agents: ["researcher","programmer","analyst"], use_reflection: useRefl, use_orchestrator: useOrch }),
          });
          const data = await parseJsonSafe(resp);
          if (!resp.ok || data?.ok === false) throw new Error(normalizeErrorMessage(data?.error || data?.detail || `HTTP ${resp.status}`));
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
        { model_name: model, profile_name: profile, user_input: `${text}${cp}`, history, num_ctx: ollamaContext, use_memory: skills.includes("memory"), use_library: skills.includes("file_context"), use_reflection: skills.includes("reflection"), use_web_search: skills.includes("web_search"), use_python_exec: skills.includes("python_exec"), use_image_gen: skills.includes("image_gen"), use_file_gen: skills.includes("file_gen"), use_http_api: skills.includes("http_api"), use_sql: skills.includes("sql_query"), use_screenshot: skills.includes("screenshot"), use_encrypt: skills.includes("encrypt"), use_archiver: skills.includes("archiver"), use_converter: skills.includes("converter"), use_regex: skills.includes("regex"), use_translator: skills.includes("translator"), use_csv: skills.includes("csv_analysis"), use_webhook: skills.includes("webhook"), use_plugins: skills.includes("plugins") },
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
      try { const fd = new FormData(); fd.append("file", f); fd.append("use_in_context", "false"); await fetch(`${API_URL}/api/lib/add`, { method: "POST", body: fd }); } catch {}
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
        await fetch(`${API_URL}/api/lib/${dbId}`, { method: "DELETE" });
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
          {[["chats","☰ Чаты"],["project","📂 Проекты"],["library","📚 Файлы"],["memory","★ Память"],["tasks","📅 Задачи"],["dashboard","📊 Dashboard"],["pipelines","🔄 Pipelines"],["telegram","✈️ Telegram"],["settings","⚙ Настройки"]].map(([k,l]) => (
            <button key={k} className={`sidebar-nav-item ${sideTab === k ? "active" : ""}`} onClick={() => { setSideTab(k); setMobileSidebar(false); if(k==="settings"){setSettingsModel(model);setSettingsProfile(profile);setSettingsContext(ollamaContext);setSettingsSaved(false);refreshModels();loadPluginList();}if(k==="dashboard"){loadDashboard();}if(k==="pipelines"){loadPipelines();}if(k==="tasks"){loadTasks();}if(k==="telegram"){loadTelegram();} }}>{l}</button>
          ))}
        </div>
        <div className="sidebar-nav-item search-shell">
          <span style={{opacity:0.4,fontSize:11}}>⌕</span>
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
          <button onClick={()=>setTheme(t=>t==="dark"?"light":"dark")} style={{background:"none",border:"1px solid var(--border)",borderRadius:6,padding:"3px 8px",cursor:"pointer",color:"var(--text-muted)",fontSize:11}} title="Ctrl+Shift+T">{theme==="dark"?"☀ Светлая":"🌙 Тёмная"}</button>
          <span style={{fontSize:9,color:"var(--text-muted)",opacity:0.5}}>Ctrl+N чат</span>
        </div>
      </aside>

      <main className="elira-main">
        <div className="elira-topbar slim">
          <button className="mobile-burger" onClick={()=>setMobileSidebar(v=>!v)}>☰</button>
          <div className="elira-brand"><svg width="22" height="22" viewBox="0 0 64 64" fill="none" style={{marginRight:7,verticalAlign:"middle",marginTop:-2}}><defs><linearGradient id="jg" x1="12" y1="10" x2="52" y2="54" gradientUnits="userSpaceOnUse"><stop stopColor="#7C3AED"/><stop offset="1" stopColor="#06B6D4"/></linearGradient></defs><rect x="5" y="5" width="54" height="54" rx="14" fill="#0B1020"/><circle cx="32" cy="32" r="14" stroke="url(#jg)" strokeWidth="3"/><circle cx="32" cy="32" r="6" fill="url(#jg)"/></svg>Elira AI</div>
          <div className="topbar-tabs">
            <button className={`soft-btn ${mainTab==="chat"?"active":""}`} onClick={() => setMainTab("chat")}>Chat</button>
            <button className={`soft-btn ${mainTab==="code"?"active":""}`} onClick={() => setMainTab("code")}>Code</button>
            <button className={`soft-btn ${showPanel?"active":""}`} onClick={() => setShowPanel(p => !p)} title="Панель кода">◇</button>
          </div>
        </div>

        <div className="chat-page">
          <div className="chat-header-row">
            <div className="chat-page-title">{sideTab==="chats"&&"Чат"}{sideTab==="memory"&&"Память"}{sideTab==="settings"&&"Настройки"}{sideTab==="library"&&"Библиотека"}{sideTab==="project"&&"Проект"}{sideTab==="dashboard"&&"Dashboard"}{sideTab==="pipelines"&&"Pipelines"}</div>
            {sideTab === "chats" && chatId && (
              <div className="chat-header-actions icon-actions" style={{display:"flex"}}>
                <div className="export-dropdown-wrap" style={{position:"relative"}}>
                  <button className="soft-btn icon-btn" title="Экспорт чата" onClick={()=>setShowExportMenu(v=>!v)}>📥</button>
                  {showExportMenu && <div className="export-dropdown" style={{position:"absolute",top:"100%",right:0,zIndex:99,background:"var(--bg-card)",border:"1px solid var(--border)",borderRadius:8,padding:"4px 0",minWidth:140,boxShadow:"0 4px 16px rgba(0,0,0,.18)"}}>
                    <button className="export-item" onClick={()=>{exportChat("md");setShowExportMenu(false)}}>📋 Markdown</button>
                    <button className="export-item" onClick={()=>{exportChat("html");setShowExportMenu(false)}}>🌐 HTML</button>
                    <button className="export-item" onClick={()=>{exportChat("json");setShowExportMenu(false)}}>📦 JSON</button>
                    <button className="export-item" onClick={()=>{exportChat("txt");setShowExportMenu(false)}}>📄 Text</button>
                  </div>}
                </div>
                <button className="soft-btn icon-btn" onClick={() => saveToMemory(chatId, chats.find(c=>c.id===chatId)?.memory_saved)}>🧠</button>
                <button className="soft-btn icon-btn" onClick={() => pinChat(chatId, chats.find(c=>c.id===chatId)?.pinned)}>📌</button>
                <button className="soft-btn icon-btn" onClick={() => { setRenaming(true); setRenameVal(chats.find(c=>c.id===chatId)?.title||""); }}>✎</button>
                <button className="soft-btn icon-btn" onClick={() => deleteChat(chatId)}>🗑</button>
              </div>
            )}
          </div>

          {renaming && sideTab==="chats" && <div className="rename-bar"><input value={renameVal} onChange={e=>setRenameVal(e.target.value)} className="rename-input wide" placeholder="Название"/><button className="mini-btn" onClick={renameActive}>OK</button></div>}

          {sideTab === "tasks" ? (
            <div className="settings-main-card" style={{overflow:"auto"}}>
              <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:12}}>
                <div style={{fontSize:15,fontWeight:700,color:"var(--text)"}}>📅 Задачи</div>
                <button className="soft-btn" style={{fontSize:10,padding:"3px 10px",border:"1px solid var(--border)"}} onClick={loadTasks}>↻</button>
              </div>

              {/* Статистика */}
              {taskStats && (
                <div style={{display:"flex",gap:8,marginBottom:12,flexWrap:"wrap"}}>
                  {[
                    {l:"Всего",v:taskStats.total,c:"var(--text)"},
                    {l:"Todo",v:taskStats.by_status?.todo||0,c:"#5b9bd5"},
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
                {[["active","Активные"],["todo","Todo"],["in_progress","В работе"],["done","Готовые"],["all","Все"]].map(([k,l])=>(
                  <button key={k} className="soft-btn" style={{fontSize:10,padding:"3px 10px",background:taskFilter===k?"var(--accent)":"transparent",color:taskFilter===k?"#fff":"var(--text)",border:"1px solid var(--border)",borderRadius:6}} onClick={()=>{setTaskFilter(k);loadTasks(k);}}>{l}</button>
                ))}
              </div>

              {/* Форма создания / редактирования */}
              <div style={{padding:12,borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:14}}>
                <div style={{fontSize:12,fontWeight:600,color:"var(--text)",marginBottom:8}}>{editingTask ? "✏️ Редактирование" : "＋ Новая задача"}</div>
                <input placeholder="Название задачи" value={taskForm.title} onChange={e=>setTaskForm({...taskForm,title:e.target.value})} className="rename-input" style={{width:"100%",fontSize:11,padding:"5px 8px",marginBottom:6}}/>
                <textarea placeholder="Описание (необязательно)" value={taskForm.description} onChange={e=>setTaskForm({...taskForm,description:e.target.value})} className="rename-input" style={{width:"100%",fontSize:11,padding:"5px 8px",marginBottom:6,minHeight:40,resize:"vertical",fontFamily:"inherit"}} rows={2}/>
                <div style={{display:"flex",gap:6,flexWrap:"wrap",marginBottom:6}}>
                  <select value={taskForm.priority} onChange={e=>setTaskForm({...taskForm,priority:e.target.value})} className="topbar-select dark-select" style={{fontSize:11}}>
                    <option value="low">🟢 Низкий</option>
                    <option value="medium">🟡 Средний</option>
                    <option value="high">🟠 Высокий</option>
                    <option value="urgent">🔴 Срочный</option>
                  </select>
                  <select value={taskForm.category} onChange={e=>setTaskForm({...taskForm,category:e.target.value})} className="topbar-select dark-select" style={{fontSize:11}}>
                    <option value="general">📋 Общее</option>
                    <option value="work">💼 Работа</option>
                    <option value="personal">👤 Личное</option>
                    <option value="study">📚 Учёба</option>
                    <option value="project">🛠 Проект</option>
                    <option value="idea">💡 Идея</option>
                  </select>
                  <input type="date" value={taskForm.due_date||""} onChange={e=>setTaskForm({...taskForm,due_date:e.target.value})} className="rename-input" style={{fontSize:11,padding:"4px 8px"}}/>
                </div>
                <div style={{display:"flex",gap:6}}>
                  <button className="soft-btn" style={{fontSize:11,padding:"4px 14px",background:"var(--accent)",color:"#fff",border:"none",borderRadius:6}} onClick={async()=>{
                    if(!taskForm.title) return;
                    try {
                      if(editingTask) {
                        await fetch(`/api/tasks/update/${editingTask}`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(taskForm)});
                        setEditingTask(null);
                      } else {
                        await fetch("/api/tasks/create",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(taskForm)});
                      }
                      setTaskForm({title:"",description:"",category:"general",priority:"medium",due_date:""});
                      loadTasks();
                    } catch{}
                  }}>{editingTask ? "Сохранить" : "Создать"}</button>
                  {editingTask && <button className="soft-btn" style={{fontSize:11,padding:"4px 10px",border:"1px solid var(--border)",borderRadius:6}} onClick={()=>{setEditingTask(null);setTaskForm({title:"",description:"",category:"general",priority:"medium",due_date:""});}}>Отмена</button>}
                </div>
              </div>

              {/* Список задач */}
              {tasksList.length===0 && <div style={{fontSize:11,color:"var(--text-muted)",padding:"12px 0",textAlign:"center"}}>Нет задач</div>}
              {tasksList.map(t=>{
                const prioColor = {urgent:"#f44336",high:"#ff9800",medium:"#f5a623",low:"#4caf50"}[t.priority]||"var(--text-muted)";
                const prioIcon = {urgent:"🔴",high:"🟠",medium:"🟡",low:"🟢"}[t.priority]||"⚪";
                const catIcon = {general:"📋",work:"💼",personal:"👤",study:"📚",project:"🛠",idea:"💡"}[t.category]||"📋";
                const isOverdue = t.due_date && t.status!=="done" && t.status!=="cancelled" && new Date(t.due_date) < new Date();
                return (
                  <div key={t.id} style={{padding:"10px 12px",borderRadius:10,border:`1px solid ${isOverdue?"#f44336":"var(--border)"}`,background:"var(--bg-surface)",marginBottom:6,opacity:t.status==="done"||t.status==="cancelled"?0.6:1}}>
                    <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:4}}>
                      <div style={{display:"flex",alignItems:"center",gap:6,flex:1,minWidth:0}}>
                        <span style={{cursor:"pointer",fontSize:16}} title={t.status==="done"?"Вернуть":"Выполнено"} onClick={async()=>{
                          const newStatus = t.status==="done" ? "todo" : "done";
                          try{await fetch(`/api/tasks/update/${t.id}`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({status:newStatus})});loadTasks();}catch{}
                        }}>{t.status==="done"?"✅":"⬜"}</span>
                        <div style={{flex:1,minWidth:0}}>
                          <div style={{fontWeight:600,fontSize:12,color:"var(--text)",textDecoration:t.status==="done"?"line-through":"none",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{t.title}</div>
                          {t.description && <div style={{fontSize:10,color:"var(--text-muted)",marginTop:2,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{t.description}</div>}
                        </div>
                      </div>
                      <div style={{display:"flex",gap:3,flexShrink:0}}>
                        {t.status!=="done" && t.status!=="cancelled" && (
                          <button className="soft-btn" style={{fontSize:9,padding:"2px 6px"}} title="В работу" onClick={async()=>{
                            const newS = t.status==="in_progress"?"todo":"in_progress";
                            try{await fetch(`/api/tasks/update/${t.id}`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({status:newS})});loadTasks();}catch{}
                          }}>{t.status==="in_progress"?"⏸":"▶"}</button>
                        )}
                        <button className="soft-btn" style={{fontSize:9,padding:"2px 6px"}} title="Редактировать" onClick={()=>{setEditingTask(t.id);setTaskForm({title:t.title,description:t.description||"",category:t.category||"general",priority:t.priority||"medium",due_date:t.due_date||""});}}>✏️</button>
                        <button className="soft-btn" style={{fontSize:9,padding:"2px 6px",color:"#f44336"}} title="Удалить" onClick={async()=>{if(!confirm("Удалить задачу?"))return;try{await fetch(`/api/tasks/delete/${t.id}`,{method:"DELETE"});loadTasks();}catch{}}}>✕</button>
                      </div>
                    </div>
                    <div style={{display:"flex",gap:8,alignItems:"center",fontSize:10,color:"var(--text-muted)",marginTop:2}}>
                      <span>{prioIcon} {t.priority}</span>
                      <span>{catIcon} {t.category}</span>
                      {t.due_date && <span style={{color:isOverdue?"#f44336":"var(--text-muted)"}}>📅 {new Date(t.due_date).toLocaleDateString("ru-RU")}{isOverdue?" ⚠️ просрочено":""}</span>}
                      {t.status==="in_progress" && <span style={{color:"#f5a623"}}>⏳ в работе</span>}
                      {t.status==="done" && t.completed_at && <span style={{color:"#4caf50"}}>✅ {new Date(t.completed_at).toLocaleDateString("ru-RU")}</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : sideTab === "telegram" ? (
            <div className="settings-main-card" style={{overflow:"auto"}}>
              <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:12}}>
                <div style={{fontSize:15,fontWeight:700,color:"var(--text)"}}>✈️ Telegram Bot</div>
                <button className="soft-btn" style={{fontSize:10,padding:"3px 10px",border:"1px solid var(--border)"}} onClick={loadTelegram}>↻</button>
              </div>

              {/* Внутренние табы */}
              <div style={{display:"flex",gap:4,marginBottom:14}}>
                {[["setup","⚙ Настройка"],["users","👥 Пользователи"],["log","📜 Лог"],["guide","📖 Инструкция"]].map(([k,l])=>(
                  <button key={k} className="soft-btn" style={{fontSize:10,padding:"3px 10px",background:tgTab===k?"var(--accent)":"transparent",color:tgTab===k?"#fff":"var(--text)",border:"1px solid var(--border)",borderRadius:6}} onClick={()=>setTgTab(k)}>{l}</button>
                ))}
              </div>

              {tgTab === "guide" && (
                <div style={{fontSize:11,color:"var(--text)",lineHeight:1.7}}>
                  <div style={{fontSize:13,fontWeight:700,marginBottom:8,color:"var(--accent)"}}>📖 Как подключить Telegram-бота</div>

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
                    <div>1. Перейди на вкладку <b>⚙ Настройка</b> выше</div>
                    <div>2. Вставь токен в поле «Bot Token»</div>
                    <div>3. Нажми <b>💾 Сохранить токен</b></div>
                    <div>4. Нажми <b>🔍 Тест</b> — должно показать имя бота</div>
                  </div>

                  <div style={{padding:12,borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:10}}>
                    <div style={{fontWeight:700,marginBottom:6}}>Шаг 3: Запусти бота</div>
                    <div>1. Нажми <b>▶ Запустить бота</b></div>
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
                    💡 <b>Совет от @BotFather:</b> после создания бота отправь <code>/setdescription</code> и <code>/setuserpic</code> чтобы задать описание и аватарку.
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
                        <button className="soft-btn" style={{fontSize:10,padding:"4px 12px",background:"#4caf50",color:"#fff",border:"none",borderRadius:6}} onClick={async()=>{try{const r=await fetch("/api/telegram/start",{method:"POST"});const d=await r.json();if(d.ok){loadTelegram();}else{alert(d.error||"Ошибка запуска");}}catch{}}}>▶ Запустить</button>
                      ) : (
                        <button className="soft-btn" style={{fontSize:10,padding:"4px 12px",background:"#f44336",color:"#fff",border:"none",borderRadius:6}} onClick={async()=>{try{await fetch("/api/telegram/stop",{method:"POST"});loadTelegram();}catch{}}}>⏹ Остановить</button>
                      )}
                      <button className="soft-btn" style={{fontSize:10,padding:"4px 10px",border:"1px solid var(--border)"}} onClick={async()=>{try{const r=await fetch("/api/telegram/test");const d=await r.json();if(d.ok){alert(`✅ Бот: @${d.bot_username} (${d.bot_name})`)}else{alert(`❌ ${d.error}`)}}catch{alert("Ошибка соединения")}}}>🔍 Тест</button>
                    </div>
                  </div>

                  {/* Токен */}
                  <div style={{padding:12,borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:12}}>
                    <div style={{fontSize:12,fontWeight:600,marginBottom:6}}>🔑 Bot Token</div>
                    {tgConfig?.has_token && <div style={{fontSize:10,color:"var(--text-muted)",marginBottom:4}}>Текущий: {tgConfig.bot_token}</div>}
                    <div style={{display:"flex",gap:6}}>
                      <input type="password" placeholder="Вставь токен от @BotFather" value={tgTokenInput} onChange={e=>setTgTokenInput(e.target.value)} className="rename-input" style={{flex:1,fontSize:11,padding:"5px 8px"}}/>
                      <button className="soft-btn" style={{fontSize:10,padding:"4px 12px",background:"var(--accent)",color:"#fff",border:"none",borderRadius:6}} onClick={async()=>{if(!tgTokenInput.trim())return;try{await fetch("/api/telegram/config",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({bot_token:tgTokenInput.trim()})});setTgTokenInput("");loadTelegram();}catch{}}}>💾 Сохранить</button>
                    </div>
                  </div>

                  {/* Настройки бота */}
                  <div style={{padding:12,borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:12}}>
                    <div style={{fontSize:12,fontWeight:600,marginBottom:8}}>⚙ Параметры</div>
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
                        💾 Память
                      </label>
                      <label style={{fontSize:11,display:"flex",alignItems:"center",gap:4,cursor:"pointer"}}>
                        <input type="checkbox" checked={tgConfig?.use_web_search||false} onChange={e=>{setTgConfig({...tgConfig,use_web_search:e.target.checked})}}/>
                        🌐 Веб-поиск
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
                        await fetch("/api/telegram/config",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(upd)});
                        loadTelegram();
                      }catch{}
                    }}>💾 Сохранить настройки</button>
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
                        try{await fetch("/api/telegram/config",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({allowed_users:val})});loadTelegram();}catch{}
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
                        <span style={{fontSize:10,color:u.allowed?"#4caf50":"#f44336"}}>{u.allowed?"✅ Разрешён":"⛔ Заблокирован"}</span>
                        <button className="soft-btn" style={{fontSize:9,padding:"2px 8px"}} onClick={async()=>{try{await fetch("/api/telegram/users/toggle",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({chat_id:u.chat_id,allowed:!u.allowed})});loadTelegram();}catch{}}}>{u.allowed?"🔒":"🔓"}</button>
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
                <div style={{fontSize:15,fontWeight:700,color:"var(--text)"}}>🔄 Autopipelines</div>
                <button className="soft-btn" style={{fontSize:10,padding:"3px 10px",border:"1px solid var(--border)"}} onClick={loadPipelines}>↻ Обновить</button>
              </div>
              <div className="settings-desc" style={{marginBottom:12}}>Автоматические задачи по расписанию</div>

              {/* Форма создания */}
              <div style={{padding:12,borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:14}}>
                <div style={{fontSize:12,fontWeight:600,color:"var(--text)",marginBottom:8}}>+ Новый pipeline</div>
                <div style={{display:"flex",gap:6,flexWrap:"wrap",marginBottom:6}}>
                  <input placeholder="Название" value={pipeForm.name} onChange={e=>setPipeForm({...pipeForm,name:e.target.value})} className="rename-input" style={{flex:1,minWidth:120,fontSize:11,padding:"4px 8px"}}/>
                  <select value={pipeForm.task_type} onChange={e=>setPipeForm({...pipeForm,task_type:e.target.value})} className="topbar-select dark-select" style={{fontSize:11}}>
                    <option value="prompt">💬 Промпт</option>
                    <option value="web_search">🔍 Веб-поиск</option>
                    <option value="plugin">🔌 Плагин</option>
                    <option value="http">🌐 HTTP</option>
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
                <button className="soft-btn" style={{fontSize:11,padding:"4px 14px",background:"var(--accent)",color:"#fff",border:"none",borderRadius:6}} onClick={async()=>{if(!pipeForm.name)return;try{await fetch("/api/pipelines/create",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(pipeForm)});setPipeForm({name:"",task_type:"prompt",interval_minutes:60,task_data:{prompt:""}});loadPipelines()}catch{}}}>Создать</button>
              </div>

              {/* Список */}
              {pipelinesList.length===0 && <div style={{fontSize:11,color:"var(--text-muted)",padding:"12px 0",textAlign:"center"}}>Нет pipelines</div>}
              {pipelinesList.map(p=>(
                <div key={p.id} style={{padding:"10px 12px",borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:6}}>
                  <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:4}}>
                    <div>
                      <span style={{fontWeight:600,fontSize:12,color:"var(--text)"}}>{p.name}</span>
                      <span style={{fontSize:10,color:"var(--text-muted)",marginLeft:8}}>{p.task_type} • каждые {p.interval_minutes} мин</span>
                      <span style={{fontSize:9,color:p.enabled?"#4caf50":"#f44336",marginLeft:6}}>{p.enabled?"● вкл":"○ выкл"}</span>
                    </div>
                    <div style={{display:"flex",gap:4}}>
                      <button className="soft-btn" style={{fontSize:9,padding:"2px 8px"}} onClick={async()=>{try{await fetch(`/api/pipelines/run/${p.id}`,{method:"POST"});loadPipelines()}catch{}}}>▶ Run</button>
                      <button className="soft-btn" style={{fontSize:9,padding:"2px 8px"}} onClick={async()=>{try{await fetch(`/api/pipelines/update/${p.id}`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled:!p.enabled})});loadPipelines()}catch{}}}>{p.enabled?"⏸":"▶"}</button>
                      <button className="soft-btn" style={{fontSize:9,padding:"2px 8px",color:"#f44336"}} onClick={async()=>{if(!confirm("Удалить?"))return;try{await fetch(`/api/pipelines/delete/${p.id}`,{method:"DELETE"});loadPipelines()}catch{}}}>✕</button>
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
                <div style={{fontSize:15,fontWeight:700,color:"var(--text)"}}>📊 Dashboard</div>
                <button className="soft-btn" style={{fontSize:10,padding:"3px 10px",border:"1px solid var(--border)"}} onClick={loadDashboard}>↻ Обновить</button>
              </div>
              {!dashData ? <div style={{color:"var(--text-muted)",fontSize:12}}>Загрузка...</div> : (
                <>
                  {/* Карточки статистики */}
                  <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(130px,1fr))",gap:8,marginBottom:16}}>
                    {[
                      {label:"Запросов",value:dashData.total_runs||0,icon:"💬"},
                      {label:"Сегодня",value:dashData.today||0,icon:"📅"},
                      {label:"За неделю",value:dashData.this_week||0,icon:"📆"},
                      {label:"Успешность",value:`${dashData.success_rate||0}%`,icon:"✅"},
                      {label:"Чатов",value:dashData.chats||0,icon:"💭"},
                      {label:"Сообщений",value:dashData.messages||0,icon:"📨"},
                      {label:"Ср. длина",value:dashData.avg_answer_length||0,icon:"📏"},
                      {label:"Плагинов",value:dashData.plugins||0,icon:"🔌"},
                    ].map(s=>(
                      <div key={s.label} style={{padding:"12px",borderRadius:10,border:"1px solid var(--border)",background:"var(--bg-surface)",textAlign:"center"}}>
                        <div style={{fontSize:20,marginBottom:4}}>{s.icon}</div>
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
                    <button onClick={()=>setTheme("dark")} style={{flex:1,padding:"6px 12px",borderRadius:6,border:"1px solid "+(theme==="dark"?"var(--accent)":"var(--border)"),background:theme==="dark"?"var(--accent-dim)":"transparent",color:"var(--text-primary)",cursor:"pointer",fontSize:12}}>🌙 Тёмная</button>
                    <button onClick={()=>setTheme("light")} style={{flex:1,padding:"6px 12px",borderRadius:6,border:"1px solid "+(theme==="light"?"var(--accent)":"var(--border)"),background:theme==="light"?"var(--accent-dim)":"transparent",color:"var(--text-primary)",cursor:"pointer",fontSize:12}}>☀️ Светлая</button>
                  </div>
                </div>
                <div className="settings-tile" style={{gridColumn:"1 / -1"}}>
                  <div className="settings-title">Оркестрация моделей</div>
                  <div className="settings-desc" style={{marginBottom:8}}>Какая модель отвечает за какой тип задачи. Первая в списке — приоритетная.</div>
                  {["code","project","research","chat"].map(route => {
                    const routeLabels = {code:"Код",project:"Проект",research:"Исследование",chat:"Чат"};
                    const routeDescs = {code:"Написание, review и отладка кода",project:"Работа с файлами проекта",research:"Поиск, анализ, факты",chat:"Обычные вопросы и диалог"};
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
              <div style={{marginTop:18}}><div className="settings-title" style={{marginBottom:8}}>Skills</div><div className="settings-desc" style={{marginBottom:10}}>Включи / выключи возможности</div>
                <div className="skills-grid">{SKILLS.map(s=><button key={s.id} className={`skill-chip ${skills.includes(s.id)?"active":""}`} onClick={()=>toggleSkill(s.id)} title={s.desc}>{s.label}</button>)}</div>
              </div>
              <div style={{marginTop:18}}>
                <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:8}}>
                  <div className="settings-title">Плагины</div>
                  <button className="soft-btn" style={{fontSize:10,padding:"3px 10px",border:"1px solid var(--border)"}} onClick={async()=>{try{const r=await fetch("/api/extra/plugins/reload",{method:"POST"});const d=await r.json();setPluginList(d.loaded?.map(n=>({name:n,enabled:true}))||[]);await loadPluginList()}catch{}}}>↻ Перезагрузить</button>
                </div>
                <div className="settings-desc" style={{marginBottom:10}}>Пользовательские .py скрипты в data/plugins/</div>
                {pluginList.length===0 && <div style={{fontSize:11,color:"var(--text-muted)",padding:"8px 0"}}>Плагинов нет. Положи .py файлы в backend/data/plugins/</div>}
                {pluginList.map(p=>(
                  <div key={p.name} style={{padding:"8px 10px",borderRadius:8,border:"1px solid var(--border)",background:"var(--bg-surface)",marginBottom:6,display:"flex",alignItems:"center",justifyContent:"space-between"}}>
                    <div>
                      <span style={{fontSize:14,marginRight:6}}>{p.icon||"🔌"}</span>
                      <span style={{fontWeight:600,fontSize:12}}>{p.name}</span>
                      <span style={{fontSize:10,color:"var(--text-muted)",marginLeft:8}}>{p.description||""}</span>
                      {p.version && <span style={{fontSize:9,color:"var(--text-muted)",marginLeft:6}}>v{p.version}</span>}
                    </div>
                    <button className={`skill-chip ${p.enabled?"active":""}`} style={{fontSize:10,padding:"2px 10px"}} onClick={async()=>{try{await fetch(`/api/extra/plugins/${p.enabled?"disable":"enable"}/${p.name}`,{method:"POST"});await loadPluginList()}catch{}}}>{p.enabled?"Вкл":"Выкл"}</button>
                  </div>
                ))}
              </div>
            </div>
          ) : sideTab === "library" ? (
            <div className="library-table-view">
              <div className={`upload-dropzone ${drag?"active":""}`} onDragOver={onDragOver} onDragLeave={onDragLeave} onDrop={onDrop} onClick={()=>fileRef.current?.click()}>Перетащи файлы (PDF, код, текст)</div>
              <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
                <div className="library-search-row" style={{flex:1}}><span className="library-search-icon">⌕</span><input value={libSearch} onChange={e=>setLibSearch(e.target.value)} placeholder="Поиск" className="library-search-input"/></div>
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
              {ctxF.length > 0 && <div className="context-bar"><div className="context-bar-title">📎 {ctxF.length} файлов доступно (упомяни «файл» или «документ»)</div><div className="context-tags">{ctxF.map(f=><span key={f.id} className="context-tag">{f.name}<button className="context-tag-remove" onClick={()=>toggleCtx(f.id,false)} title="Убрать из контекста">✕</button></span>)}</div></div>}
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
                    <span>📊 {chartData.valueLabel}</span>
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
                  <button className="send-btn" onClick={working ? handleStop : handleSend} style={working ? {background:"rgba(255,70,70,0.15)",borderColor:"rgba(255,70,70,0.3)",color:"#ff9090"} : undefined}>{working?"■":"➤"}</button>
                  <input ref={fileRef} type="file" multiple hidden onChange={e=>handleFiles(e.target.files)}/>
                </div>
                <div className="composer-selectors" style={{justifyContent:"center"}}>
                  <select value={model} onChange={e=>setModel(e.target.value)} className="composer-select">{(modelOpts?.length?modelOpts:[{name:model}]).map((i,idx)=>{const n=typeof i==="string"?i:(i.name||i.model||"model");return <option key={n+idx} value={n}>{shortModelName(n)}</option>})}</select>
                  <select value={profile} onChange={e=>setProfile(e.target.value)} className="composer-select">{Object.keys(PROFILE_DESCRIPTIONS).map(n=><option key={n} value={n}>{n}</option>)}</select>
                  <button onClick={() => setMultiAgent(p => !p)} style={{padding:"2px 10px",borderRadius:99,fontSize:10,border:"1px solid " + (multiAgent ? "rgba(244,114,182,0.4)" : "var(--border)"),background:multiAgent ? "rgba(244,114,182,0.12)" : "transparent",color:multiAgent ? "#f472b6" : "var(--text-muted)",cursor:"pointer"}}>{multiAgent ? "🤖 Multi" : "🤖"}</button>
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
