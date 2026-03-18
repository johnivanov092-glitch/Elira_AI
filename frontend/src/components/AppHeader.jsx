export default function AppHeader({
  title = "Jarvis",
  llm = "qwen3:8b",
  setLlm,
  llmOptions = [],
  profile = "Сбалансированный",
  setProfile,
  activeTab = "chat",
  setActiveTab,
}) {
  const tabs = [
    { key: "chat", label: "Chat" },
    { key: "code", label: "Code" },
    { key: "research", label: "Research" },
    { key: "orchestrator", label: "Orchestrator" },
    { key: "image", label: "Text-to-Image" },
  ];

  return (
    <div
      className="jarvis-header-hotfix"
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: 12,
        padding: "16px 18px 12px",
        borderBottom: "1px solid rgba(255,255,255,0.08)",
      }}
    >
      <div
        style={{
          minWidth: 120,
          fontSize: 18,
          fontWeight: 700,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {title}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center", flex: 1 }}>
        <select
          value={llm}
          onChange={(e) => setLlm?.(e.target.value)}
          style={{ minWidth: 150 }}
        >
          {(llmOptions?.length ? llmOptions : [{ name: llm }]).map((item) => (
            <option key={item.name || item} value={item.name || item}>
              {item.name || item}
            </option>
          ))}
        </select>

        <select
          value={profile}
          onChange={(e) => setProfile?.(e.target.value)}
          style={{ minWidth: 140 }}
        >
          <option value="Сбалансированный">Сбалансированный</option>
          <option value="Точный">Точный</option>
          <option value="Быстрый">Быстрый</option>
        </select>

        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 8,
            alignItems: "center",
            flex: 1,
          }}
        >
          {tabs.map((tab) => (
            <button
              key={tab.key}
              className="soft-btn"
              onClick={() => setActiveTab?.(tab.key)}
              style={{
                opacity: activeTab === tab.key ? 1 : 0.85,
                whiteSpace: "nowrap",
              }}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
