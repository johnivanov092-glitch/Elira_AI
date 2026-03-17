
export default function Phase19Panel({
  goal,
  setGoal,
  selectedPaths,
  runResult,
  historyItems,
  selectedHistoryId,
  onRun,
  onAutoStagePlan,
  onApplyPlanned,
  onVerifyPlanned,
  onSelectHistory,
}) {
  const active = runResult;
  const planItems = active?.plan || [];
  const verifyChecks = active?.verify?.checks || [];

  return (
    <div className="phase19-panel">
      <div className="pane-title">Phase 19 Multi‑File Dev Loop</div>

      <label className="task-field">
        <span>Goal</span>
        <textarea
          className="patch-instruction"
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
        />
      </label>

      <div className="phase19-meta">
        Staged files: {selectedPaths.length}
      </div>

      <div className="patch-buttons">
        <button className="soft-btn" onClick={onRun}>
          Run Phase19
        </button>

        <button
          className="soft-btn"
          onClick={onAutoStagePlan}
          disabled={!planItems.length}
        >
          Stage Plan Files
        </button>

        <button
          className="soft-btn"
          onClick={onApplyPlanned}
          disabled={!selectedPaths.length}
        >
          Apply Planned
        </button>

        <button
          className="soft-btn"
          onClick={onVerifyPlanned}
          disabled={!selectedPaths.length}
        >
          Verify Planned
        </button>
      </div>

      {active && (
        <div className="phase19-result">
          <div className="task-run-title">Plan</div>
          {planItems.map((p, i) => (
            <div key={i} className="task-run-row">
              <div className="task-run-action">{p.action}</div>
              <div className="task-run-path">{p.path}</div>
              <div className="task-run-reason">{p.reason}</div>
            </div>
          ))}

          <div className="task-run-title">Verify</div>
          {verifyChecks.map((c, i) => (
            <div key={i} className="task-run-log">• {c}</div>
          ))}
        </div>
      )}

      <div className="phase19-history">
        <div className="task-run-title">Phase19 History</div>

        {historyItems.map((item) => (
          <button
            key={item.id}
            className={`task-history-row ${
              selectedHistoryId === item.id ? "active" : ""
            }`}
            onClick={() => onSelectHistory(item)}
          >
            <div className="task-history-goal">{item.goal}</div>
            <div className="task-history-meta">{item.created_at}</div>
          </button>
        ))}
      </div>
    </div>
  );
}
