
import React from "react";

export default function TimelinePanel({ events }) {
  return (
    <div>
      <h4>Timeline</h4>
      {events.map((e, i) => (
        <div key={i}>
          {e.timestamp} — {e.type}
        </div>
      ))}
    </div>
  );
}
