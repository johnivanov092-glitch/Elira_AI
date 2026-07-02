import {
  cancelCodeAgent,
  resolveApproval,
  resumeCodeAgent,
  streamCodeAgent,
  type CodeAgentMode,
  type CodeAgentStreamEvent,
  type ContextState,
  type ContextUsage,
  type ConversationMessage,
  type PermissionMode,
  type StreamHandlers,
  type TaskLedgerEntry,
} from "../api/codeAgent";
import type { ChatAttachment } from "../api/chat";
import { streamAdvancedMultiAgent } from "../api/project";
import type { AgentTurnData, FileEntry, Turn } from "./types";

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
  contextState: ContextState | null;
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
  /** Mode of the in-flight run. */
  lastMode: CodeAgentMode | null;
};

const _runs = new Map<string, RunEntry>();

function contextUsageFromState(state: ContextState | null): ContextUsage | null {
  if (!state) return null;
  const direct = state as Partial<ContextUsage>;
  if (
    typeof direct.current_tokens === "number" &&
    typeof direct.reserved_output_tokens === "number" &&
    typeof direct.ctx_size === "number" &&
    typeof direct.percent === "number" &&
    typeof direct.free_tokens === "number" &&
    direct.breakdown &&
    typeof direct.breakdown === "object"
  ) {
    return direct as ContextUsage;
  }
  return state.last_context_usage || null;
}

function withUsageState(state: ContextState | null, usage: ContextUsage): ContextState {
  return { ...(state || {}), ...usage, last_context_usage: usage };
}

function emptySnapshot(state: ContextState | null = null): RunSnapshot {
  const contextUsage = contextUsageFromState(state);
  return {
    turns: [],
    running: false,
    taskLedger: [],
    contextUsage,
    contextState: state,
    autoApprove: false,
    usageSeeded: contextUsage != null,
  };
}

