import { useEffect, useMemo, useState } from "react";
import { api } from "../api/ide";
import FileExplorer from "./FileExplorer";
import CodeEditor from "./CodeEditor";
import DiffViewer from "./DiffViewer";
import TerminalPanel from "./TerminalPanel";
import PatchHistoryPanel from "./PatchHistoryPanel";
import BatchVerifyPanel from "./BatchVerifyPanel";
import ProjectMapPanel from "./ProjectMapPanel";
import PatchPlanPanel from "./PatchPlanPanel";
import FileOpsPanel from "./FileOpsPanel";

export default function CodeWorkspace() {
  const [files, setFiles] = useState([]);
  const [selectedPath, setSelectedPath] = useState("");
  const [editorValue, setEditorValue] = useState("");
  const [originalValue, setOriginalValue] = useState("");
  const [instruction, setInstruction] = useState("Улучши выбранный файл безопасно и без лишних переписываний.");
  const [previewValue, setPreviewValue] = useState("");
  const [logs, setLogs] = useState([]);
  const [loadingFiles, setLoadingFiles] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [verifyResult, setVerifyResult] = useState(null);
  const [applyLoading, setApplyLoading] = useState(false);
  const [rollbackLoading, setRollbackLoading] = useState(false);
  const [verifyLoading, setVerifyLoading] = useState(false);
  const [diffText, setDiffText] = useState("");
  const [diffStats, setDiffStats] = useState(null);
  const [historyItems, setHistoryItems] = useState([]);
  const [selectedHistoryId, setSelectedHistoryId] = useState(null);
  const [selectedHistoryItem, setSelectedHistoryItem] = useState(null);
  const [stagedPaths, setStagedPaths] = useState([]);
  const [stagedContents, setStagedContents] = useState({});
  const [batchVerifyResult, setBatchVerifyResult] = useState(null);
  const [batchLoading, setBatchLoading] = useState(false);
  const [projectMap, setProjectMap] = useState(null);
  const [patchPlan, setPatchPlan] = useState(null);

  useEffect(() => {
    loadFiles();
    loadProjectMap();
  }, []);

  useEffect(() => {
    if (selectedPath) {
      loadHistory(selectedPath);
    } else {
      setHistoryItems([]);
      setSelectedHistoryId(null);
      setSelectedHistoryItem(null);
    }
  }, [selectedPath]);

  async function loadFiles() {
    try {
      setLoadingFiles(true);
      appendLog("Загрузка snapshot проекта...");
      const payload = await api.getProjectSnapshot();
      const items = payload?.files || payload?.items || [];
      setFiles(items);
      appendLog(`Snapshot загружен: ${items.length} файлов.`);
    } catch (e) {
      appendLog(`Ошибка загрузки snapshot: ${e.message || "unknown error"}`);
    } finally {
      setLoadingFiles(false);
    }
  }

  async function loadProjectMap() {
    try {
      const result = await api.getProjectMap();
      setProjectMap(result);
      appendLog(`Project Map загружен: ${result.count || 0} файлов.`);
    } catch (e) {
      appendLog(`Ошибка project map: ${e.message || "unknown error"}`);
    }
  }

  async function loadHistory(path) {
    try {
      const items = await api.listPatchHistory(path);
      setHistoryItems(items);
    } catch (e) {
      appendLog(`Ошибка истории патчей: ${e.message || "unknown error"}`);
    }
  }

  async function openFile(file) {
    try {
      appendLog(`Открытие файла: ${file.path}`);
      setSelectedPath(file.path);
      setVerifyResult(null);
      setSelectedHistoryId(null);
      setSelectedHistoryItem(null);

      const payload = await api.getProjectFile(file.path);
      const content = payload?.content || "";

      setEditorValue(content);
      setOriginalValue(content);
      setPreviewValue("");
      setDiffText("");
      setDiffStats(null);

      setStagedContents((prev) => ({
        ...prev,
        [file.path]: content,
      }));

      appendLog(`Файл открыт: ${file.path}`);
    } catch (e) {
      appendLog(`Ошибка чтения файла: ${e.message || "unknown error"}`);
    }
  }

  function appendLog(message) {
    setLogs((prev) => [
      `${new Date().toLocaleTimeString()}  ${message}`,
      ...prev,
    ].slice(0, 160));
  }

  async function buildDiff(original, updated) {
    if (!selectedPath) return;

    try {
      const result = await api.diffPatch({
        path: selectedPath,
        original,
        updated,
      });
      setDiffText(result?.diff_text || "");
      setDiffStats(result?.stats || null);
    } catch (e) {
      appendLog(`Ошибка diff: ${e.message || "unknown error"}`);
    }
  }

  async function handlePreviewPatch() {
    if (!selectedPath) {
      appendLog("Сначала выбери файл.");
      return;
    }

    try {
      setPreviewLoading(true);
      appendLog(`Preview patch: ${selectedPath}`);

      const payload = await api.previewPatch({
        path: selectedPath,
        instruction,
        content: editorValue,
      });

      const updated =
        payload?.updated_content ||
        payload?.content ||
        payload?.answer ||
        "";

      setPreviewValue(updated);
      await buildDiff(originalValue, updated);
      appendLog("Preview patch готов.");
    } catch (e) {
      appendLog(`Ошибка preview patch: ${e.message || "unknown error"}`);
    } finally {
      setPreviewLoading(false);
    }
  }

  async function handleApplyLocalPreview() {
    if (!previewValue) {
      appendLog("Нет preview для применения.");
      return;
    }

    setEditorValue(previewValue);
    setVerifyResult(null);
    setStagedContents((prev) => ({
      ...prev,
      [selectedPath]: previewValue,
    }));
    await buildDiff(originalValue, previewValue);
    appendLog("Preview применён локально в редактор.");
  }

  function toggleStage(path) {
    setStagedPaths((prev) =>
      prev.includes(path) ? prev.filter((item) => item !== path) : [...prev, path]
    );

    setStagedContents((prev) => ({
      ...prev,
      [path]: path === selectedPath ? editorValue : (prev[path] ?? ""),
    }));
  }

  useEffect(() => {
    if (selectedPath) {
      setStagedContents((prev) => ({
        ...prev,
        [selectedPath]: editorValue,
      }));
    }
  }, [editorValue, selectedPath]);

  async function handleApplyToDisk() {
    if (!selectedPath) {
      appendLog("Нет выбранного файла.");
      return;
    }

    try {
      setApplyLoading(true);
      appendLog(`Apply patch to disk: ${selectedPath}`);

      const result = await api.applyPatch({
        path: selectedPath,
        content: editorValue,
      });

      setOriginalValue(editorValue);
      setPreviewValue("");
      setVerifyResult(null);
      await buildDiff(editorValue, editorValue);

      appendLog(`Patch применён: ${result.path}`);
      await loadHistory(selectedPath);
    } catch (e) {
      appendLog(`Ошибка apply patch: ${e.message || "unknown error"}`);
    } finally {
      setApplyLoading(false);
    }
  }

  async function handleApplyBatch() {
    if (!stagedPaths.length) {
      appendLog("Нет staged файлов.");
      return;
    }

    try {
      setBatchLoading(true);

      const items = stagedPaths.map((path) => ({
        path,
        content:
          path === selectedPath
            ? editorValue
            : (stagedContents[path] ?? ""),
      }));

      appendLog(`Batch apply: ${items.length} файлов`);

      const result = await api.applyPatchBatch(items);
      appendLog(`Batch apply завершён: ${result.count} файлов`);

      if (selectedPath && stagedPaths.includes(selectedPath)) {
        setOriginalValue(editorValue);
      }

      await loadHistory(selectedPath || "");
      await loadFiles();
    } catch (e) {
      appendLog(`Ошибка batch apply: ${e.message || "unknown error"}`);
    } finally {
      setBatchLoading(false);
    }
  }

  async function handleRollbackDisk() {
    if (!selectedPath) {
      appendLog("Нет выбранного файла.");
      return;
    }

    try {
      setRollbackLoading(true);
      appendLog(`Rollback from backup: ${selectedPath}`);

      await api.rollbackPatch({ path: selectedPath });

      const payload = await api.getProjectFile(selectedPath);
      const content = payload?.content || "";

      setEditorValue(content);
      setOriginalValue(content);
      setPreviewValue("");
      setVerifyResult(null);

      setStagedContents((prev) => ({
        ...prev,
        [selectedPath]: content,
      }));

      await buildDiff(content, content);
      appendLog("Rollback выполнен.");
      await loadHistory(selectedPath);
    } catch (e) {
      appendLog(`Ошибка rollback: ${e.message || "unknown error"}`);
    } finally {
      setRollbackLoading(false);
    }
  }

  async function handleVerify() {
    if (!selectedPath) {
      appendLog("Нет выбранного файла.");
      return;
    }

    try {
      setVerifyLoading(true);
      appendLog(`Verify: ${selectedPath}`);

      const result = await api.verifyPatch({
        path: selectedPath,
        content: editorValue,
      });

      setVerifyResult(result);
      setDiffText(result?.diff_text || "");
      setDiffStats(result?.stats || null);

      appendLog("Verify завершён.");
    } catch (e) {
      appendLog(`Ошибка verify: ${e.message || "unknown error"}`);
    } finally {
      setVerifyLoading(false);
    }
  }

  async function handleVerifyBatch() {
    if (!stagedPaths.length) {
      appendLog("Нет staged файлов для verify.");
      return;
    }

    try {
      setBatchLoading(true);

      const items = stagedPaths.map((path) => ({
        path,
        content:
          path === selectedPath
            ? editorValue
            : (stagedContents[path] ?? ""),
      }));

      appendLog(`Batch verify: ${items.length} файлов`);

      const result = await api.verifyPatchBatch(items);
      setBatchVerifyResult(result);

      appendLog(`Batch verify завершён: ${result.count} файлов`);
    } catch (e) {
      appendLog(`Ошибка batch verify: ${e.message || "unknown error"}`);
    } finally {
      setBatchLoading(false);
    }
  }

  async function handleSelectHistory(item) {
    try {
      setSelectedHistoryId(item.id);

      const full = await api.getPatchHistoryItem(item.id);
      setSelectedHistoryItem(full);
      setDiffText(full?.diff_text || "");
      setDiffStats(full?.stats || null);

      appendLog(`Открыта история патча #${item.id}`);
    } catch (e) {
      appendLog(`Ошибка открытия history item: ${e.message || "unknown error"}`);
    }
  }

  async function handleBuildPlan() {
    try {
      const result = await api.patchPlan({
        goal: instruction,
        current_path: selectedPath,
        current_content: editorValue,
        staged_paths: stagedPaths,
      });
      setPatchPlan(result);
      appendLog("Patch plan построен.");
    } catch (e) {
      appendLog(`Ошибка patch plan: ${e.message || "unknown error"}`);
    }
  }

  async function handleCreateFile(path, content) {
    try {
      const result = await api.createFile({ path, content });
      appendLog(`Файл создан: ${result.path}`);
      await loadFiles();
      await loadProjectMap();
    } catch (e) {
      appendLog(`Ошибка create file: ${e.message || "unknown error"}`);
    }
  }

  async function handleRenameFile(oldPath, newPath) {
    if (!oldPath || !newPath) {
      appendLog("Для rename нужен текущий и новый путь.");
      return;
    }
    try {
      const result = await api.renameFile({ old_path: oldPath, new_path: newPath });
      appendLog(`Файл переименован: ${result.old_path} → ${result.new_path}`);
      if (selectedPath === oldPath) {
        setSelectedPath(newPath);
      }
      await loadFiles();
      await loadProjectMap();
    } catch (e) {
      appendLog(`Ошибка rename file: ${e.message || "unknown error"}`);
    }
  }

  async function handleDeleteFile(path) {
    if (!path) {
      appendLog("Нет выбранного файла для удаления.");
      return;
    }
    try {
      const result = await api.deleteFile({ path });
      appendLog(`Файл удалён: ${result.path}`);
      if (selectedPath === path) {
        setSelectedPath("");
        setEditorValue("");
        setOriginalValue("");
        setPreviewValue("");
        setDiffText("");
        setDiffStats(null);
      }
      await loadFiles();
      await loadProjectMap();
    } catch (e) {
      appendLog(`Ошибка delete file: ${e.message || "unknown error"}`);
    }
  }

  const stagedCount = useMemo(() => stagedPaths.length, [stagedPaths]);

  return (
    <div className="code-workspace-v5">
      <div className="code-left">
        <FileExplorer
          files={files}
          selectedPath={selectedPath}
          stagedPaths={stagedPaths}
          onSelect={openFile}
          onToggleStage={toggleStage}
        />
      </div>

      <div className="code-center">
        <CodeEditor
          filePath={selectedPath}
          value={editorValue}
          onChange={setEditorValue}
        />

        <div className="patch-controls">
          <div className="patch-controls-title">Patch Engine</div>

          <textarea
            className="patch-instruction"
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            spellCheck={false}
          />

          <div className="patch-buttons">
            <button
              className="soft-btn"
              onClick={handlePreviewPatch}
              disabled={previewLoading || loadingFiles}
            >
              {previewLoading ? "Preview..." : "Preview Patch"}
            </button>

            <button className="soft-btn" onClick={handleApplyLocalPreview}>
              Apply to Editor
            </button>

            <button
              className="soft-btn"
              onClick={handleApplyToDisk}
              disabled={applyLoading}
            >
              {applyLoading ? "Applying..." : "Apply Patch"}
            </button>

            <button
              className="soft-btn"
              onClick={handleRollbackDisk}
              disabled={rollbackLoading}
            >
              {rollbackLoading ? "Rollback..." : "Rollback"}
            </button>

            <button
              className="soft-btn"
              onClick={handleVerify}
              disabled={verifyLoading}
            >
              {verifyLoading ? "Verify..." : "Verify"}
            </button>
          </div>

          <div className="batch-bar">
            <div className="batch-bar-meta">Staged: {stagedCount}</div>

            <div className="patch-buttons">
              <button
                className="soft-btn"
                onClick={handleVerifyBatch}
                disabled={batchLoading}
              >
                {batchLoading ? "Batch..." : "Verify Staged"}
              </button>

              <button
                className="soft-btn"
                onClick={handleApplyBatch}
                disabled={batchLoading}
              >
                {batchLoading ? "Batch..." : "Apply Staged"}
              </button>
            </div>
          </div>
        </div>

        <DiffViewer
          diffText={diffText}
          stats={diffStats}
          loading={previewLoading}
        />
      </div>

      <div className="code-right">
        <PatchPlanPanel
          plan={patchPlan}
          onBuildPlan={handleBuildPlan}
        />

        <ProjectMapPanel
          projectMap={projectMap}
          onRefresh={loadProjectMap}
        />

        <FileOpsPanel
          onCreate={handleCreateFile}
          onRename={handleRenameFile}
          onDelete={handleDeleteFile}
          selectedPath={selectedPath}
        />

        <PatchHistoryPanel
          items={historyItems}
          selectedId={selectedHistoryId}
          onSelect={handleSelectHistory}
        />

        <BatchVerifyPanel result={batchVerifyResult} />

        <TerminalPanel
          logs={logs}
          verifyResult={verifyResult}
          historyItem={selectedHistoryItem}
        />
      </div>
    </div>
  );
}
