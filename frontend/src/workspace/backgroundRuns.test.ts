import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { StreamCodeAgentArgs, StreamHandlers } from "../api/codeAgent";

const fake = vi.hoisted(() => ({ handlers: [] as StreamCodeAgentArgs[], cancels: [] as string[], input: vi.fn(), cancel: vi.fn(),
  resumed: [] as (StreamHandlers & { signal?: AbortSignal })[] }));
vi.mock("../api/codeAgent", () => ({
  sendCodeAgentInput: fake.input,
  streamCodeAgent: (args: StreamCodeAgentArgs) => {
    fake.handlers.push(args);
    return new Promise<void>(() => {});
  },
  resumeCodeAgent: (_runId: string, handlers: StreamHandlers & { signal?: AbortSignal }) => {
    fake.resumed.push(handlers);
    return new Promise<void>(() => {});
  },
  cancelCodeAgent: (id: string) => {
    fake.cancels.push(id);
    return fake.cancel(id);
  },
}));
import { send, resume, stop, steer, getSnapshot, seed, setPersist } from "./backgroundRuns";
import { isAcceptedAnswer } from "./answerLifecycle";
import { uploadResource } from "../api/resources";

beforeEach(() => {
  fake.cancels.length = 0;
  fake.cancel.mockReset().mockImplementation(async (id: string) => ({ ok: true, state: "stopped", run_id: id }));
});

describe("server history reference", () => {
  it("uses the last visible accepted answer, without requiring citations", () => {
    const sessionId = "history-reference";
    seed(sessionId, [
      { kind: "user", id: "u1", text: "Read" },
      { kind: "agent", id: "a1", text: "Observed", toolCalls: [], running: false,
        answerState: "accepted", stopReason: "answer", answerStatus: "degraded", runId: "visible-run" },
      { kind: "agent", id: "draft", text: "Hidden draft", toolCalls: [], running: false,
        answerState: "interrupted", stopReason: "error", runId: "hidden-run" },
      { kind: "agent", id: "empty", text: "", toolCalls: [], running: false,
        answerState: "accepted", stopReason: "answer", runId: "empty-run" },
    ]);
    send({ sessionId, text: "Continue", mode: "code", projectRoot: "", model: "auto" });
    expect(fake.handlers.at(-1)?.historyRunId).toBe("visible-run");
    expect(fake.handlers.at(-1)?.conversationHistory).not.toContainEqual({ role: "assistant", content: "Hidden draft" });
  });

  it("does not reuse an earlier run ID when the newest visible answer has none", () => {
    const sessionId = "history-reference-legacy";
    seed(sessionId, [
      { kind: "agent", id: "a1", text: "Old", toolCalls: [], running: false, runId: "old-run" },
      { kind: "agent", id: "a2", text: "Legacy visible answer", toolCalls: [], running: false },
    ]);
    send({ sessionId, text: "Continue", mode: "code", projectRoot: "", model: "auto" });
    expect(fake.handlers.at(-1)?.historyRunId).toBeUndefined();
  });
});

