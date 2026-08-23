import { describe, expect, it } from "vitest";
import type { TaskLedgerEntry } from "../api/codeAgent";
import type { Turn } from "./types";
import { taskHistoryItems } from "./taskHistory";

describe("taskHistoryItems", () => {
  it("restores task labels from chat turns for legacy terminal ledger rows", () => {
    const turns: Turn[] = [
      { kind: "user", id: "u1", text: "Сделай лендинг" },
      { kind: "agent", id: "a1", text: "Готово", toolCalls: [], running: false },
      { kind: "user", id: "u2", text: "Добавь параллакс" },
      { kind: "agent", id: "a2", text: "Не завершено", toolCalls: [], running: false },
    ];
    const ledger: TaskLedgerEntry[] = [
      { timestamp: 1, type: "tool_call", action: "write_file", result: "completed" },
      { timestamp: 2, type: "final", action: "answer", result: "completed" },
      { timestamp: 3, type: "error", action: "cancelled", result: "cancelled" },
    ];

    expect(taskHistoryItems(ledger, turns)).toEqual([
      expect.objectContaining({ label: "Добавь параллакс", type: "error" }),
      expect.objectContaining({ label: "Сделай лендинг", type: "final" }),
    ]);
  });

  it("shows only terminal outcomes and keeps the newest bounded entries", () => {
    const ledger: TaskLedgerEntry[] = Array.from({ length: 10 }, (_, i) => ({
      timestamp: i,
      type: i % 2 ? "partial" : "final",
      action: `Задача ${i}`,
      result: "completed",
    }));

    const items = taskHistoryItems(ledger, [], 3);
    expect(items.map((item) => item.label)).toEqual(["Задача 9", "Задача 8", "Задача 7"]);
  });
});
