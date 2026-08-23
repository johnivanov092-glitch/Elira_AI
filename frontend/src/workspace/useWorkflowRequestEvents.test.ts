import { afterEach, describe, expect, it, vi } from "vitest";
import type { WorkflowEventStreamOptions } from "../api/workflows";
import { connectWorkflowRequestEvents } from "./useWorkflowRequestEvents";


describe("workflow request event connection", () => {
  afterEach(() => vi.useRealTimers());

  it("loads immediately, reconciles after the durable cursor, and caps reconnects", async () => {
    const order: string[] = [];
    let attempt = 0;
    const stream = vi.fn(async (options: WorkflowEventStreamOptions) => {
      order.push(`connect-${attempt}`);
      if (attempt === 0) options.onCursor?.(7);
      attempt += 1;
      throw new Error("stream disconnected");
    });
    let activeRefreshes = 0;
    let maxActiveRefreshes = 0;
    const refresh = vi.fn(async () => {
      activeRefreshes += 1;
      maxActiveRefreshes = Math.max(maxActiveRefreshes, activeRefreshes);
      order.push("snapshot");
      await Promise.resolve();
      activeRefreshes -= 1;
    });
    const waits: number[] = [];

    const result = await connectWorkflowRequestEvents({
      signal: new AbortController().signal,
      refresh,
      stream,
      waitForRetry: async (delayMs) => {
        waits.push(delayMs);
      },
    });

    expect(result).toBe("degraded");
    expect(stream).toHaveBeenCalledTimes(5);
    expect(waits).toEqual([1_000, 2_000, 4_000, 8_000]);
    expect(order.slice(0, 4)).toEqual(["snapshot", "connect-0", "snapshot", "connect-1"]);
    expect(refresh).toHaveBeenCalledTimes(2);
    expect(maxActiveRefreshes).toBe(1);
  });

  it("cancels while waiting for a reconnect without entering degraded mode", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const stream = vi.fn(async () => {
      throw new Error("stream disconnected");
    });

    const connection = connectWorkflowRequestEvents({
      signal: controller.signal,
      refresh: async () => {},
      stream,
    });
    await Promise.resolve();
    expect(stream).toHaveBeenCalledTimes(1);
    controller.abort();
    const result = await connection;

    expect(result).toBe("stopped");
    expect(stream).toHaveBeenCalledTimes(1);
  });
});