describe("step notes", () => {
  it("keeps notes separate from the answer through reload and Resume", async () => {
    const sessionId = "step-notes-resume";
    const previous = { kind: "agent" as const, id: "t1", text: "Сохранённый ответ", toolCalls: [], running: false, answerState: "accepted" as const };
    seed(sessionId, [previous]);
    send({ sessionId, text: "проверь файл", mode: "code", projectRoot: "", model: "auto" });
    const handler = fake.handlers.at(-1)!;
    handler.onRunId?.("notes-run");
    handler.onEvent?.({ type: "reasoning_delta", step: 1, text: "Внутреннее рассуждение" });
    expect(getSnapshot(sessionId).turns.at(-1)).toMatchObject({ reasoningActive: true });
    handler.onEvent?.({ type: "delta", step: 1, text: "Читаю файл." });
    expect(getSnapshot(sessionId).turns.at(-1)).toMatchObject({ reasoningActive: false });
    handler.onEvent?.({ type: "step_note", step: 1, note_id: "first-note", text: "Читаю файл." });
    handler.onEvent?.({ type: "tool_call", step: 1, tool: "read_file", arguments: {}, result: "ok", ok: true });
    handler.onEvent?.({ type: "step_started", step: 2 });
    expect(getSnapshot(sessionId).turns.at(-1)).toMatchObject({ text: "", stepNotes: [{ id: "first-note", step: 1, text: "Читаю файл.", toolCallIndex: 0 }] });
    handler.onEvent?.({ type: "reasoning_delta", step: 2, text: "Думаю над результатом" });
    handler.onError?.(new Error("connection lost"));
    const saved = JSON.parse(JSON.stringify(getSnapshot(sessionId).turns));
    seed(sessionId, saved);
    const agent = getSnapshot(sessionId).turns.at(-1)!;
    resume(sessionId, agent.id, "notes-run");
    expect(getSnapshot(sessionId).turns.at(-1)).toMatchObject({ reasoningActive: false });
    const continued = fake.resumed.at(-1)!;
    continued.onRunId?.("notes-run");
    continued.onEvent?.({ type: "step_note", step: 1, note_id: "first-note", text: "Читаю файл." });
    continued.onEvent?.({ type: "step_note", step: 1, note_id: "resumed-note", text: "Проверяю результат." });
    continued.onEvent?.({ type: "final_response", step: 4, text: "Сделано: прочитан. Проверено: ok. Осталось: ничего." });
    expect(getSnapshot(sessionId).turns.at(-1)).toMatchObject({
      text: "Сделано: прочитан. Проверено: ok. Осталось: ничего.",
      stepNotes: [
        { id: "first-note", step: 1, text: "Читаю файл.", toolCallIndex: 0 },
        { id: "resumed-note", step: 1, text: "Проверяю результат.", toolCallIndex: 1 },
      ],
      answerState: "accepted",
    });
    expect(getSnapshot(sessionId).turns[0]).toEqual(previous);
    expect(new Set(getSnapshot(sessionId).turns.map(turn => turn.id)).size).toBe(getSnapshot(sessionId).turns.length);
    await stop(sessionId);
  });
});

