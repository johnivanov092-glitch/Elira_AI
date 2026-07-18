import { describe, expect, it } from "vitest";
import { doneLedgerEntries } from "./backgroundRuns";
import type { CodeAgentStreamEvent } from "../api/codeAgent";

type DoneEvent = Extract<CodeAgentStreamEvent, { type: "done" }>;

function doneEvent(overrides: Partial<DoneEvent>): DoneEvent {
  return {
    type: "done",
    ok: false,
    steps: 3,
    stop_reason: "timeout",
    error: null,
    ...overrides,
  } as DoneEvent;
}

describe("doneLedgerEntries — delivery next_milestone surfacing", () => {
  it("adds an explicit «Следующий шаг» line on an honest-partial terminal", () => {
    // ok=false timeout keeps its pre-existing "error" ledger type — the fix
    // only ADDS the milestone line, it must not reshape the first entry.
    const entries = doneLedgerEntries(doneEvent({
      partial: true,
      resumable: true,
      completion_status: "partial",
      next_milestone: "entrypoint, README, тест и проверка",
    }));
    expect(entries).toHaveLength(2);
    expect(entries[0].type).toBe("error");
    expect(entries[0].action).toBe("timeout");
    expect(entries[1].type).toBe("partial");
    expect(entries[1].action).toBe("next_milestone");
    expect(entries[1].result).toBe("Следующий шаг: entrypoint, README, тест и проверка");
  });

  it("adds the milestone line on an ok-but-unverified answer too", () => {
    const entries = doneLedgerEntries(doneEvent({
      ok: true,
      stop_reason: "answer",
      completion_status: "unverified",
      next_milestone: "проверить критерий verifier'ом",
    }));
    expect(entries).toHaveLength(2);
    expect(entries[0].type).toBe("partial");
    expect(entries[1].result).toBe("Следующий шаг: проверить критерий verifier'ом");
  });

  it("never adds the milestone line on a solved/confirmed terminal", () => {
    const entries = doneLedgerEntries(doneEvent({
      ok: true,
      stop_reason: "answer",
      completion_status: "confirmed",
      // even if the backend accidentally attached one, solved must not show it
      next_milestone: "лишний шаг",
    }));
    expect(entries).toHaveLength(1);
    expect(entries[0].type).toBe("final");
    expect(entries[0].result).toBe("completed");
  });

  it("keeps the single existing line when a partial terminal has no milestone", () => {
    const entries = doneLedgerEntries(doneEvent({
      partial: true,
      completion_status: "unverified",
    }));
    expect(entries).toHaveLength(1);
    expect(entries[0].type).toBe("error"); // ok=false → pre-existing shape
    expect(entries[0].result).toBe("задача: unverified (не solved)");
  });

  it("keeps the error shape for runtime failures", () => {
    const entries = doneLedgerEntries(doneEvent({
      error: "boom",
      completion_status: "failed",
      next_milestone: "починить сборку",
    }));
    expect(entries).toHaveLength(2);
    expect(entries[0].type).toBe("error");
    expect(entries[0].result).toBe("boom");
    expect(entries[1].result).toBe("Следующий шаг: починить сборку");
  });
});
