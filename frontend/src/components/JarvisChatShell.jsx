/**
 * JarvisChatShell.jsx — v3
 *
 * Фиксы:
 *   • Индикатор прогресса: "Поиск...", "Генерация...", "Проверка..."
 *   • Быстрее ощущается — фазы видны до первого токена
 *   • Code tab работает как артефакты Claude
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { api, executeStream } from "../api/ide";
import IdeWorkspaceShell from "./IdeWorkspaceShell";
import MarkdownRenderer from "./MarkdownRenderer";
import ArtifactPanel from "./ArtifactPanel";
import MemoryPanel from "./MemoryPanel";
import ProjectPanel from "./ProjectPanel";
import "../styles/markdown.css";

const LIBRARY_KEY = "jarvis_library_files_v7";
const CHAT_CONTEXT_KEY = "jarvis_chat_context_map_v7";
const MAX_HISTORY_PAIRS = 10;

const PROFILE_DESCRIPTIONS = {
  "Универсальный": "Ясный, структурированный и профессиональный тон.",
  "Программист": "Код, исправления, архитектура, рефакторинг.",
  "Оркестратор": "Планирование, multi-agent, пайплайны.",
  "Исследователь": "Факты, источники, web-поиск.",
  "Аналитик": "Выводы, риски, декомпозиция.",
  "Сократ": "Обучение через вопросы.",
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
];

// Tauri window controls
function loadJson(k, f) { try { return JSON.parse(localStorage.getItem(k) || JSON.stringify(f)); } catch { return f; } }
function saveJson(k, v) { localStorage.setItem(k, JSON.stringify(v)); }
function loadLibraryFiles() { return loadJson(LIBRARY_KEY, []); }
function saveLibraryFiles(i) { saveJson(LIBRARY_KEY, i); }
function loadChatContextMap() { return loadJson(CHAT_CONTEXT_KEY, {}); }
function saveChatContextMap(v) { saveJson(CHAT_CONTEXT_KEY, v); }
function makeId(p = "id") { return `${p}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`; }
function deriveChatTitle(t) { const c = String(t || "").trim().replace(/\s+/g, " "); return !c ? "Новый чат" : c.length > 28 ? `${c.slice(0, 28)}…` : c; }

function normalizeErrorMessage(e, fb = "Ошибка") {
  const v = e?.message ?? e?.detail ?? e;
  if (!v) return fb;
  if (typeof v === "string") return v;
  if (Array.isArray(v)) return v.map(i => normalizeErrorMessage(i, "")).filter(Boolean).join(" | ") || fb;
  if (typeof v === "object") return v.message || v.msg || JSON.stringify(v);
  return String(v);
}

async function fileToLibraryRecord(file) {
  let preview = "";
  const name = file.name || "";
  const ext = name.split(".").pop().toLowerCase();
  const API_URL = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

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

/** All library files with use_in_context AND preview go to LLM context */
function getActiveLibFiles(lib) { return lib.filter(i => (i.use_in_context !== false) && i.preview); }
function buildHistory(msgs) { if (!msgs?.length) return []; const p = msgs.filter(m => m.role === "user" || m.role === "assistant").map(m => ({ role: m.role, content: m.content || "" })); return p.length > MAX_HISTORY_PAIRS * 2 ? p.slice(-MAX_HISTORY_PAIRS * 2) : p; }


