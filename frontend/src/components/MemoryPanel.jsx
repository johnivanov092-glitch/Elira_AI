/**
 * MemoryPanel.jsx — панель умной памяти Jarvis.
 *
 * Показывает все воспоминания, поиск, добавление, удаление, статистику.
 * Работает через /api/smart-memory/*
 */
import { useEffect, useState, useMemo } from "react";

const API = import.meta.env.VITE_API_BASE_URL || `http://${window.location.hostname}:8000`;

async function fetchJson(path, options = {}) {
  const resp = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

const CATEGORIES = [
  { id: "all", label: "Все", icon: "📋" },
  { id: "fact", label: "Факты", icon: "📌" },
  { id: "preference", label: "Предпочтения", icon: "⭐" },
  { id: "instruction", label: "Инструкции", icon: "📝" },
  { id: "context", label: "Контекст", icon: "🔗" },
];

export default function MemoryPanel() {
  const [memories, setMemories] = useState([]);
  const [stats, setStats] = useState(null);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  const [newText, setNewText] = useState("");
  const [newCat, setNewCat] = useState("fact");
  const [loading, setLoading] = useState(false);

  useEffect(() => { loadMemories(); loadStats(); }, []);

  async function loadMemories() {
    setLoading(true);
    try {
      const data = await fetchJson("/api/smart-memory/list?limit=100");
      setMemories(data.items || []);
    } catch (e) {
      console.warn("Failed to load memories:", e);
    }
    setLoading(false);
  }

  async function loadStats() {
    try {
      const data = await fetchJson("/api/smart-memory/stats");
      setStats(data);
    } catch {}
  }

  async function handleAdd() {
    const text = newText.trim();
    if (!text) return;
    await fetchJson("/api/smart-memory/add", {
      method: "POST",
      body: JSON.stringify({ text, category: newCat, importance: 7 }),
    });
    setNewText("");
    await loadMemories();
    await loadStats();
  }

  async function handleDelete(id) {
    await fetchJson(`/api/smart-memory/${id}`, { method: "DELETE" });
    setMemories(prev => prev.filter(m => m.id !== id));
    await loadStats();
  }

  async function handleSearch() {
    if (!search.trim()) { await loadMemories(); return; }
    setLoading(true);
    try {
      const data = await fetchJson("/api/smart-memory/search", {
        method: "POST",
        body: JSON.stringify({ query: search, limit: 20 }),
      });
      setMemories(data.items || []);
    } catch {}
    setLoading(false);
  }

  const filtered = useMemo(() => {
    if (category === "all") return memories;
    return memories.filter(m => m.category === category);
  }, [memories, category]);

  const catIcon = (cat) => (CATEGORIES.find(c => c.id === cat)?.icon || "📋");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, padding: "16px 20px", overflow: "auto", flex: 1 }}>

      {/* Stats */}
      {stats && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <div style={statChip}>🧠 {stats.total || 0} воспоминаний</div>
          {stats.by_category && Object.entries(stats.by_category).map(([k, v]) => (
            <div key={k} style={statChip}>{catIcon(k)} {k}: {v}</div>
          ))}
        </div>
      )}

      {/* Search */}
      <div style={{ display: "flex", gap: 6 }}>
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          onKeyDown={e => e.key === "Enter" && handleSearch()}
          placeholder="Поиск по памяти..."
          style={inputStyle}
        />
        <button onClick={handleSearch} style={btnSmall}>🔍</button>
        <button onClick={loadMemories} style={btnSmall}>↻</button>
      </div>

      {/* Add new */}
      <div style={{ display: "flex", gap: 6, alignItems: "end" }}>
        <div style={{ flex: 1 }}>
          <input
            value={newText}
            onChange={e => setNewText(e.target.value)}
            onKeyDown={e => e.key === "Enter" && handleAdd()}
            placeholder="Запомни: мой сервер на 192.168.1.100..."
            style={inputStyle}
          />
        </div>
        <select value={newCat} onChange={e => setNewCat(e.target.value)} style={{ ...inputStyle, width: 120 }}>
          <option value="fact">Факт</option>
          <option value="preference">Предпочтение</option>
          <option value="instruction">Инструкция</option>
          <option value="context">Контекст</option>
        </select>
        <button onClick={handleAdd} style={{ ...btnSmall, background: "rgba(124,159,255,0.15)", borderColor: "rgba(124,159,255,0.3)" }}>+ Добавить</button>
      </div>

      {/* Category filter */}
      <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
        {CATEGORIES.map(c => (
          <button
            key={c.id}
            onClick={() => setCategory(c.id)}
            style={{
              padding: "3px 10px", borderRadius: 99, fontSize: 11, cursor: "pointer",
              border: "1px solid var(--border)",
              background: category === c.id ? "var(--bg-surface-active)" : "transparent",
              color: category === c.id ? "var(--text-primary)" : "var(--text-muted)",
            }}
          >
            {c.icon} {c.label}
          </button>
        ))}
      </div>

      {/* Memory list */}
      {loading ? (
        <div style={{ color: "var(--text-muted)", fontSize: 12 }}>Загрузка...</div>
      ) : filtered.length === 0 ? (
        <div style={{ color: "var(--text-muted)", fontSize: 12, textAlign: "center", padding: 20 }}>
          <div style={{ fontSize: 24, opacity: 0.2, marginBottom: 8 }}>🧠</div>
          Память пуста. Скажи "Jarvis, запомни что..." или добавь вручную.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {filtered.map(mem => (
            <div key={mem.id} style={memCard}>
              <div style={{ display: "flex", alignItems: "start", gap: 8 }}>
                <span style={{ fontSize: 14 }}>{catIcon(mem.category)}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, lineHeight: 1.4, color: "var(--text-primary)", wordBreak: "break-word" }}>
                    {mem.text}
                  </div>
                  <div style={{ display: "flex", gap: 8, marginTop: 4, fontSize: 10, color: "var(--text-muted)" }}>
                    <span>{mem.category}</span>
                    <span>·</span>
                    <span>{mem.source}</span>
                    <span>·</span>
                    <span>важность: {mem.importance}/10</span>
                    {mem.access_count > 0 && <><span>·</span><span>использовано: {mem.access_count}×</span></>}
                  </div>
                </div>
                <button
                  onClick={() => handleDelete(mem.id)}
                  style={{ border: "none", background: "transparent", color: "var(--text-muted)", cursor: "pointer", fontSize: 12 }}
                >✕</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const statChip = {
  padding: "4px 10px", borderRadius: 99, fontSize: 10,
  background: "var(--bg-surface)", border: "1px solid var(--border)",
  color: "var(--text-secondary)",
};

const inputStyle = {
  padding: "6px 10px", borderRadius: 8, fontSize: 12,
  border: "1px solid var(--border)", background: "var(--bg-input)",
  color: "var(--text-primary)", outline: "none", width: "100%",
};

const btnSmall = {
  padding: "6px 10px", borderRadius: 8, fontSize: 11,
  border: "1px solid var(--border)", background: "var(--bg-surface)",
  color: "var(--text-secondary)", cursor: "pointer", whiteSpace: "nowrap",
};

const memCard = {
  padding: "8px 10px", borderRadius: 8,
  border: "1px solid var(--border)", background: "var(--bg-surface)",
};
