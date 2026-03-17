export default function TerminalPanel({
  logs,
  verifyResult,
  historyItem,
}) {
  return (
    <div className="terminal-panel">
      <div className="pane-title">Терминал / События</div>

      {verifyResult ? (
        <div className="verify-box">
          <div className="verify-title">Verify</div>
          <div className="verify-meta">
            {verifyResult.path} • {verifyResult.changed_vs_disk ? "изменён" : "совпадает"}
          </div>
          <ul className="verify-list">
            {(verifyResult.checks || []).map((item, index) => (
              <li key={`${index}-${item}`}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {historyItem ? (
        <div className="history-preview-box">
          <div className="verify-title">History item</div>
          <div className="verify-meta">
            #{historyItem.id} • {historyItem.action} • {historyItem.path}
          </div>
          <div className="history-preview-stats">
            + {historyItem?.stats?.added || 0} / - {historyItem?.stats?.removed || 0}
          </div>
        </div>
      ) : null}

      <div className="terminal-log">
        {logs.length ? (
          logs.map((line, index) => (
            <div key={`${index}-${line}`} className="terminal-line">
              {line}
            </div>
          ))
        ) : (
          <div className="pane-empty">Пока нет событий.</div>
        )}
      </div>
    </div>
  );
}
