export default function DiffViewer({
  diffText,
  stats,
  loading,
}) {
  return (
    <div className="diff-viewer">
      <div className="pane-title">Diff Preview</div>

      {loading ? <div className="pane-empty">Подготовка diff...</div> : null}

      {!loading && !diffText ? (
        <div className="pane-empty">
          Здесь появится diff по строкам после Preview Patch или Verify.
        </div>
      ) : null}

      {!loading && diffText ? (
        <>
          <div className="diff-stats">
            <span>+ {stats?.added || 0}</span>
            <span>- {stats?.removed || 0}</span>
          </div>
          <pre className="unified-diff">{diffText}</pre>
        </>
      ) : null}
    </div>
  );
}