function ensureEntry(sessionId: string): RunEntry {
  let entry = _runs.get(sessionId);
  if (!entry) {
    entry = {
      snapshot: emptySnapshot(),
      abort: null,
      runId: null,
      lastMode: null,
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
  state: ContextState | null = null,
): void {
  const entry = ensureEntry(sessionId);
  if (entry.snapshot.running) return; // background run owns the snapshot
  entry.autoApprove = false;
  const contextUsage = contextUsageFromState(state);
  entry.snapshot = {
    turns,
    running: false,
    taskLedger: ledger,
    contextUsage,
    contextState: state,
    autoApprove: false,
    usageSeeded: contextUsage != null,
  };
  entry.runId = null;
  entry.activeAgentId = null;
  notify(entry);
}

/** Apply a freshly-fetched context-profile seed if the meter is still unseeded
 *  and no live event has set it. */
export function applyContextSeed(sessionId: string, usage: ContextUsage): void {
  const entry = _runs.get(sessionId);
  if (!entry || entry.snapshot.usageSeeded || entry.snapshot.contextUsage) return;
  update(entry, (s) => ({ ...s, contextUsage: usage, contextState: withUsageState(s.contextState, usage) }));
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
      else if (e.type === "reasoning_delta") patch((a) => ({ ...a, reasoning: (a.reasoning ?? "") + e.text }));
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
        update(entry, (s) => {
          const contextState = e.context ? withUsageState(s.contextState, e.context) : { ...(s.contextState || {}) };
          const summary = (e.rolling_summary || "").trim();
          if (summary) {
            contextState.rolling_summary_text = summary;
            contextState.rolling_summary_updated_at = Date.now();
            contextState.rolling_summary_source = "code-agent-compaction";
          }
          return {
            ...s,
            usageSeeded: true,
            contextUsage: e.context || s.contextUsage,
            contextState,
          };
        });
        pushLedger({ timestamp: Date.now(), type: "compression", action: `step ${e.step}`, result: "completed" });
      } else if (e.type === "usage") {
        if (e.context) {
          update(entry, (s) => ({
            ...s,
            usageSeeded: true,
            contextUsage: e.context!,
            contextState: withUsageState(s.contextState, e.context!),
          }));
        }
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
  /** Active UI persona profile (the `agent_profile` global setting). Threaded
   *  into the code-agent stream so the user's selected mode reaches Elira's
   *  persona prompt; undefined falls back to the backend default. */
  profileName?: string;
  /** Approval policy picked in the composer's permission selector; undefined →
   *  backend default "ask". */
  permissionMode?: PermissionMode;
  /** Enable model reasoning for this run («Рассуждение» chip). */
  thinking?: boolean;
};

/** Start a run for a session. Appends the user + agent turns to that session's
 *  snapshot and begins streaming into it (in the background, regardless of
 *  which session is currently displayed). */
export function send(args: SendArgs): void {
  const { sessionId, text, mode, projectRoot, model, attachments, profileName, permissionMode, thinking } = args;
  const msg = text.trim();
  const entry = ensureEntry(sessionId);
  if (!msg || entry.snapshot.running) return;

  const history: ConversationMessage[] = [];
  const rollingSummary = typeof entry.snapshot.contextState?.rolling_summary_text === "string"
    ? entry.snapshot.contextState.rolling_summary_text.trim()
    : "";
  if (rollingSummary) {
    history.push({ role: "assistant", content: `[CONTEXT SUMMARY]\n${rollingSummary}` });
  }
  for (const t of entry.snapshot.turns) {
    if (t.kind === "user") history.push({ role: "user", content: t.text });
    else if (t.kind === "agent" && t.text) history.push({ role: "assistant", content: t.text });
  }
  const agentId = nid();
  entry.runId = null;
  entry.lastMode = mode;
  // Per-message attachments: surface a file chip in the transcript so the user
  // sees the message carried a file (the parsed text/transcription itself goes
  // to the agent inline). "attached" renders neutrally — no library status.
  const fileEntries: FileEntry[] = (attachments ?? [])
    .filter((a) => a && a.filename)
    .map((a): FileEntry => ({
      name: a.filename || "файл",
      isImage: a.kind === "image",
      status: a.ok === false ? "error" : "attached",
    }));
  update(entry, (s) => ({
    ...s,
    running: true,
    turns: [
      ...s.turns,
      ...(fileEntries.length ? [{ kind: "files" as const, id: nid(), files: fileEntries }] : []),
      { kind: "user", id: nid(), text: msg },
      { kind: "agent", id: agentId, toolCalls: [], text: "", running: true },
    ],
  }));
  // One stream invoker for every mode: `/api/code-agent/stream` already accepts
  // a project root and parsed attachments together, so the unified "Чат\Код"
  // chip carries both at once.
  wire(entry, agentId, (handlers) =>
    streamCodeAgent({ message: msg, projectRoot, model, mode, conversationHistory: history, attachments, profileName, permissionMode, thinking, ...handlers }));
}

/** Start a MULTI-AGENT run for a session. `/api/advanced/multi-agent/stream`
 *  runs a pipeline of 3–5 chained LLM calls and emits one SSE `step` event per
 *  workflow step before the final `done` event carries the combined report. So
 *  we append the user + a "working…" agent turn (running:true), then drive that
 *  turn from the stream: each `step` updates `activeTool` to a "Шаг N/total: …"
 *  status, `done` writes the whole report as one block, `error` surfaces the
 *  message. Stop aborts the SSE fetch (entry.abort); the backend then cancels
 *  the pipeline between steps. Late events after a stop are ignored. */
export function sendMultiAgent(
  args: { sessionId: string; text: string; useOrchestrator: boolean; useReflection: boolean; projectRoot?: string },
): void {
  const { sessionId, text, useOrchestrator, useReflection, projectRoot } = args;
  const msg = text.trim();
  const entry = ensureEntry(sessionId);
  if (!msg || entry.snapshot.running) return;

  const agentId = nid();
  entry.runId = null;
  entry.persistedAtDone = false;
  // AbortController so Stop genuinely tears down the SSE connection. When the
  // fetch aborts, the backend generator gets GeneratorExit and cancels the
  // pipeline between steps. stop() calls entry.abort?.abort().
  const ctrl = new AbortController();
  entry.abort = ctrl;
  entry.activeAgentId = agentId;
  // After a stop, a late event (a `done` already in flight) must not overwrite
  // the turn or re-flip persistence. stop() aborts the controller and clears
  // activeAgentId, so either guard catches a stale callback.
  const stopped = () => ctrl.signal.aborted || entry.activeAgentId !== agentId;
  update(entry, (s) => ({
    ...s,
    running: true,
    turns: [
      ...s.turns,
      { kind: "user", id: nid(), text: msg },
      { kind: "agent", id: agentId, toolCalls: [], text: "", running: true },
    ],
  }));

  void streamAdvancedMultiAgent(
    {
      query: msg,
      use_orchestrator: useOrchestrator,
      use_reflection: useReflection,
      ...(projectRoot ? { project_root: projectRoot } : {}),
    },
    {
      onStep: (index, total, label) => {
        if (stopped()) return;
        patchAgent(entry, agentId, (a) => ({
          ...a,
          activeTool: `Шаг ${index}/${total}: ${label}`,
        }));
      },
      onDone: (res) => {
        if (stopped()) return;
        const ok = res.ok !== false;
        const report = typeof res.report === "string" ? res.report : "";
        const errMsg = typeof res.error === "string" ? res.error : "";
        patchAgent(entry, agentId, (a) => ({
          ...a,
          running: false,
          activeTool: undefined,
          text: ok ? (report || "Мульти-агент не вернул ответ.") : a.text,
          error: ok ? undefined : (errMsg || "Мульти-агент завершился с ошибкой."),
        }));
      },
      onError: (e) => {
        if (stopped()) return;
        patchAgent(entry, agentId, (a) => ({
          ...a,
          running: false,
          activeTool: undefined,
          error: e instanceof Error ? e.message : "Не удалось выполнить мульти-агентный запуск.",
        }));
      },
    },
    ctrl.signal,
  ).finally(() => {
    if (entry.abort === ctrl) entry.abort = null;
    if (stopped()) return; // Stop already reset running/turn state.
    entry.activeAgentId = null;
    update(entry, (s) => ({ ...s, running: false }));
    if (!entry.persistedAtDone) {
      entry.persistedAtDone = true;
      entry.persist?.(entry.snapshot);
    }
  });
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
  if (rid) {
    void cancelCodeAgent(rid).catch(() => {});
  }
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

/** Re-activate an approval's buttons if resolving it failed (network error etc.),
 *  so the chip isn't stuck on "отправлено…" forever (FIX-15). */
function revertResolving(entry: RunEntry, approvalId: string): void {
  update(entry, (s) => ({
    ...s,
    turns: s.turns.map((t) => (t.kind === "agent" && t.pendingApproval?.approvalId === approvalId
      ? { ...t, pendingApproval: { ...t.pendingApproval, resolving: false } }
      : t)),
  }));
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
    void resolveApproval(approvalId, decision).catch(() => revertResolving(entry, approvalId));
  }
}

export function approveAll(sessionId: string): void {
  const entry = ensureEntry(sessionId);
  entry.autoApprove = true;
  update(entry, (s) => ({
    ...s,
    autoApprove: true,
    turns: s.turns.map((t) => {
      if (t.kind === "agent" && t.pendingApproval && !t.pendingApproval.resolving) {
        const id = t.pendingApproval.approvalId;
        void resolveApproval(id, "approve").catch(() => revertResolving(entry, id));
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
