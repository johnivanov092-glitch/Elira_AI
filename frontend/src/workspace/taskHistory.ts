import type { TaskLedgerEntry } from "../api/codeAgent";
import type { Turn } from "./types";

export type TaskHistoryItem = TaskLedgerEntry & { label: string };

const TERMINAL_TYPES = new Set<TaskLedgerEntry["type"]>(["final", "partial", "error"]);
const LEGACY_ACTIONS = new Set([
  "answer",
  "cancelled",
  "error",
  "timeout",
]);

function compactLabel(value: string): string {
  const text = value.replace(/\s+/g, " ").trim();
  return text.length <= 140 ? text : `${text.slice(0, 137)}...`;
}

export function latestUserTaskLabel(turns: Turn[]): string {
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    const turn = turns[i];
    if (turn.kind === "user" && turn.text.trim()) return compactLabel(turn.text);
  }
  return "";
}

/** Build a compact, newest-first history from the persisted terminal ledger.
 * Older rows stored stop_reason in `action`; pair those with the chat's user
 * turns so switching away and back still shows meaningful task names. */
export function taskHistoryItems(
  ledger: TaskLedgerEntry[],
  turns: Turn[],
  limit = 8,
): TaskHistoryItem[] {
  const terminal = ledger.filter((entry) => TERMINAL_TYPES.has(entry.type));
  const users = turns
    .filter((turn): turn is Extract<Turn, { kind: "user" }> => turn.kind === "user")
    .map((turn) => compactLabel(turn.text));

  const items = terminal.map((entry, index) => {
    const userIndex = users.length - terminal.length + index;
    const paired = userIndex >= 0 ? users[userIndex] : "";
    const label = LEGACY_ACTIONS.has(entry.action)
      ? paired || compactLabel(entry.action)
      : compactLabel(entry.action) || paired || "Задача";
    return { ...entry, label };
  });
  return items.slice(-Math.max(1, limit)).reverse();
}
