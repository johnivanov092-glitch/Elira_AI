export default function FileExplorerPanel({
  files = [],
  selectedPath = "",
  onOpen,
}) {
  const normalized = Array.isArray(files) ? files : [];

  return (
    <div className="explorer-card">
      <div className="explorer-title">Файлы проекта</div>

      <div className="explorer-list">
        {normalized.length ? normalized.map((item, index) => {
          const path = item?.path || item?.name || `file-${index}`;
          return (
            <button
              key={path}
              className={`explorer-item ${selectedPath === path ? "active" : ""}`}
              onClick={() => onOpen?.(path)}
            >
              {path}
            </button>
          );
        }) : (
          <div className="sidebar-empty">Файлы не найдены.</div>
        )}
      </div>
    </div>
  );
}
