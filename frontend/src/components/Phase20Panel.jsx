export default function Phase20Panel({
  goal,
  setGoal,
  selectedPaths,
  runResult,
  historyItems,
  selectedHistoryId,
  onRun,
  onAutoStageExecution,
  onApplyExecution,
  onVerifyExecution,
  onSelectHistory,
}) {
  const active = runResult;
  const plannerItems = active?.planner?.items || [];
  const coderOps = active?.coder?.operations || [];
  const reviewerNotes = active?.reviewer?.notes || [];
  const testerChecks = active?.tester?.checks || [];
  const previewTargets = active?.execution?.preview_targets || [];

  return (
    <div className="phase20-panel">
      <div className="pane-title">Phase 20 Autonomous Project Agent</div>

      <div className="phase20-body">
        <label className="task-field">
          <span>Goal</span>
          <textarea
            className="patch-instruction"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            spellCheck={false}
          />
        </label>

        <div className="phase20-meta">
          Selected files: {selectedPaths.length}
        </div>

        <div className="patch-buttons">
          <button className="soft-btn" onClick={onRun}>
            Run Phase 20
          </button>
          <button className="soft-btn" onClick={onAutoStageExecution} disabled={!previewTargets.length}>
            Stage Execution Files
          </button>
          <button className="soft-btn" onClick={onApplyExecution} disabled={!previewTargets.length}>
            Apply Execution
          </button>
          <button className="soft-btn" onClick={onVerifyExecution} disabled={!previewTargets.length}>
            Verify Execution
          </button>
        </div>

        {active ? (
          <div className="phase20-result">
            <div className="task-run-header">
              run #{active.run_id || active.id}
            </div>

            <div className="task-run-section">
              <div className="task-run-title">Reasoning</div>
              <div className="task-run-log">
                Scope: {active.reasoning?.scope || "—"}
              </div>
              {(active.reasoning?.advice || []).map((item, index) => (
                <div key={`${index}-${item}`} className="task-run-log">• {item}</div>
              ))}
            </div>

            <div className="task-run-section">
              <div className="task-run-title">Planner</div>
              {plannerItems.map((item, index) => (
                <div key={`${index}-${item.path}`} className="task-run-row">
                  <div className="task-run-action">{item.action}</div>
                  <div className="task-run-path">{item.path}</div>
                  <div className="task-run-reason">{item.reason}</div>
                </div>
              ))}
            </div>

            <div className="task-run-section">
              <div className="task-run-title">Coder</div>
              {coderOps.map((item, index) => (
                <div key={`${index}-${item.path}`} className="task-run-row">
                  <div className="task-run-action">{item.operation}</div>
                  <div className="task-run-path">{item.path}</div>
                  <div className="task-run-reason">{item.status}</div>
                </div>
              ))}
            </div>

            <div className="task-run-section">
              <div className="task-run-title">Reviewer</div>
              {reviewerNotes.map((item, index) => (
                <div key={`${index}-${item}`} className="task-run-log">• {item}</div>
              ))}
            </div>

            <div className="task-run-section">
              <div className="task-run-title">Tester</div>
              {testerChecks.map((item, index) => (
                <div key={`${index}-${item}`} className="task-run-log">• {item}</div>
              ))}
            </div>

            <div className="task-run-section">
              <div className="task-run-title">Execution</div>
              {(active.execution?.flow || []).map((item, index) => (
                <div key={`${index}-${item}`} className="task-run-log">• {item}</div>
              ))}
            </div>
          </div>
        ) : (
          <div className="pane-empty">Здесь появится multi-agent reasoning по проекту.</div>
        )}

        <div className="phase20-history">
          <div className="task-run-title">Phase 20 History</div>
          <div className="task-history-list">
            {historyItems.length ? (
              historyItems.map((item) => (
                <button
                  key={item.id}
                  className={`task-history-row ${selectedHistoryId === item.id ? "active" : ""}`}
                  onClick={() => onSelectHistory(item)}
                >
                  <div className="task-history-top">
                    <span className="task-history-id">#{item.id}</span>
                  </div>
                  <div className="task-history-goal">{item.goal}</div>
                  <div className="task-history-meta">{item.created_at}</div>
                </button>
              ))
            ) : (
              <div className="pane-empty">История Phase 20 пока пустая.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
