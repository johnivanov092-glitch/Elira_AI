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

  async function loadHistory() {
    try {
      const res = await api.listRunHistory();
      const items = Array.isArray(res?.items) ? res.items : Array.isArray(res) ? res : [];
      setHistory(items);
      setEvents(items.flatMap((x) => x.events || []));
    } catch {
      // silent
    }
  }

  useEffect(() => {
    let mounted = true;
    async function tick() {
      if (!mounted) return;
      await loadHistory();
    }
    tick();
    const timer = setInterval(tick, 3000);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, []);

  function handlePatchApplied(nextContent, result) {
    setContent(nextContent);
    const now = new Date().toISOString();
    setHistory((prev) => [
      {
        id: result?.run_id || `local-${Date.now()}`,
        path: selectedPath,
        timestamp: now,
        status: "applied",
        events: [{ type: "patch_applied", timestamp: now, message: selectedPath }],
      },
      ...prev,
    ]);
    setEvents((prev) => [
      { type: "patch_applied", timestamp: now, message: selectedPath },
      ...prev,
    ]);
  }

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <AutoCodingPanel
        selectedPath={selectedPath}
        currentContent={content}
        onPatchApplied={handlePatchApplied}
        onHistoryRefresh={loadHistory}
      />

      <div style={{
        display: "grid",
        gridTemplateColumns: "1.2fr 1fr 1fr",
        gap: 12
      }}>
        <TerminalPanel events={events} />
        <TimelinePanel events={events} />
        <ToolStreamPanel events={events.filter((x) => x.tool || x.type === "tool")} />
      </div>

      <div style={{
        border: "1px solid #2a2f3a",
        borderRadius: 12,
        padding: 12,
        background: "#11161d",
        color: "#e6edf3",
      }}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>Run History</div>
        <div style={{ display: "grid", gap: 8 }}>
          {history.length === 0 ? (
            <div style={{ opacity: 0.7 }}>No run history yet</div>
          ) : (
            history.map((item) => (
              <div
                key={item.id}
                style={{
                  border: "1px solid #2a2f3a",
                  borderRadius: 10,
                  padding: 10,
                  background: "#0b1016",
                }}
              >
                <div><strong>ID:</strong> {item.id}</div>
                <div><strong>Path:</strong> {item.path || "-"}</div>
                <div><strong>Status:</strong> {item.status || "-"}</div>
                <div><strong>Timestamp:</strong> {item.timestamp || "-"}</div>
              </div>
            ))
          )}
        </div>
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
