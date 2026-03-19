/**
 * TerminalPanel.jsx — реальный терминал в Code вкладке.
 */
import { useState, useRef, useEffect } from "react";

const API = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

export default function TerminalPanel() {
  const [history, setHistory] = useState([{ type: "info", text: "Jarvis Terminal. Введи команду." }]);
  const [input, setInput] = useState("");
  const [cwd, setCwd] = useState("");
  const [running, setRunning] = useState(false);
  const [cmdHistory, setCmdHistory] = useState([]);
  const [histIdx, setHistIdx] = useState(-1);
  const endRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    fetch(`${API}/api/terminal/cwd`).then(r => r.json()).then(d => { if (d.cwd) setCwd(d.cwd); }).catch(() => {});
  }, []);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [history]);

  async function handleExec() {
    const cmd = input.trim();
    if (!cmd || running) return;
    setInput("");
    setRunning(true);
    setCmdHistory(prev => [...prev, cmd]);
    setHistIdx(-1);
    setHistory(prev => [...prev, { type: "cmd", text: cmd, cwd }]);

    try {
      const resp = await fetch(`${API}/api/terminal/exec`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command: cmd, cwd }),
      });
      const data = await resp.json();

      if (data.cwd) setCwd(data.cwd);

      if (data.ok) {
        if (data.stdout) setHistory(prev => [...prev, { type: "stdout", text: data.stdout }]);
        if (data.stderr) setHistory(prev => [...prev, { type: "stderr", text: data.stderr }]);
        if (!data.stdout && !data.stderr) setHistory(prev => [...prev, { type: "info", text: "(нет вывода)" }]);
      } else {
        setHistory(prev => [...prev, { type: "error", text: data.error || "Ошибка" }]);
      }
    } catch (e) {
      setHistory(prev => [...prev, { type: "error", text: e.message }]);
    } finally {
      setRunning(false);
      inputRef.current?.focus();
    }
  }

  function handleKeyDown(e) {
    if (e.key === "Enter") { e.preventDefault(); handleExec(); }
    else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (cmdHistory.length > 0) {
        const idx = histIdx < 0 ? cmdHistory.length - 1 : Math.max(0, histIdx - 1);
        setHistIdx(idx);
        setInput(cmdHistory[idx] || "");
      }
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      if (histIdx >= 0) {
        const idx = histIdx + 1;
        if (idx >= cmdHistory.length) { setHistIdx(-1); setInput(""); }
        else { setHistIdx(idx); setInput(cmdHistory[idx] || ""); }
      }
    }
  }

  const shortCwd = cwd.length > 40 ? "..." + cwd.slice(-37) : cwd;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "#0d0d0f", fontFamily: "var(--font-mono)", fontSize: 12 }}>
      <div style={{ flex: 1, overflow: "auto", padding: "8px 12px" }}>
        {history.map((item, i) => (
          <div key={i} style={{ marginBottom: 2 }}>
            {item.type === "cmd" && (
              <div style={{ color: "#6ee7b7" }}>
                <span style={{ color: "#555" }}>{(item.cwd || "").split(/[/\\]/).pop() || "~"}</span>
                <span style={{ color: "#4ade80" }}> $ </span>
                <span>{item.text}</span>
              </div>
            )}
            {item.type === "stdout" && <pre style={prePre}>{item.text}</pre>}
            {item.type === "stderr" && <pre style={{ ...prePre, color: "#fbbf24" }}>{item.text}</pre>}
            {item.type === "error" && <pre style={{ ...prePre, color: "#f87171" }}>{item.text}</pre>}
            {item.type === "info" && <div style={{ color: "#666", fontStyle: "italic" }}>{item.text}</div>}
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <div style={{ display: "flex", alignItems: "center", padding: "6px 12px", borderTop: "1px solid #222", background: "#111114" }}>
        <span style={{ color: "#555", fontSize: 11, marginRight: 6, flexShrink: 0 }}>{shortCwd}</span>
        <span style={{ color: "#4ade80", marginRight: 4 }}>$</span>
        <input
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="команда..."
          disabled={running}
          style={{ flex: 1, background: "transparent", border: "none", outline: "none", color: "#e5e5e5", fontFamily: "inherit", fontSize: 12 }}
          autoFocus
        />
        {running && <span style={{ color: "#fbbf24", fontSize: 10 }}>⏳</span>}
      </div>
    </div>
  );
}

const prePre = {
  margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word",
  color: "#d4d4d4", lineHeight: 1.4,
};
