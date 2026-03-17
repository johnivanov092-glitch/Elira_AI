export default function ProjectMapPanel({
  projectMap,
  onRefresh,
}) {
  const items = projectMap?.items || [];
  const summary = projectMap?.summary || [];

  return (
    <div className="project-map-panel">
      <div className="pane-title">Project Map</div>

      <div className="project-map-toolbar">
        <button className="soft-btn" onClick={onRefresh}>Refresh Map</button>
      </div>

      <div className="project-map-summary">
        {summary.length ? (
          summary.map((item) => (
            <div key={item.suffix} className="project-map-chip">
              {item.suffix}: {item.count}
            </div>
          ))
        ) : (
          <div className="pane-empty">Карта проекта ещё не загружена.</div>
        )}
      </div>

      <div className="project-map-list">
        {items.slice(0, 40).map((item) => (
          <div key={item.path} className="project-map-row">
            <div className="project-map-path">{item.path}</div>
            <div className="project-map-meta">
              {item.suffix} • imports: {(item.imports || []).length}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
