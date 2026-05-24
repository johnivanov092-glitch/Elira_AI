import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { api } from "../api/ide";
import type { SmartMemoryId, SmartMemoryItem, SmartMemoryStats } from "../api/smartMemory";

const CATEGORIES = [
  { id: "all", label: "Все", icon: "🧠" },
  { id: "fact", label: "Факты", icon: "📊" },
  { id: "preference", label: "Предпочтения", icon: "❤️" },
  { id: "instruction", label: "Инструкции", icon: "📝" },
  { id: "context", label: "Контекст", icon: "🧭" },
] as const;

type CategoryId = (typeof CATEGORIES)[number]["id"];
type MemoryStats = SmartMemoryStats & {
  by_category?: Record<string, number | string>;
  total?: number;
};

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

export default function MemoryPanel() {
  const [memories, setMemories] = useState<SmartMemoryItem[]>([]);
  const [stats, setStats] = useState<MemoryStats | null>(null);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<CategoryId>("all");
  const [newText, setNewText] = useState("");
  const [newCat, setNewCat] = useState<CategoryId>("fact");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    loadMemories();
    loadStats();
  }, []);

  async function loadMemories() {
    setLoading(true);
    setError("");
    try {
      setMemories(await api.listSmartMemory(100));
    } catch (e: unknown) {
      setError(errorMessage(e, "Не удалось загрузить память"));
    } finally {
      setLoading(false);
    }
  }

  async function loadStats() {
    try {
      const data = await api.getSmartMemoryStats();
      setStats(data as MemoryStats);
    } catch (e: unknown) {
      setError(errorMessage(e, "Не удалось загрузить статистику"));
    }
  }

  async function handleAdd() {
    const text = newText.trim();
    if (!text) return;
    setError("");
    try {
      await api.addSmartMemory({ text, category: newCat, importance: 7 });
      setNewText("");
      await loadMemories();
      await loadStats();
    } catch (e: unknown) {
      setError(errorMessage(e, "Не удалось добавить запись"));
    }
  }

  async function handleDelete(id: SmartMemoryId | undefined) {
    if (id === undefined) return;
    setError("");
    try {
      await api.deleteSmartMemory(id);
      setMemories((prev) => prev.filter((item) => item.id !== id));
      await loadStats();
    } catch (e: unknown) {
      setError(errorMessage(e, "Не удалось удалить запись"));
    }
  }

  async function handleSearch() {
    if (!search.trim()) {
      await loadMemories();
      return;
    }
    setLoading(true);
    setError("");
    try {
      setMemories(await api.searchSmartMemory(search, 20));
    } catch (e: unknown) {
      setError(errorMessage(e, "Не удалось выполнить поиск"));
    } finally {
      setLoading(false);
    }
  }

  const filtered = useMemo(() => {
    if (category === "all") return memories;
    return memories.filter((item) => item.category === category);
  }, [memories, category]);

  const catIcon = (cat?: string | null) => CATEGORIES.find((entry) => entry.id === cat)?.icon || "🧠";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, padding: "16px 20px", overflow: "auto", flex: 1 }}>
      {error && <div style={{ fontSize: 11, color: "#ff6b6b" }}>{error}</div>}

      {stats && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <div style={statChip}>🧠 {stats.total || 0} записей</div>
          {stats.by_category &&
            Object.entries(stats.by_category).map(([key, value]) => (
              <div key={key} style={statChip}>
                {catIcon(key)} {key}: {value}
              </div>
            ))}
        </div>
      )}

      <div style={{ display: "flex", gap: 6 }}>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          placeholder="Поиск по памяти..."
          style={inputStyle}
        />
        <button onClick={handleSearch} style={btnSmall} title="Поиск">
          🔍
        </button>
        <button onClick={loadMemories} style={btnSmall} title="Обновить">
          ↻
        </button>
      </div>

      <div style={{ display: "flex", gap: 6, alignItems: "end" }}>
        <div style={{ flex: 1 }}>
          <input
            value={newText}
            onChange={(e) => setNewText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAdd()}
            placeholder="Запомни: мой сервер на 192.168.1.100..."
            style={inputStyle}
          />
        </div>
        <select value={newCat} onChange={(e) => setNewCat(e.target.value as CategoryId)} style={{ ...inputStyle, width: 140 }}>
          <option value="fact">Факт</option>
          <option value="preference">Предпочтение</option>
          <option value="instruction">Инструкция</option>
          <option value="context">Контекст</option>
        </select>
        <button
          onClick={handleAdd}
          style={{ ...btnSmall, background: "rgba(124,159,255,0.15)", borderColor: "rgba(124,159,255,0.3)" }}
        >
          + Добавить
        </button>
      </div>

      <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
        {CATEGORIES.map((entry) => (
          <button
            key={entry.id}
            onClick={() => setCategory(entry.id)}
            style={{
              padding: "3px 10px",
              borderRadius: 99,
              fontSize: 11,
              cursor: "pointer",
              border: "1px solid var(--border)",
              background: category === entry.id ? "var(--bg-surface-active)" : "transparent",
              color: category === entry.id ? "var(--text-primary)" : "var(--text-muted)",
            }}
          >
            {entry.icon} {entry.label}
          </button>
        ))}
      </div>

      {loading ? (
        <div style={{ color: "var(--text-muted)", fontSize: 12 }}>Загрузка...</div>
      ) : filtered.length === 0 ? (
        <div style={{ color: "var(--text-muted)", fontSize: 12, textAlign: "center", padding: 20 }}>
          <div style={{ fontSize: 24, opacity: 0.2, marginBottom: 8 }}>🧠</div>
          Память пуста. Напиши: «Elira, запомни это...», или добавь запись вручную.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {filtered.map((memory) => (
            <div key={memory.id} style={memCard}>
              <div style={{ display: "flex", alignItems: "start", gap: 8 }}>
                <span style={{ fontSize: 14 }}>{catIcon(memory.category)}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, lineHeight: 1.4, color: "var(--text-primary)", wordBreak: "break-word" }}>
                    {memory.text}
                  </div>
                  <div style={{ display: "flex", gap: 8, marginTop: 4, fontSize: 10, color: "var(--text-muted)" }}>
                    <span>{memory.category}</span>
                    <span>•</span>
                    <span>{memory.source}</span>
                    <span>•</span>
                    <span>Важность: {memory.importance}/10</span>
                    {typeof memory.access_count === "number" && memory.access_count > 0 && (
                      <>
                        <span>•</span>
                        <span>Использовано: {memory.access_count}x</span>
                      </>
                    )}
                  </div>
                </div>
                <button
                  onClick={() => handleDelete(memory.id)}
                  style={{ border: "none", background: "transparent", color: "var(--text-muted)", cursor: "pointer", fontSize: 12 }}
                  title="Удалить"
                >
                  ✕
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const statChip: CSSProperties = {
  padding: "4px 10px",
  borderRadius: 99,
  fontSize: 10,
  background: "var(--bg-surface)",
  border: "1px solid var(--border)",
  color: "var(--text-secondary)",
};

const inputStyle: CSSProperties = {
  padding: "6px 10px",
  borderRadius: 8,
  fontSize: 12,
  border: "1px solid var(--border)",
  background: "var(--bg-input)",
  color: "var(--text-primary)",
  outline: "none",
  width: "100%",
};

const btnSmall: CSSProperties = {
  padding: "6px 10px",
  borderRadius: 8,
  fontSize: 11,
  border: "1px solid var(--border)",
  background: "var(--bg-surface)",
  color: "var(--text-secondary)",
  cursor: "pointer",
  whiteSpace: "nowrap",
};

const memCard: CSSProperties = {
  padding: "8px 10px",
  borderRadius: 8,
  border: "1px solid var(--border)",
  background: "var(--bg-surface)",
};
