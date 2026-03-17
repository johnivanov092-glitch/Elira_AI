export default function BatchVerifyPanel({
  result,
}) {
  return (
    <div className="batch-verify-panel">
      <div className="pane-title">Batch Verify</div>

      {!result ? (
        <div className="pane-empty">Здесь появится проверка по staged файлам.</div>
      ) : (
        <div className="batch-verify-body">
          <div className="batch-verify-summary">
            Файлов: {result.count} • + {result.summary?.added || 0} / - {result.summary?.removed || 0}
          </div>

          <div className="batch-verify-list">
            {(result.items || []).map((item) => (
              <div key={item.path} className="batch-verify-row">
                <div className="batch-verify-path">{item.path}</div>
                <div className="batch-verify-stats">
                  {item.changed_vs_disk ? "изменён" : "совпадает"} • + {item.stats?.added || 0} / - {item.stats?.removed || 0}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
