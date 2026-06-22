import {
  cancelCodeAgent,
  resolveApproval,
  resumeCodeAgent,
  streamCodeAgent,
  type CodeAgentMode,
  type CodeAgentStreamEvent,
  type ContextUsage,
  type ConversationMessage,
  type StreamHandlers,
  type TaskLedgerEntry,
} from "../api/codeAgent";
import { streamChatPlanner, type ChatAttachment } from "../api/chat";
import type { AgentTurnData, Turn } from "./types";

/**
 * Background run manager.
 *
 * The SSE reader for a chat run lives here — keyed by session id — NOT inside a
 * React hook. That is the whole point: when you switch chats, the React view
 * detaches but the reader keeps running and keeps accumulating its events into
 * this store. Returning to the chat re-attaches the view to the already-running
 * (or already-finished) snapshot.
 *
 * Why not "park and resume"? The backend resume endpoint 409s on a run that is
 * still `running` (it is only resumable once interrupted/terminal), and a client
 * disconnect *stops* the synchronous server generator (GeneratorExit ->
 * journal.finish(interrupted=True)). So the only way to truly continue in the
 * background is to never drop the reader. This manager is that kept-alive reader.
 */

let _seq = 0;
const nid = () => `t${++_seq}`;

/** The accumulating, renderable state of one run. Mirrors what useAgentRun
 *  used to hold in React state, but lives outside React so it survives view
 *  detach. */
export type RunSnapshot = {
  turns: Turn[];
  running: boolean;
  taskLedger: TaskLedgerEntry[];
  contextUsage: ContextUsage | null;
  /** Per-session auto-approve. In the snapshot so the banner re-renders when it
   *  flips; `entry.autoApprove` mirrors it for synchronous reads inside the
   *  reader (which runs outside React). */
  autoApprove: boolean;
  /** True once a live usage/compaction event set the real meter, so a seed
   *  never clobbers it. */
  usageSeeded: boolean;
};

type Listener = () => void;

/** Persist hook: called when a run finishes (even off-screen) so the session
 *  is saved to the sidebar/storage without needing the view attached. */
export type PersistFn = (snapshot: RunSnapshot) => void;

type RunEntry = {
  snapshot: RunSnapshot;
  abort: AbortController | null;
  runId: string | null;
  autoApprove: boolean;
  /** Agent turn id currently being streamed into. */
  activeAgentId: string | null;
  listeners: Set<Listener>;
  persist: PersistFn | null;
  /** Guards a duplicate persist when `done` fires and the view also reacts. */
  persistedAtDone: boolean;
};

const _runs = new Map<string, RunEntry>();

function emptySnapshot(usage: ContextUsage | null = null): RunSnapshot {
  return { turns: [], running: false, taskLedger: [], contextUsage: usage, autoApprove: false, usageSeeded: usage != null };
}

function ensureEntry(sessionId: string): RunEntry {
  let entry = _runs.get(sessionId);
  if (!entry) {
    entry = {
      snapshot: emptySnapshot(),
      abort: null,
      runId: null,
      autoApprove: false,
      activeAgentId: null,
      listeners: new Set(),
      persist: null,
      persistedAtDone: false,
    };
    _runs.set(sessionId, entry);
  }
  return entry;
}

function notify(entry: RunEntry): void {
  for (const fn of entry.listeners) fn();
}

/** Replace the snapshot (immutably) and notify subscribers. */
function update(entry: RunEntry, fn: (s: RunSnapshot) => RunSnapshot): void {
  entry.snapshot = fn(entry.snapshot);
  notify(entry);
}

function patchAgent(
  entry: RunEntry,
  agentId: string,
  fn: (a: AgentTurnData) => AgentTurnData,
): void {
  update(entry, (s) => ({
    ...s,
    turns: s.turns.map((t) => (t.kind === "agent" && t.id === agentId ? fn(t) : t)),
  }));
}

// ── Public read API (consumed by useAgentRun via useSyncExternalStore) ──────

