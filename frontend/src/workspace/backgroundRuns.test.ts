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
import { send, stop, getSnapshot, seed, setPersist } from "./backgroundRuns";
import { isAcceptedAnswer } from "./answerLifecycle";
import { uploadResource } from "../api/resources";

describe("attachment history", () => {
  it("keeps uploaded IDs on their originating turn through follow-up and reload", async () => {
    const sessionId = "attachment-history";
    const ids = ["a".repeat(32), "b".repeat(32)];
    const upload = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({
      resource_id: ids.shift(), name: "price.csv", kind: "document", content_type: "text/csv", size: 10,
    }), { status: 200 }));
    const first = await uploadResource(new File(["first"], "price.csv"), sessionId);
    const second = await uploadResource(new File(["second"], "price.csv"), sessionId);
    upload.mockRestore();
    const args = { sessionId, text: "сравни прайсы", mode: "code" as const, projectRoot: "", model: "auto" };
    send({ ...args, resources: [first, second].map(r => ({ ...r, status: "ready" })) });
    expect(fake.handlers.at(-1)!.resources).toEqual([first, second].map(r => ({ ...r, status: "ready" })));
    const persist = vi.fn();
    setPersist(sessionId, persist);
    stop(sessionId);
    // Session storage JSON is the same boundary used by WorkspaceShell.
    const saved = JSON.parse(JSON.stringify(persist.mock.calls[0][0].turns));
    seed(sessionId, []);
    seed(sessionId, saved);
    send({ ...args, text: "теперь собери КП" });
    const followup = fake.handlers.at(-1)!;
    expect(followup.resources).toEqual([]);
    expect(followup.conversationHistory).toContainEqual({
      role: "user", content: "сравни прайсы",
      resources: [{ resource_id: first.resource_id }, { resource_id: second.resource_id }],
    });
    expect(JSON.stringify(saved)).not.toContain('"first"');
    stop(sessionId);

    send({ ...args, sessionId: "other-attachment-session", text: "продолжай" });
    expect(fake.handlers.at(-1)!.conversationHistory).toEqual([]);
    expect(fake.handlers.at(-1)!.resources).toEqual([]);
    stop("other-attachment-session");
  });

  it("does not carry uploading or failed files as attached resources", () => {
    const sessionId = "failed-attachments";
    const resource = { resource_id: "c".repeat(32), name: "voice.ogg", kind: "audio" as const, content_type: "audio/ogg", size: 10 };
    const args = { sessionId, text: "расшифруй", mode: "code" as const, projectRoot: "", model: "auto" };
    send({ ...args, resources: [{ ...resource, status: "error" }, { ...resource, status: "uploading" }] });
    stop(sessionId);
    send({ ...args, text: "продолжай" });
    expect(fake.handlers.at(-1)!.conversationHistory).toEqual([{ role: "user", content: "расшифруй" }]);
    stop(sessionId);
  });
});

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
  it("persists a late accepted Workflow answer after Stop before the next message", () => {
    const args = { sessionId: "late-workflow-input", text: "собери КП", mode: "code" as const, projectRoot: "", model: "auto" };
    send(args);
    const first = fake.handlers.at(-1)!;
    const input = { request_id: "late-request-1", question: "Количество?", answer: "1 ИБП, 40 АКБ" };
    const persist = vi.fn();
    setPersist(args.sessionId, persist);
    stop(args.sessionId);
    const event = {
      type: "tool_call" as const, step: 1, tool: "ask_user", arguments: {},
      result: "RAW LATE OUTPUT", ok: true, workflow_input: input,
    };
    first.onEvent?.(event);
    first.onEvent?.(event);
    expect(persist).toHaveBeenCalledTimes(2); // Stop + the one accepted receipt
    expect(getSnapshot(args.sessionId).running).toBe(false);
    const saved = JSON.parse(JSON.stringify(persist.mock.calls.at(-1)![0].turns));
    expect(JSON.stringify(saved)).not.toContain("RAW LATE OUTPUT");
    seed(args.sessionId, saved);
    send({ ...args, text: "продолжай" });
    const history = fake.handlers.at(-1)!.conversationHistory!;
    expect(history.filter(m => m.role === "user" && m.content.includes(input.answer))).toHaveLength(1);
    stop(args.sessionId);
  });

  it("confines late Workflow input to its stopped turn while another run is active", () => {
    const args = { sessionId: "late-workflow-new-run", text: "first", mode: "code" as const, projectRoot: "", model: "auto" };
    send(args);
    const old = fake.handlers.at(-1)!;
    const stoppedId = getSnapshot(args.sessionId).turns.at(-1)!.id;
    stop(args.sessionId);
    send({ ...args, text: "second" });
    const current = fake.handlers.at(-1)!;
    current.onEvent?.({ type: "tool_started", step: 1, tool: "read_file", arguments: {} });
    const before = getSnapshot(args.sessionId);
    const input = { request_id: "late-request-2", question: "Quantity?", answer: "40" };
    const event = {
      type: "tool_call" as const, step: 1, tool: "ask_user", arguments: {},
      result: "RAW LATE OUTPUT", ok: true, workflow_input: input,
    };
    old.onEvent?.({ ...event, tool: "workflow_request" });
    old.onEvent?.({ ...event, ok: false });
    old.onEvent?.({ ...event, workflow_input: undefined });
    expect(getSnapshot(args.sessionId)).toBe(before);
    old.onEvent?.(event);
    old.onEvent?.({ type: "done", ok: false, steps: 1, stop_reason: "cancelled", error: null });
    const after = getSnapshot(args.sessionId);
    expect(after.running).toBe(true);
    expect(after.turns.at(-1)).toBe(before.turns.at(-1));
    expect(after.taskLedger).toBe(before.taskLedger);
    const stopped = after.turns.find(t => t.id === stoppedId)!;
    if (stopped.kind !== "agent") throw new Error("expected stopped agent");
    expect(stopped.toolCalls).toHaveLength(1);
    expect(stopped.toolCalls[0].workflow_input).toEqual(input);
    expect(stopped.running).toBe(false);
    stop(args.sessionId);
  });

  it("preserves accepted Workflow input after cancellation and reload without retaining draft text", () => {
    const args = { sessionId: "workflow-input-history", text: "собери КП", mode: "code" as const, projectRoot: "", model: "auto" };
    send(args);
    const first = fake.handlers.at(-1)!;
    const input = { request_id: "request-1", question: "Сколько ИБП и АКБ?", answer: "1 ИБП, 40 АКБ" };
    const event = {
      type: "tool_call" as const, step: 1, tool: "ask_user", arguments: { question: input.question },
      result: "unstructured result must not be reused", ok: true, workflow_input: input,
    };
    first.onEvent?.(event);
    first.onEvent?.(event); // replay must not duplicate user input in the next prompt
    first.onEvent?.({ ...event, workflow_input: undefined, result: "LEGACY RAW ANSWER" });
    first.onEvent?.({ type: "delta", step: 1, text: "UNACCEPTED DRAFT", answer_state: "draft" });
    stop(args.sessionId);
    seed(args.sessionId, JSON.parse(JSON.stringify(getSnapshot(args.sessionId).turns)));
    send({ ...args, text: "продолжай" });
    const history = fake.handlers.at(-1)!.conversationHistory!;
    const answers = history.filter(m => m.content.includes(input.answer));
    expect(answers).toHaveLength(1);
    expect(answers[0].role).toBe("user");
    expect(answers[0].content).toContain(JSON.stringify(input));
    expect(JSON.stringify(history)).not.toContain("UNACCEPTED DRAFT");
    expect(JSON.stringify(history)).not.toContain("LEGACY RAW ANSWER");
    stop(args.sessionId);
  });

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
