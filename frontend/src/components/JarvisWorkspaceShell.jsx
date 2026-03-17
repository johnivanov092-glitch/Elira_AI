import { useMemo, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Bot,
  Briefcase,
  ChevronDown,
  Code2,
  Compass,
  FolderKanban,
  Grid3X3,
  LayoutPanelLeft,
  Menu,
  MessageSquare,
  Mic,
  Plus,
  Search,
  Settings2,
  Sparkles,
  User,
} from "lucide-react";

const recentItems = [
  "Неполное сообщение",
  "Мнение о загруженных файлах",
  "Анализ проекта",
  "Continuing the conversation",
  "Анализ и улучшение проекта",
  "Unclear input",
  "Цветовой профиль для отчета по к...",
  "Обновление резюме из PDF",
  "Let's go",
  "Artifacts в панели управления при...",
];

const codeFiles = [
  { name: "agents.py", lines: "PY" },
  { name: "files.py", lines: "PY" },
  { name: "llm.py", lines: "PY" },
  { name: "main.py", lines: "PY" },
  { name: "config.py", lines: "149 lines" },
  { name: "memory.py", lines: "313 lines" },
  { name: "web.py", lines: "75 lines" },
];

const codePreview = `1375    st.session_state.last_terminal_output = run_terminal(term_cmd, timeout=term_to)
1376    add_memory(f"TERMINAL: {term_cmd}\\n{st.session_state.last_terminal_output[:3000]}",
1377               source="terminal", memory_type="terminal", profile_name=memory_profile)

1383    # TAB 6 — ОТЧЁТЫ
1384    with tabs[6]:
1385        st.subheader("Отчёты")
1386        reports = sorted(OUTPUT_DIR.glob("*.md"), reverse=True)
1387        chat_files = list_chat_files()

1390        with ra:
1391            st.markdown("### Сохранённые отчёты")
1392            if reports:
1393                sel_r = st.selectbox("Отчёт", [p.name for p in reports])
1394                rc_text = (OUTPUT_DIR / sel_r).read_text(encoding="utf-8")
1395                st.text_area("Содержимое", rc_text, height=320)
1396                st.download_button("↓ Скачать", data=rc_text)

1406        with rb:
1407            st.markdown("### Все чаты")
1408            if chat_files:
1409                _chat_labels = [get_chat_rel_label(p) for p in chat_files]
1410                chat_map = {get_chat_rel_label(p): p for p in chat_files}`;

function NavItem({ icon: Icon, label, active = false }) {
  return (
    <button type="button" className={`nav-item ${active ? "active" : ""}`}>
      <Icon size={18} />
      <span>{label}</span>
    </button>
  );
}

function TabButton({ label, active }) {
  return (
    <button type="button" className={`top-tab ${active ? "active" : ""}`}>
      {label}
    </button>
  );
}

function FileCard({ file, active, onClick }) {
  return (
    <button type="button" className={`file-card ${active ? "active" : ""}`} onClick={onClick}>
      <div className="file-card-name">{file.name}</div>
      <div className="file-card-meta">{file.lines}</div>
    </button>
  );
}

