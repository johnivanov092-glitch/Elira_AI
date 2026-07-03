import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import { fetchContextProfile, type CodeAgentMode, type PermissionMode } from "../api/codeAgent";
import type { ChatAttachment } from "../api/chat";
import { uploadLibraryFile } from "../api/library";
import { getActiveProfile } from "../api/profiles";
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
  const { turns, running, taskLedger, contextUsage, autoApprove } = snapshot;

  // Active UI persona profile (the `agent_profile` global setting), held in a
  // ref so `send` can read it synchronously. Refreshed on mount and whenever the
  // window regains focus, so a switch made in the Composer's ProfilePicker is
  // picked up before the next send. Empty string → backend persona default.
  const profileRef = useRef<string>("");
  useEffect(() => {
    let cancelled = false;
    const refresh = () => {
      void getActiveProfile().then(({ active }) => {
        if (!cancelled) profileRef.current = active;
      }).catch(() => {});
    };
    refresh();
    window.addEventListener("focus", refresh);
    return () => { cancelled = true; window.removeEventListener("focus", refresh); };
  }, []);

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

  const send = useCallback((text: string, mode: CodeAgentMode, attachments?: ChatAttachment[], permissionMode?: PermissionMode, thinking?: boolean) => {
    const profileName = profileRef.current || undefined;
    bg.send({ sessionId, text, mode, projectRoot, model, attachments, profileName, permissionMode, thinking });
  }, [sessionId, projectRoot, model]);

  // Multi-agent run: streams per-step progress from the pipeline endpoint via
  // the background manager. Forwards the two run-mode flags. Independent of
  // `agent_profile`.
  const sendMultiAgent = useCallback((text: string, useOrchestrator: boolean, useReflection: boolean) => {
    bg.sendMultiAgent({ sessionId, text, useOrchestrator, useReflection, projectRoot });
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

  const addFiles = useCallback((files: File[]) => {
    if (files.length === 0) return;
    const names = files.map((f) => ({
      name: f.name,
      isImage: f.type.startsWith("image/"),
      url: f.type.startsWith("image/") ? URL.createObjectURL(f) : undefined,
    }));
    const { setStatus } = bg.appendFilesTurn(sessionId, names);
    files.forEach((f, i) => uploadLibraryFile(f, { useInContext: true }).then(() => setStatus(i, "saved")).catch(() => setStatus(i, "error")));
  }, [sessionId]);

  const approve = useCallback((approvalId: string, decision: "approve" | "reject") => {
    bg.approve(sessionId, approvalId, decision);
  }, [sessionId]);

  const approveAll = useCallback(() => {
    bg.approveAll(sessionId);
  }, [sessionId]);

  const answer = useCallback((questionId: string, text: string) => {
    bg.answer(sessionId, questionId, text);
  }, [sessionId]);

  return { turns, running, send, sendMultiAgent, resume, stop, addFiles, reset, approve, approveAll, answer, autoApprove, contextUsage, taskLedger };
}
