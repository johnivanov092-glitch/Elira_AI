import React from "react";

export default function SourcesPanel({ sources = [], engineLinks = [] }) {
  if ((!sources || sources.length === 0) && (!engineLinks || engineLinks.length === 0)) {
    return null;
  }

  return (
    <div className="panel-section">
      {sources && sources.length > 0 && (
        <>
          <div className="panel-title">Sources</div>
          <div className="sources-list">
            {sources.map((s, idx) => (
              <div key={idx} className="source-card">
                <a href={s.url} target="_blank" rel="noreferrer" className="source-link">
                  {s.title || s.url}
                </a>
                <div className="source-meta">
                  {(s.engine_label || s.engine || "search")} {s.score !== undefined ? `· score ${Number(s.score).toFixed(1)}` : ""}
                </div>
                {s.snippet ? <div className="source-snippet">{s.snippet}</div> : null}
              </div>
            ))}
          </div>
        </>
      )}

      {engineLinks && engineLinks.length > 0 && (
        <>
          <div className="panel-title" style={{ marginTop: 12 }}>Open search in engines</div>
          <div className="sources-list">
            {engineLinks.map((e, idx) => (
              <div key={idx} className="source-card">
                <a href={e.url} target="_blank" rel="noreferrer" className="source-link">
                  {e.label}
                </a>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
