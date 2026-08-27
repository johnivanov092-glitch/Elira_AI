import { useCallback, useEffect, useSyncExternalStore } from "react";
import { fetchContextProfile, type CodeAgentMode, type PermissionMode, type ReasoningEffort } from "../api/codeAgent";
import type { ResourceAttachment } from "../api/resources";
import * as bg from "./backgroundRuns";
import type { Turn } from "./types";

/**
 * Thin React binding over the background-run manager.
 *
 * All run state (turns, running flag, ledger, context meter) lives in
 * `backgroundRuns`, keyed by session id — NOT in this hook. The hook just
 * subscribes the active session's snapshot into React and forwards mutations.
 * That is what makes a run survive a chat switch: when `sessionId` changes the
 * view re-subscribes to a different entry, while the old entry's SSE reader
 * keeps streaming in the background.
 *
 * Public API is unchanged from the old single-run hook, so WorkspaceShell's
 * usage stays the same — only `sessionId` is now passed in.
 */
export function useAgentRun(sessionId: string, projectRoot: string, model: string) {
  const snapshot = useSyncExternalStore(
    useCallback((cb) => bg.subscribe(sessionId, cb), [sessionId]),
    useCallback(() => bg.getSnapshot(sessionId), [sessionId]),
  );
  const { turns, running, taskLedger, contextUsage } = snapshot;

  // Seed the composer's context meter at 0% before the first turn, using the
  // live ctx_size from the backend. Re-seeds on model change while still unseeded
  // and not running. Applied only to the currently-displayed session.
  useEffect(() => {
    let cancelled = false;
    void fetchContextProfile(model).then((seed) => {
      if (cancelled || !seed) return;
      bg.applyContextSeed(sessionId, seed);
    });
    return () => { cancelled = true; };
  }, [model, sessionId]);

  const send = useCallback((text: string, mode: CodeAgentMode, resources?: ResourceAttachment[], permissionMode?: PermissionMode, reasoningEffort?: ReasoningEffort) => {
    bg.send({ sessionId, text, mode, projectRoot, model, resources, profileName: "Авто", permissionMode, reasoningEffort });
  }, [sessionId, projectRoot, model]);

  // Multi-agent run: streams per-step progress from the pipeline endpoint via
  // the background manager. Forwards the two run-mode flags. Independent of
  // `agent_profile`.
  const sendMultiAgent = useCallback((text: string, useOrchestrator: boolean, useReflection: boolean, permissionMode: PermissionMode, reasoningEffort: ReasoningEffort) => {
    bg.sendMultiAgent({ sessionId, text, useOrchestrator, useReflection, projectRoot, permissionMode, reasoningEffort });
  }, [sessionId, projectRoot]);

  const resume = useCallback((agentId: string, runId: string) => {
    bg.resume(sessionId, agentId, runId);
  }, [sessionId]);

  const stop = useCallback(() => {
    bg.stop(sessionId);
  }, [sessionId]);

  const reset = useCallback((next: Turn[], ledger: bg.RunSnapshot["taskLedger"] = [], contextState: bg.RunSnapshot["contextState"] = null) => {
    // Switch the displayed session's snapshot. This NEVER cancels a run: a
    // background run owning the snapshot is left untouched (seed() is a no-op
    // while running). Only the explicit Stop button cancels. A fresh chat (usage
    // null) refills the 0% meter from the backend via the seed effect above.
    bg.seed(sessionId, next, ledger, contextState);
    if (contextState == null) void fetchContextProfile(model).then((seed) => {
      if (seed) bg.applyContextSeed(sessionId, seed);
    });
  }, [sessionId, model]);

  return { turns, running, send, sendMultiAgent, resume, stop, reset, contextUsage, taskLedger };
}
