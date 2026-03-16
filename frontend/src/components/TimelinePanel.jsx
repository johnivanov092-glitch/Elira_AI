export default function TimelinePanel({ timeline }) {
  return (
    <div className="timeline-list">
      {timeline.length === 0 ? (
        <div className="memory-small">Шагов пока нет.</div>
      ) : (
        timeline.map((item, idx) => (
          <div key={`${item.step}-${idx}`} className="timeline-card">
            <div className="timeline-head">
              <div className="timeline-title">{item.title}</div>
              <div className={`timeline-status ${item.status}`}>{item.status}</div>
            </div>
            <div className="timeline-detail">{item.detail}</div>
          </div>
        ))
      )}
    </div>
  )
}
