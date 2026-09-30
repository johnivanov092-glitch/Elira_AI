import { ApiError, request } from "./client";

export const RELEASE_PHASES = [
  "idle", "preparing", "prepared", "checking", "verified", "awaiting_confirmation", "waiting", "switching",
  "completed", "rolling_back", "failed", "interrupted", "unavailable",
] as const;
export type ReleasePhase = typeof RELEASE_PHASES[number];
export type ReleaseConfirmation = {
  request_id: string;
  release_id: string;
  sha256: string;
  requested_at: number;
};
export type ReleaseStatus = {
  version: 1;
  mode: "legacy" | "foundation" | "development";
  phase: ReleasePhase;
  active_release_id: string | null;
  previous_release_id: string | null;
  rollback_available: boolean;
  saved_release_action?: "rollback" | "update";
  target_release_id: string | null;
  operation_id: string | null;
  updated_at: number | null;
  step: { index: number; total: number; label: string } | null;
  error: string | null;
  confirmation: ReleaseConfirmation | null;
};

export type ReleaseObservation = {
  status: ReleaseStatus | null;
  connection: "connecting" | "connected" | "reconnecting";
  receivedAt: number | null;
};
export const RELEASE_INITIAL_OBSERVATION: ReleaseObservation = {
  status: null, connection: "connecting", receivedAt: null,
};
export const RELEASE_REQUEST_TIMEOUT_MS = 6_000;
export const RELEASE_CONFIRM_TIMEOUT_MS = 135_000;
export const RELEASE_POLL_MS = 10_000;
export const RELEASE_BUSY_POLL_MS = 3_000;