export function getSnapshot(sessionId: string): RunSnapshot {
  return _runs.get(sessionId)?.snapshot ?? EMPTY;
}
const EMPTY = emptySnapshot();

export function subscribe(sessionId: string, listener: Listener): () => void {
  const entry = ensureEntry(sessionId);
  entry.listeners.add(listener);
  return () => {
    entry.listeners.delete(listener);
    // Keep finished/idle entries with no listeners from leaking forever, but
    // never drop one that is still running in the background.
    if (entry.listeners.size === 0 && !entry.snapshot.running && entry.runId == null) {
      _runs.delete(sessionId);
    }
  };
}

export function isRunning(sessionId: string): boolean {
  return _runs.get(sessionId)?.snapshot.running ?? false;
}

/** Register where a finished run should be persisted. Re-set whenever the
 *  active session's persist closure changes (it captures project/model). */
export function setPersist(sessionId: string, persist: PersistFn | null): void {
  ensureEntry(sessionId).persist = persist;
}

// ── Seeding / restoring ─────────────────────────────────────────────────────

/** Seed (or reset) a session's snapshot — used when restoring a saved chat or
 *  clearing a fresh one. Never touches a session that is actively running. */
export function seed(
  sessionId: string,
  turns: Turn[],
  ledger: TaskLedgerEntry[] = [],
  usage: ContextUsage | null = null,
): void {
  const entry = ensureEntry(sessionId);
  if (entry.snapshot.running) return; // background run owns the snapshot
  entry.autoApprove = false;
  entry.snapshot = { turns, running: false, taskLedger: ledger, contextUsage: usage, autoApprove: false, usageSeeded: usage != null };
  entry.runId = null;
  entry.activeAgentId = null;
  notify(entry);
}

/** Apply a freshly-fetched context-profile seed if the meter is still unseeded
 *  and no live event has set it. */
export function applyContextSeed(sessionId: string, usage: ContextUsage): void {
  const entry = _runs.get(sessionId);
  if (!entry || entry.snapshot.usageSeeded || entry.snapshot.contextUsage) return;
  update(entry, (s) => ({ ...s, contextUsage: usage }));
}

// ── Wiring the SSE reader (shared by send + resume) ─────────────────────────

