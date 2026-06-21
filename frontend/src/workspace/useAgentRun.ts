import { useCallback, useRef, useState } from "react";
import {
  cancelCodeAgent,
  resolveApproval,
  streamCodeAgent,
  type CodeAgentMode,
  type CodeAgentStreamEvent,
  type ContextUsage,
  type ConversationMessage,
  type TaskLedgerEntry,
} from "../api/codeAgent";
import { uploadLibraryFile } from "../api/library";
import type { AgentTurnData, FileEntry, Turn } from "./types";

let _seq = 0;
const nid = () => `t${++_seq}`;

/** Drives a code-agent run against the existing SSE stream and accumulates events. */
export function useAgentRun(projectRoot: string, model: string) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [running, setRunning] = useState(false);
  const [autoApprove, setAutoApprove] = useState(false);
  const [contextUsage, setContextUsage] = useState<ContextUsage | null>(null);
  const [taskLedger, setTaskLedger] = useState<TaskLedgerEntry[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const autoApproveRef = useRef(false);
  const runIdRef = useRef<string | null>(null);

  const send = useCallback((text: string, mode: CodeAgentMode) => {
    const msg = text.trim();
    if (!msg || running) return;
    const history: ConversationMessage[] = [];
    for (const t of turns) {
      if (t.kind === "user") history.push({ role: "user", content: t.text });
      else if (t.kind === "agent" && t.text) history.push({ role: "assistant", content: t.text });
    }
    const agentId = nid();
    setTurns((p) => [...p, { kind: "user", id: nid(), text: msg }, { kind: "agent", id: agentId, toolCalls: [], text: "", running: true }]);
    setRunning(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    runIdRef.current = null;
    const patch = (fn: (a: AgentTurnData) => AgentTurnData) =>
      setTurns((p) => p.map((t) => (t.kind === "agent" && t.id === agentId ? fn(t) : t)));

    void streamCodeAgent({
      message: msg, projectRoot, model, mode, conversationHistory: history, signal: ctrl.signal,
      onRunId: (id) => { runIdRef.current = id; },
      onEvent: (e: CodeAgentStreamEvent) => {
        if (e.type === "run_started") runIdRef.current = e.run_id;
        if (e.type === "tool_started") patch((a) => ({ ...a, activeTool: e.tool, pendingApproval: undefined }));
        else if (e.type === "delta") patch((a) => ({ ...a, text: a.text + e.text }));
        else if (e.type === "tool_call") {
          patch((a) => ({ ...a, toolCalls: [...a.toolCalls, e], activeTool: undefined, pendingApproval: undefined }));
          const result = e.result.trim();
          const entry: TaskLedgerEntry = {
            timestamp: Date.now(),
            type: "tool_call",
            action: e.tool,
            result: /^error\b/i.test(result)
              ? result.slice(0, 500)
              : `completed (${result.length} chars)`,
          };
          setTaskLedger((items) => [...items, entry].slice(-200));
        }
        else if (e.type === "approval_pending") {
          if (autoApproveRef.current) {
            void resolveApproval(e.approval_id, "approve").catch(() => {});
            patch((a) => ({ ...a, pendingApproval: undefined }));
          } else patch((a) => ({ ...a, pendingApproval: { approvalId: e.approval_id, tool: e.tool, arguments: e.arguments } }));
        } else if (e.type === "approval_wait") patch((a) => (a.pendingApproval ? { ...a, pendingApproval: { ...a.pendingApproval, waitedS: e.waited_s } } : a));
        else if (e.type === "context_compacted") {
          const entry: TaskLedgerEntry = { timestamp: Date.now(), type: "compression", action: `step ${e.step}`, result: "completed" };
          setTaskLedger((items) => [...items, entry].slice(-200));
        }
        else if (e.type === "usage" && e.context) setContextUsage(e.context);
        else if (e.type === "final_response") patch((a) => ({ ...a, text: e.text }));
        else if (e.type === "done") {
          const entry: TaskLedgerEntry = { timestamp: Date.now(), type: e.ok ? "final" : "error", action: e.stop_reason, result: e.error || "completed" };
          setTaskLedger((items) => [...items, entry].slice(-200));
          patch((a) => ({ ...a, running: false, activeTool: undefined, pendingApproval: undefined, stopReason: e.stop_reason, error: e.error }));
          setRunning(false);
        }
      },
      onError: (err) => {
        patch((a) => ({ ...a, running: false, activeTool: undefined, error: err.message }));
        setRunning(false);
      },
    });
  }, [projectRoot, model, running, turns]);

  const stop = useCallback(() => {
    const rid = runIdRef.current;
    if (rid) void cancelCodeAgent(rid).catch(() => {});
    abortRef.current?.abort();
    setRunning(false);
    setTurns((p) => p.map((t) => (t.kind === "agent" && t.running ? { ...t, running: false } : t)));
  }, []);

  const reset = useCallback((next: Turn[], ledger: TaskLedgerEntry[] = [], usage: ContextUsage | null = null) => {
    abortRef.current?.abort();
    abortRef.current = null;
    autoApproveRef.current = false;
    setAutoApprove(false);
    setContextUsage(usage);
    setTaskLedger(ledger);
    setRunning(false);
    setTurns(next);
  }, []);

  const addFiles = useCallback((files: File[]) => {
    if (files.length === 0) return;
    const turnId = nid();
    const entries: FileEntry[] = files.map((f) => ({ name: f.name, isImage: f.type.startsWith("image/"), url: f.type.startsWith("image/") ? URL.createObjectURL(f) : undefined, status: "uploading" }));
    setTurns((p) => [...p, { kind: "files", id: turnId, files: entries }]);
    const setStatus = (idx: number, status: FileEntry["status"]) => setTurns((p) => p.map((t) => t.kind === "files" && t.id === turnId ? { ...t, files: t.files.map((f, i) => i === idx ? { ...f, status } : f) } : t));
    files.forEach((f, i) => uploadLibraryFile(f, { useInContext: true }).then(() => setStatus(i, "saved")).catch(() => setStatus(i, "error")));
  }, []);

  const approve = useCallback((approvalId: string, decision: "approve" | "reject") => {
    setTurns((p) => p.map((t) => t.kind === "agent" && t.pendingApproval?.approvalId === approvalId ? { ...t, pendingApproval: { ...t.pendingApproval, resolving: true } } : t));
    void resolveApproval(approvalId, decision).catch(() => {});
  }, []);

  const approveAll = useCallback(() => {
    autoApproveRef.current = true;
    setAutoApprove(true);
    setTurns((p) => p.map((t) => t.kind === "agent" && t.pendingApproval && !t.pendingApproval.resolving
      ? (void resolveApproval(t.pendingApproval.approvalId, "approve").catch(() => {}), { ...t, pendingApproval: { ...t.pendingApproval, resolving: true } }) : t));
  }, []);

  return { turns, running, send, stop, addFiles, reset, approve, approveAll, autoApprove, contextUsage, taskLedger };
}
