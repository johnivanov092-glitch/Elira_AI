import React from "react";

function lineClass(kind) {
  if (kind === "add") return { background: "rgba(46, 160, 67, 0.18)" };
  if (kind === "remove") return { background: "rgba(248, 81, 73, 0.18)" };
  return { background: "transparent" };
}

export default function DiffViewer({ diffLines = [] }) {
  return (
    <div
      style={{
        border: "1px solid #2a2f3a",
        borderRadius: 12,
        overflow: "hidden",
        background: "#0b1016",
        color: "#e6edf3",
      }}
    >
      <div
        style={{
          padding: "10px 12px",
          borderBottom: "1px solid #2a2f3a",
          fontWeight: 700,
          background: "#11161d",
        }}
      >
        Diff Preview
      </div>

      <div style={{ fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace", fontSize: 13 }}>
        {diffLines.length === 0 ? (
          <div style={{ padding: 12, opacity: 0.75 }}>No diff available</div>
        ) : (
          diffLines.map((line, index) => (
            <div
              key={index}
              style={{
                ...lineClass(line.kind),
                display: "grid",
                gridTemplateColumns: "60px 1fr",
                gap: 12,
                padding: "4px 12px",
                whiteSpace: "pre-wrap",
                borderBottom: "1px solid rgba(255,255,255,0.03)",
              }}
            >
              <div style={{ opacity: 0.55 }}>{line.marker}</div>
              <div>{line.text}</div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
