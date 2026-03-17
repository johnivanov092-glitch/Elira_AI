export default function PatchHistoryPanel({
  items,
  selectedId,
  onSelect,
}) {
  return (
    <div className="patch-history-panel">
      <div className="pane-title">История патчей</div>

      <div className="patch-history-list">
        {items.length ? (
          items.map((item) => (
            <button
              key={item.id}
              className={`history-row ${selectedId === item.id ? "active" : ""}`}
              onClick={() => onSelect(item)}
            >
              <div className="history-row-top">
                <span className="history-action">{item.action}</span>
                <span className="history-id">#{item.id}</span>
              </div>
              <div className="history-path">{item.path}</div>
              <div className="history-time">{item.created_at}</div>
            </button>
          ))
        ) : (
          <div className="pane-empty">История пока пустая.</div>
        )}
      </div>
    </div>
  );
}
