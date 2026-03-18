import { useMemo, useState } from "react";

const BLOCKED_PARTS = new Set([
  ".git",
  ".idea",
  ".vscode",
  ".venv",
  "venv",
  "node_modules",
  "target",
  "dist",
  "build",
  "coverage",
  "__pycache__",
  ".next",
  ".turbo",
  ".cache",
  ".jarvis_chat_uploads",
]);

function isAllowedPath(path) {
  const parts = String(path || "").split("/").filter(Boolean);
  return !parts.some((part) => BLOCKED_PARTS.has(part));
}

export default function FileExplorerPanel({
  files = [],
  selectedPath = "",
  onOpen,
}) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const list = Array.isArray(files) ? files : [];
    const safe = list.filter((item) => isAllowedPath(item?.path || item?.name || ""));
    const q = query.trim().toLowerCase();
    if (!q) return safe;
    return safe.filter((item) => {
      const text = `${item?.path || ""} ${item?.name || ""}`.toLowerCase();
      return text.includes(q);
    });
  }, [files, query]);

  return (
    <div className="explorer-card" style={{ display: "flex", flexDirection: "column", gap: 12, minHeight: 0 }}>
      <div className="explorer-header" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 0.4, opacity: 0.9 }}>
          Файлы проекта
        </div>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Поиск файла"
          style={{
            width: "100%",
            boxSizing: "border-box",
            borderRadius: 10,
            border: "1px solid rgba(255,255,255,0.12)",
            background: "rgba(255,255,255,0.04)",
            color: "inherit",
            padding: "10px 12px",
            outline: "none",
          }}
        />
      </div>

      <div style={{ overflow: "auto", minHeight: 0, display: "flex", flexDirection: "column", gap: 6 }}>
        {filtered.length ? (
          filtered.map((item, index) => {
            const path = item?.path || item?.name || `file-${index}`;
            const isActive = path === selectedPath;
            return (
              <button
                key={path}
                type="button"
                onClick={() => onOpen?.(path)}
                title={path}
                style={{
                  textAlign: "left",
                  border: isActive ? "1px solid rgba(120,180,255,0.6)" : "1px solid rgba(255,255,255,0.06)",
                  background: isActive ? "rgba(120,180,255,0.16)" : "rgba(255,255,255,0.03)",
                  color: "inherit",
                  borderRadius: 10,
                  padding: "10px 12px",
                  cursor: "pointer",
                  display: "flex",
                  flexDirection: "column",
                  gap: 4,
                }}
              >
                <div style={{ fontSize: 12, lineHeight: 1.35, wordBreak: "break-word" }}>{path}</div>
                <div style={{ fontSize: 11, opacity: 0.65 }}>
                  {(item?.suffix || "").replace(/^\./, "") || "file"}
                  {typeof item?.size === "number" ? ` • ${item.size} B` : ""}
                </div>
              </button>
            );
          })
        ) : (
          <div style={{ opacity: 0.7, fontSize: 12 }}>Файлы не найдены.</div>
        )}
      </div>
    </div>
  );
}