describe("updates to a live run", () => {
  afterEach(() => { fake.cancels.length = 0; fake.input.mockReset(); });
  it("waits for the same starting stream identity and preserves the draft if stopped first", async () => {
    const args = { sessionId: "steer-starting", text: "проверь", mode: "code" as const, projectRoot: "", model: "auto" };
    send(args);
    const handler = fake.handlers.at(-1)!;
    fake.input.mockImplementationOnce(async (_run, _session, id, text) => ({ request_id: id, text, state: "queued" }));
    const pending = steer(args.sessionId, "только прочитай");
    expect(fake.input).not.toHaveBeenCalled();
    handler.onRunId?.("starting-run");
    await pending;
    expect(fake.input.mock.calls[0].slice(0, 2)).toEqual(["starting-run", args.sessionId]);
    await stop(args.sessionId);
    fake.input.mockClear();
    send({ ...args, sessionId: "steer-stopped-at-start" });
    const interrupted = steer("steer-stopped-at-start", "сохранённый текст");
    const rejected = expect(interrupted).rejects.toThrow("Текст сохранён");
    await stop("steer-stopped-at-start");
    await rejected;
    expect(fake.input).not.toHaveBeenCalled();
  });
  it("keeps the same stream, preserves an early applied receipt and saves the model reply", async () => {
    const sessionId = "steer-live";
    send({ sessionId, text: "исходная задача", mode: "code", projectRoot: "", model: "auto" });
    const handler = fake.handlers.at(-1)!;
    handler.onRunId?.("live-run");
    let resolve: (value: unknown) => void = () => {};
    fake.input.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    const streamCount = fake.handlers.length;
    const result = steer(sessionId, "только прочитай файл");
    const requestId = fake.input.mock.calls.at(-1)![2];
    handler.onEvent?.({ type: "user_input_applied", run_id: "live-run", step: 2, request_id: requestId, text: "только прочитай файл" });
    handler.onEvent?.({ type: "user_input_reply", run_id: "live-run", step: 2, request_ids: [requestId], text: "Ок, только прочитаю." });
    resolve({ request_id: requestId, text: "только прочитай файл", state: "queued" });
    await result;
    expect(fake.handlers).toHaveLength(streamCount);
    expect(getSnapshot(sessionId).running).toBe(true);
    const updates = getSnapshot(sessionId).turns.filter(t => t.kind === "user" && t.steering);
    expect(updates).toHaveLength(1);
    expect(updates[0]).toMatchObject({ steering: { state: "applied", reply: "Ок, только прочитаю." } });
    await stop(sessionId);
    expect(fake.cancels.at(-1)).toBe("live-run");
  });

  it("retains the same request ID after uncertainty and refuses a completed run", async () => {
    const sessionId = "steer-retry";
    send({ sessionId, text: "проверь", mode: "code", projectRoot: "", model: "auto" });
    fake.handlers.at(-1)!.onRunId?.("retry-run");
    fake.input.mockRejectedValueOnce(new Error("connection lost"));
    await expect(steer(sessionId, "сохрани данные")).rejects.toThrow("connection lost");
    const first = fake.input.mock.calls.at(-1)!;
    fake.input.mockImplementationOnce(async (_run, _session, id, text) => ({ request_id: id, text, state: "queued" }));
    await steer(sessionId, "сохрани данные");
    expect(fake.input.mock.calls.at(-1)![2]).toBe(first[2]);
    await stop(sessionId);
    await expect(steer(sessionId, "поздно")).rejects.toThrow("Текст сохранён");
  });
});

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
    await stop(sessionId);
    // Session storage JSON is the same boundary used by WorkspaceShell.
    const saved = JSON.parse(JSON.stringify(persist.mock.calls.at(-1)![0].turns));
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
    await stop(sessionId);

    send({ ...args, sessionId: "other-attachment-session", text: "продолжай" });
    expect(fake.handlers.at(-1)!.conversationHistory).toEqual([]);
    expect(fake.handlers.at(-1)!.resources).toEqual([]);
    await stop("other-attachment-session");
  });

  it("does not carry uploading or failed files as attached resources", async () => {
    const sessionId = "failed-attachments";
    const resource = { resource_id: "c".repeat(32), name: "voice.ogg", kind: "audio" as const, content_type: "audio/ogg", size: 10 };
    const args = { sessionId, text: "расшифруй", mode: "code" as const, projectRoot: "", model: "auto" };
    send({ ...args, resources: [{ ...resource, status: "error" }, { ...resource, status: "uploading" }] });
    await stop(sessionId);
    send({ ...args, text: "продолжай" });
    expect(fake.handlers.at(-1)!.conversationHistory).toEqual([{ role: "user", content: "расшифруй" }]);
    await stop(sessionId);
  });
});

describe("SSE run ownership", () => {
  it("ignores cancelled callbacks while a new run owns the session", async () => {
    const sessionId = "stale-events";
    const args = { sessionId, text: "first", mode: "code" as const, projectRoot: "", model: "auto" };
    send(args);
    const old = fake.handlers.at(-1)!;
    old.onRunId?.("old-run");
    await stop(sessionId);
    send({ ...args, text: "second" });
    const current = fake.handlers.at(-1)!;
    current.onRunId?.("new-run");
    old.onEvent?.({ type: "done", ok: false, run_id: "old-run", steps: 1, stop_reason: "cancelled", error: null });
    old.onRunId?.("stale-run");
    old.onError?.(new Error("old transport failed"));
    expect(getSnapshot(sessionId).running).toBe(true);
    expect(getSnapshot(sessionId).turns.at(-1)).toMatchObject({ kind: "agent", running: true, runId: "new-run" });
    await stop(sessionId);
    expect(fake.cancels).toEqual(["old-run", "new-run"]);
  });
});

