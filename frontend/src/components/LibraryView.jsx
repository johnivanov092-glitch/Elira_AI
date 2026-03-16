export default function LibraryView({ files, onToggle, onDelete, refresh }) {
  return (
    <section className="view-shell">
      <div className="chat-header">
        <div>
          <div className="panel-title">Library</div>
          <div className="panel-subtitle">Файлы из data/uploads и их активация в контексте</div>
        </div>
        <button className="ghost-button compact" onClick={refresh}>Обновить</button>
      </div>

      <div className="view-content">
        {files.length === 0 ? (
          <div className="empty-state">
            <div className="empty-title">Файлов пока нет</div>
            <div className="empty-subtitle">Положи файлы в папку data/uploads, потом нажми «Обновить».</div>
          </div>
        ) : (
          <div className="table-list">
            {files.map((file) => (
              <div key={file.name} className="file-row">
                <div className="file-main">
                  <div className="file-name">{file.name}</div>
                  <div className="file-meta">{Math.round((file.size || 0) / 1024)} KB</div>
                </div>
                <div className="file-actions">
                  <button className={`tag-button ${file.active ? 'active' : ''}`} onClick={() => onToggle(file.name, !file.active)}>
                    {file.active ? 'Активен' : 'Неактивен'}
                  </button>
                  <button className="tag-button danger" onClick={() => onDelete(file.name)}>Удалить</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
