/**
 * CodeAgentChatShell.tsx
 *
 * Streaming Claude Code / Codex-style transcript. The conversation IS
 * the code-agent. User types a task -> SSE stream from
 * /api/code-agent/stream -> events (run_started, step_started,
 * tool_call, final_response, done) progressively assemble the current
 * agent turn in real time.
 *
 * Features
 *  - Streaming via fetch + ReadableStream (SSE protocol).
 *  - Cancel button while running (AbortController + POST /cancel).
 *  - Multi-turn: prior user/assistant text is sent as conversation_history.
 *  - Inline diff preview for write_file / edit_file tool calls.
 *  - Calls onAgentTouchedFile(path) when the agent reads/writes a file,
 *    so the parent workspace shell can auto-open it in the IDE pane.
 *
 * State that is owned by this component:
 *  - transcript history (localStorage)
 *  - current input text
 *  - in-flight stream / cancel id
 *
 * State that is controlled (props):
 *  - projectRoot / model / maxSteps (the parent toolbar)
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { BookmarkPlus, ChevronDown, ChevronRight, GitPullRequest, Loader2, Minimize2, Pin, Plus, Send, Sparkles, Square, Star, Trash2, Wrench } from "lucide-react";
import { api } from "../api/ide";
import type {
  CodeAgentStreamEvent,
  CodeAgentToolCall,
  ConversationMessage,
} from "../api/codeAgent";
import { estimateTokens } from "../api/codeAgent";
import { UiIcon, IconText } from "./StatusPanels";
import MarkdownRenderer from "./MarkdownRenderer";

const HISTORY_KEY_PREFIX = "elira_code_agent_history_v2_"; // suffix = sessionId
const LEGACY_HISTORY_KEY = "elira_code_agent_history_v2";   // pre-session key (one global history)
const SAVED_PROMPTS_KEY = "elira_code_agent_saved_prompts_v1";

type SavedPrompt = {
  id: string;
  label: string;
  text: string;
  ts: number;
};

/**
 * Diff review prompt — used both by the "Review diff" toolbar button
 * and by the corresponding template in DEFAULT_SAVED_PROMPTS. Kept as
 * a single source so updates only need to happen in one place.
 *
 * The prompt is deliberately prescriptive about the workflow (status →
 * diff → file reads → structured report) and the output sections, so
 * small models produce consistent reviews instead of free-form prose.
 */
const DIFF_REVIEW_PROMPT = `Сделай code review текущих изменений в проекте.

ПОШАГОВО:
1. Запусти \`git status --short\` чтобы увидеть какие файлы изменены/добавлены/удалены.
2. Запусти \`git diff HEAD\` чтобы увидеть полный diff. Если ничего нет, попробуй \`git diff --cached\` (для staged).
3. Если git не инициализирован или diff пуст — честно скажи это и остановись.
4. Для каждого затронутого файла прочитай его через \`read_file\` чтобы понять контекст вокруг изменений.
5. Проанализируй и выдай отчёт ТОЧНО в этом формате:

## 🐛 Баги
(конкретные баги: нерабочая логика, race conditions, off-by-one, неверная обработка edge cases. Цитируй файл:строку.)

## 🔒 Безопасность
(SQL/command injection, утечки secrets, unsafe deserialization, отсутствие валидации входа, эскалация прав.)

## ⚡ Производительность
(N+1 запросы, лишние циклы, неиспользуемая память, блокирующие I/O.)

## 🎨 Стиль / читаемость
(длинные функции, magic numbers, плохие имена, отсутствие type hints, дублирование.)

## ✅ Что хорошо
(удачные решения которые стоит сохранить.)

Если в какой-то секции пусто — пиши "—".

В конце дай **verdict** одной строкой: 🟢 ready to ship / 🟡 minor fixes / 🔴 blocking issues.`;

const DEFAULT_SAVED_PROMPTS: SavedPrompt[] = [
  {
    id: "tpl-tests",
    label: "Тесты для файла",
    text: "Напиши pytest-тесты для всего публичного API файла <путь к файлу>. Запусти их и убедись что зелёные.",
    ts: 0,
  },
  {
    id: "tpl-diff-review",
    label: "Diff review",
    text: DIFF_REVIEW_PROMPT,
    ts: 0,
  },
  {
    id: "tpl-refactor",
    label: "Рефакторинг",
    text: "Рефактор файла <путь>: убери дубликаты, разбей длинные функции, добавь type hints где их нет. Запусти тесты до и после чтобы убедиться что ничего не сломалось.",
    ts: 0,
  },
];

