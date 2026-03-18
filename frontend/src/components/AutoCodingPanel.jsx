import React, { useMemo, useState } from "react";
import * as api from "../api/ide";
import DiffViewer from "./DiffViewer";

export default function AutoCodingPanel({
  selectedPath,
  currentContent,
  onPatchApplied,
  onHistoryRefresh,
}) {
  const [goal, setGoal] = useState("");
  const [maxSteps, setMaxSteps] = useState(2);
  const [loading, setLoading] = useState(false);
  const [proposal, setProposal] = useState(null);
  const [preview, setPreview] = useState(null);
  const [verifyResult, setVerifyResult] = useState(null);

  const canRun = useMemo(
    () => Boolean(selectedPath && goal.trim()),
    [selectedPath, goal]
  );

  async function handleSuggest() {
    if (!canRun) return;
    setLoading(true);
    setProposal(null);
    setPreview(null);
    setVerifyResult(null);
    try {
      const result = await api.autocodeSuggest({
        path: selectedPath,
        content: currentContent,
        goal: goal.trim(),
        max_steps: Number(maxSteps) || 1,
      });
      setProposal(result);
    } catch (error) {
      alert(error.message || "Failed to build suggestion");
    } finally {
      setLoading(false);
    }
  }

  async function handlePreview() {
    if (!proposal?.patch) return;
    setLoading(true);
    try {
      const result = await api.previewPatch({
        path: selectedPath,
        original_content: currentContent,
        content: proposal.patch,
        goal: goal.trim(),
      });
      setPreview(result);
    } catch (error) {
      alert(error.message || "Preview failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleApply() {
    if (!proposal?.patch || !preview?.ok) {
      alert("Сначала сделай Preview");
      return;
    }
    setLoading(true);
    try {
      const result = await api.applyPatch({
        path: selectedPath,
        content: proposal.patch,
        goal: goal.trim(),
        run_id: preview?.run_id,
      });
      if (onPatchApplied) {
        onPatchApplied(proposal.patch, result);
      }
      if (onHistoryRefresh) {
        await onHistoryRefresh();
      }
      alert("Patch applied");
    } catch (error) {
      alert(error.message || "Apply failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleRollback() {
    const runId = preview?.run_id || proposal?.run_id;
    if (!runId) {
      alert("Нет run_id для rollback");
      return;
    }
    setLoading(true);
    try {
      await api.rollbackPatch({ run_id: runId, path: selectedPath });
      if (onHistoryRefresh) {
        await onHistoryRefresh();
      }
      alert("Rollback done");
    } catch (error) {
      alert(error.message || "Rollback failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleVerify() {
    setLoading(true);
    try {
      const result = await api.verifyPatch({
        path: selectedPath,
        goal: goal.trim(),
        run_id: preview?.run_id,
      });
      setVerifyResult(result);
      if (onHistoryRefresh) {
        await onHistoryRefresh();
      }
    } catch (error) {
      alert(error.message || "Verify failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{
      border: "1px solid #2a2f3a",
      borderRadius: 12,
      padding: 12,
      background: "#11161d",
      color: "#e6edf3",
      display: "grid",
      gap: 12,
    }}>
      <div style={{ fontWeight: 700 }}>Auto-Coding + Diff Flow</div>

      <label style={{ display: "grid", gap: 6 }}>
        <span style={{ fontSize: 12, opacity: 0.8 }}>Goal</span>
        <textarea
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="Например: исправь null-check и добавь защиту от пустого path"
          style={{
            width: "100%",
            minHeight: 90,
            background: "#0b1016",
            color: "#e6edf3",
            border: "1px solid #2a2f3a",
            borderRadius: 10,
            padding: 10,
            resize: "vertical",
          }}
        />
      </label>

      <label style={{ display: "grid", gap: 6, maxWidth: 160 }}>
        <span style={{ fontSize: 12, opacity: 0.8 }}>Max steps</span>
        <input
          type="number"
          min="1"
          max="5"
          value={maxSteps}
          onChange={(e) => setMaxSteps(e.target.value)}
          style={{
            background: "#0b1016",
            color: "#e6edf3",
            border: "1px solid #2a2f3a",
            borderRadius: 10,
            padding: "8px 10px",
          }}
        />
      </label>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button onClick={handleSuggest} disabled={!canRun || loading}>
          {loading ? "Running..." : "Suggest Patch"}
        </button>
        <button onClick={handlePreview} disabled={!proposal?.patch || loading}>
          Preview Patch
        </button>
        <button onClick={handleApply} disabled={!preview?.ok || loading}>
          Apply Patch
        </button>
        <button onClick={handleRollback} disabled={!preview?.run_id || loading}>
          Rollback
        </button>
        <button onClick={handleVerify} disabled={!selectedPath || loading}>
          Verify
        </button>
      </div>

      <div style={{ fontSize: 12, opacity: 0.8 }}>
        File: {selectedPath || "not selected"}
      </div>

      {proposal && (
        <div style={{
          border: "1px solid #2a2f3a",
          borderRadius: 10,
          padding: 10,
          background: "#0b1016",
          display: "grid",
          gap: 8,
        }}>
          <div style={{ fontWeight: 600 }}>Suggestion Summary</div>
          <div style={{ whiteSpace: "pre-wrap" }}>{proposal.summary || "No summary"}</div>
          <div style={{ fontSize: 12, opacity: 0.8 }}>
            Steps used: {proposal.steps_used ?? "-"}
          </div>
        </div>
      )}

      <DiffViewer diffLines={preview?.diff_lines || []} />

      {verifyResult && (
        <div style={{
          border: "1px solid #2a2f3a",
          borderRadius: 10,
          padding: 10,
          background: "#0b1016",
        }}>
          <div style={{ fontWeight: 600 }}>Verify Result</div>
          <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>
            {JSON.stringify(verifyResult, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
