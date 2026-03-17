export default function PatchPlanPanel({
  plan,
  onBuildPlan,
}) {
  const items = plan?.items || [];
  const notes = plan?.notes || [];

  return (
    <div className="patch-plan-panel">
      <div className="pane-title">Patch Plan</div>

      <div className="patch-plan-toolbar">
        <button className="soft-btn" onClick={onBuildPlan}>Build Plan</button>
      </div>

      {!plan ? (
        <div className="pane-empty">Здесь появится план изменений по задаче.</div>
      ) : (
        <div className="patch-plan-body">
          <div className="patch-plan-goal">{plan.goal}</div>

          <div className="patch-plan-items">
            {items.map((item, index) => (
              <div key={`${index}-${item.path}`} className="patch-plan-row">
                <div className="patch-plan-action">{item.action}</div>
                <div className="patch-plan-path">{item.path}</div>
                <div className="patch-plan-reason">{item.reason}</div>
              </div>
            ))}
          </div>

          <div className="patch-plan-notes">
            {notes.map((note, index) => (
              <div key={`${index}-${note}`} className="patch-plan-note">
                • {note}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
