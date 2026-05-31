import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { executeTerminal, getTerminalCwd } from "../api/terminal";

type TerminalHistoryItem = {
  type: "cmd" | "error" | "info" | "stderr" | "stdout";
  text: string;
  cwd?: string;
};

export default function TerminalPanel() {
  const [history, setHistory] = useState<TerminalHistoryItem[]>([
    { type: "info", text: "Elira Terminal. Введи команду." },
  ]);
  const [input, setInput] = useState("");
  const [cwd, setCwd] = useState("");
  const [running, setRunning] = useState(false);
  const [cmdHistory, setCmdHistory] = useState<string[]>([]);
  const [histIdx, setHistIdx] = useState(-1);
  const endRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    getTerminalCwd()
      .then((data) => {
        if (data?.cwd) setCwd(data.cwd);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [history]);

  async function handleExec() {
    const cmd = input.trim();
    if (!cmd || running) return;
    setInput("");
    setRunning(true);
    setCmdHistory((prev) => [...prev, cmd]);
    setHistIdx(-1);
    setHistory((prev) => [...prev, { type: "cmd", text: cmd, cwd }]);

    try {
      const data = await executeTerminal({ command: cmd, cwd });

      const nextCwd = typeof data.cwd === "string" ? data.cwd : "";
      const stdout = typeof data.stdout === "string" ? data.stdout : "";
      const stderr = typeof data.stderr === "string" ? data.stderr : "";

      if (nextCwd) setCwd(nextCwd);

      if (data.ok) {
        if (stdout) setHistory((prev) => [...prev, { type: "stdout", text: stdout }]);
        if (stderr) setHistory((prev) => [...prev, { type: "stderr", text: stderr }]);
        if (!stdout && !stderr) {
          setHistory((prev) => [...prev, { type: "info", text: "(нет вывода)" }]);
        }
      } else {
        const message = typeof data.error === "string" ? data.error : "Ошибка";
        setHistory((prev) => [...prev, { type: "error", text: message }]);
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setHistory((prev) => [...prev, { type: "error", text: message }]);
    } finally {
      setRunning(false);
      inputRef.current?.focus();
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      handleExec();
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      if (cmdHistory.length > 0) {
        const idx = histIdx < 0 ? cmdHistory.length - 1 : Math.max(0, histIdx - 1);
        setHistIdx(idx);
        setInput(cmdHistory[idx] || "");
      }
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (histIdx >= 0) {
        const idx = histIdx + 1;
        if (idx >= cmdHistory.length) {
          setHistIdx(-1);
          setInput("");
        } else {
          setHistIdx(idx);
          setInput(cmdHistory[idx] || "");
        }
      }
    }
  }

  const shortCwd = cwd.length > 40 ? "..." + cwd.slice(-37) : cwd;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "#0d0d0f", fontFamily: "var(--font-mono)", fontSize: 12 }}>
      <div style={{ flex: 1, overflow: "auto", padding: "8px 12px" }}>
        {history.map((item, index) => (
          <div key={index} style={{ marginBottom: 2 }}>
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
          onChange={(e) => setInput(e.target.value)}
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

const prePre: CSSProperties = {
  margin: 0,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  color: "#d4d4d4",
  lineHeight: 1.4,
};
