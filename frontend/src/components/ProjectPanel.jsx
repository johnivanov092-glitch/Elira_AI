/**
 * ProjectPanel.jsx — открытие и навигация по проекту.
 * Кнопка "Открыть проект" → ввод пути → дерево файлов → просмотр.
 */
import { useState, useEffect } from "react";

const API = import.meta.env.VITE_API_BASE_URL || `http://${window.location.hostname}:8000`;

async function fetchJson(path, options = {}) {
  const resp = await fetch(`${API}${path}`, { headers: { "Content-Type": "application/json" }, ...options });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

export default function ProjectPanel() {
  const [project, setProject] = useState(null);
  const [tree, setTree] = useState([]);
  const [pathInput, setPathInput] = useState("");
  const [selectedFile, setSelectedFile] = useState(null);
  const [fileContent, setFileContent] = useState("");
  const [search, setSearch] = useState("");
  const [searchResults, setSearchResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchJson("/api/advanced/project/info").then(d => {
      if (d.ok) { setProject(d); loadTree(); }
    }).catch((e) => { setError("Не удалось загрузить проект: " + (e.message || "")); });
  }, []);

  async function openProject() {
    const path = pathInput.trim();
    if (!path) return;
    setLoading(true);
    const d = await fetchJson("/api/advanced/project/open", { method: "POST", body: JSON.stringify({ path }) });
    if (d.ok) {
      setProject(d);
      await loadTree();
    }
    setLoading(false);
  }

  async function loadTree() {
    const d = await fetchJson("/api/advanced/project/tree?max_depth=3&max_items=300");
    if (d.ok) setTree(d.items || []);
  }

  async function readFile(path) {
    setSelectedFile(path);
    const d = await fetchJson("/api/advanced/project/read", { method: "POST", body: JSON.stringify({ path }) });
    if (d.ok) setFileContent(d.content || "");
    else setFileContent(`Ошибка: ${d.error}`);
  }

  async function handleSearch() {
    if (!search.trim()) return;
    const d = await fetchJson("/api/advanced/project/search", { method: "POST", body: JSON.stringify({ query: search }) });
    setSearchResults(d.items || []);
  }

  async function closeProject() {
    await fetchJson("/api/advanced/project/close");
    setProject(null); setTree([]); setSelectedFile(null); setFileContent("");
  }

  const dirs = tree.filter(i => i.type === "dir");
  const files = tree.filter(i => i.type === "file");
  const iconFor = (ext) => {
    if ([".py",".js",".jsx",".ts",".tsx"].includes(ext)) return "◈";
    if ([".css",".html",".yml",".json"].includes(ext)) return "◇";
    if ([".md",".txt"].includes(ext)) return "📄";
    return "○";
  };

  if (!project) {
    return (
      <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ fontSize: 14, fontWeight: 500, color: "var(--text-primary)" }}>📂 Открыть проект</div>
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Укажи путь к папке проекта</div>
        <div style={{ display: "flex", gap: 6 }}>
          <input
            value={pathInput}
            onChange={e => setPathInput(e.target.value)}
            onKeyDown={e => e.key === "Enter" && openProject()}
            placeholder="D:\MyProject или /home/user/project"
            style={{ flex: 1, padding: "8px 12px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-input)", color: "var(--text-primary)", fontSize: 12, outline: "none" }}
          />
          <button onClick={openProject} disabled={loading} style={{ padding: "8px 16px", borderRadius: 8, border: "1px solid var(--accent)", background: "var(--accent-dim)", color: "var(--accent)", cursor: "pointer", fontSize: 12 }}>
            {loading ? "..." : "Открыть"}
          </button>
        </div>
        <div style={{ fontSize: 10, color: "var(--text-muted)", lineHeight: 1.5 }}>
          Jarvis получит доступ к файлам проекта — сможет анализировать код, искать по содержимому и предлагать изменения.
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
        <span style={{ fontSize: 13 }}>📂</span>
        <span style={{ fontSize: 12, fontWeight: 500, color: "var(--text-primary)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{project.name || project.path}</span>
        <button onClick={closeProject} style={{ border: "none", background: "transparent", color: "var(--text-muted)", cursor: "pointer", fontSize: 11 }}>✕ Закрыть</button>
      </div>

      {/* Search */}
      <div style={{ display: "flex", gap: 4, padding: "6px 12px", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
        <input value={search} onChange={e => setSearch(e.target.value)} onKeyDown={e => e.key === "Enter" && handleSearch()} placeholder="Поиск по проекту..." style={{ flex: 1, padding: "4px 8px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-input)", color: "var(--text-primary)", fontSize: 11, outline: "none" }} />
        <button onClick={handleSearch} style={{ padding: "4px 8px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-surface)", color: "var(--text-muted)", cursor: "pointer", fontSize: 11 }}>🔍</button>
      </div>

      {/* Body */}
      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "220px 1fr", minHeight: 0 }}>
        {/* File tree */}
        <div style={{ borderRight: "1px solid var(--border)", overflow: "auto", padding: "4px 0" }}>
          {searchResults.length > 0 ? (
            <div>
              <div style={{ padding: "4px 12px", fontSize: 10, color: "var(--text-muted)" }}>Результаты: {searchResults.length}</div>
              {searchResults.map((r) => (
                <button key={`${r.path}:${r.line}`} onClick={() => readFile(r.path)} style={treeItem(selectedFile === r.path)}>
                  <span style={{ fontSize: 10 }}>📍</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 11 }}>{r.path}</div>
                    <div style={{ fontSize: 9, color: "var(--text-muted)" }}>L{r.line}: {r.text?.slice(0, 60)}</div>
                  </div>
                </button>
              ))}
              <button onClick={() => setSearchResults([])} style={{ ...treeItem(false), color: "var(--text-muted)", fontStyle: "italic" }}>← Назад к дереву</button>
            </div>
          ) : (
            <>
              <div style={{ padding: "4px 12px", fontSize: 10, color: "var(--text-muted)" }}>{files.length} файлов, {dirs.length} папок</div>
              {tree.map((item) => (
                <button key={item.path} onClick={() => item.type === "file" && readFile(item.path)} style={treeItem(selectedFile === item.path)} disabled={item.type === "dir"}>
                  <span style={{ fontSize: 10, opacity: 0.6 }}>{item.type === "dir" ? "📁" : iconFor(item.ext)}</span>
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 11, color: item.type === "dir" ? "var(--text-muted)" : "var(--text-primary)", paddingLeft: item.path.split("/").length > 1 ? (item.path.split("/").length - 1) * 8 : 0 }}>{item.name}</span>
                </button>
              ))}
            </>
          )}
        </div>

        {/* File content */}
        <div style={{ overflow: "auto" }}>
          {selectedFile ? (
            <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
              <div style={{ padding: "6px 12px", borderBottom: "1px solid var(--border)", fontSize: 11, color: "var(--text-muted)", flexShrink: 0 }}>{selectedFile}</div>
              <pre style={{ flex: 1, margin: 0, padding: 12, fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.5, color: "var(--text-primary)", overflow: "auto", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{fileContent}</pre>
            </div>
          ) : (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--text-muted)", fontSize: 12 }}>Выбери файл слева</div>
          )}
        </div>
      </div>
    </div>
  );
}

const treeItem = (active) => ({
  display: "flex", alignItems: "center", gap: 6, width: "100%",
  padding: "3px 12px", border: "none", cursor: "pointer", textAlign: "left",
  background: active ? "var(--bg-surface-active)" : "transparent",
  color: "var(--text-primary)", fontSize: 11,
});
