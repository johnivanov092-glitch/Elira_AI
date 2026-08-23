import {
  cancelCodeAgent,
  resumeCodeAgent,
  streamCodeAgent,
  type CodeAgentMode,
  type CodeAgentStreamEvent,
  type ContextState,
  type ContextUsage,
  type ConversationMessage,
  type PermissionMode,
  type ReasoningEffort,
  type StreamHandlers,
  type TaskLedgerEntry,
} from "../api/codeAgent";
import type { ResourceAttachment } from "../api/resources";
import { streamAdvancedMultiAgent } from "../api/project";
import type { AgentTurnData, FileEntry, Turn } from "./types";
import { latestUserTaskLabel } from "./taskHistory";

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
  /** Agent turn id currently being streamed into. */
  activeAgentId: string | null;
  listeners: Set<Listener>;
  persist: PersistFn | null;
  /** Guards a duplicate persist when `done` fires and the view also reacts. */
  persistedAtDone: boolean;
  /** Mode of the in-flight run. */
  lastMode: CodeAgentMode | null;
};

type CodeAgentDoneEvent = Extract<CodeAgentStreamEvent, { type: "done" }>;

/** Ledger lines for a terminal `done`. Pure (exported for unit tests): the
 * task-outcome line as before, plus — ONLY on a non-solved terminal that
 * carries the server-derived `next_milestone` — an explicit
 * «Следующий шаг: …» line so an honest partial tells the user what remains. */
export function doneLedgerEntries(
  e: CodeAgentDoneEvent,
  action: string = e.stop_reason,
): TaskLedgerEntry[] {
  const cs = e.completion_status;
  const answerComplete = !e.answer_status || e.answer_status === "complete";
  const solved = answerComplete && (
    cs === "confirmed" || ((!cs || cs === "none") && e.ok && e.stop_reason === "answer")
  );
  const ledgerType = solved ? "final" : !e.ok || cs === "failed" ? "error" : "partial";
  const ledgerResult =
    e.error || (
      e.answer_status === "needs_input"
        ? "нужно уточнение пользователя"
        : e.answer_status === "degraded"
          ? "ответ с ограничениями"
          : solved
            ? "completed"
            : cs && cs !== "none"
              ? `задача: ${cs} (не solved)`
              : e.stop_reason
    );
  const entries: TaskLedgerEntry[] = [
    { timestamp: Date.now(), type: ledgerType, action, result: ledgerResult },
  ];
  if (!solved && e.next_milestone) {
    entries.push({
      timestamp: Date.now(),
      type: "partial",
      action: "next_milestone",
      result: `Следующий шаг: ${e.next_milestone}`,
    });
  }
  return entries;
}

const _runs = new Map<string, RunEntry>();

/** Collapse consecutive duplicate paragraphs in an assistant answer before it
 *  enters conversation_history. A degenerate ×N-repeated answer that slipped
 *  into a saved turn would otherwise teach the model "my style is to repeat
 *  myself" on every following turn — this breaks that feedback loop. Exact
 *  consecutive matches only, so legitimately repeated short lines survive. */