describe("answer lifecycle", () => {
  it("persists a late accepted Workflow answer after Stop before the next message", async () => {
    const args = { sessionId: "late-workflow-input", text: "собери КП", mode: "code" as const, projectRoot: "", model: "auto" };
    send(args);
    const first = fake.handlers.at(-1)!;
    const input = { request_id: "late-request-1", question: "Количество?", answer: "1 ИБП, 40 АКБ" };
    const persist = vi.fn();
    setPersist(args.sessionId, persist);
    await stop(args.sessionId);
    const event = {
      type: "tool_call" as const, step: 1, tool: "ask_user", arguments: {},
      result: "RAW LATE OUTPUT", ok: true, workflow_input: input,
    };
    first.onEvent?.(event);
    first.onEvent?.(event);
    expect(persist).toHaveBeenCalledTimes(3); // stopping + stopped + the accepted receipt
    expect(getSnapshot(args.sessionId).running).toBe(false);
    const saved = JSON.parse(JSON.stringify(persist.mock.calls.at(-1)![0].turns));
    expect(JSON.stringify(saved)).not.toContain("RAW LATE OUTPUT");
    seed(args.sessionId, saved);
    send({ ...args, text: "продолжай" });
    const history = fake.handlers.at(-1)!.conversationHistory!;
    expect(history.filter(m => m.role === "user" && m.content.includes(input.answer))).toHaveLength(1);
    await stop(args.sessionId);
  });

  it("confines late Workflow input to its stopped turn while another run is active", async () => {
    const args = { sessionId: "late-workflow-new-run", text: "first", mode: "code" as const, projectRoot: "", model: "auto" };
    send(args);
    const old = fake.handlers.at(-1)!;
    const stoppedId = getSnapshot(args.sessionId).turns.at(-1)!.id;
    await stop(args.sessionId);
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
    await stop(args.sessionId);
  });

  it("preserves accepted Workflow input after cancellation and reload without retaining draft text", async () => {
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
    await stop(args.sessionId);
    seed(args.sessionId, JSON.parse(JSON.stringify(getSnapshot(args.sessionId).turns)));
    send({ ...args, text: "продолжай" });
    const history = fake.handlers.at(-1)!.conversationHistory!;
    const answers = history.filter(m => m.content.includes(input.answer));
    expect(answers).toHaveLength(1);
    expect(answers[0].role).toBe("user");
    expect(answers[0].content).toContain(JSON.stringify(input));
    expect(JSON.stringify(history)).not.toContain("UNACCEPTED DRAFT");
    expect(JSON.stringify(history)).not.toContain("LEGACY RAW ANSWER");
    await stop(args.sessionId);
  });

  it("does not retract an accepted answer on a later transport error", async () => {
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
  it.each(["stop", "error"])("keeps a %s draft out of next history and speech", async (ending) => {
    const args = { sessionId: `draft-${ending}`, text: "question", mode: "code" as const, projectRoot: "", model: "auto" };
    send(args);
    const first = fake.handlers.at(-1)!;
    first.onEvent?.({ type: "delta", step: 1, text: "UNACCEPTED DRAFT", answer_state: "draft" });
    const persist = vi.fn();
    setPersist(args.sessionId, persist);
    if (ending === "stop") await stop(args.sessionId);
    else first.onError?.(new Error("transport interrupted"));
    const turn = getSnapshot(args.sessionId).turns.at(-1)!;
    expect(turn).toMatchObject({ text: "UNACCEPTED DRAFT", answerState: "interrupted" });
    expect(persist).toHaveBeenCalledWith(expect.objectContaining({ running: false }));
    if (turn.kind !== "agent") throw new Error("expected agent turn");
    expect(isAcceptedAnswer(turn)).toBe(false);
    send({ ...args, text: "continue" });
    expect(fake.handlers.at(-1)!.conversationHistory?.some(m => m.content.includes("UNACCEPTED"))).toBe(false);
    await stop(args.sessionId);
  });

  it("stores exactly the accepted replacement and carries its journal reference", async () => {
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
    await stop(args.sessionId);
  });
});

describe("confirmed Stop", () => {
  it("waits for the current Resume cancellation generation before stopping its reused ID", async () => {
    const sessionId = "stop-before-resume-headers";
    seed(sessionId, [{ kind: "agent", id: "resume-turn", text: "draft", toolCalls: [], running: false,
      runId: "resumed-run", resumable: true, answerState: "interrupted" }]);
    resume(sessionId, "resume-turn", "resumed-run");
    const handlers = fake.resumed.at(-1)!;
    const stopping = stop(sessionId);
    await Promise.resolve();
    expect(fake.cancels).toEqual([]);
    expect(getSnapshot(sessionId).runControlState).toBe("stopping");
    handlers.onRunId?.("resumed-run");
    await stopping;
    expect(fake.cancels).toEqual(["resumed-run"]);
    expect(getSnapshot(sessionId).runControlState).toBe("stopped");
  });

  it("retains identity on failed cleanup, freezes late output and retries the same run", async () => {
    const sessionId = "confirmed-stop-retry";
    const args = { sessionId, text: "проверь", mode: "code" as const, projectRoot: "", model: "auto" };
    send(args);
    const handler = fake.handlers.at(-1)!;
    const runId = handler.runId!;
    expect(runId).toMatch(/^[a-f0-9]{32}$/);
    handler.onRunId?.(runId);
    handler.onEvent?.({ type: "delta", step: 1, text: "сохранённый черновик", answer_state: "draft" });
    let resolve!: (value: unknown) => void;
    fake.cancel.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    const stopping = stop(sessionId);
    expect(stop(sessionId)).toBe(stopping);
    expect(getSnapshot(sessionId)).toMatchObject({ running: false, runControlState: "stopping", cancelError: null });
    expect(handler.signal!.aborted).toBe(false);
    const count = fake.handlers.length;
    send({ ...args, text: "новая задача" });
    handler.onEvent?.({ type: "delta", step: 1, text: "поздний вывод", answer_state: "draft" });
    handler.onEvent?.({ type: "done", ok: true, run_id: runId, steps: 1, stop_reason: "answer", error: null });
    await Promise.resolve();
    resolve({ ok: false, state: "cancel_failed", run_id: runId, error: "process tree 7312 is still alive" });
    await stopping;
    expect(getSnapshot(sessionId)).toMatchObject({ runControlState: "cancel_failed", cancelError: "process tree 7312 is still alive" });
    expect(getSnapshot(sessionId).turns.at(-1)).toMatchObject({ runId, text: "сохранённый черновик", resumable: false });
    expect(handler.signal!.aborted).toBe(false);
    seed(sessionId, []);
    send({ ...args, text: "новая задача" });
    expect(fake.handlers).toHaveLength(count);
    await stop(sessionId);
    expect(fake.cancels).toEqual([runId, runId]);
    expect(getSnapshot(sessionId).runControlState).toBe("stopped");
    expect(handler.signal!.aborted).toBe(true);
    send({ ...args, text: "новая задача" });
    expect(fake.handlers).toHaveLength(count + 1);
    await stop(sessionId);
  });

  it("persists an unresolved Stop across reload and requires a confirmed receipt", async () => {
    const sessionId = "restored-stop-retry";
    seed(sessionId, [{ kind: "agent", id: "restored-turn", text: "черновик", toolCalls: [], running: false,
      runId: "restored-run", runControlState: "stopping", answerState: "interrupted" }]);
    expect(getSnapshot(sessionId).runControlState).toBe("cancel_failed");
    fake.cancel.mockResolvedValueOnce({ ok: true, found: false, run_id: "restored-run" });
    await stop(sessionId);
    expect(getSnapshot(sessionId).runControlState).toBe("cancel_failed");
    await stop(sessionId);
    expect(fake.cancels).toEqual(["restored-run", "restored-run"]);
    expect(getSnapshot(sessionId).runControlState).toBe("stopped");
  });
});
