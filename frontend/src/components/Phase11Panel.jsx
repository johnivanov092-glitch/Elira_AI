import { useEffect, useState } from "react";
import {
  getPhase11Status,
  previewPatch,
  applyPatch,
  rollbackPatch,
  verifyPatch,
  listPatchBackups,
} from "../api/phase11";

export default function Phase11Panel() {
  const [status, setStatus] = useState(null);
  const [filePath, setFilePath] = useState("");
  const [newContent, setNewContent] = useState("");
  const [preview, setPreview] = useState(null);
  const [verify, setVerify] = useState(null);
  const [backups, setBackups] = useState(null);
  const [rollbackId, setRollbackId] = useState("");
  const [applyResult, setApplyResult] = useState(null);
  const [rollbackResult, setRollbackResult] = useState(null);
  const [error, setError] = useState("");

  async function refresh() {
    try {
      const [s, b] = await Promise.all([getPhase11Status(), listPatchBackups(20)]);
      setStatus(s);
      setBackups(b);
      setError("");
    } catch (err) {
      setError(String(err));
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function handlePreview() {
    try {
      const data = await previewPatch(filePath, newContent);
      setPreview(data);
      setError("");
    } catch (err) {
      setError(String(err));
    }
  }

  async function handleApply() {
    try {
      const data = await applyPatch(filePath, newContent, preview?.old_sha256 || null);
      setApplyResult(data);
      await refresh();
      setError("");
    } catch (err) {
      setError(String(err));
    }
  }

  async function handleVerify() {
    try {
      const data = await verifyPatch(filePath);
      setVerify(data);
      setError("");
    } catch (err) {
      setError(String(err));
    }
  }

  async function handleRollback() {
    try {
      const data = await rollbackPatch(rollbackId);
      setRollbackResult(data);
      await refresh();
      setError("");
    } catch (err) {
      setError(String(err));
    }
  }

  return (
    <section className="workspace-card">
      <div className="section-header">
        <h2>Phase 11 — Safe Patch Engine</h2>
        <button onClick={refresh}>Refresh</button>
      </div>

      <div className="goal-box">
        <input value={filePath} onChange={(e) => setFilePath(e.target.value)} placeholder="backend/app/main.py" />
      </div>

      <div className="json-block">
        <textarea
          value={newContent}
          onChange={(e) => setNewContent(e.target.value)}
          placeholder="New file content"
          style={{ width: "100%", minHeight: 180, background: "#0f1115", color: "#e8ecf1", border: "1px solid #2b3240", borderRadius: 12, padding: 12 }}
        />
      </div>

      <div className="actions-row" style={{ justifyContent: "flex-start", marginBottom: 12 }}>
        <button onClick={handlePreview}>Preview</button>
        <button onClick={handleApply}>Apply</button>
        <button onClick={handleVerify}>Verify</button>
      </div>

      <div className="goal-box">
        <input value={rollbackId} onChange={(e) => setRollbackId(e.target.value)} placeholder="backup_id for rollback" />
        <button onClick={handleRollback}>Rollback</button>
      </div>

      {status ? <div className="json-block"><h3>Status</h3><pre>{JSON.stringify(status, null, 2)}</pre></div> : null}
      {preview ? <div className="json-block"><h3>Preview</h3><pre>{JSON.stringify(preview, null, 2)}</pre></div> : null}
      {applyResult ? <div className="json-block"><h3>Apply Result</h3><pre>{JSON.stringify(applyResult, null, 2)}</pre></div> : null}
      {verify ? <div className="json-block"><h3>Verify</h3><pre>{JSON.stringify(verify, null, 2)}</pre></div> : null}
      {rollbackResult ? <div className="json-block"><h3>Rollback Result</h3><pre>{JSON.stringify(rollbackResult, null, 2)}</pre></div> : null}
      {backups ? <div className="json-block"><h3>Backups</h3><pre>{JSON.stringify(backups, null, 2)}</pre></div> : null}
      {error ? <div className="panel-error">{error}</div> : null}
    </section>
  );
}