export default function JarvisWorkspaceShell() {
  const [activeTopTab, setActiveTopTab] = useState("Chat");
  const [selectedFile, setSelectedFile] = useState(null);

  const showCodePanel = Boolean(selectedFile);

  const heroTitle = useMemo(() => {
    if (activeTopTab === "Code") return "Кодовое рабочее пространство";
    if (activeTopTab === "Cowork") return "Совместная работа";
    return "Afternoon, John";
  }, [activeTopTab]);

  return (
    <div className="workspace-root">
      <aside className="sidebar">
        <div className="sidebar-toolbar">
          <button type="button" className="ghost-icon"><Menu size={18} /></button>
          <button type="button" className="ghost-icon"><LayoutPanelLeft size={18} /></button>
          <button type="button" className="ghost-icon"><ArrowLeft size={18} /></button>
          <button type="button" className="ghost-icon"><ArrowRight size={18} /></button>
        </div>

        <div className="primary-actions">
          <button type="button" className="new-chat-btn">
            <Plus size={18} />
            <span>New chat</span>
          </button>
          <NavItem icon={Search} label="Search" />
          <NavItem icon={Settings2} label="Customize" />
        </div>

        <div className="sidebar-group">
          <NavItem icon={MessageSquare} label="Chats" active />
          <NavItem icon={FolderKanban} label="Projects" />
          <NavItem icon={Grid3X3} label="Artifacts" />
        </div>

        <div className="recents-block">
          <div className="section-title">Recents</div>
          <div className="recents-list">
            {recentItems.map((item, index) => (
              <button key={index} type="button" className={`recent-item ${index === 2 ? "active" : ""}`}>
                {item}
              </button>
            ))}
          </div>
        </div>

        <div className="profile-card">
          <div className="profile-avatar">J</div>
          <div className="profile-meta">
            <div className="profile-name">John</div>
            <div className="profile-plan">Pro plan</div>
          </div>
        </div>
      </aside>

      <main className="main-shell">
        <header className="topbar">
          <div className="top-tabs">
            {["Chat", "Cowork", "Code"].map((tab) => (
              <div key={tab} onClick={() => setActiveTopTab(tab)}>
                <TabButton label={tab} active={activeTopTab === tab} />
              </div>
            ))}
          </div>
        </header>

        <section className={`content-area ${showCodePanel ? "with-preview" : ""}`}>
          <div className="conversation-pane">
            <div className="chat-breadcrumb">
              <span>ИИ агент Личный</span>
              <span>/</span>
              <button type="button" className="breadcrumb-current">
                Анализ проекта
                <ChevronDown size={14} />
              </button>
            </div>

            <div className="message-card">
              <div className="message-card-header">
                <Sparkles size={18} />
                <span>Qwen3 Coder 480B — облачный кодер</span>
                <span className="divider">|</span>
                <span>DeepSeek V3.1 671B — облачный</span>
                <span className="divider">|</span>
                <span>↤ появляется если ✓</span>
              </div>
              <p>
                Облачные модели скрыты за чекбоксом — не мешают, но доступны когда нужно.
              </p>
              <h3>Почему deepseek-r1:8b не показывался</h3>
              <p>
                Старая логика <code>get_available_models()</code> работала так: если ollama вернул модели —
                показывал только их, игнорируя конфиг. Новая логика всегда начинает со статических моделей,
                потом добавляет то, что пришло из ollama.
              </p>
            </div>

            <div className="artifact-stack">
              <div className="artifact-row">
                <div className="artifact-meta">
                  <div className="artifact-icon">📄</div>
                  <div>
                    <div className="artifact-name">Main</div>
                    <div className="artifact-type">PY</div>
                  </div>
                </div>
                <button type="button" className="artifact-action" onClick={() => setSelectedFile("main.py")}>
                  Open in Python
                </button>
              </div>

              <div className="artifact-row">
                <div className="artifact-meta">
                  <div className="artifact-icon">📄</div>
                  <div>
                    <div className="artifact-name">Llm</div>
                    <div className="artifact-type">PY</div>
                  </div>
                </div>
                <button type="button" className="artifact-action" onClick={() => setSelectedFile("llm.py")}>
                  Open in Python
                </button>
              </div>

              <button type="button" className="download-all">↓ Download all</button>
            </div>

            <div className="reaction-row">
              <button type="button" className="ghost-round">⧉</button>
              <button type="button" className="ghost-round">👍</button>
              <button type="button" className="ghost-round">👎</button>
              <button type="button" className="ghost-round">↻</button>
            </div>

            <div className="file-grid">
              {codeFiles.map((file) => (
                <FileCard
                  key={file.name}
                  file={file}
                  active={selectedFile === file.name}
                  onClick={() => setSelectedFile(file.name)}
                />
              ))}
            </div>

            {!showCodePanel && (
              <div className="hero-input-wrap">
                <div className="hero-greeting">
                  <span className="hero-mark">✺</span>
                  <span>{heroTitle}</span>
                </div>

                <div className="composer-card">
                  <div className="composer-input">Type / for skills</div>
                  <div className="composer-footer">
                    <button type="button" className="ghost-icon big"><Plus size={18} /></button>
                    <div className="composer-model">
                      <span>Sonnet 4.6 Extended</span>
                      <ChevronDown size={14} />
                      <Mic size={16} />
                    </div>
                  </div>
                </div>

                <div className="skill-row">
                  <button type="button" className="skill-pill"><Code2 size={16} />Code</button>
                  <button type="button" className="skill-pill"><Compass size={16} />Learn</button>
                  <button type="button" className="skill-pill"><Briefcase size={16} />Strategize</button>
                  <button type="button" className="skill-pill"><Sparkles size={16} />Write</button>
                  <button type="button" className="skill-pill"><User size={16} />Life stuff</button>
                </div>
              </div>
            )}
          </div>

          {showCodePanel && (
            <aside className="code-preview-pane">
              <div className="code-preview-header">
                <div className="code-preview-title">{selectedFile || "Main · PY"}</div>
                <div className="code-preview-actions">
                  <button type="button" className="tiny-btn">Copy</button>
                  <button type="button" className="ghost-icon">↻</button>
                  <button type="button" className="ghost-icon" onClick={() => setSelectedFile(null)}>✕</button>
                </div>
              </div>
              <pre className="code-block">{codePreview}</pre>
            </aside>
          )}
        </section>
      </main>
    </div>
  );
}
