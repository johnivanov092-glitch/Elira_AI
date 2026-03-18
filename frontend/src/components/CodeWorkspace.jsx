import React, { useEffect, useState } from "react";
import * as api from "../api/ide";
import AutoCodingPanel from "./AutoCodingPanel";
import TerminalPanel from "./TerminalPanel";
import TimelinePanel from "./TimelinePanel";
import ToolStreamPanel from "./ToolStreamPanel";

export default function CodeWorkspace({ initialPath = "" }) {
  const [selectedPath, setSelectedPath] = useState(initialPath);
  const [content, setContent] = useState("");
  const [events, setEvents] = useState([]);
  const [history, setHistory] = useState([]);

  useEffect(() => {
    if (!selectedPath) return;
    api.getProjectFile(selectedPath)
      .then((res) => setContent(res.content || ""))
      .catch((err) => alert(err.message || "Failed to open file"));
  }, [selectedPath]);

  useEffect(() => {
    let mounted = true;
    async function loadHistory() {
      try {
        const res = await api.listRunHistory();
        if (mounted) {
          const items = Array.isArray(res?.items) ? res.items : Array.isArray(res) ? res : [];
          setHistory(items);
          setEvents(items.flatMap((x) => x.events || []));
        }
      } catch {
        // silent
      }
    }
    loadHistory();
    const timer = setInterval(loadHistory, 3000);
    return () => {
      mounted = false
    };
  }, []);

  function handlePatchApplied(nextContent, result) {
    setContent(nextContent);
    setHistory((prev) => [
      {
        id: result?.id || `local-${Date.now()}`,
        timestamp: new Date().toISOString(),
        events: [{ type: "patch_applied", timestamp: new Date().toISOString(), message: selectedPath }],
      },
      ...prev,
    ]);
    setEvents((prev) => [
      { type: "patch_applied", timestamp: new Date().toISOString(), message: selectedPath },
      ...prev,
    ]);
  }

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <AutoCodingPanel
        selectedPath={selectedPath}
        currentContent={content}
        onPatchApplied={handlePatchApplied}
      />

      <div style={{
        display: "grid",
        gridTemplateColumns: "1.2fr 1fr 1fr",
        gap: 12
      }}>
        <TerminalPanel events={events} />
        <TimelinePanel events={events} />
        <ToolStreamPanel
          events={events.filter((x) => x.tool || x.type === "tool")}
        />
      </div>

      <div style={{
        border: "1px solid #2a2f3a",
        borderRadius: 12,
        padding: 12,
        background: "#11161d",
        color: "#e6edf3",
      }}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>Current file content</div>
        <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{content}</pre>
      </div>
    </div>
  );
}
