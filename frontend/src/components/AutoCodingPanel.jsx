import React, { useMemo, useState } from "react";
import * as api from "../api/ide";

export default function AutoCodingPanel({
  selectedPath,
  currentContent,
  onPatchApplied,
}) {
  const [goal, setGoal] = useState("");
  const [maxSteps, setMaxSteps] = useState(2);
  const [loading, setLoading] = useState(false);
  const [proposal, setProposal] = useState(null);
  const [verifyResult, setVerifyResult] = useState(null);
  const canRun = useMemo(
    () => Boolean(selectedPath && goal.trim()),
    [selectedPath, goal]
  );

  async function handleSuggest() {
    if (!canRun) return;
    setLoading(true);
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

  async function handleApply() {
    if (!proposal?.patch) return;
    setLoading(true);
    try {
      const result = await api.applyPatch({
        path: selectedPath,
        content: proposal.patch,
      });
      if (onPatchApplied) {
        onPatchApplied(proposal.patch, result);
      }
      alert("Patch applied");
    } catch (error) {
      alert(error.message || "Apply failed");
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
      });
      setVerifyResult(result);
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
      gap: 10,
    }}>
      <div style={{ fontWeight: 700 }}>Auto-Coding Loop</div>

      <label style={{ display: "grid", gap: 6 }}>
        <span style={{ fontSize: 12, opacity: 0.8 }}>Goal</span>
        <textarea
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="Например: исправь обработку ошибок и добавь защиту от пустого path"
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
        <button onClick={handleApply} disabled={!proposal?.patch || loading}>
          Apply Suggested Patch
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
          <div style={{ fontWeight: 600 }}>Proposed content</div>
          <pre style={{
            margin: 0,
            whiteSpace: "pre-wrap",
            overflowX: "auto",
            background: "#06090d",
            padding: 10,
            borderRadius: 8,
          }}>
            {proposal.patch || ""}
          </pre>
        </div>
      )}

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
