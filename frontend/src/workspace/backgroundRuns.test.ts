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
import { send, stop, getSnapshot, setPersist } from "./backgroundRuns";
import { isAcceptedAnswer } from "./answerLifecycle";

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

describe("answer lifecycle", () => {
  it("does not retract an accepted answer on a later transport error", () => {
    const args = { sessionId: "post-accept-error", text: "question", mode: "code" as const, projectRoot: "", model: "auto" };
    send(args);
    const first = fake.handlers.at(-1)!;
    first.onEvent?.({ type: "final_response", step: 1, text: "Accepted", answer_state: "accepted" });
    first.onError?.(new Error("EOF after final"));
    const turn = getSnapshot(args.sessionId).turns.at(-1)!;
    if (turn.kind !== "agent") throw new Error("expected agent turn");
    expect(isAcceptedAnswer(turn)).toBe(true);
    expect(turn.error).toBeFalsy();
  });
  it.each(["stop", "error"])("keeps a %s draft out of next history and speech", (ending) => {
    const args = { sessionId: `draft-${ending}`, text: "question", mode: "code" as const, projectRoot: "", model: "auto" };
    send(args);
    const first = fake.handlers.at(-1)!;
    first.onEvent?.({ type: "delta", step: 1, text: "UNACCEPTED DRAFT", answer_state: "draft" });
    const persist = vi.fn();
    setPersist(args.sessionId, persist);
    if (ending === "stop") stop(args.sessionId);
    else first.onError?.(new Error("transport interrupted"));
    const turn = getSnapshot(args.sessionId).turns.at(-1)!;
    expect(turn).toMatchObject({ text: "UNACCEPTED DRAFT", answerState: "interrupted" });
    expect(persist).toHaveBeenCalledWith(expect.objectContaining({ running: false }));
    if (turn.kind !== "agent") throw new Error("expected agent turn");
    expect(isAcceptedAnswer(turn)).toBe(false);
    send({ ...args, text: "continue" });
    expect(fake.handlers.at(-1)!.conversationHistory?.some(m => m.content.includes("UNACCEPTED"))).toBe(false);
    stop(args.sessionId);
  });

  it("stores exactly the accepted replacement and carries its journal reference", () => {
    const args = { sessionId: "accepted-replacement", text: "question", mode: "code" as const, projectRoot: "", model: "auto" };
    send(args);
    const first = fake.handlers.at(-1)!;
    first.onRunId?.("source-run");
    first.onEvent?.({ type: "delta", step: 1, text: "provisional", answer_state: "draft" });
    const text = "Accepted.\n\nAccepted. [[source:w_1]]";
    first.onEvent?.({ type: "final_response", step: 1, text, citations: [{ source_id: "w_1", status: "unresolved", claim_support: "not_assessed" }] });
    first.onEvent?.({ type: "done", ok: true, run_id: "source-run", steps: 1, stop_reason: "answer", error: null });
    const turn = getSnapshot(args.sessionId).turns.at(-1)!;
    if (turn.kind !== "agent") throw new Error("expected agent turn");
    expect(isAcceptedAnswer(turn)).toBe(true);
    expect(turn.text).toBe(text);
    send({ ...args, text: "follow up" });
    expect(fake.handlers.at(-1)!.conversationHistory).toContainEqual({ role: "assistant", content: text });
    expect(fake.handlers.at(-1)!.sourceRunIds).toEqual(["source-run"]);
    stop(args.sessionId);
  });
});