export default function JarvisChatShell() {
  const fileRef = useRef(null);
  const msgRef = useRef(null);
  const taRef = useRef(null);
  const streamRef = useRef(null);
  const initRef = useRef(false);

  const [mainTab, setMainTab] = useState("chat");
  const [sideTab, setSideTab] = useState("chats");
  const [model, setModel] = useState("qwen3:8b");
  const [modelOpts, setModelOpts] = useState([]);
  const [profile, setProfile] = useState("Универсальный");
  const [skills, setSkills] = useState(["web_search", "file_context", "memory", "pdf_reader"]);
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
  const [multiAgent, setMultiAgent] = useState(false);

  const API_URL = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

  useEffect(() => { init(); }, []);
  useEffect(() => { msgRef.current && (msgRef.current.scrollTop = msgRef.current.scrollHeight); }, [messages, chatId, streamText]);
  useEffect(() => { if (!taRef.current) return; taRef.current.style.height = "36px"; taRef.current.style.height = `${Math.min(120, taRef.current.scrollHeight)}px`; }, [input]);

  // Sync library from SQLite backend on mount (optional)
  useEffect(() => {
    fetch(`${API_URL}/api/lib/list`).then(r => { if (!r.ok) return null; return r.json(); }).then(d => {
      if (d?.ok && d.items?.length) {
        const merged = [...d.items.map(i => ({...i, id: `db-${i.id}`, source: "sqlite"})), ...libraryFiles.filter(f => f.source !== "sqlite")];
        const seen = new Set();
        const unique = merged.filter(f => { const k = f.name + f.size; if (seen.has(k)) return false; seen.add(k); return true; });
        setLibraryFiles(unique);
        saveLibraryFiles(unique);
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
      const [m, c] = await Promise.all([api.listOllamaModels(), api.listChats()]);
      const ml = Array.isArray(m?.models) ? m.models : Array.isArray(m) ? m : [];
      setModelOpts(ml);
      const pref = ml.find(i => (typeof i==="string"?i:(i.name||i.model||"")) === "qwen3:8b");
      setModel(pref ? (typeof pref==="string"?pref:(pref.name||pref.model||"qwen3:8b")) : ml.length ? (typeof ml[0]==="string"?ml[0]:(ml[0].name||ml[0].model||"qwen3:8b")) : "qwen3:8b");
      if (c?.length) { setChats(c); }
      // Всегда новый чат при запуске
      const n = await newChat(true); if (n?.id) setMessages([]);
    } catch (e) { setError(normalizeErrorMessage(e)); }
  }

  async function loadChats(sel = "") { setChats(await api.listChats() || []); if (sel) setChatId(sel); }
  async function newChat(silent = false) {
    try { setMessages([]); setInput(""); setRenaming(false); setStreamText(""); setStreaming(false); setPhase("");
      const c = await api.createChat({ title: "Новый чат", clean: true }); await loadChats(c.id); setChatId(c.id); setSideTab("chats"); if (!silent) setError(""); return c;
    } catch (e) { setError(normalizeErrorMessage(e)); return null; }
  }
  async function openChat(id) {
    try { streamRef.current?.abort(); setStreamText(""); setStreaming(false); setPhase(""); setChatId(id); setMessages(await api.getMessages({ chatId: id }) || []); setSideTab("chats"); setMainTab("chat"); setRenaming(false);
    } catch (e) { setError(normalizeErrorMessage(e)); }
  }
  async function renameActive() { const t = renameVal.trim(); if (!t || !chatId) return; try { await api.renameChat({ id: chatId, title: t }); await loadChats(chatId); setRenaming(false); } catch (e) { setError(normalizeErrorMessage(e)); } }
  async function autoRename(text) { const a = chats.find(c => c.id === chatId); if (!chatId || !a || (a.title && a.title !== "Новый чат")) return; try { await api.renameChat({ id: chatId, title: deriveChatTitle(text) }); await loadChats(chatId); } catch {} }

  async function handleSend() {
    const text = input.trim();
    if (!text || !chatId || working) return;
    try {
      setWorking(true); setStreaming(true); setStreamText(""); setError(""); setPhase("");
      const userMsg = await api.addMessage({ chatId, role: "user", content: text });
      setMessages(prev => [...prev, userMsg]); setInput(""); await autoRename(text);
      const history = buildHistory(messages);

      // Файлы библиотеки
      const cf = getActiveLibFiles(libraryFiles);
      const tl = text.toLowerCase();
      const wantsFiles = cf.length > 0 && (
        tl.includes("файл") || tl.includes("документ") || tl.includes("библиотек") ||
        tl.includes("загруженн") || tl.includes("прочитай") || tl.includes("опиши") ||
        tl.includes("file") || tl.includes("document") || tl.includes("pdf") ||
        tl.includes("резюме") || tl.includes("отчёт") || tl.includes("отчет") ||
        tl.includes("что в ") || tl.includes("покажи содержимое") || tl.includes("проанализируй")
      );
      let cp = wantsFiles ? "\n\nФайлы пользователя:\n" + cf.map(f => `=== ${f.name} ===\n${f.preview.slice(0, 1500)}`).join("\n\n") : "";

      // Контекст проекта (если открыт)
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

      // Multi-agent режим
      if (multiAgent) {
        setPhase("🤖 Multi-agent: Исследователь → Программист → Аналитик...");
        try {
          const resp = await fetch(`${API_URL}/api/advanced/multi-agent`, {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ query: `${text}${cp}`, model_name: model, context: "", agents: ["researcher","programmer","analyst"] }),
          });
          const data = await resp.json();
          const final = data.report || data.error || "Нет результата";
          try { await api.addMessage({ chatId, role: "assistant", content: final }); } catch {}
          setMessages(prev => [...prev, { id: `a-${Date.now()}`, role: "assistant", content: final }]);
          setStreamText(""); setStreaming(false); setWorking(false); setPhase("");
          return;
        } catch (e) { setError(e.message); setStreamText(""); setStreaming(false); setWorking(false); setPhase(""); return; }
      }

      // Обычный стриминг
      let fullText = "";
      const ctrl = executeStream(
        { model_name: model, profile_name: profile, user_input: `${text}${cp}`, history, use_memory: skills.includes("memory"), use_library: skills.includes("file_context"), use_reflection: skills.includes("reflection") },
        {
          onToken(t) { fullText += t; setStreamText(fullText); setPhase(""); },
          onPhase(ev) {
            if (ev.phase === "reflection_replace" && ev.full_text) { fullText = ev.full_text; setStreamText(fullText); }
            else if (ev.message) { setPhase(ev.message); }
          },
          async onDone({ full_text }) {
            const final = full_text || fullText;
            try { const p = await api.addMessage({ chatId, role: "assistant", content: final }); setMessages(prev => [...prev, p]); }
            catch { setMessages(prev => [...prev, { id: `a-${Date.now()}`, role: "assistant", content: final }]); }
            setStreamText(""); setStreaming(false); setWorking(false); setPhase(""); streamRef.current = null;
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
      try { const fd = new FormData(); fd.append("file", f); fd.append("use_in_context", "true"); await fetch(`${API_URL}/api/lib/add`, { method: "POST", body: fd }); } catch {}
    }
    const next = [...recs, ...libraryFiles]; setLibraryFiles(next); saveLibraryFiles(next); setSideTab("library"); setSelLibId(recs[0]?.id || "");
    if (chatId) { const map = loadChatContextMap(); map[chatId] = Array.from(new Set([...recs.map(r => r.id), ...(map[chatId] || [])])); saveChatContextMap(map); }
  }
  function onDrop(e) { e.preventDefault(); e.stopPropagation(); setDrag(false); handleFiles(e.dataTransfer.files); }
  function onDragOver(e) { e.preventDefault(); e.stopPropagation(); setDrag(true); }
  function onDragLeave(e) { e.preventDefault(); e.stopPropagation(); setDrag(false); }
  function removeLib(id) { const n = libraryFiles.filter(i => i.id !== id); setLibraryFiles(n); saveLibraryFiles(n); const m = loadChatContextMap(); saveChatContextMap(Object.fromEntries(Object.entries(m).map(([k,v]) => [k,(v||[]).filter(x=>x!==id)]))); if (selLibId === id) setSelLibId(n[0]?.id || ""); }
  function toggleCtx(id, on) { const n = libraryFiles.map(i => i.id === id ? {...i, use_in_context: on} : i); setLibraryFiles(n); saveLibraryFiles(n); if (!chatId) return; const m = loadChatContextMap(); const s = new Set(m[chatId]||[]); on ? s.add(id) : s.delete(id); m[chatId] = Array.from(s); saveChatContextMap(m); }
  function toggleSkill(id) { setSkills(p => p.includes(id) ? p.filter(s => s !== id) : [...p, id]); }
  function handleStop() {
    if (streamRef.current) { streamRef.current.abort(); streamRef.current = null; }
    if (streamText) {
      setMessages(prev => [...prev, { id: `a-${Date.now()}`, role: "assistant", content: streamText + "\n\n*[остановлено]*" }]);
      api.addMessage({ chatId, role: "assistant", content: streamText + "\n\n*[остановлено]*" }).catch(() => {});
    }
    setStreamText(""); setStreaming(false); setWorking(false); setPhase("");
  }

  function selectAllLib(on) {
    const next = libraryFiles.map(i => ({ ...i, use_in_context: on }));
    setLibraryFiles(next); saveLibraryFiles(next);
  }

  function handleKeyDown(e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); } }

  const fChats = useMemo(() => { const q = sideSearch.trim().toLowerCase(); return q ? chats.filter(c => (c.title||"").toLowerCase().includes(q)) : chats; }, [sideSearch, chats]);
  const pinned = useMemo(() => fChats.filter(c => c.pinned), [fChats]);
  const regular = useMemo(() => fChats.filter(c => !c.pinned), [fChats]);
  const memChats = useMemo(() => chats.filter(c => c.memory_saved), [chats]);
  const fLib = useMemo(() => { const q = libSearch.trim().toLowerCase(); return q ? libraryFiles.filter(i => `${i.name} ${i.preview||""}`.toLowerCase().includes(q)) : libraryFiles; }, [libSearch, libraryFiles]);
  const selLib = useMemo(() => libraryFiles.find(i => i.id === selLibId) || libraryFiles[0] || null, [libraryFiles, selLibId]);
  const ctxF = useMemo(() => getActiveLibFiles(libraryFiles), [libraryFiles]);

  if (mainTab === "code") return <IdeWorkspaceShell messages={messages} libraryFiles={libraryFiles} setLibraryFiles={setLibraryFiles} onBackToChat={() => setMainTab("chat")} />;

  return (
    <div className="jarvis-shell" style={showPanel && sideTab === "chats" ? {gridTemplateColumns: "200px 1fr auto"} : undefined}>
      <aside className="jarvis-sidebar">
        <button className="sidebar-newchat-btn" onClick={() => newChat(false)}>+ Новый чат</button>
        <div className="sidebar-nav">
          {[["chats","☰ Чаты"],["project","📂 Проекты"],["library","📚 Файлы"],["memory","★ Память"],["settings","⚙ Настройки"]].map(([k,l]) => (
            <button key={k} className={`sidebar-nav-item ${sideTab === k ? "active" : ""}`} onClick={() => setSideTab(k)}>{l}</button>
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
      </aside>

      <main className="jarvis-main">
        <div className="jarvis-topbar slim">
          <div className="jarvis-brand">Jarvis</div>
          <div className="topbar-tabs">
            <button className={`soft-btn ${mainTab==="chat"?"active":""}`} onClick={() => setMainTab("chat")}>Chat</button>
            <button className={`soft-btn ${mainTab==="code"?"active":""}`} onClick={() => setMainTab("code")}>Code</button>
            <button className={`soft-btn ${showPanel?"active":""}`} onClick={() => setShowPanel(p => !p)} title="Панель кода">◇</button>
          </div>
        </div>

        <div className="chat-page">
          <div className="chat-header-row">
            <div className="chat-page-title">{sideTab==="chats"&&"Чат"}{sideTab==="memory"&&"Память"}{sideTab==="settings"&&"Настройки"}{sideTab==="library"&&"Библиотека"}{sideTab==="project"&&"Проект"}</div>
            {sideTab === "chats" && chatId && (
              <div className="chat-header-actions icon-actions" style={{display:"flex"}}>
                <div className={`working-chip ${working?"active":""}`}>{working ? (phase || "⏳ Работает") : "○ Готов"}</div>
                <button className="soft-btn icon-btn" onClick={() => saveToMemory(chatId, chats.find(c=>c.id===chatId)?.memory_saved)}>🧠</button>
                <button className="soft-btn icon-btn" onClick={() => pinChat(chatId, chats.find(c=>c.id===chatId)?.pinned)}>📌</button>
                <button className="soft-btn icon-btn" onClick={() => { setRenaming(true); setRenameVal(chats.find(c=>c.id===chatId)?.title||""); }}>✎</button>
                <button className="soft-btn icon-btn" onClick={() => deleteChat(chatId)}>🗑</button>
              </div>
            )}
          </div>

          {renaming && sideTab==="chats" && <div className="rename-bar"><input value={renameVal} onChange={e=>setRenameVal(e.target.value)} className="rename-input wide" placeholder="Название"/><button className="mini-btn" onClick={renameActive}>OK</button></div>}

          {sideTab === "settings" ? (
            <div className="settings-main-card">
              <div className="settings-tile-grid">
                <div className="settings-tile"><div className="settings-title">Модель</div><select value={model} onChange={e=>setModel(e.target.value)} className="topbar-select full dark-select">{(modelOpts?.length?modelOpts:[{name:model}]).map((i,idx)=>{const n=typeof i==="string"?i:(i.name||i.model||"model");return <option key={n+idx} value={n}>{n}</option>})}</select></div>
                <div className="settings-tile"><div className="settings-title">Профиль</div><select value={profile} onChange={e=>setProfile(e.target.value)} className="topbar-select full dark-select">{Object.keys(PROFILE_DESCRIPTIONS).map(n=><option key={n} value={n}>{n}</option>)}</select><div className="settings-desc">{PROFILE_DESCRIPTIONS[profile]}</div></div>
              </div>
              <div style={{marginTop:18}}><div className="settings-title" style={{marginBottom:8}}>Skills</div><div className="settings-desc" style={{marginBottom:10}}>Включи / выключи возможности</div>
                <div className="skills-grid">{SKILLS.map(s=><button key={s.id} className={`skill-chip ${skills.includes(s.id)?"active":""}`} onClick={()=>toggleSkill(s.id)} title={s.desc}>{s.label}</button>)}</div>
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
              <input ref={fileRef} type="file" multiple hidden onChange={e=>handleFiles(e.target.files)}/>
              <div className="library-table">
                <div className="library-table-row header"><div>Имя</div><div>Тип</div><div>Размер</div><div>Контекст</div><div></div></div>
                {fLib.length ? fLib.map(i => <div key={i.id} className={`library-table-row ${selLibId===i.id?"active":""}`} onClick={()=>setSelLibId(i.id)}><div className="table-name">{i.name}</div><div>{i.type.split("/").pop()}</div><div>{Math.round(i.size/1024)||0}K</div><div><input type="checkbox" checked={i.use_in_context !== false} onChange={e=>{e.stopPropagation();toggleCtx(i.id,e.target.checked);}}/></div><div><button className="mini-icon-btn" onClick={e=>{e.stopPropagation();removeLib(i.id);}}>✕</button></div></div>) : <div className="sidebar-empty" style={{padding:10}}>Нет файлов</div>}
              </div>
              {selLib && <div className="content-card"><div className="content-card-title">{selLib.name}</div><div className="content-card-text">{selLib.type} · {Math.round(selLib.size/1024)||0} KB</div>{selLib.preview ? <pre className="library-preview">{selLib.preview}</pre> : <div className="content-card-text" style={{marginTop:6}}>Превью недоступно</div>}</div>}
            </div>
          ) : sideTab === "memory" ? (
            <MemoryPanel />
          ) : sideTab === "project" ? (
            <ProjectPanel />
          ) : (
            <>
              {ctxF.length > 0 && <div className="context-bar"><div className="context-bar-title">📎 {ctxF.length} файлов доступно (упомяни «файл» или «документ»)</div><div className="context-tags">{ctxF.map(f=><span key={f.id} className="context-tag">{f.name}</span>)}</div></div>}
              {messages.length === 0 && !streaming && <div style={{flex:1,display:"flex",alignItems:"center",justifyContent:"center"}}><div style={{textAlign:"center",color:"var(--text-muted)"}}><div style={{fontSize:28,marginBottom:8,opacity:0.2}}>✺</div><div style={{fontSize:14}}>Чем могу помочь?</div></div></div>}

              <div className="message-stream compact-stream" ref={msgRef}>
                {messages.map(msg => (
                  <div key={msg.id} className={`message-row ${msg.role}`}>
                    <div className="message-bubble smaller-text">{msg.role === "assistant" ? <MarkdownRenderer content={msg.content}/> : msg.content}</div>
                  </div>
                ))}
                {streaming && streamText && <div className="message-row assistant"><div className="message-bubble smaller-text streaming-cursor"><MarkdownRenderer content={streamText}/></div></div>}
                {streaming && !streamText && <div className="message-row assistant"><div className="message-bubble smaller-text phase-indicator">{phase || "Jarvis думает..."}</div></div>}
              </div>

              {error && <div className="error-banner smaller-text">{error}</div>}

              <div className="composer-wrap" onDragOver={onDragOver} onDragLeave={onDragLeave} onDrop={onDrop}>
                <div className={`chat-input-shell ${drag?"drag-active":""}`}>
                  <button className="input-plus-btn" onClick={()=>fileRef.current?.click()}>+</button>
                  <textarea ref={taRef} value={input} onChange={e=>setInput(e.target.value)} onKeyDown={handleKeyDown} placeholder="Напиши сообщение..." className="chat-textarea"/>
                  <button className="send-btn" onClick={working ? handleStop : handleSend} style={working ? {background:"rgba(255,70,70,0.15)",borderColor:"rgba(255,70,70,0.3)",color:"#ff9090"} : undefined}>{working?"■":"➤"}</button>
                  <input ref={fileRef} type="file" multiple hidden onChange={e=>handleFiles(e.target.files)}/>
                </div>
                <div className="composer-selectors">
                  <select value={model} onChange={e=>setModel(e.target.value)} className="composer-select">{(modelOpts?.length?modelOpts:[{name:model}]).map((i,idx)=>{const n=typeof i==="string"?i:(i.name||i.model||"model");return <option key={n+idx} value={n}>{n}</option>})}</select>
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
