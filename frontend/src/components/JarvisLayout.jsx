import { useEffect, useRef, useState } from "react";
import {
  Folder,
  MessageSquare,
  Paperclip,
  Plus,
  Search,
  Settings,
  Send,
  X,
  SlidersHorizontal,
  Bot,
  FolderKanban,
} from "lucide-react";

const NAV_ITEMS = [
  { key: "search", label: "Search", icon: Search },
  { key: "chats", label: "Chats", icon: MessageSquare },
  { key: "projects", label: "Projects", icon: Folder },
  { key: "settings", label: "Settings", icon: Settings },
];

const SAMPLE_CHATS = [
  "Анализ проекта",
  "Продумать архитектуру агента",
  "Поиск ошибок в backend",
  "Улучшение Ollama профиля",
  "Интеграция памяти",
];

const SAMPLE_PROJECTS = [
  { id: 1, name: "Jarvis Work", task: "Главный desktop AI агент" },
  { id: 2, name: "Research Engine", task: "Мультипоиск и разбор страниц" },
  { id: 3, name: "Code Agent", task: "Кодинг, патчи, workflow" },
];

const DEFAULT_SETTINGS = {
  ollamaContext: 8192,
  defaultModel: "qwen3:8b",
  agentProfile: "Сбалансированный",
};

const DEFAULT_MESSAGES = [
  {
    id: 1,
    role: "assistant",
    text:
      "Jarvis готов. Прикрепляй файлы прямо в чат, пиши задачу обычным языком — я сам выберу режим: чат, код, план, исследование или мульти-агентный оркестратор.",
  },
];

