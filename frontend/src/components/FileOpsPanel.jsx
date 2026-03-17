import { useState } from "react";

export default function FileOpsPanel({
  onCreate,
  onRename,
  onDelete,
  selectedPath,
}) {
  const [createPath, setCreatePath] = useState("frontend/src/components/NewFeaturePanel.jsx");
  const [createContent, setCreateContent] = useState('export default function NewFeaturePanel(){\n  return <div>New Feature</div>\n}\n');
  const [renamePath, setRenamePath] = useState("");

  return (
    <div className="file-ops-panel">
      <div className="pane-title">File Ops</div>

      <div className="file-op-section">
        <div className="file-op-title">Create File</div>
        <input
          className="pane-input"
          value={createPath}
          onChange={(e) => setCreatePath(e.target.value)}
        />
        <textarea
          className="patch-instruction"
          value={createContent}
          onChange={(e) => setCreateContent(e.target.value)}
          spellCheck={false}
        />
        <button className="soft-btn" onClick={() => onCreate(createPath, createContent)}>
          Create
        </button>
      </div>

      <div className="file-op-section">
        <div className="file-op-title">Rename Selected File</div>
        <input
          className="pane-input"
          placeholder="Новый путь..."
          value={renamePath}
          onChange={(e) => setRenamePath(e.target.value)}
        />
        <div className="file-op-buttons">
          <button className="soft-btn" onClick={() => onRename(selectedPath, renamePath)} disabled={!selectedPath || !renamePath.trim()}>
            Rename
          </button>
          <button className="soft-btn" onClick={() => onDelete(selectedPath)} disabled={!selectedPath}>
            Delete Selected
          </button>
        </div>
      </div>
    </div>
  );
}