export function releaseIsBusy(phase: ReleasePhase): boolean {
  return phase === "preparing" || phase === "checking" || phase === "switching" || phase === "rolling_back";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function isOneOf<T extends string>(value: unknown, choices: readonly T[]): value is T {
  return typeof value === "string" && choices.some((choice) => choice === value);
}
function nullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

/** A malformed response must never become a synthetic idle/success state. */
export function parseReleaseStatus(value: unknown): ReleaseStatus {
  if (!isRecord(value) || value.version !== 1
    || !isOneOf(value.mode, ["legacy", "foundation", "development"] as const)
    || !isOneOf(value.phase, RELEASE_PHASES)
    || !nullableString(value.active_release_id) || !nullableString(value.target_release_id)
    || !nullableString(value.operation_id) || !nullableString(value.error)
    || !(value.updated_at === null || (typeof value.updated_at === "number"
      && Number.isFinite(value.updated_at) && value.updated_at >= 0 && value.updated_at <= 8.64e12))) {
    throw new Error("invalid release status response");
  }
  const previous = value.previous_release_id ?? null;
  const rollbackAvailable = value.rollback_available ?? false;
  const savedAction = value.saved_release_action ?? "rollback";
  if (!nullableString(previous) || typeof rollbackAvailable !== "boolean"
    || !isOneOf(savedAction, ["rollback", "update"] as const)
    || (rollbackAvailable && (!previous || previous === value.active_release_id || !value.active_release_id))) {
    throw new Error("invalid release rollback selection");
  }
  let step: ReleaseStatus["step"] = null;
  if (value.step !== null) {
    if (!isRecord(value.step) || typeof value.step.index !== "number" || typeof value.step.total !== "number"
      || !Number.isInteger(value.step.index) || !Number.isInteger(value.step.total)
      || value.step.index < 1 || value.step.total < 1 || value.step.index > value.step.total
      || typeof value.step.label !== "string" || !value.step.label.trim()) {
      throw new Error("invalid release status step");
    }
    step = { index: value.step.index, total: value.step.total, label: value.step.label };
  }
  let confirmation: ReleaseConfirmation | null = null;
  if (value.confirmation !== null) {
    const proposal = value.confirmation;
    if (!isRecord(proposal) || typeof proposal.request_id !== "string" || !proposal.request_id.trim()
      || proposal.request_id.length > 128 || typeof proposal.release_id !== "string" || !proposal.release_id.trim()
      || typeof proposal.sha256 !== "string" || !/^[a-f0-9]{64}$/i.test(proposal.sha256)
      || typeof proposal.requested_at !== "number" || !Number.isFinite(proposal.requested_at)
      || proposal.requested_at <= 0 || proposal.requested_at > 8.64e12) {
      throw new Error("invalid release confirmation");
    }
    confirmation = { request_id: proposal.request_id, release_id: proposal.release_id,
      sha256: proposal.sha256, requested_at: proposal.requested_at };
  }
  if (value.phase === "awaiting_confirmation" && (!confirmation || confirmation.release_id !== value.target_release_id)) {
    throw new Error("invalid release confirmation target");
  }
  return {
    version: 1, mode: value.mode, phase: value.phase, active_release_id: value.active_release_id,
    previous_release_id: previous, rollback_available: rollbackAvailable,
    saved_release_action: savedAction,
    target_release_id: value.target_release_id, operation_id: value.operation_id,
    updated_at: value.updated_at, step, error: value.error, confirmation,
  };
}

/** Called only by the explicit confirmation action, never by status polling. */
export async function confirmRelease(requestId: string, signal: AbortSignal): Promise<void> {
  const result = await request<unknown>("/api/release/confirm", {
    method: "POST", body: { request_id: requestId }, signal, timeoutMs: RELEASE_CONFIRM_TIMEOUT_MS,
  });
  if (!isRecord(result) || result.ok !== true) throw new Error("invalid release confirmation response");
}

export type ReleaseApproval = {
  requestId: string;
  state: "sending" | "accepted" | "conflict" | "uncertain";
};

export type RollbackApproval = {
  active: string;
  previous: string;
  state: "sending" | "accepted" | "conflict" | "uncertain";
};

/** Submit once after the user confirms the displayed pair; never retry a mutation. */
export function createReleaseRollback(options: {
  current: () => ReleaseObservation;
  observe: (approval: RollbackApproval) => void;
  refresh: () => void;
}) {
  let stopped = false;
  let busy = false;
  let controller: AbortController | undefined;
  let deadline: ReturnType<typeof setTimeout> | undefined;
  return {
    async confirm(active: string, previous: string) {
      if (stopped || busy) return;
      const publish = (state: RollbackApproval["state"]) => {
        if (!stopped) options.observe({ active, previous, state });
      };
      const { connection, status } = options.current();
      if (connection !== "connected" || !status?.rollback_available
        || status.active_release_id !== active || status.previous_release_id !== previous) {
        publish("conflict");
        options.refresh();
        return;
      }
      busy = true;
      controller = new AbortController();
      publish("sending");
      try {
        const result = await Promise.race([
          request<unknown>("/api/release/rollback", {
            method: "POST", body: { active_release_id: active, previous_release_id: previous },
            signal: controller.signal, timeoutMs: RELEASE_CONFIRM_TIMEOUT_MS,
          }),
          new Promise<never>((_resolve, reject) => {
            deadline = setTimeout(() => { controller?.abort(); reject(new Error("rollback timeout")); }, RELEASE_CONFIRM_TIMEOUT_MS);
          }),
        ]);
        if (!isRecord(result) || result.ok !== true) throw new Error("invalid rollback response");
        publish("accepted");
      } catch (error) {
        publish(error instanceof ApiError && error.status === 409 ? "conflict" : "uncertain");
      } finally {
        clearTimeout(deadline);
        busy = false;
        if (!stopped) options.refresh();
      }
    },
    stop() { stopped = true; clearTimeout(deadline); controller?.abort(); },
  };
}

/** Guards a rendered button against a changed proposal and never retries a POST. */
export function createReleaseApproval(options: {
  current: () => ReleaseObservation;
  observe: (approval: ReleaseApproval) => void;
  refresh: () => void;
}) {
  let stopped = false;
  let busy = false;
  let controller: AbortController | undefined;
  let deadline: ReturnType<typeof setTimeout> | undefined;
  const publish = (requestId: string, state: ReleaseApproval["state"]) => {
    if (!stopped) options.observe({ requestId, state });
  };
  return {
    async confirm(requestId: string): Promise<void> {
      if (stopped || busy) return;
      const current = options.current();
      if (current.connection !== "connected" || current.status?.phase !== "awaiting_confirmation"
        || current.status.confirmation?.request_id !== requestId) {
        publish(requestId, "conflict");
        options.refresh();
        return;
      }
      busy = true;
      controller = new AbortController();
      const signal = controller.signal;
      publish(requestId, "sending");
      try {
        await Promise.race([
          confirmRelease(requestId, signal),
          new Promise<never>((_resolve, reject) => {
            deadline = setTimeout(() => {
              controller?.abort();
              reject(new Error("release confirmation timeout"));
            }, RELEASE_CONFIRM_TIMEOUT_MS);
          }),
        ]);
        publish(requestId, "accepted");
      } catch (error) {
        publish(requestId, error instanceof ApiError && error.status === 409 ? "conflict" : "uncertain");
      } finally {
        clearTimeout(deadline);
        busy = false;
        if (!stopped) options.refresh();
      }
    },
    stop() {
      stopped = true;
      clearTimeout(deadline);
      controller?.abort();
    },
  };
}

export async function getReleaseStatus(signal?: AbortSignal): Promise<ReleaseStatus> {
  return parseReleaseStatus(await request<unknown>("/api/release/status", {
    signal, cache: "no-store", timeoutMs: RELEASE_REQUEST_TIMEOUT_MS,
  }));
}

/** One request at a time; failures retain evidence and stop the progress animation. */
export function pollReleaseStatus(observe: (value: ReleaseObservation) => void) {
  let stopped = false;
  let inFlight = false;
  let refreshPending = false;
  let next: ReturnType<typeof setTimeout> | undefined;
  let deadline: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;
  let value = RELEASE_INITIAL_OBSERVATION;
  const disconnected = () => {
    if (!stopped) {
      value = { ...value, connection: "reconnecting" };
      observe(value);
    }
  };
  async function poll() {
    if (stopped || inFlight) return;
    inFlight = true;
    const current = new AbortController();
    controller = current;
    deadline = setTimeout(() => {
      current.abort();
      disconnected();
    }, RELEASE_REQUEST_TIMEOUT_MS);
    try {
      const status = await getReleaseStatus(current.signal);
      if (!stopped && !current.signal.aborted) {
        value = { status, connection: "connected", receivedAt: Date.now() };
        observe(value);
      }
    } catch {
      disconnected();
    } finally {
      clearTimeout(deadline);
      inFlight = false;
      if (!stopped) {
        const delay = value.connection === "connected" && value.status && releaseIsBusy(value.status.phase)
          ? RELEASE_BUSY_POLL_MS : RELEASE_POLL_MS;
        next = setTimeout(() => { void poll(); }, refreshPending ? 0 : delay);
        refreshPending = false;
      }
    }
  }
  void poll();
  return {
    refresh() {
      if (stopped) return;
      clearTimeout(next);
      if (inFlight) refreshPending = true;
      else void poll();
    },
    stop() {
      stopped = true;
      clearTimeout(next);
      clearTimeout(deadline);
      controller?.abort();
    },
  };
}
