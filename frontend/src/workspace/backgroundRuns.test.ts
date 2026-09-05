import { describe, expect, it, vi } from "vitest";
import type { StreamCodeAgentArgs } from "../api/codeAgent";

const fake = vi.hoisted(() => ({ handlers: [] as StreamCodeAgentArgs[], cancels: [] as string[] }));
vi.mock("../api/codeAgent", () => ({
  streamCodeAgent: (args: StreamCodeAgentArgs) => {
    fake.handlers.push(args);
    return new Promise<void>(() => {});
  },
  resumeCodeAgent: () => new Promise<void>(() => {}),
  cancelCodeAgent: (id: string) => {
    fake.cancels.push(id);
    return new Promise<void>(() => {});
  },
}));
import { send, stop, getSnapshot } from "./backgroundRuns";

describe("SSE run ownership", () => {
  it("ignores cancelled callbacks while a new run owns the session", () => {
    const sessionId = "stale-events";
    const args = { sessionId, text: "first", mode: "code" as const, projectRoot: "", model: "auto" };
    send(args);
    const old = fake.handlers.at(-1)!;
    old.onRunId?.("old-run");
    stop(sessionId);
    send({ ...args, text: "second" });
    const current = fake.handlers.at(-1)!;
    current.onRunId?.("new-run");
    old.onEvent?.({ type: "done", ok: false, run_id: "old-run", steps: 1, stop_reason: "cancelled", error: null });
    old.onRunId?.("stale-run");
    old.onError?.(new Error("old transport failed"));
    expect(getSnapshot(sessionId).running).toBe(true);
    expect(getSnapshot(sessionId).turns.at(-1)).toMatchObject({ kind: "agent", running: true, runId: "new-run" });
    stop(sessionId);
    expect(fake.cancels).toEqual(["old-run", "new-run"]);
  });
});
