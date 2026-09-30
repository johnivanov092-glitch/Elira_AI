import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import {
  createReleaseApproval, createReleaseRollback, getReleaseStatus, parseReleaseStatus, pollReleaseStatus, RELEASE_BUSY_POLL_MS,
  RELEASE_CONFIRM_TIMEOUT_MS, RELEASE_POLL_MS, RELEASE_REQUEST_TIMEOUT_MS, type ReleaseObservation, type ReleaseStatus,
  type ReleaseApproval,
} from "./releases";

const { requestMock } = vi.hoisted(() => ({ requestMock: vi.fn() }));
vi.mock("./client", async (importOriginal) => ({
  ...await importOriginal<typeof import("./client")>(), request: requestMock,
}));

const status: ReleaseStatus = {
  version: 1, mode: "legacy", phase: "checking", active_release_id: "release-a",
  target_release_id: "release-b", operation_id: "check-b", updated_at: 1_790_770_000,
  previous_release_id: null, rollback_available: false,
  saved_release_action: "rollback",
  step: { index: 2, total: 4, label: "Проверка backend" }, error: null, confirmation: null,
};
const proposal = { request_id: "proposal-b", release_id: "release-b", sha256: "a".repeat(64), requested_at: 1_790_770_000 };
const awaiting: ReleaseStatus = { ...status, phase: "awaiting_confirmation", confirmation: proposal };

afterEach(() => {
  vi.useRealTimers();
  requestMock.mockReset();
});

describe("manual rollback", () => {
  it("submits only the confirmed current pair once and rejects stale or disconnected views", async () => {
    let view: ReleaseObservation = { connection: "connected", receivedAt: 1,
      status: { ...status, phase: "completed", previous_release_id: "previous", rollback_available: true } };
    const observe = vi.fn();
    const action = createReleaseRollback({ current: () => view, observe, refresh: vi.fn() });
    expect(requestMock).not.toHaveBeenCalled();
    let resolve: (value: unknown) => void = () => {};
    requestMock.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    const pending = action.confirm("release-a", "previous");
    await action.confirm("release-a", "previous");
    expect(requestMock).toHaveBeenCalledTimes(1);
    expect(requestMock.mock.calls[0][1].body).toEqual({ active_release_id: "release-a", previous_release_id: "previous" });
    resolve({ ok: true });
    await pending;
    expect(observe).toHaveBeenLastCalledWith({ active: "release-a", previous: "previous", state: "accepted" });
    view = { ...view, connection: "reconnecting" };
    await action.confirm("release-a", "previous");
    expect(observe).toHaveBeenLastCalledWith({ active: "release-a", previous: "previous", state: "conflict" });
    view = { ...view, connection: "connected", status: { ...view.status!, active_release_id: "changed" } };
    await action.confirm("release-a", "previous");
    expect(requestMock).toHaveBeenCalledTimes(1);
    action.stop();
  });
});

describe("release status contract", () => {
  it("accepts the saved direction and defaults only for older owners", () => {
    expect(parseReleaseStatus({ ...status, saved_release_action: "update" }).saved_release_action).toBe("update");
    expect(parseReleaseStatus({ ...status, saved_release_action: undefined }).saved_release_action).toBe("rollback");
    expect(() => parseReleaseStatus({ ...status, saved_release_action: "sideways" })).toThrow(/invalid release rollback/);
  });
  it("uses the authenticated read-only client without caching", async () => {
    requestMock.mockResolvedValue(status);
    const signal = new AbortController().signal;
    expect(await getReleaseStatus(signal)).toEqual(status);
    expect(requestMock).toHaveBeenCalledWith("/api/release/status", {
      signal, cache: "no-store", timeoutMs: RELEASE_REQUEST_TIMEOUT_MS,
    });
  });

  it.each([
    null, {}, { ...status, version: 2 }, { ...status, phase: "success" },
    { ...status, updated_at: Number.NaN }, { ...status, updated_at: Infinity },
    { ...status, mode: "remote" }, { ...status, target_release_id: 123 },
    { ...status, step: undefined }, { ...status, step: { index: 5, total: 4, label: "Check" } },
    { ...status, step: { index: 0, total: 4, label: "Check" } },
    { ...status, step: { index: 1.5, total: 4, label: "Check" } },
    { ...status, step: { index: 1, total: 0, label: "Check" } },
    { ...awaiting, confirmation: null }, { ...awaiting, confirmation: { ...proposal, sha256: "bad" } },
    { ...awaiting, confirmation: { ...proposal, release_id: "different" } },
    { ...awaiting, confirmation: { ...proposal, request_id: "" } },
    { ...awaiting, confirmation: { ...proposal, requested_at: Number.NaN } },
  ])("rejects malformed data without manufacturing idle", (invalid) => {
    expect(() => parseReleaseStatus(invalid)).toThrow(/invalid release (status|confirmation)/);
  });

  it("keeps explicit unavailability and does not infer failure from an old event", () => {
    expect(parseReleaseStatus({ ...status, updated_at: 1 }).phase).toBe("checking");
    expect(parseReleaseStatus({ ...status, phase: "unavailable", step: null, error: "Нет состояния" }))
      .toMatchObject({ phase: "unavailable", error: "Нет состояния", step: null });
  });
});

