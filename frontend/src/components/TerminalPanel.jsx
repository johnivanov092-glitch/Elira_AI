export default function TerminalPanel({ logs = [] }) {
  return (
    <div className="terminal-card">
      <div className="terminal-title">Логи</div>
      <div className="terminal-log-list">
        {logs.length ? logs.map((line, idx) => (
          <div key={`${idx}-${line}`} className="terminal-log-line">
            {line}
          </div>
        )) : (
          <div className="sidebar-empty">Пока пусто.</div>
        )}
      </div>
    </div>
  );
}
