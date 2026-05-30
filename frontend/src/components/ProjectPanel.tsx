import { useEffect, useState, type CSSProperties } from "react";
import { api } from "../api/ide";
import type { ProjectResponse, SavedProject } from "../api/project";

type ProjectInfo = ProjectResponse & {
  name?: string;
  ok?: boolean;
  path?: string;
};

type ProjectTreeItem = {
  ext?: string;
  name: string;
  path: string;
  type?: string;
  [key: string]: unknown;
};

type ProjectSearchResult = {
  line?: number | string;
  path: string;
  text?: string;
  [key: string]: unknown;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function errorMessage(error: unknown, fallback = ""): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function normalizeTreeItems(items: unknown): ProjectTreeItem[] {
  if (!Array.isArray(items)) return [];
  return items.filter(isRecord).map((item) => {
    const path = String(item.path ?? "");
    return {
      ...item,
      ext: typeof item.ext === "string" ? item.ext : "",
      name: String(item.name ?? path),
      path,
      type: typeof item.type === "string" ? item.type : "",
    };
  });
}

function normalizeSearchResults(items: unknown): ProjectSearchResult[] {
  if (!Array.isArray(items)) return [];
  return items.filter(isRecord).map((item) => ({
    ...item,
    line: typeof item.line === "number" || typeof item.line === "string" ? item.line : "",
    path: String(item.path ?? ""),
    text: typeof item.text === "string" ? item.text : String(item.text ?? ""),
  }));
}

export default function ProjectPanel() {
  const [project, setProject] = useState<ProjectInfo | null>(null);
  const [tree, setTree] = useState<ProjectTreeItem[]>([]);
  const [pathInput, setPathInput] = useState("");
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState("");
  const [search, setSearch] = useState("");
  const [searchResults, setSearchResults] = useState<ProjectSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<SavedProject[]>([]);
  const [switcherOpen, setSwitcherOpen] = useState(false);

  async function loadSaved() {
    try { setSaved(await api.listSavedProjects()); } catch { /* non-fatal */ }
  }

  async function openByPath(path: string) {
    const p = (path || "").trim();
    if (!p) return;
    setLoading(true);
    setError("");
    try {
      await api.addSavedProject(p);          // remember it in the registry
      const data = await api.openAdvancedProject(p);
      if (data?.ok) { setProject(data as ProjectInfo); await loadTree(); }
      else setError(`Не удалось открыть проект: ${typeof data?.error === "string" ? data.error : "неизвестно"}`);
      await loadSaved();
      setSwitcherOpen(false);
    } catch (e: unknown) {
      setError(`Не удалось открыть проект: ${errorMessage(e)}`);
    } finally {
      setLoading(false);
    }
  }

  async function removeSaved(id: string) {
    try { await api.removeSavedProject(id); await loadSaved(); } catch { /* non-fatal */ }
  }

  useEffect(() => {
    loadSaved();
    api.getAdvancedProjectInfo()
      .then((data) => {
        if (!data?.ok) return;
        setProject(data as ProjectInfo);
        return loadTree();
      })
      .catch((e: unknown) => {
        setError(`Не удалось загрузить проект: ${errorMessage(e)}`);
      });
  }, []);

  async function openProject() {
    await openByPath(pathInput);
  }

  async function loadTree() {
    try {
      const data = await api.getAdvancedProjectTree({ maxDepth: 3, maxItems: 300 });
      if (data?.ok) setTree(normalizeTreeItems(data.items));
    } catch (e: unknown) {
      setError(`Не удалось загрузить дерево файлов: ${errorMessage(e)}`);
    }
  }

  async function readFile(path: string) {
    setSelectedFile(path);
    setError("");
    try {
      const data = await api.readAdvancedProjectFile(path);
      if (data?.ok) {
        setFileContent(typeof data.content === "string" ? data.content : String(data.content ?? ""));
      } else {
        setFileContent(`Ошибка: ${typeof data?.error === "string" ? data.error : "неизвестно"}`);
      }
    } catch (e: unknown) {
      const message = errorMessage(e, "Ошибка чтения");
      setError(message);
      setFileContent(`Ошибка: ${message}`);
    }
  }

  async function handleSearch() {
    if (!search.trim()) return;
    setError("");
    try {
      const data = await api.searchAdvancedProject(search);
      setSearchResults(normalizeSearchResults(data.items));
    } catch (e: unknown) {
      setError(`Не удалось выполнить поиск: ${errorMessage(e)}`);
    }
  }

  async function closeProject() {
    try {
      await api.closeAdvancedProject();
      setProject(null);
      setTree([]);
      setSelectedFile(null);
      setFileContent("");
      setSearchResults([]);
      setError("");
    } catch (e: unknown) {
      setError(`Не удалось закрыть проект: ${errorMessage(e)}`);
    }
  }

  const dirs = tree.filter((item) => item.type === "dir");
  const files = tree.filter((item) => item.type === "file");
  const iconFor = (ext = "") => {
    if ([".py", ".js", ".jsx", ".ts", ".tsx"].includes(ext)) return "●";
    if ([".css", ".html", ".yml", ".json"].includes(ext)) return "◆";
    if ([".md", ".txt"].includes(ext)) return "📄";
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
            onChange={(e) => setPathInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && openProject()}
            placeholder="D:\\MyProject или /home/user/project"
            style={{
              flex: 1,
              padding: "8px 12px",
              borderRadius: 8,
              border: "1px solid var(--border)",
              background: "var(--bg-input)",
              color: "var(--text-primary)",
              fontSize: 12,
              outline: "none",
            }}
          />
          <button
            onClick={openProject}
            disabled={loading}
            style={{
              padding: "8px 16px",
              borderRadius: 8,
              border: "1px solid var(--accent)",
              background: "var(--accent-dim)",
              color: "var(--accent)",
              cursor: "pointer",
              fontSize: 12,
            }}
          >
            {loading ? "..." : "Открыть"}
          </button>
        </div>
        {error && <div style={{ fontSize: 11, color: "#ff6b6b" }}>{error}</div>}

        {saved.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
            <div style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 500 }}>Сохранённые проекты</div>
            {saved.map((p) => (
              <div key={p.id} style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 8px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-surface)" }}>
                <button onClick={() => openByPath(p.path)} title={p.path} style={{ flex: 1, minWidth: 0, textAlign: "left", background: "transparent", border: "none", color: "var(--text-primary)", cursor: "pointer", padding: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.name}</div>
                  <div style={{ fontSize: 10, color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontFamily: "var(--font-mono)" }}>{p.path}</div>
                </button>
                <button onClick={() => removeSaved(p.id)} title="Удалить из списка" style={{ flexShrink: 0, background: "transparent", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: 13, padding: "2px 6px" }}>✕</button>
              </div>
            ))}
          </div>
        )}

        <div style={{ fontSize: 10, color: "var(--text-muted)", lineHeight: 1.5 }}>
          Elira получит доступ к файлам проекта, сможет анализировать код, искать по содержимому и
          предлагать изменения. Путь сохраняется в список — переключайся между проектами в один клик.
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "8px 12px",
          borderBottom: "1px solid var(--border)",
          flexShrink: 0,
        }}
      >
        <span style={{ fontSize: 13 }}>📂</span>
        <span
          style={{
            fontSize: 12,
            fontWeight: 500,
            color: "var(--text-primary)",
            flex: 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {project.name || project.path}
        </span>
        <div style={{ position: "relative", flexShrink: 0 }}>
          <button
            onClick={() => { if (!switcherOpen) loadSaved(); setSwitcherOpen((v) => !v); }}
            title="Переключить проект"
            style={{ border: "1px solid var(--border)", background: "var(--bg-surface)", color: "var(--text-secondary)", cursor: "pointer", fontSize: 11, padding: "4px 8px", borderRadius: 6 }}
          >
            ⇄ Проекты
          </button>
          {switcherOpen && (
            <div style={{ position: "absolute", top: "100%", right: 0, zIndex: 50, marginTop: 4, minWidth: 240, maxWidth: 340, maxHeight: 320, overflow: "auto", background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 8, boxShadow: "0 6px 20px rgba(0,0,0,.25)", padding: 4 }}>
              {saved.length === 0 && <div style={{ padding: 10, fontSize: 11, color: "var(--text-muted)" }}>Список пуст. Открой проект — он сохранится.</div>}
              {saved.map((p) => {
                const active = project.path === p.path;
                return (
                  <div key={p.id} style={{ display: "flex", alignItems: "center", gap: 4, padding: "5px 6px", borderRadius: 6, background: active ? "var(--accent-dim)" : "transparent" }}>
                    <button onClick={() => openByPath(p.path)} title={p.path} style={{ flex: 1, minWidth: 0, textAlign: "left", background: "transparent", border: "none", color: active ? "var(--accent)" : "var(--text-primary)", cursor: "pointer", padding: 0 }}>
                      <div style={{ fontSize: 12, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.name}</div>
                      <div style={{ fontSize: 9, color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontFamily: "var(--font-mono)" }}>{p.path}</div>
                    </button>
                    <button onClick={() => removeSaved(p.id)} title="Удалить из списка" style={{ flexShrink: 0, background: "transparent", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: 12, padding: "2px 5px" }}>✕</button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
        <button
          onClick={closeProject}
          style={{ border: "none", background: "transparent", color: "var(--text-muted)", cursor: "pointer", fontSize: 11 }}
        >
          ✕ Закрыть
        </button>
      </div>

      <div style={{ display: "flex", gap: 4, padding: "6px 12px", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          placeholder="Поиск по проекту..."
          style={{
            flex: 1,
            padding: "4px 8px",
            borderRadius: 6,
            border: "1px solid var(--border)",
            background: "var(--bg-input)",
            color: "var(--text-primary)",
            fontSize: 11,
            outline: "none",
          }}
        />
        <button
          onClick={handleSearch}
          style={{
            padding: "4px 8px",
            borderRadius: 6,
            border: "1px solid var(--border)",
            background: "var(--bg-surface)",
            color: "var(--text-muted)",
            cursor: "pointer",
            fontSize: 11,
          }}
        >
          🔍
        </button>
      </div>

      {error && (
        <div style={{ padding: "6px 12px", borderBottom: "1px solid var(--border)", fontSize: 11, color: "#ff6b6b" }}>
          {error}
        </div>
      )}

      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "220px 1fr", minHeight: 0 }}>
        <div style={{ borderRight: "1px solid var(--border)", overflow: "auto", padding: "4px 0" }}>
          {searchResults.length > 0 ? (
            <div>
              <div style={{ padding: "4px 12px", fontSize: 10, color: "var(--text-muted)" }}>
                Результаты: {searchResults.length}
              </div>
              {searchResults.map((result) => (
                <button key={`${result.path}:${result.line}`} onClick={() => readFile(result.path)} style={treeItem(selectedFile === result.path)}>
                  <span style={{ fontSize: 10 }}>📌</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 11 }}>
                      {result.path}
                    </div>
                    <div style={{ fontSize: 9, color: "var(--text-muted)" }}>L{result.line}: {result.text?.slice(0, 60)}</div>
                  </div>
                </button>
              ))}
              <button onClick={() => setSearchResults([])} style={{ ...treeItem(false), color: "var(--text-muted)", fontStyle: "italic" }}>
                ← Назад к дереву
              </button>
            </div>
          ) : (
            <>
              <div style={{ padding: "4px 12px", fontSize: 10, color: "var(--text-muted)" }}>
                {files.length} файлов, {dirs.length} папок
              </div>
              {tree.map((item) => (
                <button
                  key={item.path}
                  onClick={() => item.type === "file" && readFile(item.path)}
                  style={treeItem(selectedFile === item.path)}
                  disabled={item.type === "dir"}
                >
                  <span style={{ fontSize: 10, opacity: 0.6 }}>{item.type === "dir" ? "📁" : iconFor(item.ext)}</span>
                  <span
                    style={{
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      fontSize: 11,
                      color: item.type === "dir" ? "var(--text-muted)" : "var(--text-primary)",
                      paddingLeft: item.path.split("/").length > 1 ? (item.path.split("/").length - 1) * 8 : 0,
                    }}
                  >
                    {item.name}
                  </span>
                </button>
              ))}
            </>
          )}
        </div>

        <div style={{ overflow: "auto" }}>
          {selectedFile ? (
            <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
              <div style={{ padding: "6px 12px", borderBottom: "1px solid var(--border)", fontSize: 11, color: "var(--text-muted)", flexShrink: 0 }}>
                {selectedFile}
              </div>
              <pre
                style={{
                  flex: 1,
                  margin: 0,
                  padding: 12,
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  lineHeight: 1.5,
                  color: "var(--text-primary)",
                  overflow: "auto",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                {fileContent}
              </pre>
            </div>
          ) : (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--text-muted)", fontSize: 12 }}>
              Выбери файл слева
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const treeItem = (active: boolean): CSSProperties => ({
  display: "flex",
  alignItems: "center",
  gap: 6,
  width: "100%",
  padding: "3px 12px",
  border: "none",
  cursor: "pointer",
  textAlign: "left",
  background: active ? "var(--bg-surface-active)" : "transparent",
  color: "var(--text-primary)",
  fontSize: 11,
});
