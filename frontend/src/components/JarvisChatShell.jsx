import { useEffect, useMemo, useState } from "react";
import { api } from "../api/ide";
import IdeWorkspaceShell from "./IdeWorkspaceShell";

export default function JarvisChatShell() {
  const [activeTopTab, setActiveTopTab] = useState("chat");
  const [selectedModel, setSelectedModel] = useState("qwen3:8b");
  const [modelOptions, setModelOptions] = useState([]);
  const [profile, setProfile] = useState("Сбалансированный");
  const [chats, setChats] = useState([]);
  const [activeChatId, setActiveChatId] = useState("");
  const [messages, setMessages] = useState([]);
  const [inputValue, setInputValue] = useState("");
  const [searchValue, setSearchValue] = useState("");
  const [errorText, setErrorText] = useState("");
  const [contextWindow, setContextWindow] = useState("32768");

  useEffect(() => {
    init();
  }, []);

  async function init() {
    try {
      const [models, chatItems] = await Promise.all([
        api.listOllamaModels(),
        api.listChats(),
      ]);

      setModelOptions(models || []);
      if (models?.length) setSelectedModel(models[0].name || models[0]);

      if (chatItems?.length) {
        setChats(chatItems);
        const firstId = chatItems[0].id;
        setActiveChatId(firstId);
        const msgs = await api.getMessages({ chatId: firstId });
        setMessages(msgs || []);
      } else {
        const created = await handleNewChat(true);
        if (created?.id) {
          const msgs = await api.getMessages({ chatId: created.id });
          setMessages(msgs || []);
        }
      }
    } catch (e) {
      setErrorText(e.message || "Ошибка инициализации");
    }
  }

  async function reloadChats(selectId = "") {
    const items = await api.listChats();
    setChats(items || []);
    if (selectId) setActiveChatId(selectId);
  }

  async function handleNewChat(silent = false) {
    try {
      const created = await api.createChat({ title: "Новый чат" });
      await reloadChats(created.id);
      setActiveChatId(created.id);
      setMessages(await api.getMessages({ chatId: created.id }));
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
      const msgs = await api.getMessages({ chatId });
      setMessages(msgs || []);
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
        mode: activeTopTab,
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
      await reloadChats(activeChatId);
    } catch (e) {
      setErrorText(e.message || "Ошибка закрепления");
    }
  }

  async function handleSaveToMemory(id, saved) {
    try {
      await api.saveChatToMemory({ id, saved: !saved });
      await reloadChats(activeChatId);
    } catch (e) {
      setErrorText(e.message || "Ошибка памяти");
    }
  }

  const filteredChats = useMemo(() => {
    const q = searchValue.trim().toLowerCase();
    if (!q) return chats;
    return chats.filter((item) => item.title?.toLowerCase().includes(q));
  }, [searchValue, chats]);

  const pinnedChats = useMemo(() => filteredChats.filter((item) => item.pinned), [filteredChats]);
  const regularChats = useMemo(() => filteredChats.filter((item) => !item.pinned), [filteredChats]);

  const topTabs = [
    { key: "chat", label: "Chat" },
    { key: "code", label: "Code" },
    { key: "research", label: "Research" },
    { key: "orchestrator", label: "Orchestrator" },
    { key: "image", label: "Text-to-Image" },
  ];

  if (activeTopTab === "code") {
    return <IdeWorkspaceShell onBackToChat={() => setActiveTopTab("chat")} />;
  }

  return (
    <div className="jarvis-shell">
      <aside className="jarvis-sidebar">
        <button className="sidebar-newchat-btn" onClick={() => handleNewChat(false)}>
          + Новый чат
        </button>

        <div className="sidebar-nav">
          <div className="sidebar-nav-item">
            <span>⌕</span>
            <input
              className="sidebar-search-input"
              value={searchValue}
              onChange={(e) => setSearchValue(e.target.value)}
              placeholder="Поиск"
            />
          </div>
          <div className="sidebar-nav-item active">☰ Чаты</div>
          <div className="sidebar-nav-item">★ Память</div>
          <div className="sidebar-nav-item">▣ Проекты</div>
          <div className="sidebar-nav-item">⚙ Настройки</div>
        </div>

        <div className="sidebar-section-title">Закреплённые</div>
        <div className="chat-list">
          {pinnedChats.length ? pinnedChats.map((chat) => (
            <button
              key={chat.id}
              className={`chat-list-item ${activeChatId === chat.id ? "active" : ""}`}
              onClick={() => openChat(chat.id)}
            >
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
            <button
              key={chat.id}
              className={`chat-list-item ${activeChatId === chat.id ? "active" : ""}`}
              onClick={() => openChat(chat.id)}
            >
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
      </aside>

      <main className="jarvis-main">
        <div className="jarvis-topbar">
          <div className="jarvis-brand">Jarvis</div>

          <select value={selectedModel} onChange={(e) => setSelectedModel(e.target.value)} className="topbar-select">
            {(modelOptions?.length ? modelOptions : [{ name: selectedModel }]).map((item) => (
              <option key={item.name || item} value={item.name || item}>
                {item.name || item}
              </option>
            ))}
          </select>

          <select value={profile} onChange={(e) => setProfile(e.target.value)} className="topbar-select">
            <option value="Сбалансированный">Сбалансированный</option>
            <option value="Точный">Точный</option>
            <option value="Быстрый">Быстрый</option>
          </select>

          <select value={contextWindow} onChange={(e) => setContextWindow(e.target.value)} className="topbar-select">
            {[4096, 8192, 16384, 32768, 65536, 131072, 262144].map((v) => (
              <option key={v} value={String(v)}>
                {Math.round(v / 1024)}k
              </option>
            ))}
          </select>

          <div className="topbar-tabs">
            {topTabs.map((tab) => (
              <button
                key={tab.key}
                className={`soft-btn ${activeTopTab === tab.key ? "active" : ""}`}
                onClick={() => setActiveTopTab(tab.key)}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        <div className="chat-page">
          <div className="chat-header-row">
            <div className="chat-page-title">Чаты</div>
            <div className="chat-header-actions">
              <button className="soft-btn" onClick={() => activeChatId && handleSaveToMemory(activeChatId, chats.find((c) => c.id === activeChatId)?.memory_saved)}>
                Сохранить в памяти
              </button>
              <button className="soft-btn" onClick={() => activeChatId && handlePinChat(activeChatId, chats.find((c) => c.id === activeChatId)?.pinned)}>
                Закрепить в памяти
              </button>
            </div>
          </div>

          <div className="assistant-greeting">
            Jarvis готов. Начни новый чат или напиши сообщение.
          </div>

          <div className="message-stream">
            {messages.map((msg) => (
              <div key={msg.id} className={`message-row ${msg.role}`}>
                <div className="message-bubble">{msg.content}</div>
              </div>
            ))}
          </div>

          {errorText ? <div className="error-banner">{errorText}</div> : null}

          <div className="chat-input-shell smaller">
            <button className="input-plus-btn">+</button>
            <textarea
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              placeholder="Напиши задачу... Jarvis сам выберет режим"
              className="chat-textarea"
            />
            <button className="send-btn" onClick={handleSend}>➤</button>
          </div>
        </div>
      </main>
    </div>
  );
}
