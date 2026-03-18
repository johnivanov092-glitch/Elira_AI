
import React from "react";

export default function ToolStreamPanel({ events }) {
  return (
    <div>
      <h4>Tool Stream</h4>
      {events.map((e, i) => (
        <div key={i}>
          {e.tool} → {e.result}
        </div>
      ))}
    </div>
  );
}