function wire(
  entry: RunEntry,
  agentId: string,
  invoke: (handlers: StreamHandlers & { signal: AbortSignal }) => Promise<void>,
): void {
  const ctrl = new AbortController();
  entry.abort = ctrl;
  entry.activeAgentId = agentId;
  entry.persistedAtDone = false;
  const patch = (fn: (a: AgentTurnData) => AgentTurnData) => patchAgent(entry, agentId, fn);
  const pushLedger = (item: TaskLedgerEntry) =>
    update(entry, (s) => ({ ...s, taskLedger: [...s.taskLedger, item].slice(-200) }));

  void invoke({
    signal: ctrl.signal,
    onRunId: (id) => {
      entry.runId = id;
      patch((a) => ({ ...a, runId: id }));
    },
    onEvent: (e: CodeAgentStreamEvent) => {
      if (e.type === "run_started" || e.type === "run_resumed") {
        entry.runId = e.run_id;
        patch((a) => ({ ...a, runId: e.run_id }));
      }
      if (e.type === "tool_started") patch((a) => ({ ...a, activeTool: e.tool, pendingApproval: undefined }));
      else if (e.type === "delta") patch((a) => ({ ...a, text: a.text + e.text }));
      else if (e.type === "tool_call") {
        patch((a) => ({ ...a, toolCalls: [...a.toolCalls, e], activeTool: undefined, pendingApproval: undefined }));
        const result = e.result.trim();
        pushLedger({
          timestamp: Date.now(),
          type: "tool_call",
          action: e.tool,
          result: e.ok === false || /^error\b/i.test(result) ? result.slice(0, 500) : `completed (${result.length} chars)`,
        });
      } else if (e.type === "approval_pending") {
        if (entry.autoApprove) {
          void resolveApproval(e.approval_id, "approve").catch(() => {});
          patch((a) => ({ ...a, pendingApproval: undefined }));
        } else patch((a) => ({ ...a, pendingApproval: { approvalId: e.approval_id, tool: e.tool, arguments: e.arguments } }));
      } else if (e.type === "approval_wait") patch((a) => (a.pendingApproval ? { ...a, pendingApproval: { ...a.pendingApproval, waitedS: e.waited_s } } : a));
      else if (e.type === "context_compacted") {
        if (e.context) update(entry, (s) => ({ ...s, usageSeeded: true, contextUsage: e.context! }));
        pushLedger({ timestamp: Date.now(), type: "compression", action: `step ${e.step}`, result: "completed" });
      } else if (e.type === "usage") {
        if (e.context) update(entry, (s) => ({ ...s, usageSeeded: true, contextUsage: e.context! }));
        patch((a) => ({
          ...a,
          genTokens: (a.genTokens ?? 0) + (e.completion_tokens || 0),
          tokensPerSecond: e.tokens_per_second || a.tokensPerSecond,
        }));
      } else if (e.type === "final_response") patch((a) => ({ ...a, text: e.text }));
      else if (e.type === "done") {
        pushLedger({ timestamp: Date.now(), type: e.ok ? "final" : "error", action: e.stop_reason, result: e.error || "completed" });
        patch((a) => ({
          ...a,
          running: false,
          activeTool: undefined,
          pendingApproval: undefined,
          stopReason: e.stop_reason,
          error: e.error,
          resumable: Boolean(e.resumable),
          runId: e.run_id || a.runId,
        }));
        update(entry, (s) => ({ ...s, running: false }));
        entry.runId = null;
        entry.activeAgentId = null;
        entry.abort = null;
        if (!entry.persistedAtDone) {
          entry.persistedAtDone = true;
          entry.persist?.(entry.snapshot);
        }
      }
    },
    onError: (err) => {
      patch((a) => ({ ...a, running: false, activeTool: undefined, error: err.message }));
      update(entry, (s) => ({ ...s, running: false }));
      entry.abort = null;
    },
  });
}

// ── Public mutation API ─────────────────────────────────────────────────────

export type SendArgs = {
  sessionId: string;
  text: string;
  mode: CodeAgentMode;
  projectRoot: string;
  model: string;
  attachments?: ChatAttachment[];
};

/** Start a run for a session. Appends the user + agent turns to that session's
 *  snapshot and begins streaming into it (in the background, regardless of
 *  which session is currently displayed). */
export function send(args: SendArgs): void {
  const { sessionId, text, mode, projectRoot, model, attachments } = args;
  const msg = text.trim();
  const entry = ensureEntry(sessionId);
  if (!msg || entry.snapshot.running) return;

  const history: ConversationMessage[] = [];
  for (const t of entry.snapshot.turns) {
    if (t.kind === "user") history.push({ role: "user", content: t.text });
    else if (t.kind === "agent" && t.text) history.push({ role: "assistant", content: t.text });
  }
  const agentId = nid();
  entry.runId = null;
  update(entry, (s) => ({
    ...s,
    running: true,
    turns: [
      ...s.turns,
      { kind: "user", id: nid(), text: msg },
      { kind: "agent", id: agentId, toolCalls: [], text: "", running: true },
    ],
  }));
  // "Чат" mode is an ordinary conversational planner (/api/chat/stream), not the
  // code-agent. The chat invoker translates its SSE into CodeAgentStreamEvents,
  // so wire() consumes it identically.
  if (mode === "chat") {
    wire(entry, agentId, (handlers) =>
      streamChatPlanner({ message: msg, sessionId, model, conversationHistory: history, attachments, ...handlers }));
  } else {
    wire(entry, agentId, (handlers) =>
      streamCodeAgent({ message: msg, projectRoot, model, mode, conversationHistory: history, ...handlers }));
  }
}

