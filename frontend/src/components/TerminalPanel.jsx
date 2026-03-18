
import React from "react";

export default function TerminalPanel({ events }) {
  return (
    <div style={{ background: "#000", color: "#0f0", padding: 10 }}>
      {events.map((e, i) => (
        <div key={i}>
          [{e.timestamp}] {e.type} → {e.message}
        </div>
      ))}
    </div>
  );
}