function loadSavedPrompts(): SavedPrompt[] {
  try {
    const raw = localStorage.getItem(SAVED_PROMPTS_KEY);
    if (!raw) return DEFAULT_SAVED_PROMPTS;
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) && parsed.length ? (parsed as SavedPrompt[]) : DEFAULT_SAVED_PROMPTS;
  } catch {
    return DEFAULT_SAVED_PROMPTS;
  }
}

function persistSavedPrompts(prompts: SavedPrompt[]): void {
  try { localStorage.setItem(SAVED_PROMPTS_KEY, JSON.stringify(prompts.slice(0, 50))); } catch {}
}

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
  stop_reason: CodeAgentStreamEvent extends infer T
    ? T extends { type: "done"; stop_reason: infer R }
      ? R
      : string
    : string;
  steps: number;
  error: string | null;
  tool_calls: CodeAgentToolCall[];
  model: string;
  project_root: string;
  in_progress?: boolean;
};

type SummaryTurn = {
  kind: "summary";
  id: string;
  text: string;
  replaced: number;
  ts: number;
};

type Turn = UserTurn | AgentTurn | SummaryTurn;

function summarizeToolCall(tc: CodeAgentToolCall): string {
  // Compact one-line description of a tool invocation. Used when
  // building the compress-history payload — we want the summarizer to
  // know WHAT THE AGENT DID, not just what it said in plain text. Show
  // tool name + the most identifying argument(s); avoid dumping full
  // results (they'd blow out the transcript cap on backend).
  const tool = tc.tool;
  const args: Record<string, unknown> = (tc.arguments as Record<string, unknown>) || {};
  const path = typeof args.path === "string" ? (args.path as string) : "";
  const pattern = typeof args.pattern === "string" ? (args.pattern as string) : "";
  const query = typeof args.query === "string" ? (args.query as string) : "";
  const command = typeof args.command === "string" ? (args.command as string) : "";
  const content = typeof args.content === "string" ? (args.content as string) : "";

  if (tool === "read_file") return `read_file(${path})`;
  if (tool === "write_file") return `write_file(${path}, ${content.length} chars)`;
  if (tool === "edit_file") return `edit_file(${path})`;
  if (tool === "glob") return `glob(${pattern})`;
  if (tool === "grep") {
    const inPath = typeof args.path === "string" && args.path !== "." ? `, path=${args.path}` : "";
    return `grep(${pattern}${inPath})`;
  }
  if (tool === "run_bash") {
    const short = command.length > 80 ? command.slice(0, 80) + "..." : command;
    return `run_bash(${short})`;
  }
  if (tool === "recall") return `recall(${query})`;
  return `${tool}(${Object.keys(args).slice(0, 3).join(",")})`;
}

function makeId(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

/** Remove any lingering pre-backend localStorage history for a session.
 *  History now lives on the backend (sessions.turns); this only cleans up
 *  stale keys written by older builds when a session is deleted. */
export function deleteHistoryFor(sessionId: string): void {
  try { localStorage.removeItem(HISTORY_KEY_PREFIX + sessionId); } catch {}
}

/** Read the legacy single-history key once and return its turns (or []).
 *  Caller is responsible for moving them into a session and deleting the
 *  legacy key. */
export function readLegacyHistory(): Turn[] {
  try {
    const raw = localStorage.getItem(LEGACY_HISTORY_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as Turn[]) : [];
  } catch {
    return [];
  }
}

export function clearLegacyHistory(): void {
  try { localStorage.removeItem(LEGACY_HISTORY_KEY); } catch {}
}

export type CodeAgentTurnSummary = { user_count: number; last_user?: string; last_ts?: number };

function formatArgsInline(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([k, v]) => {
      if (typeof v === "string") return `${k}=${v.length > 60 ? v.slice(0, 60) + "…" : v}`;
      return `${k}=${JSON.stringify(v)}`;
    })
    .join("  ");
}

// ─── inline diff helpers ─────────────────────────────────────────────────