export default function JarvisLayout() {
  const [activeNav, setActiveNav] = useState("chats");
  const [activeTopTab, setActiveTopTab] = useState("chat");
  const [messages, setMessages] = useState(DEFAULT_MESSAGES);
  const [draft, setDraft] = useState("");
  const [attachedFiles, setAttachedFiles] = useState([]);
  const [openedCodeFile, setOpenedCodeFile] = useState(null);
  const [settings, setSettings] = useState(DEFAULT_SETTINGS);

  const fileInputRef = useRef(null);
  const chatScrollRef = useRef(null);

  useEffect(() => {
    const raw = localStorage.getItem("jarvis_ui_settings");
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw);
      setSettings((prev) => ({ ...prev, ...parsed }));
    } catch (error) {
      console.error(error);
    }
  }, []);

  useEffect(() => {
    localStorage.setItem("jarvis_ui_settings", JSON.stringify(settings));
  }, [settings]);

  useEffect(() => {
    const el = chatScrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages, attachedFiles]);

  const handleAttachFiles = (event) => {
    const list = Array.from(event.target.files || []);
    if (!list.length) return;

    const mapped = list.map((file, index) => ({
      id: `${file.name}-${file.size}-${Date.now()}-${index}`,
      name: file.name,
      size: file.size,
      type: file.type || "file",
    }));

    setAttachedFiles((prev) => [...prev, ...mapped]);
    event.target.value = "";
  };

  const removeAttachedFile = (id) => {
    setAttachedFiles((prev) => prev.filter((item) => item.id !== id));
  };

  const sendMessage = () => {
    const text = draft.trim();
    if (!text && attachedFiles.length === 0) return;

    const userMessage = {
      id: Date.now(),
      role: "user",
      text: text || "Прикрепил файлы",
      attachments: attachedFiles,
    };

    const assistantMessage = {
      id: Date.now() + 1,
      role: "assistant",
      text:
        activeTopTab === "code"
          ? "Режим Code активен. Jarvis может открыть workflow кодинга, подключить мульти-агентный оркестратор, показать preview кода и подготовить действия по файлам."
          : "Принял задачу. Jarvis обработает чат, код, план или исследование в зависимости от запроса и настроек профиля.",
    };

    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    setDraft("");
    setAttachedFiles([]);
  };

  const titleMap = {
    search: "Поиск по чатам, проектам и памяти",
    chats: "Чаты",
    projects: "Проекты и задачи",
    settings: "Настройки Jarvis",
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <button type="button" className="new-chat-btn">
          <Plus size={16} />
          <span>Новый чат</span>
        </button>

        <div className="nav-list">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            return (
              <button
                type="button"
                key={item.key}
                className={`nav-item ${activeNav === item.key ? "active" : ""}`}
                onClick={() => setActiveNav(item.key)}
              >
                <Icon size={16} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </div>

        <div className="sidebar-block">
          <div className="block-title">Все чаты</div>
          <div className="simple-list">
            {SAMPLE_CHATS.map((item, index) => (
              <button type="button" key={index} className="simple-list-item">
                {item}
              </button>
            ))}
          </div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="topbar-title">Jarvis Агент ИИ</div>

          <div className="top-tabs">
            <button
              type="button"
              className={activeTopTab === "chat" ? "active" : ""}
              onClick={() => setActiveTopTab("chat")}
            >
              Чат
            </button>

            <button
              type="button"
              className={activeTopTab === "code" ? "active" : ""}
              onClick={() => setActiveTopTab("code")}
            >
              Code
            </button>

            <button type="button" className="orchestrator-pill">
              <FolderKanban size={14} />
              <span>Мульти агент Оркестратор</span>
            </button>
          </div>
        </header>

        <section className={`workspace ${openedCodeFile ? "with-preview" : ""}`}>
          <div className="center-pane">
            <div className="center-head">
              <div className="center-title">{titleMap[activeNav]}</div>

              {activeNav === "settings" ? (
                <div className="center-hint">
                  <SlidersHorizontal size={14} />
                  <span>Параметры профиля и Ollama</span>
                </div>
              ) : null}
            </div>

            {activeNav === "search" && (
              <div className="panel-card">
                <div className="panel-title">Search</div>
                <div className="panel-text">
                  Здесь будет поиск по чатам, проектам и полной памяти.
                </div>
              </div>
            )}

            {activeNav === "projects" && (
              <div className="panel-card">
                <div className="panel-title">Projects</div>

                <div className="projects-list">
                  {SAMPLE_PROJECTS.map((project) => (
                    <div key={project.id} className="project-row">
                      <div>
                        <div className="project-name">{project.name}</div>
                        <div className="project-task">{project.task}</div>
                      </div>

                      <button type="button" className="row-action">
                        Открыть
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {activeNav === "settings" && (
              <div className="settings-card">
                <div className="panel-title">Settings</div>

                <label className="settings-field">
                  <span>Контекст Ollama</span>
                  <input
                    type="number"
                    min="1024"
                    step="1024"
                    value={settings.ollamaContext}
                    onChange={(e) =>
                      setSettings((prev) => ({
                        ...prev,
                        ollamaContext: Number(e.target.value || 0),
                      }))
                    }
                  />
                </label>

                <label className="settings-field">
                  <span>Языковая модель по умолчанию</span>
                  <select
                    value={settings.defaultModel}
                    onChange={(e) =>
                      setSettings((prev) => ({
                        ...prev,
                        defaultModel: e.target.value,
                      }))
                    }
                  >
                    <option value="qwen3:8b">qwen3:8b</option>
                    <option value="qwen2.5-coder:7b">qwen2.5-coder:7b</option>
                    <option value="deepseek-r1:8b">deepseek-r1:8b</option>
                    <option value="mistral:7b">mistral:7b</option>
                  </select>
                </label>

                <label className="settings-field">
                  <span>Профиль агента</span>
                  <select
                    value={settings.agentProfile}
                    onChange={(e) =>
                      setSettings((prev) => ({
                        ...prev,
                        agentProfile: e.target.value,
                      }))
                    }
                  >
                    <option value="Сбалансированный">Сбалансированный</option>
                    <option value="Кодинг">Кодинг</option>
                    <option value="Исследование">Исследование</option>
                    <option value="Мульти-агентный оркестратор">
                      Мульти-агентный оркестратор
                    </option>
                  </select>
                </label>

                <div className="settings-note">
                  Эти параметры пока сохраняются локально в интерфейсе. Следующим патчем их можно связать с backend и Ollama runtime.
                </div>
              </div>
            )}

            {activeNav === "chats" && (
              <div className="chat-layout">
                <div className="chat-header-line">
                  <div className="panel-title">Чаты</div>
                  <div className="panel-subtitle">Профиль: {settings.agentProfile}</div>
                </div>

                <div className="chat-scroll" ref={chatScrollRef}>
                  {messages.map((message) => (
                    <div
                      key={message.id}
                      className={`message-row ${message.role === "user" ? "user" : "assistant"}`}
                    >
                      <div className="message-avatar">
                        {message.role === "assistant" ? <Bot size={16} /> : "E"}
                      </div>

                      <div className="message-bubble">
                        <div>{message.text}</div>

                        {Array.isArray(message.attachments) && message.attachments.length > 0 ? (
                          <div className="bubble-attachments">
                            {message.attachments.map((file) => (
                              <button
                                type="button"
                                key={file.id}
                                className="bubble-file"
                                onClick={() => setOpenedCodeFile(file.name)}
                              >
                                <Paperclip size={14} />
                                <span>{file.name}</span>
                              </button>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    </div>
                  ))}
                </div>

                <div className="composer-fixed">
                  {attachedFiles.length > 0 ? (
                    <div className="attach-strip">
                      {attachedFiles.map((file) => (
                        <div key={file.id} className="attach-chip">
                          <Paperclip size={13} />
                          <span>{file.name}</span>

                          <button type="button" onClick={() => removeAttachedFile(file.id)}>
                            <X size={12} />
                          </button>
                        </div>
                      ))}
                    </div>
                  ) : null}

                  <div className="composer-card">
                    <button
                      type="button"
                      className="attach-btn"
                      onClick={() => fileInputRef.current?.click()}
                      title="Добавить файл"
                    >
                      <Plus size={18} />
                    </button>

                    <input
                      ref={fileInputRef}
                      type="file"
                      multiple
                      className="hidden-input"
                      onChange={handleAttachFiles}
                    />

                    <textarea
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      placeholder="Напиши задачу… Jarvis сам выберет режим"
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          sendMessage();
                        }
                      }}
                    />

                    <div className="composer-actions">
                      <div className="composer-mode">
                        <span>{settings.defaultModel}</span>
                        <span className="dot">•</span>
                        <span>{settings.agentProfile}</span>
                      </div>

                      <button type="button" className="send-btn" onClick={sendMessage}>
                        <Send size={16} />
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>

          {openedCodeFile ? (
            <aside className="preview-pane">
              <div className="preview-head">
                <div className="preview-title">{openedCodeFile}</div>

                <button
                  type="button"
                  className="preview-close"
                  onClick={() => setOpenedCodeFile(null)}
                >
                  <X size={16} />
                </button>
              </div>

              <div className="preview-body">
                <div className="preview-note">
                  Code preview панель открывается по клику на файл из чата.
                </div>

                <pre>{`# ${openedCodeFile}
# Здесь будет preview кода, diff и workflow агента.`}</pre>
              </div>
            </aside>
          ) : null}
        </section>
      </main>
    </div>
  );
}
