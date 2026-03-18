
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/ide";
import IdeWorkspaceShell from "./IdeWorkspaceShell";

const LIBRARY_KEY = "jarvis_library_files_v7";
const CHAT_CONTEXT_KEY = "jarvis_chat_context_map_v7";

const PROFILE_DESCRIPTIONS = {
  "Универсальный": "Обычные ответы без лишней глубины. Подходит для повседневной работы.",
  "Программист": "Код, исправления, архитектура, реализация, рефакторинг и технические решения.",
  "Оркестратор": "Планирование, orchestration, multi-agent сценарии, маршруты работы и backend-пайплайны.",
  "Исследователь": "Факты, анализ источников, сравнения, изучение темы и web-поиск с опорой на ресурсы.",
  "Аналитик": "Выводы, риски, структура, декомпозиция, сравнение вариантов и принятие решений.",
  "Сократ": "Обучение через вопросы, постепенное раскрытие темы и сопровождение в рассуждении.",
};

function loadJson(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem(key) || JSON.stringify(fallback));
  } catch {
    return fallback;
  }
}

function saveJson(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

function loadLibraryFiles() {
  return loadJson(LIBRARY_KEY, []);
}

function saveLibraryFiles(items) {
  saveJson(LIBRARY_KEY, items);
}

function loadChatContextMap() {
  return loadJson(CHAT_CONTEXT_KEY, {});
}

function saveChatContextMap(value) {
  saveJson(CHAT_CONTEXT_KEY, value);
}

function makeId(prefix = "id") {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function deriveChatTitle(text) {
  const clean = String(text || "").trim().replace(/\s+/g, " ");
  if (!clean) return "Новый чат";
  return clean.length > 28 ? `${clean.slice(0, 28)}…` : clean;
}

async function fileToLibraryRecord(file) {
  let textPreview = "";
  const isTextLike =
    file.type.startsWith("text/") ||
    file.name.match(/\.(txt|md|json|js|jsx|ts|tsx|py|css|html|yml|yaml|xml|csv|log|ini|toml)$/i);

  if (isTextLike) {
    try {
      const text = await file.text();
      textPreview = text.slice(0, 12000);
    } catch {
      textPreview = "";
    }
  }

  return {
    id: makeId("lib"),
    name: file.name,
    size: file.size,
    type: file.type || "unknown",
    uploaded_at: new Date().toISOString(),
    preview: textPreview,
    use_in_context: true,
    source: "chat-upload",
  };
}

function getContextFilesForChat(chatId, libraryFiles) {
  if (!chatId) return [];
  const map = loadChatContextMap();
  const ids = map[chatId] || [];
  return libraryFiles.filter((item) => ids.includes(item.id));
}

export default function JarvisChatShell() {
  const fileInputRef = useRef(null);
  const messageStreamRef = useRef(null);
  const textareaRef = useRef(null);

  const [activeMainTab, setActiveMainTab] = useState("chat");
  const [activeSidebarTab, setActiveSidebarTab] = useState("chats");

  const [selectedModel, setSelectedModel] = useState("qwen3:8b");
  const [modelOptions, setModelOptions] = useState([]);
  const [profile, setProfile] = useState("Универсальный");
  const [contextWindow, setContextWindow] = useState("4096");

  const [chats, setChats] = useState([]);
  const [activeChatId, setActiveChatId] = useState("");
  const [messages, setMessages] = useState([]);
  const [inputValue, setInputValue] = useState("");
  const [sidebarSearch, setSidebarSearch] = useState("");
  const [librarySearch, setLibrarySearch] = useState("");
  const [errorText, setErrorText] = useState("");
  const [dragActive, setDragActive] = useState(false);
  const [isAgentWorking, setIsAgentWorking] = useState(false);

  const [libraryFiles, setLibraryFiles] = useState(loadLibraryFiles());
  const [selectedLibraryId, setSelectedLibraryId] = useState("");
  const [renameMode, setRenameMode] = useState(false);
  const [renameValue, setRenameValue] = useState("");

  useEffect(() => {
    init();
  }, []);

  useEffect(() => {
    if (messageStreamRef.current) {
      messageStreamRef.current.scrollTop = messageStreamRef.current.scrollHeight;
    }
  }, [messages, activeChatId]);

  useEffect(() => {
    if (!textareaRef.current) return;
    textareaRef.current.style.height = "44px";
    const next = Math.min(110, textareaRef.current.scrollHeight);
    textareaRef.current.style.height = `${next}px`;
  }, [inputValue]);

  async function init() {
    try {
      const [models, chatItems] = await Promise.all([
        api.listOllamaModels(),
        api.listChats(),
      ]);

      const normalizedModels = Array.isArray(models?.models)
        ? models.models
        : Array.isArray(models)
        ? models
        : [];
      setModelOptions(normalizedModels);
      if (normalizedModels.length) {
        setSelectedModel(normalizedModels[0].name || normalizedModels[0]);
      }

      if (chatItems?.length) {
        setChats(chatItems);
        const firstId = chatItems[0].id;
        setActiveChatId(firstId);
        const firstMessages = await api.getMessages({ chatId: firstId });
        setMessages(Array.isArray(firstMessages) ? firstMessages : []);
      } else {
        const created = await handleNewChat(true);
        if (created?.id) setMessages([]);
      }
    } catch (e) {
      setErrorText(e.message || "Ошибка инициализации");
    }
  }

  async function loadChats(selectId = "") {
    const items = await api.listChats();
    setChats(items || []);
    if (selectId) setActiveChatId(selectId);
  }

  async function handleNewChat(silent = false) {
    try {
      setMessages([]);
      setInputValue("");
      setRenameMode(false);
      setRenameValue("");
      const created = await api.createChat({ title: "Новый чат", clean: true });
      await loadChats(created.id);
      setActiveChatId(created.id);
      setMessages([]);
      setActiveSidebarTab("chats");
      if (!silent) setErrorText("");
      return created;
    } catch (e) {
      setErrorText(e.message || "Ошибка создания чата");
      return null;
    }
  }

  async function openChat(chatId) {
    try {
      setActiveChatId(chatId);
      const chatMessages = await api.getMessages({ chatId });
      setMessages(Array.isArray(chatMessages) ? chatMessages : []);
      setActiveSidebarTab("chats");
      setActiveMainTab("chat");
      setRenameMode(false);
      setRenameValue("");
    } catch (e) {
      setErrorText(e.message || "Ошибка открытия чата");
    }
  }

  async function renameActiveChat() {
    const title = renameValue.trim();
    if (!title || !activeChatId) return;
    try {
      await api.renameChat({ id: activeChatId, title });
      await loadChats(activeChatId);
      setRenameMode(false);
      setRenameValue("");
    } catch (e) {
      setErrorText(e.message || "Ошибка переименования чата");
    }
  }

  async function autoRenameChat(firstUserMessage) {
    const active = chats.find((item) => item.id === activeChatId);
    if (!activeChatId || !active) return;
    if (active.title && active.title !== "Новый чат") return;
    const nextTitle = deriveChatTitle(firstUserMessage);
    if (!nextTitle) return;
    try {
      await api.renameChat({ id: activeChatId, title: nextTitle });
      await loadChats(activeChatId);
    } catch {}
  }

  async function handleSend() {
    const text = inputValue.trim();
    if (!text || !activeChatId) return;

    try {
      setIsAgentWorking(true);
      setErrorText("");

      const userMsg = await api.addMessage({
        chatId: activeChatId,
        role: "user",
        content: text,
      });

      const nextMessages = [...messages, userMsg];
      setMessages(nextMessages);
      setInputValue("");
      await autoRenameChat(text);

      const contextFiles = getContextFilesForChat(activeChatId, libraryFiles).filter((item) => item.use_in_context);
      const contextPrefix = contextFiles.length
        ? "\n\nКонтекст из библиотеки:\n" +
          contextFiles.map((f) => `- ${f.name}${f.preview ? `: ${f.preview.slice(0, 1200)}` : ""}`).join("\n")
        : "";

      const assistantMsg = await api.execute({
        chatId: activeChatId,
        message: `${text}${contextPrefix}`,
        mode:
          profile === "Оркестратор" ? "orchestrator" :
          profile === "Исследователь" || profile === "Аналитик" ? "research" :
          profile === "Программист" ? "code" : "chat",
        model: selectedModel,
        profile_name: profile,
      });

      const persistedAssistant = await api.addMessage({
        chatId: activeChatId,
        role: "assistant",
        content: assistantMsg?.content || "",
      });

      setMessages((prev) => [...prev, persistedAssistant]);
    } catch (e) {
      setErrorText(e.message || "Ошибка отправки сообщения");
    } finally {
      setIsAgentWorking(false);
    }
  }

  async function handleDeleteChat(id) {
    try {
      await api.deleteChat({ id });
      const next = chats.filter((item) => item.id !== id);
      setChats(next);
      if (activeChatId === id) {
        if (next.length) {
          await openChat(next[0].id);
        } else {
          const created = await handleNewChat(true);
          if (created?.id) await openChat(created.id);
        }
      }
    } catch (e) {
      setErrorText(e.message || "Ошибка удаления чата");
    }
  }

  async function handlePinChat(id, pinned) {
    try {
      await api.pinChat({ id, pinned: !pinned });
      await loadChats(activeChatId);
    } catch (e) {
      setErrorText(e.message || "Ошибка закрепления");
    }
  }

  async function handleSaveToMemory(id, saved) {
    try {
      await api.saveChatToMemory({ id, saved: !saved });
      await loadChats(activeChatId);
    } catch (e) {
      setErrorText(e.message || "Ошибка памяти");
    }
  }

  async function handleFilesSelected(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;

    const records = [];
    for (const file of files) {
      records.push(await fileToLibraryRecord(file));
    }

    const next = [...records, ...libraryFiles];
    setLibraryFiles(next);
    saveLibraryFiles(next);
    setActiveSidebarTab("library");
    setSelectedLibraryId(records[0]?.id || "");

    if (activeChatId) {
      const map = loadChatContextMap();
      const currentIds = map[activeChatId] || [];
      map[activeChatId] = Array.from(new Set([...records.map((r) => r.id), ...currentIds]));
      saveChatContextMap(map);
    }
  }

  function onDrop(e) {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    handleFilesSelected(e.dataTransfer.files);
  }

  function onDragOver(e) {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(true);
  }

  function onDragLeave(e) {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
  }

  function removeLibraryItem(id) {
    const next = libraryFiles.filter((item) => item.id !== id);
    setLibraryFiles(next);
    saveLibraryFiles(next);
    const map = loadChatContextMap();
    const updated = Object.fromEntries(
      Object.entries(map).map(([chatId, ids]) => [chatId, (ids || []).filter((v) => v !== id)])
    );
    saveChatContextMap(updated);
    if (selectedLibraryId === id) setSelectedLibraryId(next[0]?.id || "");
  }

  function toggleLibraryContext(fileId, checked) {
    const next = libraryFiles.map((item) =>
      item.id === fileId ? { ...item, use_in_context: checked } : item
    );
    setLibraryFiles(next);
    saveLibraryFiles(next);

    if (!activeChatId) return;
    const map = loadChatContextMap();
    const currentIds = new Set(map[activeChatId] || []);
    if (checked) currentIds.add(fileId);
    else currentIds.delete(fileId);
    map[activeChatId] = Array.from(currentIds);
    saveChatContextMap(map);
  }

  const filteredChats = useMemo(() => {
    const q = sidebarSearch.trim().toLowerCase();
    if (!q) return chats;
    return chats.filter((item) => (item.title || "").toLowerCase().includes(q));
  }, [sidebarSearch, chats]);

  const pinnedChats = useMemo(() => filteredChats.filter((item) => item.pinned), [filteredChats]);
  const regularChats = useMemo(() => filteredChats.filter((item) => !item.pinned), [filteredChats]);
  const memoryChats = useMemo(() => chats.filter((item) => item.memory_saved), [chats]);

  const filteredLibraryFiles = useMemo(() => {
    const q = librarySearch.trim().toLowerCase();
    if (!q) return libraryFiles;
    return libraryFiles.filter((item) => {
      const text = `${item.name} ${item.preview || ""}`.toLowerCase();
      return text.includes(q);
    });
  }, [librarySearch, libraryFiles]);

  const selectedLibraryItem = useMemo(
    () => libraryFiles.find((item) => item.id === selectedLibraryId) || libraryFiles[0] || null,
    [libraryFiles, selectedLibraryId]
  );

  const currentContextFiles = useMemo(
    () => (activeChatId ? getContextFilesForChat(activeChatId, libraryFiles).filter((f) => f.use_in_context) : []),
    [activeChatId, libraryFiles]
  );

  if (activeMainTab === "code") {
    return <IdeWorkspaceShell onBackToChat={() => setActiveMainTab("chat")} />;
  }

  return (
    <div className="jarvis-shell">
      <aside className="jarvis-sidebar">
        <button className="sidebar-newchat-btn" onClick={() => handleNewChat(false)}>
          + Новый чат
        </button>

        <div className="sidebar-nav">
          <button className={`sidebar-nav-item ${activeSidebarTab === "chats" ? "active" : ""}`} onClick={() => setActiveSidebarTab("chats")}>☰ Чаты</button>
          <button className={`sidebar-nav-item ${activeSidebarTab === "memory" ? "active" : ""}`} onClick={() => setActiveSidebarTab("memory")}>★ Память</button>
          <button className={`sidebar-nav-item ${activeSidebarTab === "projects" ? "active" : ""}`} onClick={() => setActiveSidebarTab("projects")}>▣ Проекты</button>
          <button className={`sidebar-nav-item ${activeSidebarTab === "settings" ? "active" : ""}`} onClick={() => setActiveSidebarTab("settings")}>⚙ Настройки</button>
          <button className={`sidebar-nav-item ${activeSidebarTab === "library" ? "active" : ""}`} onClick={() => setActiveSidebarTab("library")}>📚 Библиотека</button>
        </div>

        <div className="sidebar-nav-item search-shell">
          <span>⌕</span>
          <input
            className="sidebar-search-input"
            value={sidebarSearch}
            onChange={(e) => setSidebarSearch(e.target.value)}
            placeholder="Поиск"
          />
        </div>

        {activeSidebarTab === "chats" && (
          <>
            <div className="sidebar-section-title">Закреплённые</div>
            <div className="chat-list">
              {pinnedChats.length ? pinnedChats.map((chat) => (
                <button key={chat.id} className={`chat-list-item simple ${activeChatId === chat.id ? "active" : ""}`} onClick={() => openChat(chat.id)}>
                  <span className="chat-list-title truncate">{chat.title || "Новый чат"}</span>
                </button>
              )) : <div className="sidebar-empty">Здесь пока пусто.</div>}
            </div>

            <div className="sidebar-section-title">Все чаты</div>
            <div className="chat-list">
              {regularChats.length ? regularChats.map((chat) => (
                <button key={chat.id} className={`chat-list-item simple ${activeChatId === chat.id ? "active" : ""}`} onClick={() => openChat(chat.id)}>
                  <span className="chat-list-title truncate">{chat.title || "Новый чат"}</span>
                </button>
              )) : <div className="sidebar-empty">Здесь пока пусто.</div>}
            </div>
          </>
        )}

        {activeSidebarTab === "memory" && (
          <>
            <div className="sidebar-section-title">Память</div>
            <div className="chat-list">
              {memoryChats.length ? memoryChats.map((chat) => (
                <button key={chat.id} className={`chat-list-item simple ${activeChatId === chat.id ? "active" : ""}`} onClick={() => openChat(chat.id)}>
                  <span className="chat-list-title truncate">{chat.title || "Новый чат"}</span>
                </button>
              )) : <div className="sidebar-empty">Сохранённых чатов пока нет.</div>}
            </div>
          </>
        )}

        {activeSidebarTab === "projects" && (
          <div className="sidebar-empty">Для работы с репозиторием используй режим Code.</div>
        )}

        {activeSidebarTab === "settings" && (
          <div className="sidebar-empty">Настройки перенесены в центральное окно.</div>
        )}

        {activeSidebarTab === "library" && (
          <div className="sidebar-empty">Поиск и таблица библиотеки находятся в центральном окне.</div>
        )}
      </aside>

      <main className="jarvis-main">
        <div className="jarvis-topbar slim">
          <div className="jarvis-brand">Jarvis</div>

          <div className="topbar-status">
            <div className="status-chip">Профиль: {profile}</div>
            <div className="status-chip">Модель: {selectedModel}</div>
          </div>

          <div className="topbar-tabs">
            <button className={`soft-btn ${activeMainTab === "chat" ? "active" : ""}`} onClick={() => setActiveMainTab("chat")}>
              Chat
            </button>
            <button className={`soft-btn ${activeMainTab === "code" ? "active" : ""}`} onClick={() => setActiveMainTab("code")}>
              Code
            </button>
          </div>
        </div>

        <div className="chat-page">
          <div className="chat-header-row">
            <div className="chat-page-title">
              {activeSidebarTab === "chats" && "Чаты"}
              {activeSidebarTab === "memory" && "Память"}
              {activeSidebarTab === "projects" && "Проекты"}
              {activeSidebarTab === "settings" && "Настройки"}
              {activeSidebarTab === "library" && "Библиотека"}
            </div>

            {activeSidebarTab === "chats" && activeChatId && (
              <div className="chat-header-actions icon-actions">
                <div className={`working-chip ${isAgentWorking ? "active" : ""}`} title="Агент думает и работает">
                  {isAgentWorking ? "⏳ Агент работает" : "○ Агент готов"}
                </div>
                <button className="soft-btn icon-btn" title="Сохранить в памяти" onClick={() => handleSaveToMemory(activeChatId, chats.find((c) => c.id === activeChatId)?.memory_saved)}>
                  🧠
                </button>
                <button className="soft-btn icon-btn" title="Закрепить в памяти" onClick={() => handlePinChat(activeChatId, chats.find((c) => c.id === activeChatId)?.pinned)}>
                  📌
                </button>
                <button className="soft-btn icon-btn" title="Переименовать чат" onClick={() => {
                  const current = chats.find((c) => c.id === activeChatId);
                  setRenameMode(true);
                  setRenameValue(current?.title || "");
                }}>
                  ✎
                </button>
                <button className="soft-btn icon-btn" title="Удалить чат" onClick={() => handleDeleteChat(activeChatId)}>
                  🗑
                </button>
              </div>
            )}
          </div>

                    {renameMode && activeSidebarTab === "chats" ? (
            <div className="rename-bar">
              <input
                value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
                className="rename-input wide"
                placeholder="Новое название чата"
              />
              <button className="mini-btn" onClick={renameActiveChat}>Сохранить</button>
            </div>
          ) : null}

          {activeSidebarTab === "settings" ? (
            <div className="settings-main-card">
              <div className="settings-tile-grid">
                <div className="settings-tile">
                  <div className="settings-title">Модель</div>
                  <select value={selectedModel} onChange={(e) => setSelectedModel(e.target.value)} className="topbar-select full dark-select">
                    {(modelOptions?.length ? modelOptions : [{ name: selectedModel }]).map((item) => (
                      <option key={item.name || item} value={item.name || item}>
                        {item.name || item}
                      </option>
                    ))}
                  </select>
                  <div className="settings-desc">Выбирает активную модель Ollama для ответов.</div>
                </div>

                <div className="settings-tile">
                  <div className="settings-title">Профиль</div>
                  <select value={profile} onChange={(e) => setProfile(e.target.value)} className="topbar-select full dark-select">
                    {Object.keys(PROFILE_DESCRIPTIONS).map((name) => (
                      <option key={name} value={name}>{name}</option>
                    ))}
                  </select>
                  <div className="settings-desc">{PROFILE_DESCRIPTIONS[profile]}</div>
                </div>

                <div className="settings-tile">
                  <div className="settings-title">Контекст</div>
                  <select value={contextWindow} onChange={(e) => setContextWindow(e.target.value)} className="topbar-select full dark-select">
                    {[4096, 8192, 16384, 32768, 65536, 131072, 262144].map((v) => (
                      <option key={v} value={String(v)}>{Math.round(v / 1024)}k</option>
                    ))}
                  </select>
                  <div className="settings-desc">Определяет объём рабочего контекста модели.</div>
                </div>
              </div>

              <div className="settings-description-list">
                <div className="content-card-title">Описание профилей работы агента</div>
                <ul className="settings-list">
                  {Object.entries(PROFILE_DESCRIPTIONS).map(([name, text]) => (
                    <li key={name}><strong>{name}:</strong> {text}</li>
                  ))}
                </ul>
              </div>
            </div>
          ) : activeSidebarTab === "projects" ? (
            <div className="content-card">
              <div className="content-card-title">Проекты</div>
              <div className="content-card-text">Для работы с репозиторием открой вкладку Code.</div>
            </div>
          ) : activeSidebarTab === "library" ? (
            <div className="library-table-view">
              <div
                className={`upload-dropzone ${dragActive ? "active" : ""}`}
                onDragOver={onDragOver}
                onDragLeave={onDragLeave}
                onDrop={onDrop}
                onClick={() => fileInputRef.current?.click()}
              >
                Перетащи файлы сюда или нажми для загрузки в библиотеку
              </div>

              <div className="library-search-row">
                <span className="library-search-icon">⌕</span>
                <input
                  value={librarySearch}
                  onChange={(e) => setLibrarySearch(e.target.value)}
                  placeholder="Поиск по файлам библиотеки"
                  className="library-search-input"
                />
              </div>

              <input
                ref={fileInputRef}
                type="file"
                multiple
                hidden
                onChange={(e) => handleFilesSelected(e.target.files)}
              />

              <div className="library-table">
                <div className="library-table-row header">
                  <div>Имя</div>
                  <div>Тип</div>
                  <div>Размер</div>
                  <div>В контексте</div>
                  <div>Удалить</div>
                </div>

                {filteredLibraryFiles.length ? filteredLibraryFiles.map((item) => (
                  <div
                    key={item.id}
                    className={`library-table-row ${selectedLibraryId === item.id ? "active" : ""}`}
                    onClick={() => setSelectedLibraryId(item.id)}
                  >
                    <div className="table-name">{item.name}</div>
                    <div>{item.type}</div>
                    <div>{Math.round(item.size / 1024) || 0} KB</div>
                    <div>
                      <input
                        type="checkbox"
                        checked={!!item.use_in_context}
                        onChange={(e) => {
                          e.stopPropagation();
                          toggleLibraryContext(item.id, e.target.checked);
                        }}
                      />
                    </div>
                    <div>
                      <button
                        className="mini-icon-btn"
                        onClick={(e) => {
                          e.stopPropagation();
                          removeLibraryItem(item.id);
                        }}
                      >
                        🗑
                      </button>
                    </div>
                  </div>
                )) : (
                  <div className="content-card">
                    <div className="content-card-text">Файлы не найдены.</div>
                  </div>
                )}
              </div>

              {selectedLibraryItem ? (
                <div className="content-card">
                  <div className="content-card-title">{selectedLibraryItem.name}</div>
                  <div className="content-card-text">
                    Тип: {selectedLibraryItem.type}<br />
                    Размер: {Math.round(selectedLibraryItem.size / 1024) || 0} KB
                  </div>
                  {selectedLibraryItem.preview ? (
                    <pre className="library-preview">{selectedLibraryItem.preview}</pre>
                  ) : (
                    <div className="content-card-text">Для этого файла доступен только мета-описатель.</div>
                  )}
                </div>
              ) : null}
            </div>
          ) : activeSidebarTab === "memory" ? (
            <div className="message-stream compact-stream" ref={messageStreamRef}>
              {memoryChats.length ? memoryChats.map((chat) => (
                <button key={chat.id} className="content-card content-card-button" onClick={() => openChat(chat.id)}>
                  <div className="content-card-title">{chat.title}</div>
                  <div className="content-card-text">Открыть сохранённый чат</div>
                </button>
              )) : <div className="content-card"><div className="content-card-text">Сохранённых чатов пока нет.</div></div>}
            </div>
          ) : (
            <>
              {currentContextFiles.length ? (
                <div className="context-bar">
                  <div className="context-bar-title">В контексте этого чата:</div>
                  <div className="context-tags">
                    {currentContextFiles.map((file) => (
                      <span key={file.id} className="context-tag">{file.name}</span>
                    ))}
                  </div>
                </div>
              ) : null}

              {messages.length === 0 && (
                <div className="assistant-greeting">
                  Jarvis готов. Начни новый чат или напиши сообщение.
                </div>
              )}

              <div className="message-stream compact-stream" ref={messageStreamRef}>
                {messages.map((msg) => (
                  <div key={msg.id} className={`message-row ${msg.role}`}>
                    <div className="message-bubble smaller-text">{msg.content}</div>
                  </div>
                ))}
              </div>

              {errorText ? <div className="error-banner smaller-text">{errorText}</div> : null}

              <div
                className={`chat-input-shell compact-input ${dragActive ? "drag-active" : ""}`}
                onDragOver={onDragOver}
                onDragLeave={onDragLeave}
                onDrop={onDrop}
              >
                <button className="input-plus-btn" onClick={() => fileInputRef.current?.click()}>+</button>

                <textarea
                  ref={textareaRef}
                  value={inputValue}
                  onChange={(e) => setInputValue(e.target.value)}
                  placeholder="Напиши задачу... Jarvis сам выберет режим"
                  className="chat-textarea smaller-text"
                />

                <button className="send-btn" onClick={handleSend}>➤</button>

                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  hidden
                  onChange={(e) => handleFilesSelected(e.target.files)}
                />
              </div>
            </>
          )}
        </div>
      </main>
    </div>
  );
}