/** Resume a persisted interrupted/partial run for a session's agent turn. */
export function resume(sessionId: string, agentId: string, runId: string): void {
  const entry = ensureEntry(sessionId);
  if (entry.snapshot.running) return;
  entry.runId = runId;
  update(entry, (s) => ({
    ...s,
    running: true,
    turns: s.turns.map((t) => (t.kind === "agent" && t.id === agentId
      ? { ...t, running: true, error: undefined, resumable: false }
      : t)),
  }));
  wire(entry, agentId, (handlers) => resumeCodeAgent(runId, handlers));
}

/** Stop a session's run: cancel server-side and drop the reader. */
export function stop(sessionId: string): void {
  const entry = _runs.get(sessionId);
  if (!entry) return;
  const rid = entry.runId;
  if (rid) void cancelCodeAgent(rid).catch(() => {});
  entry.abort?.abort();
  entry.abort = null;
  entry.runId = null;
  entry.activeAgentId = null;
  update(entry, (s) => ({
    ...s,
    running: false,
    turns: s.turns.map((t) => (t.kind === "agent" && t.running ? { ...t, running: false } : t)),
  }));
}

export function setAutoApprove(sessionId: string, on: boolean): void {
  const entry = ensureEntry(sessionId);
  entry.autoApprove = on;
  update(entry, (s) => ({ ...s, autoApprove: on }));
}

export function isAutoApprove(sessionId: string): boolean {
  return _runs.get(sessionId)?.autoApprove ?? false;
}

/** Resolve a pending approval and mark it resolving in the snapshot. */
export function approve(sessionId: string, approvalId: string, decision: "approve" | "reject"): void {
  const entry = _runs.get(sessionId);
  if (entry) {
    update(entry, (s) => ({
      ...s,
      turns: s.turns.map((t) => (t.kind === "agent" && t.pendingApproval?.approvalId === approvalId
        ? { ...t, pendingApproval: { ...t.pendingApproval, resolving: true } }
        : t)),
    }));
  }
  void resolveApproval(approvalId, decision).catch(() => {});
}

export function approveAll(sessionId: string): void {
  const entry = ensureEntry(sessionId);
  entry.autoApprove = true;
  update(entry, (s) => ({
    ...s,
    autoApprove: true,
    turns: s.turns.map((t) => {
      if (t.kind === "agent" && t.pendingApproval && !t.pendingApproval.resolving) {
        void resolveApproval(t.pendingApproval.approvalId, "approve").catch(() => {});
        return { ...t, pendingApproval: { ...t.pendingApproval, resolving: true } };
      }
      return t;
    }),
  }));
}

/** Append uploaded-files turn placeholders and return the turn id + a status
 *  setter, so the caller can drive uploads. Lives here so files survive a
 *  background switch too. */
export function appendFilesTurn(sessionId: string, names: { name: string; isImage: boolean; url?: string }[]): {
  turnId: string;
  setStatus: (idx: number, status: "uploading" | "saved" | "error") => void;
} {
  const entry = ensureEntry(sessionId);
  const turnId = nid();
  update(entry, (s) => ({
    ...s,
    turns: [...s.turns, { kind: "files", id: turnId, files: names.map((n) => ({ ...n, status: "uploading" as const })) }],
  }));
  const setStatus = (idx: number, status: "uploading" | "saved" | "error") =>
    update(entry, (s) => ({
      ...s,
      turns: s.turns.map((t) => (t.kind === "files" && t.id === turnId
        ? { ...t, files: t.files.map((f, i) => (i === idx ? { ...f, status } : f)) }
        : t)),
    }));
  return { turnId, setStatus };
}

/** Move a session's live run state to a new id (used when a lazily-created
 *  session id replaces the temporary "scratch" key after the first send). */
export function rekey(fromId: string, toId: string): void {
  if (fromId === toId) return;
  const entry = _runs.get(fromId);
  if (!entry) return;
  _runs.delete(fromId);
  const existing = _runs.get(toId);
  if (existing && existing !== entry) {
    // Merge: prefer the live (running) entry. The target is normally empty.
    for (const fn of existing.listeners) entry.listeners.add(fn);
  }
  _runs.set(toId, entry);
  notify(entry);
}
