import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/ide";
import IdeWorkspaceShell from "./IdeWorkspaceShell";

const LIBRARY_KEY = "jarvis_ui_library_files_v3";
const CHAT_CONTEXT_KEY = "jarvis_chat_context_files_v3";

const DEFAULT_PROFILES = {
  "Универсальный": "Универсальный профиль для обычных ответов, без лишней глубины.",
  "Программист": "Профиль для кода, исправлений, архитектуры и технической реализации.",
  "Оркестратор": "Профиль для планирования, multi-agent сценариев, backend orchestration и пайплайнов.",
  "Исследователь": "Профиль для поиска, сравнения, анализа источников и фактологической проверки.",
  "Аналитик": "Профиль для выводов, рисков, структурирования данных и принятия решений.",
  "Сократ": "Профиль для обучения через вопросы и постепенное раскрытие темы.",
};

function loadLibraryFiles() {
  try {
    return JSON.parse(localStorage.getItem(LIBRARY_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveLibraryFiles(items) {
  localStorage.setItem(LIBRARY_KEY, JSON.stringify(items));
}

function loadChatContextMap() {
  try {
    return JSON.parse(localStorage.getItem(CHAT_CONTEXT_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveChatContextMap(value) {
  localStorage.setItem(CHAT_CONTEXT_KEY, JSON.stringify(value));
}

function makeId(prefix = "id") {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function deriveChatTitleFromMessages(messages) {
  const firstUser = (messages || []).find((item) => item.role === "user" && item.content?.trim());
  if (!firstUser) return "Новый чат";
  const text = firstUser.content.trim().replace(/\s+/g, " ");
  return text.length > 34 ? `${text.slice(0, 34)}…` : text;
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
  const map = loadChatContextMap();
  const ids = map[chatId] || [];
  return libraryFiles.filter((item) => ids.includes(item.id));
}

export default function JarvisChatShell() {
  const fileInputRef = useRef(null);
  const messageStreamRef = useRef(null);

  const [activeMainTab, setActiveMainTab] = useState("chat");
  const [activeSidebarTab, setActiveSidebarTab] = useState("chats");

  const [selectedModel, setSelectedModel] = useState("qwen3:8b");
  const [modelOptions, setModelOptions] = useState([]);
  const [profile, setProfile] = useState("Универсальный");
  const [profileDescriptions, setProfileDescriptions] = useState(DEFAULT_PROFILES);
  const [contextWindow, setContextWindow] = useState("4096");

  const [chats, setChats] = useState([]);
  const [activeChatId, setActiveChatId] = useState("");
  const [messages, setMessages] = useState([]);
  const [inputValue, setInputValue] = useState("");
  const [searchValue, setSearchValue] = useState("");
  const [errorText, setErrorText] = useState("");
  const [dragActive, setDragActive] = useState(false);

  const [libraryFiles, setLibraryFiles] = useState(loadLibraryFiles());
  const [selectedLibraryId, setSelectedLibraryId] = useState("");
  const [renameChatId, setRenameChatId] = useState("");
  const [renameValue, setRenameValue] = useState("");

  useEffect(() => {
    init();
  }, []);

  useEffect(() => {
    const el = messageStreamRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, activeSidebarTab]);

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
        setMessages((await api.getMessages({ chatId: firstId })) || []);
      } else {
        const created = await handleNewChat(true);
        if (created?.id) {
          setMessages((await api.getMessages({ chatId: created.id })) || []);
        }
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
      const created = await api.createChat({ title: "Новый чат" });
      await loadChats(created.id);
      setActiveChatId(created.id);
      setMessages(await api.getMessages({ chatId: created.id }));
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
      setMessages((await api.getMessages({ chatId })) || []);
      setActiveSidebarTab("chats");
      setActiveMainTab("chat");
      setRenameChatId("");
      setRenameValue("");
    } catch (e) {
      setErrorText(e.message || "Ошибка открытия чата");
    }
  }

  async function handleRenameChat(id) {
    const title = renameValue.trim();
    if (!title) return;
    try {
      await api.renameChat({ id, title });
      await loadChats(id);
      setRenameChatId("");
      setRenameValue("");
    } catch (e) {
      setErrorText(e.message || "Ошибка переименования чата");
    }
  }

  async function autoRenameChatFromMessages(chatId, nextMessages) {
    const current = chats.find((item) => item.id === chatId);
    if (!current) return;
    const derived = deriveChatTitleFromMessages(nextMessages);
    if (!derived || (current.title && current.title !== "Новый чат")) return;
    try {
      await api.renameChat({ id: chatId, title: derived });
      await loadChats(chatId);
    } catch {}
  }

  async function handleSend() {
    const text = inputValue.trim();
    if (!text || !activeChatId) return;

    try {
      setErrorText("");

      const userMsg = await api.addMessage({
        chatId: activeChatId,
        role: "user",
        content: text,
      });

      const afterUserMessages = [...messages, userMsg];
      setMessages(afterUserMessages);
      setInputValue("");
      await autoRenameChatFromMessages(activeChatId, afterUserMessages);

      const contextFiles = getContextFilesForChat(activeChatId, libraryFiles)
        .filter((item) => item.use_in_context);

      const contextPrefix = contextFiles.length
        ? "\n\nКонтекст из библиотеки:\n" +
          contextFiles.map((f) => `- ${f.name}${f.preview ? `: ${f.preview.slice(0, 1200)}` : ""}`).join("\n")
        : "";

      const assistantMsg = await api.execute({
        chatId: activeChatId,
        message: `${text}${contextPrefix}`,
        mode: profile === "Оркестратор" ? "orchestrator" : profile === "Исследователь" || profile === "Аналитик" ? "research" : profile === "Программист" ? "code" : "chat",
        model: selectedModel,
        profile_name: profile,
      });

      setMessages((prev) => [...prev, assistantMsg]);
    } catch (e) {
      setErrorText(e.message || "Ошибка отправки сообщения");
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

    if (selectedLibraryId === id) {
      setSelectedLibraryId(next[0]?.id || "");
    }
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
    const q = searchValue.trim().toLowerCase();
    if (!q) return chats;
    return chats.filter((item) => item.title?.toLowerCase().includes(q));
  }, [searchValue, chats]);

  const pinnedChats = useMemo(() => filteredChats.filter((item) => item.pinned), [filteredChats]);
  const regularChats = useMemo(() => filteredChats.filter((item) => !item.pinned), [filteredChats]);
  const memoryChats = useMemo(() => chats.filter((item) => item.memory_saved), [chats]);
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
            value={searchValue}
            onChange={(e) => setSearchValue(e.target.value)}
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
          <>
            <div className="sidebar-section-title">Проекты</div>
            <div className="sidebar-empty">Для работы с репозиторием используй режим Code.</div>
          </>
        )}

        {activeSidebarTab === "settings" && (
          <>
            <div className="sidebar-section-title">Настройки</div>
            <div className="settings-stack">
              <label className="settings-row">
                <span>Модель</span>
                <select value={selectedModel} onChange={(e) => setSelectedModel(e.target.value)} className="topbar-select full dark-select">
                  {(modelOptions?.length ? modelOptions : [{ name: selectedModel }]).map((item) => (
                    <option key={item.name || item} value={item.name || item}>
                      {item.name || item}
                    </option>
                  ))}
                </select>
              </label>

              <label className="settings-row">
                <span>Профиль</span>
                <select value={profile} onChange={(e) => setProfile(e.target.value)} className="topbar-select full dark-select">
                  {Object.keys(profileDescriptions).map((name) => (
                    <option key={name} value={name}>{name}</option>
                  ))}
                </select>
              </label>

              <div className="content-card sidebar-help-card">
                <div className="content-card-text">{profileDescriptions[profile] || ""}</div>
              </div>

              <label className="settings-row">
                <span>Контекст</span>
                <select value={contextWindow} onChange={(e) => setContextWindow(e.target.value)} className="topbar-select full dark-select">
                  {[4096, 8192, 16384, 32768, 65536, 131072, 262144].map((v) => (
                    <option key={v} value={String(v)}>{Math.round(v / 1024)}k</option>
                  ))}
                </select>
              </label>
            </div>
          </>
        )}

        {activeSidebarTab === "library" && (
          <div className="sidebar-empty">Файлы библиотеки отображаются в основной панели.</div>
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
                <button className="soft-btn icon-btn" title="Сохранить в памяти" onClick={() => handleSaveToMemory(activeChatId, chats.find((c) => c.id === activeChatId)?.memory_saved)}>
                  🧠
                </button>
                <button className="soft-btn icon-btn" title="Закрепить в памяти" onClick={() => handlePinChat(activeChatId, chats.find((c) => c.id === activeChatId)?.pinned)}>
                  📌
                </button>
                <button className="soft-btn icon-btn" title="Переименовать чат" onClick={() => {
                  const current = chats.find((c) => c.id === activeChatId);
                  setRenameChatId(activeChatId);
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

          {renameChatId === activeChatId && activeSidebarTab === "chats" ? (
            <div className="rename-bar">
              <input
                value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
                className="rename-input wide"
                placeholder="Новое название чата"
              />
              <button className="mini-btn" onClick={() => handleRenameChat(activeChatId)}>Сохранить</button>
            </div>
          ) : null}

          {activeSidebarTab === "settings" ? (
            <div className="content-card">
              <div className="content-card-title">Профили работы агента</div>
              <div className="content-card-text">
                Универсальный — обычные ответы. Программист — код и исправления.
                Оркестратор — планирование и orchestration. Исследователь — факты и анализ источников.
                Аналитик — выводы и риски. Сократ — обучение через вопросы.
              </div>
            </div>
          ) : activeSidebarTab === "projects" ? (
            <div className="content-card">
              <div className="content-card-title">Проекты</div>
              <div className="content-card-text">
                Для работы с репозиторием открой вкладку Code.
              </div>
            </div>
          ) : activeSidebarTab === "library" ? (
            <div className="library-view">
              <div
                className={`upload-dropzone ${dragActive ? "active" : ""}`}
                onDragOver={onDragOver}
                onDragLeave={onDragLeave}
                onDrop={onDrop}
                onClick={() => fileInputRef.current?.click()}
              >
                Перетащи файлы сюда или нажми для загрузки в библиотеку
              </div>

              <input
                ref={fileInputRef}
                type="file"
                multiple
                hidden
                onChange={(e) => handleFilesSelected(e.target.files)}
              />

              <div className="library-grid">
                {libraryFiles.length ? libraryFiles.map((item) => (
                  <button
                    key={item.id}
                    className={`library-card ${selectedLibraryId === item.id ? "active" : ""}`}
                    onClick={() => setSelectedLibraryId(item.id)}
                  >
                    <div className="library-card-head">
                      <div>
                        <div className="content-card-title small">{item.name}</div>
                        <div className="content-card-text small">{item.type} • {Math.round(item.size / 1024) || 0} KB</div>
                      </div>
                      <span className="trash-inline" onClick={(e) => { e.stopPropagation(); removeLibraryItem(item.id); }}>🗑</span>
                    </div>

                    <label className="context-toggle compact">
                      <input
                        type="checkbox"
                        checked={!!item.use_in_context}
                        onChange={(e) => toggleLibraryContext(item.id, e.target.checked)}
                      />
                      <span>В контекст чата</span>
                    </label>
                  </button>
                )) : (
                  <div className="content-card">
                    <div className="content-card-title">Библиотека пуста</div>
                    <div className="content-card-text">Загрузи файлы через drag and drop.</div>
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
                className={`chat-input-shell smaller ${dragActive ? "drag-active" : ""}`}
                onDragOver={onDragOver}
                onDragLeave={onDragLeave}
                onDrop={onDrop}
              >
                <button className="input-plus-btn" onClick={() => fileInputRef.current?.click()}>+</button>

                <textarea
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
