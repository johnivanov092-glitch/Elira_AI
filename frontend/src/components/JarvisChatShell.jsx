
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/ide";
import IdeWorkspaceShell from "./IdeWorkspaceShell";

const LIBRARY_KEY = "jarvis_ui_library_files_v2";

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

function makeId(prefix = "id") {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

async function fileToLibraryRecord(file) {
  let textPreview = "";
  const isTextLike =
    file.type.startsWith("text/") ||
    file.name.match(/\.(txt|md|json|js|jsx|ts|tsx|py|css|html|yml|yaml|xml|csv|log)$/i);

  if (isTextLike) {
    try {
      const text = await file.text();
      textPreview = text.slice(0, 4000);
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
  };
}

export default function JarvisChatShell() {
  const fileInputRef = useRef(null);

  const [activeMainTab, setActiveMainTab] = useState("chat");
  const [activeSidebarTab, setActiveSidebarTab] = useState("chats");

  const [selectedModel, setSelectedModel] = useState("qwen3:8b");
  const [modelOptions, setModelOptions] = useState([]);
  const [profile, setProfile] = useState("Универсальный");
  const [profileOptions, setProfileOptions] = useState([]);
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

  useEffect(() => {
    init();
  }, []);

  async function init() {
    try {
      const [models, profiles, chatItems] = await Promise.all([
        api.listOllamaModels(),
        api.listProfiles(),
        api.listChats(),
      ]);

      setModelOptions(models || []);
      if (models?.length) setSelectedModel(models[0].name || models[0]);

      setProfileOptions(profiles || []);
      if (profiles?.length) setProfile(profiles[0].name || profiles[0]);

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
    } catch (e) {
      setErrorText(e.message || "Ошибка открытия чата");
    }
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

      setMessages((prev) => [...prev, userMsg]);
      setInputValue("");

      const assistantMsg = await api.execute({
        chatId: activeChatId,
        message: text,
        profileName: profile,
        model: selectedModel,
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
    if (selectedLibraryId === id) {
      setSelectedLibraryId(next[0]?.id || "");
    }
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
  const selectedProfileItem = useMemo(
    () => profileOptions.find((item) => item.name === profile) || null,
    [profileOptions, profile]
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

        {activeSidebarTab === "settings" && (
          <>
            <div className="sidebar-section-title">Настройки</div>

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
                {(profileOptions?.length ? profileOptions : [{ name: profile }]).map((item) => (
                  <option key={item.name || item} value={item.name || item}>
                    {item.name || item}
                  </option>
                ))}
              </select>
            </label>

            {selectedProfileItem?.description ? (
              <div className="profile-help">
                {selectedProfileItem.description}
              </div>
            ) : null}

            <label className="settings-row">
              <span>Контекст</span>
              <select value={contextWindow} onChange={(e) => setContextWindow(e.target.value)} className="topbar-select full dark-select">
                {[4096, 8192, 16384, 32768, 65536, 131072, 262144].map((v) => (
                  <option key={v} value={String(v)}>
                    {Math.round(v / 1024)}k
                  </option>
                ))}
              </select>
            </label>

            <div className="profile-help">
              Профиль определяет стиль и глубину работы агента. Оркестратор и Исследователь используют более сложный маршрут ответа, если backend это поддерживает.
            </div>
          </>
        )}

        {activeSidebarTab === "chats" && (
          <>
            <div className="sidebar-section-title">Закреплённые</div>
            <div className="chat-list">
              {pinnedChats.length ? pinnedChats.map((chat) => (
                <button key={chat.id} className={`chat-list-item ${activeChatId === chat.id ? "active" : ""}`} onClick={() => openChat(chat.id)}>
                  <div className="chat-list-title">{chat.title}</div>
                  <div className="chat-list-actions">
                    <span onClick={(e) => { e.stopPropagation(); handlePinChat(chat.id, chat.pinned); }}>📌</span>
                    <span onClick={(e) => { e.stopPropagation(); handleDeleteChat(chat.id); }}>🗑</span>
                  </div>
                </button>
              )) : <div className="sidebar-empty">Здесь пока пусто.</div>}
            </div>

            <div className="sidebar-section-title">Все чаты</div>
            <div className="chat-list">
              {regularChats.length ? regularChats.map((chat) => (
                <button key={chat.id} className={`chat-list-item ${activeChatId === chat.id ? "active" : ""}`} onClick={() => openChat(chat.id)}>
                  <div>
                    <div className="chat-list-title">{chat.title}</div>
                    <div className="chat-list-subtitle">{chat.memory_saved ? "Память чатов" : ""}</div>
                  </div>
                  <div className="chat-list-actions">
                    <span onClick={(e) => { e.stopPropagation(); handlePinChat(chat.id, chat.pinned); }}>📌</span>
                    <span onClick={(e) => { e.stopPropagation(); handleSaveToMemory(chat.id, chat.memory_saved); }}>🧠</span>
                    <span onClick={(e) => { e.stopPropagation(); handleDeleteChat(chat.id); }}>🗑</span>
                  </div>
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
                <button key={chat.id} className={`chat-list-item ${activeChatId === chat.id ? "active" : ""}`} onClick={() => openChat(chat.id)}>
                  <div className="chat-list-title">{chat.title}</div>
                </button>
              )) : <div className="sidebar-empty">Сохранённых чатов пока нет.</div>}
            </div>
          </>
        )}

        {activeSidebarTab === "projects" && (
          <>
            <div className="sidebar-section-title">Проекты</div>
            <div className="sidebar-empty">Открой Code для работы с файлами проекта.</div>
          </>
        )}

        {activeSidebarTab === "library" && (
          <>
            <div className="sidebar-section-title">Библиотека</div>
            <button className="soft-btn upload-btn" onClick={() => fileInputRef.current?.click()}>
              Загрузить файлы
            </button>
            <div className="chat-list">
              {libraryFiles.length ? libraryFiles.map((item) => (
                <button
                  key={item.id}
                  className={`chat-list-item ${selectedLibraryId === item.id ? "active" : ""}`}
                  onClick={() => setSelectedLibraryId(item.id)}
                >
                  <div>
                    <div className="chat-list-title">{item.name}</div>
                    <div className="chat-list-subtitle">{item.type} • {Math.round(item.size / 1024) || 0} KB</div>
                  </div>
                  <div className="chat-list-actions">
                    <span onClick={(e) => { e.stopPropagation(); removeLibraryItem(item.id); }}>🗑</span>
                  </div>
                </button>
              )) : <div className="sidebar-empty">Сюда попадут файлы, загруженные через чат или библиотеку.</div>}
            </div>
          </>
        )}
      </aside>

      <main className="jarvis-main">
        <div className="jarvis-topbar slim">
          <div className="jarvis-brand">Jarvis</div>
          <div className="topbar-tabs">
            <button className={`soft-btn ${activeMainTab === "chat" ? "active" : ""}`} onClick={() => setActiveMainTab("chat")}>Chat</button>
            <button className={`soft-btn ${activeMainTab === "code" ? "active" : ""}`} onClick={() => setActiveMainTab("code")}>Code</button>
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

            {activeSidebarTab === "chats" && (
              <div className="chat-header-actions">
                <button className="soft-btn" onClick={() => activeChatId && handleSaveToMemory(activeChatId, chats.find((c) => c.id === activeChatId)?.memory_saved)}>
                  Сохранить в памяти
                </button>
                <button className="soft-btn" onClick={() => activeChatId && handlePinChat(activeChatId, chats.find((c) => c.id === activeChatId)?.pinned)}>
                  Закрепить в памяти
                </button>
              </div>
            )}
          </div>

          {activeSidebarTab === "settings" ? (
            <div className="content-card">
              <div className="content-card-title">Профили работы агента</div>
              <div className="content-card-text">
                Универсальный — обычные ответы. Программист — код и исправления. Оркестратор — планирование и orchestration. Исследователь — факты и анализ источников. Аналитик — выводы и риски. Сократ — обучение через вопросы.
              </div>
            </div>
          ) : activeSidebarTab === "projects" ? (
            <div className="content-card">
              <div className="content-card-title">Проекты</div>
              <div className="content-card-text">Для работы с кодом открой вкладку Code. В Code теперь используется snapshot из backend.</div>
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
                className="hidden-file-input"
                onChange={(e) => handleFilesSelected(e.target.files)}
              />

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
                    <div className="content-card-text">Для этого файла доступно только описание.</div>
                  )}
                </div>
              ) : (
                <div className="content-card">
                  <div className="content-card-title">Библиотека пуста</div>
                  <div className="content-card-text">Загрузи файлы сюда или через кнопку «Загрузить файлы».</div>
                </div>
              )}
            </div>
          ) : activeSidebarTab === "memory" ? (
            <div className="message-stream">
              {memoryChats.length ? memoryChats.map((chat) => (
                <button key={chat.id} className="content-card content-card-button" onClick={() => openChat(chat.id)}>
                  <div className="content-card-title">{chat.title}</div>
                  <div className="content-card-text">Открыть сохранённый чат</div>
                </button>
              )) : <div className="content-card"><div className="content-card-text">Сохранённых чатов пока нет.</div></div>}
            </div>
          ) : (
            <>
              {messages.length === 0 && (
                <div className="assistant-greeting">
                  Jarvis готов. Начни новый чат или напиши сообщение.
                </div>
              )}

              <div className="message-stream">
                {messages.map((msg) => (
                  <div key={msg.id} className={`message-row ${msg.role}`}>
                    <div className="message-bubble">{msg.content}</div>
                  </div>
                ))}
              </div>

              {errorText ? <div className="error-banner">{errorText}</div> : null}

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
                  className="chat-textarea"
                />
                <button className="send-btn" onClick={handleSend}>➤</button>

                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  className="hidden-file-input"
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
