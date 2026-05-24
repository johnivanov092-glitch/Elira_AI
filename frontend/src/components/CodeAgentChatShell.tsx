/**
 * CodeAgentChatShell.tsx
 *
 * Chat-style transcript where the conversation IS the code-agent (Claude
 * Code / Codex style). User types a task -> POST /api/code-agent/run ->
 * assistant answer and tool calls land in the transcript inline.
 *
 * State is split:
 *   - projectRoot / model / maxSteps are CONTROLLED via props from the
 *     parent wrapper (CodeWorkspaceShell owns the toolbar).
 *   - Transcript history, current input and running flag stay local and
 *     are persisted to localStorage.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { ChevronDown, ChevronRight, Loader2, Send, Sparkles, Trash2, Wrench } from "lucide-react";
import { api } from "../api/ide";
import type { CodeAgentResponse, CodeAgentToolCall } from "../api/codeAgent";
import { UiIcon, IconText } from "./StatusPanels";

const HISTORY_KEY = "elira_code_agent_history_v1";

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

type ToolBlockProps = { call: CodeAgentToolCall };

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

export type CodeAgentChatShellProps = {
  projectRoot: string;
  model: string;
  maxSteps: number;
};

export default function CodeAgentChatShell({ projectRoot, model, maxSteps }: CodeAgentChatShellProps) {
  const [history, setHistory] = useState<Turn[]>(() => loadHistory());
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    saveHistory(history);
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [history]);

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
      {/* Mini header — just transcript controls */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "6px 12px",
          borderBottom: "1px solid var(--border)",
          flexShrink: 0,
        }}
      >
        <UiIcon icon={Sparkles} size={13} />
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{history.length} turns</span>
        <button
          onClick={clearHistory}
          disabled={!history.length}
          className="soft-btn"
          style={{ marginLeft: "auto", fontSize: 10, padding: "3px 8px", opacity: history.length ? 1 : 0.4 }}
        >
          <IconText icon={Trash2} size={11} gap={4}>
            Очистить
          </IconText>
        </button>
      </div>

      {/* Transcript */}
      <div ref={scrollRef} style={{ flex: 1, overflow: "auto", padding: "12px 14px" }}>
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
            <div style={{ textAlign: "center", maxWidth: 380 }}>
              <div style={{ display: "flex", justifyContent: "center", opacity: 0.2, marginBottom: 12 }}>
                <UiIcon icon={Sparkles} size={36} />
              </div>
              <div style={{ fontSize: 13, marginBottom: 6 }}>Code-Агент</div>
              <div style={{ fontSize: 11, lineHeight: 1.6, marginBottom: 8 }}>
                Опиши задачу — агент прочитает файлы, внесёт правки и запустит проверки в указанном проекте.
                Каждый шаг (tool call) виден в истории.
              </div>
              <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 12, lineHeight: 1.7 }}>
                Инструменты:&nbsp;
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
              <div key={turn.id} style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
                <div
                  style={{
                    maxWidth: "82%",
                    padding: "7px 11px",
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
          return (
            <div key={turn.id} style={{ marginBottom: 16 }}>
              {turn.tool_calls.length > 0 && (
                <div style={{ marginBottom: 6 }}>
                  {turn.tool_calls.map((c, idx) => (
                    <ToolBlock key={`${turn.id}-tc-${idx}`} call={c} />
                  ))}
                </div>
              )}
              {turn.error && (
                <div
                  style={{
                    padding: "7px 11px",
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
                    padding: "9px 12px",
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
              <div
                style={{
                  marginTop: 4,
                  fontSize: 10,
                  color: "var(--text-muted)",
                  display: "flex",
                  gap: 10,
                  flexWrap: "wrap",
                }}
              >
                <span>{turn.ok ? "✓ готово" : "✕ не завершено"}</span>
                <span>шаги: {turn.steps}</span>
                <span>stop: {turn.stop_reason}</span>
                <span style={{ fontFamily: "var(--font-mono)" }}>{turn.model}</span>
              </div>
            </div>
          );
        })}

        {running && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              color: "var(--text-muted)",
              fontSize: 12,
              padding: "6px 4px",
            }}
          >
            <UiIcon icon={Loader2} size={14} />
            <span>Агент работает...</span>
          </div>
        )}
      </div>

      {/* Composer */}
      <div style={{ padding: "8px 12px 10px", borderTop: "1px solid var(--border)", flexShrink: 0 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onInputKey}
            placeholder="Опиши задачу. Ctrl+Enter — отправить."
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
        <div style={{ marginTop: 4, fontSize: 10, color: "var(--text-muted)" }}>
          Ctrl+Enter — отправить. Каждое сообщение — отдельный single-shot запуск.
        </div>
      </div>
    </div>
  );
}
