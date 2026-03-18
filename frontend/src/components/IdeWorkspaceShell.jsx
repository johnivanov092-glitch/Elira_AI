import { useEffect, useState } from "react";
import { api } from "../api/ide";
import FileExplorerPanel from "./FileExplorerPanel";
import TerminalPanel from "./TerminalPanel";

export default function IdeWorkspaceShell({ onBackToChat }) {
  const [files, setFiles] = useState([]);
  const [selectedPath, setSelectedPath] = useState("");
  const [content, setContent] = useState("");
  const [logs, setLogs] = useState([]);

  useEffect(() => {
    loadSnapshot();
  }, []);

  async function loadSnapshot() {
    try {
      const payload = await api.getProjectSnapshot();
      setFiles(payload?.files || payload?.items || []);
    } catch (e) {
      setLogs((prev) => [`Ошибка snapshot: ${e.message}`, ...prev]);
    }
  }

  async function openFile(path) {
    try {
      const payload = await api.getProjectFile(path);
      setSelectedPath(path);
      setContent(payload?.content || "");
      setLogs((prev) => [`Открыт файл: ${path}`, ...prev]);
    } catch (e) {
      setLogs((prev) => [`Ошибка файла: ${e.message}`, ...prev]);
    }
  }

  return (
    <div className="ide-shell">
      <div className="ide-toolbar">
        <button className="soft-btn" onClick={onBackToChat}>← Chat</button>
        <div className="ide-title">Code Workspace</div>
      </div>

      <div className="ide-grid">
        <div className="ide-col ide-left">
          <FileExplorerPanel files={files} selectedPath={selectedPath} onOpen={openFile} />
        </div>

        <div className="ide-col ide-center">
          <div className="editor-card">
            <div className="editor-title">{selectedPath || "Файл не выбран"}</div>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              className="editor-textarea"
              placeholder="Открой файл слева"
            />
          </div>
        </div>

        <div className="ide-col ide-right">
          <TerminalPanel logs={logs} />
        </div>
      </div>
    </div>
  );
}