function dedupeParagraphs(text: string): string {
  if (!text.includes("\n")) return text;
  const out: string[] = [];
  let prev: string | null = null;
  for (const para of text.split("\n")) {
    const key = para.trim();
    if (key.length > 40 && key === prev) continue;
    out.push(para);
    if (key) prev = key;
  }
  return out.join("\n");
}

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
  const contextUsage = contextUsageFromState(state);
  entry.snapshot = {
    turns,
    running: false,
    taskLedger: ledger,
    contextUsage,
    contextState: state,
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
      if (e.type === "planning_started") patch((a) => ({ ...a, brainPhase: "planning" }));
      else if (e.type === "phase_changed") patch((a) => ({ ...a, brainPhase: e.phase }));
      else if (e.type === "planning_fallback") {
        patch((a) => ({ ...a, brainPhase: "execution" }));
      }
      else if (e.type === "tool_started") patch((a) => ({ ...a, activeTool: e.tool }));
      else if (e.type === "step_started") {
        // Show only the CURRENT step's stream. Deltas used to concatenate across
        // all steps into one blob; final_response normally replaced it, but on
        // abort paths (LLM error / cancel / stream timeout) no final arrives and
        // the multi-step concat got persisted as the answer — and then fed back
        // into the next turn's history, teaching the model to repeat itself.
        patch((a) => (a.running ? { ...a, text: "", reasoning: undefined } : a));
      }
      else if (e.type === "delta") patch((a) => ({ ...a, text: a.text + e.text }));
      else if (e.type === "reasoning_delta") patch((a) => ({ ...a, reasoning: (a.reasoning ?? "") + e.text }));
      else if (e.type === "tool_call") {
        patch((a) => ({ ...a, toolCalls: [...a.toolCalls, e], activeTool: undefined }));
        const result = e.result.trim();
        pushLedger({
          timestamp: Date.now(),
          type: "tool_call",
          action: e.tool,
          // Show the real output (not a bland "completed") when the call failed
          // semantically: ok===false, an ERROR text, or a non-zero shell exit —
          // so an `exit=1` no longer reads as a success in the ledger.
          result: e.ok === false || /^error\b/i.test(result) || /\bexit=(?!0\b)\d+/.test(result) ? result.slice(0, 500) : `completed (${result.length} chars)`,
        });
      } else if (e.type === "context_compacted") {
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
      } else if (e.type === "final_response") patch((a) => ({ ...a, text: e.text, answerStatus: e.answer_status || a.answerStatus, establishedFacts: e.established_facts || a.establishedFacts, recentToolOutput: e.recent_tool_output || a.recentToolOutput, media: e.media ?? a.media }));
      else if (e.type === "delivery_continuing") {
        // Informational slice boundary: the run keeps going on the SAME run_id
        // after context-window rollover. The turn
        // stays `running`; only the ledger surfaces the transition honestly.
        pushLedger({
          timestamp: Date.now(),
          type: "partial",
          action: `delivery ${e.slice}→${e.next_slice}`,
          result: `продолжаю тот же прогон (автопродолжение ${e.auto_continuation}, без лимита; причина: ${e.stop_reason})`,
        });
      }
      else if (e.type === "done") {
        // Ledger reports the TASK outcome (completion_status), not runtime ok — a
        // run can be ok=true yet unverified/partial/failed. SOLVED ("final") ⇔
        // completion_status === "confirmed", OR a no-criteria run that reached a
        // clean answer. failed verifier / runtime failure = "error". Everything
        // else that's ok-but-not-confirmed = "partial" (surfaced, never "solved").
        // On an honest-partial terminal the server-derived next_milestone gets
        // its own explicit ledger line (see doneLedgerEntries).
        const action = latestUserTaskLabel(entry.snapshot.turns) || e.stop_reason;
        for (const le of doneLedgerEntries(e, action)) pushLedger(le);
        patch((a) => ({
          ...a,
          running: false,
          activeTool: undefined,
          brainPhase: undefined,
          stopReason: e.stop_reason,
          answerStatus: e.answer_status || a.answerStatus,
          error: e.error,
          completionStatus: e.completion_status,
          criteria: e.criteria,
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
      patch((a) => ({ ...a, running: false, activeTool: undefined, brainPhase: undefined, error: err.message }));
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
  /** Durable resources attached to this message (uploaded, not processed). */
  resources?: ResourceAttachment[];
  /** Active UI persona profile (the `agent_profile` global setting). Threaded
   *  into the code-agent stream so the user's selected mode reaches Elira's
   *  persona prompt; undefined falls back to the backend default. */
  profileName?: string;
  /** Approval policy picked in the composer's permission selector; undefined →
   *  backend default "ask". */
  permissionMode?: PermissionMode;
  /** Qwen reasoning depth selected in the composer. */
  reasoningEffort?: ReasoningEffort;
};

/** Start a run for a session. Appends the user + agent turns to that session's
 *  snapshot and begins streaming into it (in the background, regardless of
 *  which session is currently displayed). */
export function send(args: SendArgs): void {
  const { sessionId, text, mode, projectRoot, model, resources, profileName, permissionMode, reasoningEffort } = args;
  const msg = text.trim();
  const entry = ensureEntry(sessionId);
  if (!msg || entry.snapshot.running) return;

  const history: ConversationMessage[] = [];
  let latestRecentOutput: string | undefined;  // most-recent agent turn's raw tool output
  const rollingSummary = typeof entry.snapshot.contextState?.rolling_summary_text === "string"
    ? entry.snapshot.contextState.rolling_summary_text.trim()
    : "";
  if (rollingSummary) {
    history.push({ role: "assistant", content: `[CONTEXT SUMMARY]\n${rollingSummary}` });
  }
  for (const t of entry.snapshot.turns) {
    if (t.kind === "user") history.push({ role: "user", content: t.text });
    else if (t.kind === "agent" && t.text) {
      history.push({ role: "assistant", content: dedupeParagraphs(t.text) });
      // Carry the turn's tool-established facts back as an authoritative grounding
      // block (backend _coerce_history re-tags the [ПРОВЕРЕННЫЕ ФАКТЫ] prefix to a
      // system message). Without this the agent loses what it learned via tools and
      // confabulates factual follow-ups. Prefix must match backend FACTS_PREFIX.
      if (t.establishedFacts) {
        history.push({ role: "assistant", content: `[ПРОВЕРЕННЫЕ ФАКТЫ]\n${t.establishedFacts}` });
      }
      latestRecentOutput = t.recentToolOutput;  // overwritten each agent turn → ends as the last
    }
  }
  // Verbatim raw output of the MOST RECENT agent turn only (not every turn — it
  // would accumulate). Placed last, right before the new user message. Prefix must
  // match backend _RECENT_PREFIX; _coerce_history re-tags it to a system message.
  if (latestRecentOutput) {
    history.push({
      role: "assistant",
      content: `[РЕЗУЛЬТАТЫ ИНСТРУМЕНТОВ ПРОШЛОГО ХОДА]\n${latestRecentOutput}`,
    });
  }
  const agentId = nid();
  entry.runId = null;
  entry.lastMode = mode;
  // Per-message resources: surface a file chip in the transcript so the user
  // sees the message carried a file. The file is NOT processed here — the agent
  // reads it on demand via resource_process. "attached" renders neutrally.
  const fileEntries: FileEntry[] = (resources ?? [])
    .filter((r) => r && r.name && r.status !== "uploading")
    .map((r): FileEntry => ({
      name: r.name || "файл",
      isImage: r.kind === "image",
      status: r.status === "error" ? "error" : "attached",
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
  // One stream invoker for every mode. Ready resources ride along as ResourceRefs
  // (resource_id only); the agent reads their content via resource_process. The
  // session id lets the backend bind only resources this session owns.
  const readyResources = (resources ?? []).filter((r) => r.status === "ready" && r.resource_id);
  wire(entry, agentId, (handlers) =>
    streamCodeAgent({ message: msg, projectRoot, model, mode, conversationHistory: history, resources: readyResources, sessionId, profileName, permissionMode, reasoningEffort, ...handlers }));
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
  args: { sessionId: string; text: string; useOrchestrator: boolean; useReflection: boolean; projectRoot?: string; permissionMode?: PermissionMode; reasoningEffort?: ReasoningEffort },
): void {
  const { sessionId, text, useOrchestrator, useReflection, projectRoot, permissionMode, reasoningEffort } = args;
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
      permission_mode: permissionMode ?? "bypass",
      reasoning_effort: reasoningEffort ?? "none",
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
