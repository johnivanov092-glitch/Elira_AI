/**
 * CodeAgentChatShell.tsx
 *
 * Claude Code / Codex-style chat where the conversation IS the code-agent.
 * User types a task -> POST /api/code-agent/run -> assistant answer +
 * tool calls land in the transcript inline.
 *
 * Each user submit is a fresh single-shot agent run (the backend has no
 * multi-turn state); past turns are kept locally as transcript history.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import {
  ChevronDown,
  ChevronRight,
  Loader2,
  Send,
  Settings2,
  Sparkles,
  Trash2,
  Wrench,
} from "lucide-react";
import { api } from "../api/ide";
import type { CodeAgentResponse, CodeAgentToolCall } from "../api/codeAgent";
import { UiIcon, IconText } from "./StatusPanels";

const HISTORY_KEY = "elira_code_agent_history_v1";
const ROOT_KEY = "elira_code_agent_root";
const MODEL_KEY = "elira_code_agent_model";
const DEFAULT_ROOT = "D:/AIWork/Elira_AI";
const DEFAULT_MODEL = "qwen2.5-coder:7b";
const DEFAULT_MAX_STEPS = 20;

type UserTurn = {
  kind: "user";
  id: string;
  text: string;
  ts: number;
};

type AgentTurn = {
  kind: "agent";
  id: string;
  parentId: string;
  ts: number;
  ok: boolean;
  text: string;
  stop_reason: CodeAgentResponse["stop_reason"];
  steps: number;
  error: string | null;
  tool_calls: CodeAgentToolCall[];
  model: string;
  project_root: string;
};

type Turn = UserTurn | AgentTurn;

function makeId(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function loadHistory(): Turn[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as Turn[]) : [];
  } catch {
    return [];
  }
}

function saveHistory(turns: Turn[]): void {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(turns.slice(-200)));
  } catch {}
}

function formatArgsInline(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([k, v]) => {
      if (typeof v === "string") return `${k}=${v.length > 60 ? v.slice(0, 60) + "…" : v}`;
      return `${k}=${JSON.stringify(v)}`;
    })
    .join("  ");
}

type ToolBlockProps = {
  call: CodeAgentToolCall;
};

function ToolBlock({ call }: ToolBlockProps) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ marginBottom: 4, border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-surface)" }}>
      <button
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          width: "100%",
          padding: "5px 10px",
          border: "none",
          background: "transparent",
          cursor: "pointer",
          textAlign: "left",
          color: "var(--text-primary)",
          fontSize: 11,
          fontFamily: "var(--font-mono)",
        }}
      >
        <UiIcon icon={open ? ChevronDown : ChevronRight} size={11} />
        <span style={{ fontSize: 10, color: "var(--text-muted)", minWidth: 28, flexShrink: 0 }}>#{call.step}</span>
        <UiIcon icon={Wrench} size={11} />
        <span style={{ color: "var(--accent)", flexShrink: 0 }}>{call.tool}</span>
        <span
          style={{
            color: "var(--text-muted)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            flex: 1,
            fontSize: 10,
          }}
        >
          {formatArgsInline(call.arguments)}
        </span>
      </button>
      {open && (
        <div style={{ borderTop: "1px solid var(--border)", padding: "7px 11px", background: "rgba(0,0,0,0.18)" }}>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 3 }}>arguments</div>
          <pre
            style={{
              margin: 0,
              marginBottom: 6,
              fontSize: 11,
              fontFamily: "var(--font-mono)",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              color: "var(--text-secondary)",
            }}
          >
            {JSON.stringify(call.arguments, null, 2)}
          </pre>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 3 }}>result</div>
          <pre
            style={{
              margin: 0,
              fontSize: 11,
              fontFamily: "var(--font-mono)",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              color: "var(--text-primary)",
              maxHeight: 260,
              overflow: "auto",
            }}
          >
            {call.result}
          </pre>
        </div>
      )}
    </div>
  );
}

export default function CodeAgentChatShell() {
  const [history, setHistory] = useState<Turn[]>(() => loadHistory());
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [projectRoot, setProjectRoot] = useState<string>(() => {
    try { return localStorage.getItem(ROOT_KEY) || DEFAULT_ROOT; } catch { return DEFAULT_ROOT; }
  });
  const [model, setModel] = useState<string>(() => {
    try { return localStorage.getItem(MODEL_KEY) || DEFAULT_MODEL; } catch { return DEFAULT_MODEL; }
  });
  const [maxSteps, setMaxSteps] = useState<number>(DEFAULT_MAX_STEPS);
  const [showSettings, setShowSettings] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    saveHistory(history);
    // auto-scroll to bottom on any history change
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [history]);

  useEffect(() => {
    try { localStorage.setItem(ROOT_KEY, projectRoot); } catch {}
  }, [projectRoot]);
  useEffect(() => {
    try { localStorage.setItem(MODEL_KEY, model); } catch {}
  }, [model]);

  const runTurn = useCallback(async () => {
    const text = input.trim();
    if (!text || running) return;
    const userTurn: UserTurn = { kind: "user", id: makeId("u"), text, ts: Date.now() };
    setHistory((h) => [...h, userTurn]);
    setInput("");
    setRunning(true);
    try {
      const res = await api.runCodeAgent({
        message: text,
        projectRoot,
        model,
        maxSteps,
      });
      const agentTurn: AgentTurn = {
        kind: "agent",
        id: makeId("a"),
        parentId: userTurn.id,
        ts: Date.now(),
        ok: res.ok,
        text: res.response,
        stop_reason: res.stop_reason,
        steps: res.steps,
        error: res.error,
        tool_calls: res.tool_calls,
        model,
        project_root: projectRoot,
      };
      setHistory((h) => [...h, agentTurn]);
    } catch (e) {
      const agentTurn: AgentTurn = {
        kind: "agent",
        id: makeId("a"),
        parentId: userTurn.id,
        ts: Date.now(),
        ok: false,
        text: "",
        stop_reason: "error",
        steps: 0,
        error: String((e as Error)?.message || e),
        tool_calls: [],
        model,
        project_root: projectRoot,
      };
      setHistory((h) => [...h, agentTurn]);
    } finally {
      setRunning(false);
    }
  }, [input, running, projectRoot, model, maxSteps]);

  const onInputKey = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        runTurn();
      }
    },
    [runTurn],
  );

  function clearHistory() {
    if (!history.length) return;
    if (!confirm("Очистить всю историю code-агента?")) return;
    setHistory([]);
  }

  const empty = useMemo(() => history.length === 0 && !running, [history.length, running]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {/* Top bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "8px 14px",
          borderBottom: "1px solid var(--border)",
          flexShrink: 0,
        }}
      >
        <UiIcon icon={Sparkles} size={14} />
        <div style={{ fontSize: 12, fontWeight: 500 }}>Code-Агент</div>
        <div style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
          {model} · {projectRoot}
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <button
            onClick={() => setShowSettings((v) => !v)}
            className="soft-btn"
            style={{ fontSize: 11, padding: "4px 10px" }}
          >
            <IconText icon={Settings2} size={12} gap={5}>
              Настройки
            </IconText>
          </button>
          <button
            onClick={clearHistory}
            disabled={!history.length}
            className="soft-btn"
            style={{ fontSize: 11, padding: "4px 10px", opacity: history.length ? 1 : 0.4 }}
          >
            <IconText icon={Trash2} size={12} gap={5}>
              Очистить
            </IconText>
          </button>
        </div>
      </div>

      {/* Settings panel */}
      {showSettings && (
        <div
          style={{
            padding: "10px 14px",
            borderBottom: "1px solid var(--border)",
            background: "var(--bg-surface)",
            display: "grid",
            gridTemplateColumns: "2fr 1fr 100px",
            gap: 10,
            flexShrink: 0,
          }}
        >
          <div>
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 3 }}>Корень проекта</div>
            <input
              value={projectRoot}
              onChange={(e) => setProjectRoot(e.target.value)}
              style={{
                width: "100%",
                padding: "6px 9px",
                borderRadius: 6,
                border: "1px solid var(--border)",
                background: "var(--bg-input)",
                color: "var(--text-primary)",
                fontSize: 11,
                outline: "none",
                boxSizing: "border-box",
                fontFamily: "var(--font-mono)",
              }}
            />
          </div>
          <div>
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 3 }}>Модель Ollama</div>
            <input
              value={model}
              onChange={(e) => setModel(e.target.value)}
              style={{
                width: "100%",
                padding: "6px 9px",
                borderRadius: 6,
                border: "1px solid var(--border)",
                background: "var(--bg-input)",
                color: "var(--text-primary)",
                fontSize: 11,
                outline: "none",
                boxSizing: "border-box",
                fontFamily: "var(--font-mono)",
              }}
            />
          </div>
          <div>
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 3 }}>Max шагов</div>
            <input
              type="number"
              min={1}
              max={50}
              value={maxSteps}
              onChange={(e) => setMaxSteps(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
              style={{
                width: "100%",
                padding: "6px 9px",
                borderRadius: 6,
                border: "1px solid var(--border)",
                background: "var(--bg-input)",
                color: "var(--text-primary)",
                fontSize: 11,
                outline: "none",
                boxSizing: "border-box",
                fontFamily: "var(--font-mono)",
              }}
            />
          </div>
        </div>
      )}

      {/* Transcript */}
      <div ref={scrollRef} style={{ flex: 1, overflow: "auto", padding: "14px 18px" }}>
        {empty && (
          <div
            style={{
              height: "100%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--text-muted)",
              fontSize: 12,
            }}
          >
            <div style={{ textAlign: "center", maxWidth: 420 }}>
              <div style={{ display: "flex", justifyContent: "center", opacity: 0.2, marginBottom: 12 }}>
                <UiIcon icon={Sparkles} size={42} />
              </div>
              <div style={{ fontSize: 13, marginBottom: 6 }}>Code-Агент с tool calling</div>
              <div style={{ fontSize: 11, lineHeight: 1.6, marginBottom: 8 }}>
                Напиши задачу — агент сам прочитает файлы, внесёт правки и запустит проверки в указанном
                проекте. Каждый шаг (tool call) будет виден в истории.
              </div>
              <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 14, lineHeight: 1.7 }}>
                Доступные инструменты:&nbsp;
                <code style={{ fontFamily: "var(--font-mono)" }}>read_file</code>,&nbsp;
                <code style={{ fontFamily: "var(--font-mono)" }}>write_file</code>,&nbsp;
                <code style={{ fontFamily: "var(--font-mono)" }}>edit_file</code>,&nbsp;
                <code style={{ fontFamily: "var(--font-mono)" }}>glob</code>,&nbsp;
                <code style={{ fontFamily: "var(--font-mono)" }}>grep</code>,&nbsp;
                <code style={{ fontFamily: "var(--font-mono)" }}>run_bash</code>
              </div>
            </div>
          </div>
        )}

        {history.map((turn) => {
          if (turn.kind === "user") {
            return (
              <div key={turn.id} style={{ display: "flex", justifyContent: "flex-end", marginBottom: 14 }}>
                <div
                  style={{
                    maxWidth: "78%",
                    padding: "8px 12px",
                    borderRadius: 10,
                    background: "var(--accent-soft, rgba(99,102,241,0.18))",
                    border: "1px solid var(--accent, #6366f1)",
                    color: "var(--text-primary)",
                    fontSize: 12,
                    lineHeight: 1.55,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                  }}
                >
                  {turn.text}
                </div>
              </div>
            );
          }
          // agent turn
          return (
            <div key={turn.id} style={{ marginBottom: 18 }}>
              {turn.tool_calls.length > 0 && (
                <div style={{ marginBottom: 8 }}>{turn.tool_calls.map((c, idx) => <ToolBlock key={`${turn.id}-tc-${idx}`} call={c} />)}</div>
              )}
              {turn.error && (
                <div
                  style={{
                    padding: "8px 12px",
                    borderRadius: 8,
                    border: "1px solid rgba(255,107,107,0.4)",
                    background: "rgba(255,107,107,0.08)",
                    color: "#ff6b6b",
                    fontSize: 12,
                    whiteSpace: "pre-wrap",
                    fontFamily: "var(--font-mono)",
                    marginBottom: 6,
                  }}
                >
                  {turn.error}
                </div>
              )}
              {turn.text && (
                <div
                  style={{
                    padding: "10px 14px",
                    borderRadius: 10,
                    background: "var(--bg-surface)",
                    border: "1px solid var(--border)",
                    color: "var(--text-primary)",
                    fontSize: 12,
                    lineHeight: 1.6,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                  }}
                >
                  {turn.text}
                </div>
              )}
              <div style={{ marginTop: 4, fontSize: 10, color: "var(--text-muted)", display: "flex", gap: 10, flexWrap: "wrap" }}>
                <span>{turn.ok ? "✓ готово" : "✕ не завершено"}</span>
                <span>шаги: {turn.steps}</span>
                <span>stop: {turn.stop_reason}</span>
                <span style={{ fontFamily: "var(--font-mono)" }}>{turn.model}</span>
              </div>
            </div>
          );
        })}

        {running && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--text-muted)", fontSize: 12, padding: "8px 4px" }}>
            <UiIcon icon={Loader2} size={14} />
            <span>Агент работает...</span>
          </div>
        )}
      </div>

      {/* Composer */}
      <div style={{ padding: "10px 14px 12px", borderTop: "1px solid var(--border)", flexShrink: 0 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onInputKey}
            placeholder="Опиши задачу для code-агента. Ctrl+Enter — отправить."
            disabled={running}
            rows={3}
            style={{
              flex: 1,
              padding: "8px 10px",
              borderRadius: 8,
              border: "1px solid var(--border)",
              background: "var(--bg-input)",
              color: "var(--text-primary)",
              fontSize: 12,
              outline: "none",
              resize: "vertical",
              fontFamily: "inherit",
              lineHeight: 1.55,
              boxSizing: "border-box",
              minHeight: 56,
              maxHeight: 220,
            }}
          />
          <button
            onClick={runTurn}
            disabled={!input.trim() || running}
            style={{
              padding: "9px 14px",
              borderRadius: 8,
              border: "none",
              background: "var(--accent, #6366f1)",
              color: "#fff",
              fontSize: 12,
              cursor: !input.trim() || running ? "not-allowed" : "pointer",
              opacity: !input.trim() || running ? 0.45 : 1,
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            {running ? <UiIcon icon={Loader2} size={13} /> : <UiIcon icon={Send} size={13} />}
            <span>Отправить</span>
          </button>
        </div>
        <div style={{ marginTop: 6, fontSize: 10, color: "var(--text-muted)" }}>
          Ctrl+Enter отправляет. Каждое сообщение — отдельный single-shot запуск агента (backend не держит state между запусками).
        </div>
      </div>
    </div>
  );
}
