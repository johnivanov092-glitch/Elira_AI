import TimelinePanel from './TimelinePanel.jsx'

export default function AgentPanel({ open, selectedModel, selectedProfile, lastMeta, timeline }) {
  const sources = Array.isArray(lastMeta?.sources) ? lastMeta.sources : []

  return (
    <aside className={`agent-panel ${open ? 'open' : 'closed'}`}>
      <div className="panel-title">Agent Panel</div>
      <div className="panel-subtitle">Правая панель со статусом, timeline и meta</div>

      <div className="stat-card">
        <div className="stat-label">Статус</div>
        <div className="stat-value">{open ? 'Активна' : 'Скрыта'}</div>
      </div>

      <div className="stat-card">
        <div className="stat-label">Модель</div>
        <div className="stat-value small">{selectedModel || '—'}</div>
      </div>

      <div className="stat-card">
        <div className="stat-label">Профиль</div>
        <div className="stat-value small">{selectedProfile || '—'}</div>
      </div>

      <div className="stat-card">
        <div className="stat-label">Последний timeline</div>
        <TimelinePanel timeline={Array.isArray(timeline) ? timeline : []} />
      </div>

      <div className="stat-card">
        <div className="stat-label">Sources</div>
        {sources.length === 0 ? (
          <div className="muted-text">Источников пока нет.</div>
        ) : (
          <div className="table-list">
            {sources.slice(0, 5).map((src, idx) => (
              <div key={idx} className="file-row">
                <div className="file-main">
                  <div className="file-name">{src.title || 'Источник'}</div>
                  <div className="file-meta">{src.url || '—'}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="stat-card">
        <div className="stat-label">Meta</div>
        <pre className="meta-box">{JSON.stringify(lastMeta || {}, null, 2)}</pre>
      </div>
    </aside>
  )
}
