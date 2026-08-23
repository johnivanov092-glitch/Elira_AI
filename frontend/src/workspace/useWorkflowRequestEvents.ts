import { useCallback, useEffect, useState } from "react";
import {
  streamWorkflowEvents,
  type WorkflowControlEvent,
  type WorkflowEventStreamOptions,
} from "../api/workflows";

const REQUEST_REFRESH_EVENTS = new Set([
  "item/request",
  "serverRequest/resolved",
  "workflow/waiting",
  "workflow/completed",
  "workflow/cancelled",
  "workflow.run.completed",
  "workflow.run.cancelled",
]);
const STREAM_RECONNECT_DELAYS_MS = [1_000, 2_000, 4_000, 8_000] as const;
const STREAM_ATTEMPT_LIMIT = STREAM_RECONNECT_DELAYS_MS.length + 1;
const FALLBACK_REFRESH_MS = 15_000;

type WorkflowEventConnectionResult = "degraded" | "stopped";
type WorkflowEventStream = (options: WorkflowEventStreamOptions) => Promise<void>;

type WorkflowEventConnectionOptions = {
  signal: AbortSignal;
  refresh: () => Promise<void>;
  stream?: WorkflowEventStream;
  waitForRetry?: (delayMs: number, signal: AbortSignal) => Promise<void>;
};

function eventRefreshesRequests(event: WorkflowControlEvent): boolean {
  return REQUEST_REFRESH_EVENTS.has(event.event_type);
}

function waitForAbortableDelay(delayMs: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const finish = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", finish);
      resolve();
    };
    const timer = setTimeout(finish, delayMs);
    signal.addEventListener("abort", finish, { once: true });
  });
}

export async function connectWorkflowRequestEvents({
  signal,
  refresh,
  stream = streamWorkflowEvents,
  waitForRetry = waitForAbortableDelay,
}: WorkflowEventConnectionOptions): Promise<WorkflowEventConnectionResult> {
  let cursor: number | undefined;
  let readySnapshotStarted = false;
  let refreshRunning = false;
  let refreshQueued = false;

  const queueRefresh = () => {
    refreshQueued = true;
    if (refreshRunning) return;
    refreshRunning = true;
    void (async () => {
      try {
        while (refreshQueued && !signal.aborted) {
          refreshQueued = false;
          try {
            await refresh();
          } catch {
            // The caller owns the visible REST error; later events may retry it.
          }
        }
      } finally {
        refreshRunning = false;
      }
    })();
  };

  queueRefresh();

  for (let attempt = 0; attempt < STREAM_ATTEMPT_LIMIT; attempt += 1) {
    try {
      await stream({
        afterId: cursor,
        signal,
        onCursor: (nextCursor) => {
          cursor = nextCursor;
          if (!readySnapshotStarted) {
            readySnapshotStarted = true;
            queueRefresh();
          }
        },
        onEvent: (event) => {
          if (eventRefreshesRequests(event)) queueRefresh();
        },
      });
    } catch {
      if (signal.aborted) return "stopped";
    }
    if (signal.aborted) return "stopped";
    const retryDelay = STREAM_RECONNECT_DELAYS_MS[attempt];
    if (retryDelay !== undefined) await waitForRetry(retryDelay, signal);
    if (signal.aborted) return "stopped";
  }
  return "degraded";
}

export function useWorkflowRequestEvents(
  connected: boolean,
  refresh: () => Promise<void>,
): { streamError: string; retryStream: () => void } {
  const [streamError, setStreamError] = useState("");
  const [retryGeneration, setRetryGeneration] = useState(0);
  const retryStream = useCallback(() => {
    setStreamError("");
    setRetryGeneration((current) => current + 1);
  }, []);

  useEffect(() => {
    if (!connected) {
      setStreamError("");
      return;
    }
    let stopped = false;
    const controller = new AbortController();
    let fallbackTimer: number | undefined;

    const connect = async () => {
      const result = await connectWorkflowRequestEvents({
        signal: controller.signal,
        refresh,
      });
      if (!stopped && result === "degraded") {
        setStreamError(
          "Live-соединение workflow недоступно; работает резервное обновление.",
        );
        void refresh();
        fallbackTimer = window.setInterval(() => void refresh(), FALLBACK_REFRESH_MS);
      }
    };
    void connect();

    return () => {
      stopped = true;
      controller.abort();
      if (fallbackTimer !== undefined) window.clearInterval(fallbackTimer);
    };
  }, [connected, refresh, retryGeneration]);

  return { streamError, retryStream };
}