type DiffLine = { tag: "ctx" | "del" | "add"; text: string };

function lineDiff(oldText: string, newText: string): DiffLine[] {
  // Simple LCS-based diff per line. Tolerant of small files (we cap at
  // 4000 lines combined to keep the UI snappy).
  const a = oldText.split("\n");
  const b = newText.split("\n");
  if (a.length + b.length > 4000) {
    return [
      ...a.map<DiffLine>((t) => ({ tag: "del", text: t })),
      ...b.map<DiffLine>((t) => ({ tag: "add", text: t })),
    ];
  }
  const m = a.length;
  const n = b.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (a[i] === b[j]) {
      out.push({ tag: "ctx", text: a[i] });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ tag: "del", text: a[i] });
      i++;
    } else {
      out.push({ tag: "add", text: b[j] });
      j++;
    }
  }
  while (i < m) out.push({ tag: "del", text: a[i++] });
  while (j < n) out.push({ tag: "add", text: b[j++] });
  return out;
}

function DiffView({ oldContent, newContent }: { oldContent: string; newContent: string }) {
  const lines = useMemo(() => lineDiff(oldContent, newContent), [oldContent, newContent]);
  const adds = lines.filter((l) => l.tag === "add").length;
  const dels = lines.filter((l) => l.tag === "del").length;
  return (
    <div style={{ marginTop: 6, border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden" }}>
      <div
        style={{
          padding: "5px 10px",
          background: "var(--bg-surface)",
          borderBottom: "1px solid var(--border)",
          fontSize: 10,
          color: "var(--text-muted)",
          fontFamily: "var(--font-mono)",
        }}
      >
        <span style={{ color: "#4ade80" }}>+{adds}</span>{"  "}
        <span style={{ color: "#ff6b6b" }}>-{dels}</span>
      </div>
      <div style={{ maxHeight: 360, overflow: "auto", background: "rgba(0,0,0,0.18)" }}>
        <pre style={{ margin: 0, padding: "4px 0", fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.45 }}>
          {lines.map((l, idx) => {
            const bg =
              l.tag === "add"
                ? "rgba(74,222,128,0.12)"
                : l.tag === "del"
                ? "rgba(255,107,107,0.12)"
                : "transparent";
            const fg =
              l.tag === "add" ? "#4ade80" : l.tag === "del" ? "#ff6b6b" : "var(--text-secondary)";
            const prefix = l.tag === "add" ? "+" : l.tag === "del" ? "-" : " ";
            return (
              <div key={idx} style={{ background: bg, padding: "0 10px", color: fg, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                {prefix} {l.text}
              </div>
            );
          })}
        </pre>
      </div>
    </div>
  );
}

// ─── tool block ──────────────────────────────────────────────────────────

type ToolBlockProps = { call: CodeAgentToolCall };

function ToolBlock({ call }: ToolBlockProps) {
  const hasDiff = !!call.diff_action && typeof call.new_content === "string";
  const [open, setOpen] = useState(hasDiff);
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
        {hasDiff && (
          <span style={{ fontSize: 10, color: "var(--accent)", flexShrink: 0 }}>
            {call.diff_action}
          </span>
        )}
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
          {hasDiff && (
            <>
              <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 3 }}>diff</div>
              <DiffView oldContent={call.old_content || ""} newContent={call.new_content || ""} />
            </>
          )}
          {!hasDiff && (
            <>
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
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ─── main component ──────────────────────────────────────────────────────

export type CodeAgentChatShellProps = {
  /** Unique id for the current session. localStorage keys are derived
   *  from it so every session has its own independent history. */
  sessionId: string;
  projectRoot: string;
  model: string;
  maxSteps: number;
  numCtx: number;
  autoRemember: boolean;
  /** Called whenever a tool call touches a file path (read/write/edit). */
  onAgentTouchedFile?: (path: string) => void;
  /** Called when the user submits a new task — gives the parent a chance
   *  to bump session's updated_at and auto-derive its title. */
  onUserTurn?: (text: string) => void;
  /** Called when the user clicks the in-shell '+ Новый чат' button.
   *  Parent should create a new session and switch to it. */
  onRequestNewSession?: () => void;
};

export default function CodeAgentChatShell({
  sessionId,
  projectRoot,
  model,
  maxSteps,
  numCtx,
  autoRemember,
  onAgentTouchedFile,
  onUserTurn,
  onRequestNewSession,
}: CodeAgentChatShellProps) {
  const [history, setHistory] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [liveStep, setLiveStep] = useState<number | null>(null);
  const [savedPrompts, setSavedPrompts] = useState<SavedPrompt[]>(() => loadSavedPrompts());
  const [promptsOpen, setPromptsOpen] = useState(false);
  const [summarizing, setSummarizing] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const runIdRef = useRef<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  // Which session the in-state `history` belongs to, whether it has finished
  // loading from the backend, and whether the next settle should skip the
  // persist write (used to avoid writing freshly-loaded history straight back).
  const historySessionRef = useRef<string>("");
  const historyLoadedRef = useRef(false);
  const skipPersistRef = useRef(false);

  useEffect(() => { persistSavedPrompts(savedPrompts); }, [savedPrompts]);

  function applySavedPrompt(p: SavedPrompt) {
    setInput((cur) => (cur.trim() ? cur + "\n\n" + p.text : p.text));
    setPromptsOpen(false);
  }
  function saveCurrentAsPrompt() {
    const text = input.trim();
    if (!text) return;
    const label = text.split("\n")[0].slice(0, 50) || "Без названия";
    const newPrompt: SavedPrompt = { id: `tpl-${Date.now().toString(36)}`, label, text, ts: Date.now() };
    setSavedPrompts((prev) => [newPrompt, ...prev]);
    setPromptsOpen(true);
  }
  function deletePrompt(id: string) {
    setSavedPrompts((prev) => prev.filter((p) => p.id !== id));
  }

  // Keep the conversation pinned to the bottom as it grows.
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [history]);

  // Load the active session's history from the backend (the source of truth).
  // On session switch: abort any running stream of the previous session,
  // reset live state, then fetch turns. `historySessionRef` guards against a
  // late response from a previous session overwriting the current one.
  useEffect(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setRunning(false);
    setLiveStep(null);
    setInput("");
    setSummaryError(null);
    historySessionRef.current = sessionId;
    historyLoadedRef.current = false;
    skipPersistRef.current = true; // don't write the freshly-loaded history back
    setHistory([]);
    if (!sessionId) {
      historyLoadedRef.current = true;
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const full = await api.getCodeSession(sessionId);
        if (cancelled || historySessionRef.current !== sessionId) return;
        setHistory(Array.isArray(full?.turns) ? (full.turns as Turn[]) : []);
      } catch {
        if (!cancelled && historySessionRef.current === sessionId) setHistory([]);
      } finally {
        if (!cancelled && historySessionRef.current === sessionId) historyLoadedRef.current = true;
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // Persist settled history to the backend. Skipped while a stream is running
  // (avoids a write per token), until the session has finished loading, and
  // for the one settle right after a load (avoids echoing it straight back).
  useEffect(() => {
    if (!sessionId || !historyLoadedRef.current || running) return;
    if (skipPersistRef.current) { skipPersistRef.current = false; return; }
    api.patchCodeSession(sessionId, { turns: history }).catch((e) => {
      console.warn("session history save failed:", e);
    });
  }, [history, running, sessionId]);

  // Build conversation_history payload from prior text turns (skip tool data —
  // backend doesn't need our local tool transcript, it re-runs fresh tools).
  // Summary turns are sent as assistant messages prefixed with "[CONTEXT
  // SUMMARY]" so the LLM knows it's compressed history, not a real reply.
  const buildHistoryPayload = useCallback((): ConversationMessage[] => {
    const out: ConversationMessage[] = [];
    for (const t of history) {
      if (t.kind === "user") {
        out.push({ role: "user", content: t.text });
      } else if (t.kind === "agent" && t.text) {
        out.push({ role: "assistant", content: t.text });
      } else if (t.kind === "summary" && t.text) {
        out.push({ role: "assistant", content: "[CONTEXT SUMMARY]\n" + t.text });
      }
    }
    return out;
  }, [history]);

  // Token estimate for the visible conversation. We can't see the system
  // prompt (server-built) so we add a fixed ~400-token allowance for it.
  const tokenEstimate = useMemo(() => {
    const SYSTEM_OVERHEAD = 400;
    let sum = SYSTEM_OVERHEAD;
    for (const t of history) {
      if (t.kind === "user") sum += estimateTokens(t.text);
      else if (t.kind === "agent") {
        sum += estimateTokens(t.text);
        for (const tc of t.tool_calls) sum += estimateTokens(tc.result);
      } else if (t.kind === "summary") sum += estimateTokens(t.text);
    }
    return sum;
  }, [history]);

  const ctxPct = Math.min(100, Math.round((tokenEstimate / numCtx) * 100));
  const ctxColor = ctxPct >= 90 ? "#ff6b6b" : ctxPct >= 70 ? "#f0a020" : "#4ade80";

  const runTurn = useCallback(async (overrideText?: string) => {
    // overrideText is used by toolbar buttons that submit a pre-built
    // prompt (e.g. "Review diff") without going through the textarea.
    const text = (overrideText ?? input).trim();
    if (!text || running) return;

    const userTurn: UserTurn = { kind: "user", id: makeId("u"), text, ts: Date.now() };
    const agentId = makeId("a");
    const conversationHistory = buildHistoryPayload();

    const liveTurn: AgentTurn = {
      kind: "agent",
      id: agentId,
      parentId: userTurn.id,
      ts: Date.now(),
      ok: false,
      text: "",
      stop_reason: "in_progress" as never,
      steps: 0,
      error: null,
      tool_calls: [],
      model,
      project_root: projectRoot,
      in_progress: true,
    };

    setHistory((h) => [...h, userTurn, liveTurn]);
    onUserTurn?.(text);
    // Only clear the textarea if we just consumed it. For toolbar-driven
    // submits (overrideText), leave the user's draft alone.
    if (overrideText === undefined) setInput("");
    setRunning(true);
    setLiveStep(null);

    const controller = new AbortController();
    abortRef.current = controller;
    runIdRef.current = null;

    function patchAgent(patch: (prev: AgentTurn) => AgentTurn) {
      setHistory((h) =>
        h.map((t) => (t.kind === "agent" && t.id === agentId ? patch(t) : t)),
      );
    }

    try {
      await api.streamCodeAgent({
        message: text,
        projectRoot,
        model,
        maxSteps,
        numCtx,
        autoRemember,
        conversationHistory,
        signal: controller.signal,
        onRunId: (rid) => {
          runIdRef.current = rid;
        },
        onEvent: (event) => {
          switch (event.type) {
            case "run_started":
              runIdRef.current = event.run_id;
              break;
            case "step_started":
              setLiveStep(event.step);
              break;
            case "tool_call": {
              const tc: CodeAgentToolCall = {
                step: event.step,
                tool: event.tool,
                arguments: event.arguments,
                result: event.result,
                touched_path: event.touched_path,
                old_content: event.old_content,
                new_content: event.new_content,
                diff_action: event.diff_action,
              };
              patchAgent((prev) => ({ ...prev, tool_calls: [...prev.tool_calls, tc] }));
              if (event.touched_path && onAgentTouchedFile) {
                onAgentTouchedFile(event.touched_path);
              }
              break;
            }
            case "final_response":
              patchAgent((prev) => ({ ...prev, text: event.text }));
              break;
            case "done":
              patchAgent((prev) => ({
                ...prev,
                ok: event.ok,
                steps: event.steps,
                stop_reason: event.stop_reason as never,
                error: event.error,
                in_progress: false,
              }));
              break;
          }
        },
        onError: (err) => {
          patchAgent((prev) => ({
            ...prev,
            ok: false,
            stop_reason: "error" as never,
            error: String(err?.message || err),
            in_progress: false,
          }));
        },
      });
    } finally {
      setRunning(false);
      setLiveStep(null);
      abortRef.current = null;
      // If the stream finished without a 'done' (e.g. server crash), mark turn finished.
      patchAgent((prev) => (prev.in_progress ? { ...prev, in_progress: false } : prev));
    }
  }, [input, running, projectRoot, model, maxSteps, numCtx, autoRemember, buildHistoryPayload, onAgentTouchedFile]);

  const compressHistory = useCallback(async () => {
    if (summarizing || running) return;
    // Keep last 2 text turns as-is, summarize everything older.
    const lastTwoIds: string[] = [];
    for (let i = history.length - 1; i >= 0 && lastTwoIds.length < 2; i--) {
      const t = history[i];
      if (t.kind === "user" || t.kind === "agent") lastTwoIds.push(t.id);
    }
    const olderText: ConversationMessage[] = [];
    let replaced = 0;
    for (const t of history) {
      if (lastTwoIds.includes(t.id)) continue;
      if (t.kind === "user") {
        olderText.push({ role: "user", content: t.text });
        replaced++;
      } else if (t.kind === "agent") {
        // Include tool-call summary alongside the agent text so the
        // summarizer sees WHAT THE AGENT DID, not just what it said.
        // Without this an 8-step turn that did write_file + run_bash +
        // edit_file ... but ended with "Готово." would collapse to just
        // "Готово." in the summary — completely useless for resuming.
        const parts: string[] = [];
        if (t.tool_calls && t.tool_calls.length > 0) {
          const calls = t.tool_calls.map(summarizeToolCall).join("; ");
          parts.push(`[tools used] ${calls}`);
        }
        if (t.text) parts.push(t.text);
        if (parts.length > 0) {
          olderText.push({ role: "assistant", content: parts.join("\n\n") });
          replaced++;
        }
      } else if (t.kind === "summary" && t.text) {
        olderText.push({ role: "assistant", content: "[PRIOR SUMMARY]\n" + t.text });
        replaced++;
      }
    }
    if (olderText.length < 2) {
      setSummaryError("Слишком мало сообщений для сжатия (нужно ≥ 2 в старой части).");
      setTimeout(() => setSummaryError(null), 4000);
      return;
    }
    setSummarizing(true);
    setSummaryError(null);
    try {
      const res = await api.summarizeHistory({ messages: olderText, model, numCtx });
      if (!res.ok) {
        setSummaryError(res.error || "Сжатие не удалось");
        return;
      }
      const newHistory: Turn[] = [];
      newHistory.push({
        kind: "summary",
        id: makeId("s"),
        text: res.summary,
        replaced,
        ts: Date.now(),
      });
      for (const t of history) {
        if (lastTwoIds.includes(t.id)) newHistory.push(t);
      }
      setHistory(newHistory);
    } catch (e) {
      setSummaryError(String((e as Error)?.message || e));
    } finally {
      setSummarizing(false);
    }
  }, [summarizing, running, history, model, numCtx]);

  const stopRun = useCallback(async () => {
    const rid = runIdRef.current;
    abortRef.current?.abort();
    if (rid) {
      try {
        await api.cancelCodeAgent(rid);
      } catch {
        // best-effort; abort already stopped the client side
      }
    }
  }, []);

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

  function startNewSession() {
    // If parent owns sessions (sidebar), delegate — preserves current
    // session, creates a new one alongside, switches active.
    if (onRequestNewSession) {
      if (running) {
        if (!confirm("Сейчас идёт стрим. Прервать его и создать новый чат?")) return;
        abortRef.current?.abort();
        setRunning(false);
        setLiveStep(null);
      }
      onRequestNewSession();
      return;
    }
    // Standalone fallback: wipe current session's history (legacy behaviour
    // for any consumer that doesn't provide onRequestNewSession).
    if (running) {
      if (!confirm("Сейчас идёт стрим. Прервать его и начать новую сессию?")) return;
      abortRef.current?.abort();
      setRunning(false);
      setLiveStep(null);
    }
    if (history.length > 0) {
      if (!confirm(`Начать новую сессию? Текущая история (${history.length} сообщений) будет удалена.`)) return;
    }
    setHistory([]);
    setInput("");
    setSummaryError(null);
  }

  const empty = useMemo(() => history.length === 0 && !running, [history.length, running]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {/* Mini header */}
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
        <button
          onClick={startNewSession}
          className="soft-btn"
          title="Начать новую сессию code-агента (текущая история будет удалена)"
          style={{ fontSize: 11, padding: "4px 10px", border: "1px solid var(--accent)", color: "var(--accent)", background: "transparent", display: "inline-flex", alignItems: "center", gap: 5 }}
        >
          <UiIcon icon={Plus} size={12} />
          <span>Новый чат</span>
        </button>
        <UiIcon icon={Sparkles} size={13} />
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          {history.length} turns
          {liveStep != null && running ? `  ·  шаг ${liveStep}` : ""}
        </span>

        {/* Context meter */}
        <div
          title={`Оценка ${tokenEstimate.toLocaleString()} токенов из ${numCtx.toLocaleString()} (${ctxPct}%). Жми «Сжать» когда подходит к 70-80%.`}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "2px 8px",
            borderRadius: 10,
            background: "var(--bg-surface)",
            border: "1px solid var(--border)",
            fontSize: 10,
            fontFamily: "var(--font-mono)",
          }}
        >
          <span style={{ width: 60, height: 6, background: "var(--border)", borderRadius: 3, overflow: "hidden", display: "inline-block" }}>
            <span style={{ display: "block", width: `${ctxPct}%`, height: "100%", background: ctxColor, transition: "width 200ms" }} />
          </span>
          <span style={{ color: ctxColor }}>
            {tokenEstimate.toLocaleString()} / {numCtx.toLocaleString()}
          </span>
        </div>

        <button
          onClick={compressHistory}
          disabled={summarizing || running || history.length < 3}
          className="soft-btn"
          title="Сжать всю историю кроме последних 2 сообщений в краткое summary, чтобы освободить контекст"
          style={{ fontSize: 10, padding: "3px 8px", opacity: summarizing || running || history.length < 3 ? 0.4 : 1 }}
        >
          {summarizing
            ? <IconText icon={Loader2} size={11} gap={4}>Сжимаю...</IconText>
            : <IconText icon={Minimize2} size={11} gap={4}>Сжать</IconText>
          }
        </button>

        <button
          onClick={() => { void runTurn(DIFF_REVIEW_PROMPT); }}
          disabled={running}
          className="soft-btn"
          title="Code review текущего git diff. Агент сам запустит git status + git diff, прочитает контекст файлов и выдаст структурированный отчёт (баги / безопасность / perf / стиль)."
          style={{ fontSize: 10, padding: "3px 8px", opacity: running ? 0.4 : 1 }}
        >
          <IconText icon={GitPullRequest} size={11} gap={4}>Review diff</IconText>
        </button>

        <button
          onClick={clearHistory}
          disabled={!history.length || running}
          className="soft-btn"
          style={{ marginLeft: "auto", fontSize: 10, padding: "3px 8px", opacity: !history.length || running ? 0.4 : 1 }}
        >
          <IconText icon={Trash2} size={11} gap={4}>
            Очистить
          </IconText>
        </button>
      </div>

      {summaryError && (
        <div style={{ padding: "6px 12px", borderBottom: "1px solid var(--border)", background: "rgba(255,107,107,0.08)", color: "#ff6b6b", fontSize: 11, flexShrink: 0 }}>
          {summaryError}
        </div>
      )}

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
                Опиши задачу — агент стримит шаги в реальном времени. Tool calls появляются
                сверху вниз, diff показывается inline для каждой записи в файл.
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
          if (turn.kind === "summary") {
            return (
              <div
                key={turn.id}
                style={{
                  marginBottom: 14,
                  padding: "8px 12px",
                  borderRadius: 8,
                  border: "1px dashed var(--accent, #6366f1)",
                  background: "rgba(99,102,241,0.07)",
                  fontSize: 11,
                  lineHeight: 1.6,
                  color: "var(--text-secondary)",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4, color: "var(--accent, #6366f1)", fontSize: 10, textTransform: "uppercase", letterSpacing: 0.4 }}>
                  <UiIcon icon={Pin} size={11} />
                  <span>Сжатое summary прошлых {turn.replaced} сообщений</span>
                </div>
                <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", fontFamily: "var(--font-mono)", fontSize: 11 }}>{turn.text}</div>
              </div>
            );
          }
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
              {turn.in_progress && !turn.text && (
                <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--text-muted)", fontSize: 11, padding: "4px 4px" }}>
                  <UiIcon icon={Loader2} size={12} />
                  <span>агент думает...</span>
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
                    wordBreak: "break-word",
                  }}
                >
                  <MarkdownRenderer content={turn.text} />
                </div>
              )}
              {!turn.in_progress && (
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
                  <span>{turn.ok ? "✓ готово" : `✕ ${String(turn.stop_reason)}`}</span>
                  <span>шаги: {turn.steps}</span>
                  <span style={{ fontFamily: "var(--font-mono)" }}>{turn.model}</span>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Saved prompts drawer */}
      {promptsOpen && (
        <div
          style={{
            padding: "8px 12px",
            borderTop: "1px solid var(--border)",
            background: "var(--bg-surface)",
            flexShrink: 0,
            maxHeight: 180,
            overflow: "auto",
          }}
        >
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 6, textTransform: "uppercase", letterSpacing: 0.4 }}>
            Шаблоны задач (клик — вставить в ввод)
          </div>
          {savedPrompts.length === 0 ? (
            <div style={{ fontSize: 11, color: "var(--text-muted)", padding: "4px 0" }}>Шаблоны пустые. Введи задачу и нажми «Сохранить как шаблон».</div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {savedPrompts.map((p) => (
                <div
                  key={p.id}
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    gap: 6,
                    padding: "5px 8px",
                    borderRadius: 6,
                    background: "var(--bg-input)",
                    border: "1px solid var(--border)",
                  }}
                >
                  <button
                    onClick={() => applySavedPrompt(p)}
                    style={{
                      flex: 1,
                      border: "none",
                      background: "transparent",
                      cursor: "pointer",
                      textAlign: "left",
                      color: "var(--text-primary)",
                      padding: 0,
                      minWidth: 0,
                    }}
                  >
                    <div style={{ fontSize: 11, fontWeight: 500, marginBottom: 2 }}>
                      <UiIcon icon={Star} size={10} /> {p.label}
                    </div>
                    <div
                      style={{
                        fontSize: 10,
                        color: "var(--text-muted)",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        fontFamily: "var(--font-mono)",
                      }}
                    >
                      {p.text.split("\n")[0]}
                    </div>
                  </button>
                  <button
                    onClick={() => deletePrompt(p.id)}
                    style={{
                      border: "none",
                      background: "transparent",
                      cursor: "pointer",
                      color: "var(--text-muted)",
                      padding: 2,
                      flexShrink: 0,
                    }}
                    title="Удалить шаблон"
                  >
                    <UiIcon icon={Trash2} size={11} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Composer */}
      <div style={{ padding: "8px 12px 10px", borderTop: "1px solid var(--border)", flexShrink: 0 }}>
        <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 6 }}>
          <button
            onClick={() => setPromptsOpen((v) => !v)}
            className="soft-btn"
            style={{ fontSize: 10, padding: "3px 8px" }}
            title="Шаблоны задач"
          >
            <IconText icon={Star} size={11} gap={4}>
              Шаблоны {savedPrompts.length > 0 && `(${savedPrompts.length})`}
            </IconText>
          </button>
          <button
            onClick={saveCurrentAsPrompt}
            disabled={!input.trim()}
            className="soft-btn"
            style={{ fontSize: 10, padding: "3px 8px", opacity: !input.trim() ? 0.4 : 1 }}
            title="Сохранить текущий ввод как шаблон"
          >
            <IconText icon={BookmarkPlus} size={11} gap={4}>
              Сохранить как шаблон
            </IconText>
          </button>
        </div>
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
          {running ? (
            <button
              onClick={stopRun}
              style={{
                padding: "9px 14px",
                borderRadius: 8,
                border: "1px solid #ff6b6b",
                background: "rgba(255,107,107,0.15)",
                color: "#ff6b6b",
                fontSize: 12,
                cursor: "pointer",
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <UiIcon icon={Square} size={13} />
              <span>Стоп</span>
            </button>
          ) : (
            <button
              onClick={() => { void runTurn(); }}
              disabled={!input.trim()}
              style={{
                padding: "9px 14px",
                borderRadius: 8,
                border: "none",
                background: "var(--accent, #6366f1)",
                color: "#fff",
                fontSize: 12,
                cursor: !input.trim() ? "not-allowed" : "pointer",
                opacity: !input.trim() ? 0.45 : 1,
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <UiIcon icon={Send} size={13} />
              <span>Отправить</span>
            </button>
          )}
        </div>
        <div style={{ marginTop: 4, fontSize: 10, color: "var(--text-muted)" }}>
          Ctrl+Enter — отправить. История сообщений идёт в conversation_history; агент помнит контекст в рамках вкладки.
        </div>
      </div>
    </div>
  );
}
