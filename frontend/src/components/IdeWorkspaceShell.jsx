import { useEffect, useMemo, useState } from "react";
import { api } from "../api/ide";
import FileExplorerPanel from "./FileExplorerPanel";

function pushLog(setLogs, line) {
  setLogs((prev) => [
    `${new Date().toLocaleTimeString()} · ${line}`,
    ...prev,
  ].slice(0, 120));
}

export default function IdeWorkspaceShell({ onBackToChat }) {
  const [files, setFiles] = useState([]);
  const [selectedPath, setSelectedPath] = useState("");
  const [originalContent, setOriginalContent] = useState("");
  const [content, setContent] = useState("");
  const [diffText, setDiffText] = useState("");
  const [diffStats, setDiffStats] = useState(null);
  const [verifyResult, setVerifyResult] = useState(null);
  const [historyItems, setHistoryItems] = useState([]);
  const [logs, setLogs] = useState([]);
  const [busy, setBusy] = useState(false);
  const [loadingFile, setLoadingFile] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    loadSnapshot();
  }, []);

  const isDirty = useMemo(() => content !== originalContent, [content, originalContent]);

  async function loadSnapshot() {
    try {
      setError("");
      const payload = await api.getProjectSnapshot();
      const nextFiles = Array.isArray(payload?.files) ? payload.files : [];
      setFiles(nextFiles);
      pushLog(setLogs, `Загружен snapshot: ${nextFiles.length} файлов`);
    } catch (e) {
      setError(e.message || "Ошибка snapshot");
      pushLog(setLogs, `Ошибка snapshot: ${e.message}`);
    }
  }

  async function openFile(path) {
    try {
      setLoadingFile(true);
      setError("");
      const payload = await api.getProjectFile(path);
      const text = payload?.content || "";
      setSelectedPath(path);
      setOriginalContent(text);
      setContent(text);
      setDiffText("");
      setDiffStats(null);
      setVerifyResult(null);
      await loadHistory(path);
      pushLog(setLogs, `Открыт файл: ${path}`);
    } catch (e) {
      setError(e.message || "Ошибка чтения файла");
      pushLog(setLogs, `Ошибка файла: ${e.message}`);
    } finally {
      setLoadingFile(false);
    }
  }

  async function loadHistory(path = selectedPath) {
    if (!path) {
      setHistoryItems([]);
      return;
    }
    try {
      const payload = await api.listPatchHistory({ path, limit: 20 });
      setHistoryItems(Array.isArray(payload?.items) ? payload.items : []);
    } catch (e) {
      pushLog(setLogs, `История недоступна: ${e.message}`);
    }
  }

  async function handlePreviewPatch() {
    if (!selectedPath) return;
    try {
      setBusy(true);
      setError("");
      const payload = await api.previewPatch({
        path: selectedPath,
        original: originalContent,
        updated: content,
      });
      setDiffText(payload?.diff_text || "");
      setDiffStats(payload?.stats || null);
      pushLog(setLogs, `Preview patch: ${selectedPath}`);
    } catch (e) {
      setError(e.message || "Ошибка preview patch");
      pushLog(setLogs, `Ошибка preview: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  async function handleApplyPatch() {
    if (!selectedPath) return;
    try {
      setBusy(true);
      setError("");
      await api.applyPatch({ path: selectedPath, content });
      setOriginalContent(content);
      await loadHistory(selectedPath);
      await handleVerifyPatch();
      pushLog(setLogs, `Patch применён: ${selectedPath}`);
    } catch (e) {
      setError(e.message || "Ошибка apply patch");
      pushLog(setLogs, `Ошибка apply: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  async function handleRollbackPatch() {
    if (!selectedPath) return;
    try {
      setBusy(true);
      setError("");
      await api.rollbackPatch({ path: selectedPath });
      await openFile(selectedPath);
      pushLog(setLogs, `Rollback выполнен: ${selectedPath}`);
    } catch (e) {
      setError(e.message || "Ошибка rollback");
      pushLog(setLogs, `Ошибка rollback: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  async function handleVerifyPatch() {
    if (!selectedPath) return;
    try {
      setBusy(true);
      setError("");
      const payload = await api.verifyPatch({ path: selectedPath, content });
      setVerifyResult(payload || null);
      if (!diffText && payload?.diff_text) {
        setDiffText(payload.diff_text);
        setDiffStats(payload.stats || null);
      }
      pushLog(setLogs, `Verify выполнен: ${selectedPath}`);
    } catch (e) {
      setError(e.message || "Ошибка verify");
      pushLog(setLogs, `Ошибка verify: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="ide-shell"
      style={{
        display: "grid",
        gridTemplateRows: "auto auto 1fr",
        gap: 14,
        height: "100%",
        minHeight: 0,
        padding: 16,
        boxSizing: "border-box",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <button
          type="button"
          onClick={onBackToChat}
          style={{
            borderRadius: 10,
            border: "1px solid rgba(255,255,255,0.12)",
            background: "rgba(255,255,255,0.04)",
            color: "inherit",
            padding: "10px 14px",
            cursor: "pointer",
          }}
        >
          ← Chat
        </button>

        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <button type="button" onClick={loadSnapshot} disabled={busy} style={actionBtn()}>
            Refresh Snapshot
          </button>
          <button type="button" onClick={handlePreviewPatch} disabled={busy || !selectedPath} style={actionBtn()}>
            Preview Patch
          </button>
          <button type="button" onClick={handleApplyPatch} disabled={busy || !selectedPath || !isDirty} style={actionBtn(true)}>
            Apply Patch
          </button>
          <button type="button" onClick={handleRollbackPatch} disabled={busy || !selectedPath} style={actionBtn()}>
            Rollback
          </button>
          <button type="button" onClick={handleVerifyPatch} disabled={busy || !selectedPath} style={actionBtn()}>
            Verify
          </button>
        </div>
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 16,
          borderRadius: 12,
          padding: "12px 14px",
          background: "rgba(255,255,255,0.03)",
          border: "1px solid rgba(255,255,255,0.06)",
        }}
      >
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700 }}>Code Workspace</div>
          <div style={{ fontSize: 12, opacity: 0.72, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {selectedPath || "Файл не выбран"}
          </div>
        </div>
        <div style={{ fontSize: 12, opacity: 0.72 }}>
          {loadingFile ? "Открытие файла..." : busy ? "Выполнение..." : isDirty ? "Есть несохранённые изменения" : "Синхронизировано"}
        </div>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "320px minmax(0, 1fr) 360px",
          gap: 14,
          minHeight: 0,
        }}
      >
        <FileExplorerPanel files={files} selectedPath={selectedPath} onOpen={openFile} />

        <div
          style={{
            minHeight: 0,
            display: "grid",
            gridTemplateRows: "1fr auto auto",
            gap: 12,
          }}
        >
          <div
            style={{
              minHeight: 0,
              borderRadius: 14,
              border: "1px solid rgba(255,255,255,0.08)",
              background: "rgba(255,255,255,0.03)",
              padding: 12,
              display: "flex",
              flexDirection: "column",
              gap: 10,
            }}
          >
            <div style={{ fontSize: 12, opacity: 0.72 }}>Editor</div>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              className="editor-textarea"
              placeholder="Открой файл слева"
              spellCheck={false}
              style={{
                flex: 1,
                minHeight: 0,
                width: "100%",
                resize: "none",
                borderRadius: 12,
                border: "1px solid rgba(255,255,255,0.08)",
                background: "rgba(0,0,0,0.18)",
                color: "inherit",
                padding: 14,
                boxSizing: "border-box",
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
                fontSize: 13,
                lineHeight: 1.55,
                outline: "none",
              }}
            />
          </div>

          <div
            style={{
              borderRadius: 14,
              border: "1px solid rgba(255,255,255,0.08)",
              background: "rgba(255,255,255,0.03)",
              padding: 12,
              display: "flex",
              flexDirection: "column",
              gap: 8,
              maxHeight: 220,
              overflow: "auto",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
              <div style={{ fontSize: 12, opacity: 0.72 }}>Diff Preview</div>
              {diffStats ? (
                <div style={{ fontSize: 11, opacity: 0.72 }}>
                  +{diffStats.added ?? 0} / -{diffStats.removed ?? 0}
                </div>
              ) : null}
            </div>
            <pre
              style={{
                margin: 0,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
                fontSize: 12,
                lineHeight: 1.45,
                opacity: diffText ? 0.96 : 0.65,
              }}
            >
              {diffText || "Сначала нажми Preview Patch."}
            </pre>
          </div>

          {error ? (
            <div
              style={{
                borderRadius: 12,
                padding: "10px 12px",
                background: "rgba(255,80,80,0.12)",
                border: "1px solid rgba(255,80,80,0.25)",
                color: "#ffb6b6",
                fontSize: 12,
              }}
            >
              {error}
            </div>
          ) : null}
        </div>

        <div
          style={{
            minHeight: 0,
            display: "grid",
            gridTemplateRows: "auto auto 1fr",
            gap: 12,
          }}
        >
          <div panel style={panelStyle()}>
            <div style={{ fontSize: 12, opacity: 0.72 }}>Verify</div>
            {verifyResult ? (
              <>
                <div style={{ fontSize: 12 }}>
                  changed_vs_disk: {String(Boolean(verifyResult.changed_vs_disk))}
                </div>
                <div style={{ fontSize: 11, opacity: 0.72 }}>
                  +{verifyResult?.stats?.added ?? 0} / -{verifyResult?.stats?.removed ?? 0}
                </div>
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, lineHeight: 1.45 }}>
                  {(verifyResult.checks || []).map((item, idx) => (
                    <li key={`${item}-${idx}`}>{item}</li>
                  ))}
                </ul>
              </>
            ) : (
              <div style={{ fontSize: 12, opacity: 0.65 }}>Пока пусто.</div>
            )}
          </div>

          <div style={panelStyle()}>
            <div style={{ fontSize: 12, opacity: 0.72 }}>Patch History</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 180, overflow: "auto" }}>
              {historyItems.length ? historyItems.map((item) => (
                <div
                  key={item.id}
                  style={{
                    borderRadius: 10,
                    padding: "8px 10px",
                    background: "rgba(255,255,255,0.04)",
                    border: "1px solid rgba(255,255,255,0.06)",
                  }}
                >
                  <div style={{ fontSize: 12 }}>{item.action || "action"}</div>
                  <div style={{ fontSize: 11, opacity: 0.65 }}>{item.created_at || ""}</div>
                </div>
              )) : <div style={{ fontSize: 12, opacity: 0.65 }}>История по файлу пока пуста.</div>}
            </div>
          </div>

          <div style={panelStyle({ minHeight: 0 })}>
            <div style={{ fontSize: 12, opacity: 0.72 }}>Логи</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6, overflow: "auto", minHeight: 0 }}>
              {logs.length ? logs.map((line, idx) => (
                <div
                  key={`${line}-${idx}`}
                  style={{
                    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
                    fontSize: 11,
                    lineHeight: 1.45,
                    opacity: 0.85,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                  }}
                >
                  {line}
                </div>
              )) : <div style={{ fontSize: 12, opacity: 0.65 }}>Пока пусто.</div>}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function actionBtn(primary = false) {
  return {
    borderRadius: 10,
    border: primary
      ? "1px solid rgba(120,180,255,0.45)"
      : "1px solid rgba(255,255,255,0.12)",
    background: primary ? "rgba(120,180,255,0.18)" : "rgba(255,255,255,0.04)",
    color: "inherit",
    padding: "10px 14px",
    cursor: "pointer",
  };
}

function panelStyle(extra = {}) {
  return {
    borderRadius: 14,
    border: "1px solid rgba(255,255,255,0.08)",
    background: "rgba(255,255,255,0.03)",
    padding: 12,
    display: "flex",
    flexDirection: "column",
    gap: 8,
    ...extra,
  };
}