describe("sequential release polling", () => {
  it("never overlaps requests and preserves last evidence through failure and reconnect", async () => {
    vi.useFakeTimers();
    let resolveFirst: (value: ReleaseStatus) => void = () => {};
    requestMock.mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue({ ...status, phase: "completed", active_release_id: "release-b", step: null });
    const observations: ReleaseObservation[] = [];
    const { stop } = pollReleaseStatus((value) => observations.push(value));
    await vi.advanceTimersByTimeAsync(RELEASE_BUSY_POLL_MS + 1);
    expect(requestMock).toHaveBeenCalledTimes(1);
    resolveFirst(status);
    await vi.advanceTimersByTimeAsync(0);
    expect(observations.at(-1)?.connection).toBe("connected");
    await vi.advanceTimersByTimeAsync(RELEASE_BUSY_POLL_MS);
    expect(requestMock).toHaveBeenCalledTimes(2);
    expect(observations.at(-1)).toMatchObject({ connection: "reconnecting", status });
    await vi.advanceTimersByTimeAsync(RELEASE_POLL_MS);
    expect(requestMock).toHaveBeenCalledTimes(3);
    expect(observations.at(-1)).toMatchObject({ connection: "connected", status: { phase: "completed" } });
    stop();
    await vi.advanceTimersByTimeAsync(RELEASE_POLL_MS * 2);
    expect(requestMock).toHaveBeenCalledTimes(3);
  });

  it("aborts a hung request, reports unknown rather than idle, and cancels on unmount", async () => {
    vi.useFakeTimers();
    const signals: AbortSignal[] = [];
    requestMock.mockImplementation((_path, options: { signal: AbortSignal }) => new Promise((_resolve, reject) => {
      signals.push(options.signal);
      options.signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
    }));
    const observations: ReleaseObservation[] = [];
    const { stop } = pollReleaseStatus((value) => observations.push(value));
    await vi.advanceTimersByTimeAsync(RELEASE_REQUEST_TIMEOUT_MS);
    expect(signals[0].aborted).toBe(true);
    expect(observations.at(-1)).toMatchObject({ status: null, connection: "reconnecting" });
    await vi.advanceTimersByTimeAsync(RELEASE_POLL_MS);
    expect(signals).toHaveLength(2);
    const count = observations.length;
    stop();
    expect(signals[1].aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(RELEASE_REQUEST_TIMEOUT_MS + RELEASE_POLL_MS);
    expect(observations).toHaveLength(count);
    expect(requestMock).toHaveBeenCalledTimes(2);
  });

  it("ignores a late successful response after cancellation", async () => {
    vi.useFakeTimers();
    let resolveRequest: (value: ReleaseStatus) => void = () => {};
    requestMock.mockImplementation(() => new Promise((resolve) => { resolveRequest = resolve; }));
    const observe = vi.fn();
    const { stop } = pollReleaseStatus(observe);
    stop();
    resolveRequest({ ...status, phase: "completed" });
    await vi.advanceTimersByTimeAsync(RELEASE_POLL_MS);
    expect(observe).not.toHaveBeenCalled();
    expect(requestMock).toHaveBeenCalledTimes(1);
  });

  it("coalesces confirmation refresh during a pending GET without automatic approval", async () => {
    vi.useFakeTimers();
    let resolveRequest: (value: ReleaseStatus) => void = () => {};
    requestMock.mockImplementationOnce(() => new Promise((resolve) => { resolveRequest = resolve; }))
      .mockResolvedValue(awaiting);
    const polling = pollReleaseStatus(vi.fn());
    polling.refresh();
    polling.refresh();
    expect(requestMock).toHaveBeenCalledTimes(1);
    resolveRequest(awaiting);
    await vi.advanceTimersByTimeAsync(0);
    expect(requestMock).toHaveBeenCalledTimes(2);
    expect(requestMock.mock.calls.every(([path]) => path === "/api/release/status")).toBe(true);
    polling.stop();
  });
});

describe("explicit installation confirmation", () => {
  function controller(initial = awaiting) {
    let current: ReleaseObservation = { status: initial, connection: "connected", receivedAt: Date.now() };
    const observations: ReleaseApproval[] = [];
    const refresh = vi.fn();
    const action = createReleaseApproval({
      current: () => current, observe: (value) => observations.push(value), refresh,
    });
    return { action, observations, refresh, setCurrent: (value: ReleaseObservation) => { current = value; } };
  }

  it("does nothing until explicit confirmation, then posts only the selected request id", async () => {
    requestMock.mockResolvedValue({ ok: true });
    const { action, observations, refresh } = controller();
    expect(requestMock).not.toHaveBeenCalled();
    await action.confirm(proposal.request_id);
    expect(requestMock).toHaveBeenCalledWith("/api/release/confirm", {
      method: "POST", body: { request_id: proposal.request_id }, signal: expect.any(AbortSignal),
      timeoutMs: RELEASE_CONFIRM_TIMEOUT_MS,
    });
    expect(observations.map((value) => value.state)).toEqual(["sending", "accepted"]);
    expect(refresh).toHaveBeenCalledTimes(1);
    action.stop();
  });

  it("rejects an old rendered button after the proposal changes without confirming the new id", async () => {
    const { action, observations, refresh, setCurrent } = controller();
    setCurrent({ connection: "connected", receivedAt: Date.now(), status: {
      ...awaiting, confirmation: { ...proposal, request_id: "new-request" },
    } });
    await action.confirm(proposal.request_id);
    expect(requestMock).not.toHaveBeenCalled();
    expect(observations.at(-1)?.state).toBe("conflict");
    expect(refresh).toHaveBeenCalledTimes(1);
    action.stop();
  });

  it("does not submit the last known proposal while disconnected", async () => {
    const { action, setCurrent } = controller();
    setCurrent({ connection: "reconnecting", receivedAt: Date.now(), status: awaiting });
    await action.confirm(proposal.request_id);
    expect(requestMock).not.toHaveBeenCalled();
    action.stop();
  });

  it("refreshes after owner rejects stale bytes, without retrying the POST", async () => {
    requestMock.mockRejectedValue(new ApiError("proposal changed", 409));
    const { action, observations, refresh } = controller();
    await action.confirm(proposal.request_id);
    expect(observations.at(-1)?.state).toBe("conflict");
    expect(requestMock).toHaveBeenCalledTimes(1);
    expect(refresh).toHaveBeenCalledTimes(1);
    action.stop();
  });

  it("bounds a hung confirmation and treats timeout as uncertain; no retry or false failure", async () => {
    vi.useFakeTimers();
    requestMock.mockImplementation(() => new Promise(() => {}));
    const { action, observations, refresh } = controller();
    const first = action.confirm(proposal.request_id);
    await action.confirm(proposal.request_id); // Double click while the first POST is pending.
    await vi.advanceTimersByTimeAsync(130_000);
    expect(observations.at(-1)?.state).toBe("sending");
    expect(requestMock.mock.calls[0][1].signal.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(RELEASE_CONFIRM_TIMEOUT_MS - 130_000);
    await first;
    expect(observations.map((value) => value.state)).toEqual(["sending", "uncertain"]);
    expect(requestMock.mock.calls[0][1].signal.aborted).toBe(true);
    expect(refresh).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(RELEASE_POLL_MS * 2);
    expect(requestMock).toHaveBeenCalledTimes(1);
    action.stop();
  });

  it("ignores a confirmation response after unmount", async () => {
    let resolveRequest: (value: unknown) => void = () => {};
    requestMock.mockImplementation(() => new Promise((resolve) => { resolveRequest = resolve; }));
    const { action, observations, refresh } = controller();
    const pending = action.confirm(proposal.request_id);
    action.stop();
    resolveRequest({ ok: true });
    await pending;
    expect(observations.map((value) => value.state)).toEqual(["sending"]);
    expect(refresh).not.toHaveBeenCalled();
    expect(requestMock.mock.calls[0][1].signal.aborted).toBe(true);
  });
});
