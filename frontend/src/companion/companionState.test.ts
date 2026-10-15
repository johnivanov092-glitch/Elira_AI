import { describe, expect, it } from "vitest";
import type { RunSnapshot } from "../workspace/backgroundRuns";
import type { AgentTurnData } from "../workspace/types";
import { deriveView, speakingMs } from "./companionState";

const agent = (patch: Partial<AgentTurnData> = {}): AgentTurnData => ({
  kind: "agent", id: "a1", toolCalls: [], text: "", running: false, ...patch,
});

const snapshot = (turn?: AgentTurnData): RunSnapshot => ({
  turns: turn ? [{ kind: "user", id: "u1", text: "Объясни экран" }, turn] : [],
  running: Boolean(turn?.running), runControlState: "idle", cancelError: null, taskLedger: [],
  contextUsage: null, contextState: null, usageSeeded: false,
});

const base = { capturing: false, inputOpen: false, hiddenTurnIds: new Set<string>(), speakingUntil: 0, now: 1000 };
const answered = agent({ text: "Это документация asyncio.", answerState: "accepted", stopReason: "answer" });

describe("companion view", () => {
  it("looks at the screen while capturing, even over an old answer", () => {
    expect(deriveView({ ...base, snapshot: snapshot(answered), capturing: true }).state).toBe("looking");
  });

  it("thinks while the run is going", () => {
    const view = deriveView({ ...base, snapshot: snapshot(agent({ running: true })) });
    expect(view).toMatchObject({ state: "thinking", answer: "" });
  });

  it("speaks a fresh answer, then keeps it in the bubble", () => {
    expect(deriveView({ ...base, snapshot: snapshot(answered), speakingUntil: 2000 })).toMatchObject({ state: "speaking", answer: "Это документация asyncio." });
    expect(deriveView({ ...base, snapshot: snapshot(answered), speakingUntil: 900 })).toMatchObject({ state: "idle", answer: "Это документация asyncio." });
  });

  it("hides a closed or old answer and listens while the question box is open", () => {
    const view = deriveView({ ...base, snapshot: snapshot(answered), hiddenTurnIds: new Set(["a1"]), inputOpen: true });
    expect(view).toMatchObject({ state: "listening", answer: "", notice: null });
  });

  it("explains a run without an answer instead of staying silent", () => {
    expect(deriveView({ ...base, snapshot: snapshot(agent({ error: "backend unavailable" })) }).notice).toContain("backend unavailable");
    expect(deriveView({ ...base, snapshot: snapshot(agent({ stopReason: "workflow_request", answerState: "interrupted" })) }).notice)
      .toContain("открой этот разговор в чате");
  });

  it("idles with nothing to show", () => {
    expect(deriveView({ ...base, snapshot: null })).toMatchObject({ state: "idle", answer: "", notice: null });
  });

  it("caps the typing time of long answers", () => {
    expect(speakingMs("коротко")).toBeLessThan(1000);
    expect(speakingMs("x".repeat(5000))).toBe(6000);
  });
});
