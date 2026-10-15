import { describe, expect, it } from "vitest";
import type { RunSnapshot } from "../workspace/backgroundRuns";
import type { AgentTurnData, Turn } from "../workspace/types";
import { THREAD_LIMIT, deriveState, speakingMs, stepNote, threadOf } from "./companionState";

const agent = (id: string, patch: Partial<AgentTurnData> = {}): AgentTurnData => ({
  kind: "agent", id, toolCalls: [], text: "", running: false, ...patch,
});
const answered = (id: string, text: string) => agent(id, { text, answerState: "accepted", stopReason: "answer" });

const snapshot = (turns: Turn[]): RunSnapshot => ({
  turns, running: turns.some((turn) => turn.kind === "agent" && turn.running), runControlState: "idle", cancelError: null,
  taskLedger: [], contextUsage: null, contextState: null, usageSeeded: false,
});

const conversation = snapshot([
  { kind: "user", id: "u1", text: "Объясни экран" },
  answered("a1", "Это документация asyncio."),
  { kind: "user", id: "u2", text: "А что такое gather?" },
  agent("a2", { running: true, stepNotes: [{ id: "n1", step: 1, text: "Читаю снимок через vision.\nДальше OCR.", toolCallIndex: 0 }] }),
]);

const base = { capturing: false, inputOpen: false, aloud: false, speakingUntil: 0, now: 1000 };

describe("companion conversation", () => {
  it("keeps the exchanges after the hidden ones, with the running step", () => {
    const items = threadOf(conversation, new Set(["u1", "a1"]));
    expect(items.map((item) => [item.role, item.text, item.running])).toEqual([
      ["user", "А что такое gather?", false],
      ["agent", "", true],
    ]);
    expect(items[1].note).toBe("Читаю снимок через vision.");
  });

  it("shows answers and explains a turn without one", () => {
    const items = threadOf(snapshot([
      { kind: "user", id: "u1", text: "Вопрос" }, answered("a1", "Ответ"),
      { kind: "user", id: "u2", text: "Ещё" }, agent("a2", { error: "backend unavailable" }),
    ]), new Set());
    expect(items[1]).toMatchObject({ role: "agent", text: "Ответ", notice: null });
    expect(items[3].notice).toContain("backend unavailable");
  });

  it("shows only the last exchanges", () => {
    const turns: Turn[] = [];
    for (let i = 0; i < 6; i += 1) turns.push({ kind: "user", id: `u${i}`, text: `q${i}` }, answered(`a${i}`, `a${i}`));
    const items = threadOf(snapshot(turns), new Set());
    expect(items).toHaveLength(THREAD_LIMIT);
    expect(items.at(-1)?.text).toBe("a5");
  });

  it("shortens a long step note to its first line", () => {
    expect(stepNote(agent("a", { running: true, stepNotes: [{ id: "n", step: 1, text: "x".repeat(200), toolCallIndex: 0 }] }))).toHaveLength(90);
    expect(stepNote(answered("a", "готово"))).toBeNull();
  });
});

describe("companion state", () => {
  it("looks while capturing and thinks while the run goes", () => {
    expect(deriveState({ ...base, snapshot: conversation, capturing: true })).toBe("looking");
    expect(deriveState({ ...base, snapshot: conversation })).toBe("thinking");
  });

  it("speaks while typing a fresh answer or reading it aloud", () => {
    const done = snapshot([{ kind: "user", id: "u1", text: "Вопрос" }, answered("a1", "Ответ")]);
    expect(deriveState({ ...base, snapshot: done, speakingUntil: 2000 })).toBe("speaking");
    expect(deriveState({ ...base, snapshot: done, aloud: true })).toBe("speaking");
    expect(deriveState({ ...base, snapshot: done, inputOpen: true })).toBe("listening");
    expect(deriveState({ ...base, snapshot: null })).toBe("idle");
  });

  it("caps the typing time of long answers", () => {
    expect(speakingMs("коротко")).toBeLessThan(1000);
    expect(speakingMs("x".repeat(5000))).toBe(6000);
  });
});
