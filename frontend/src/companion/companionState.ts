import type { RunSnapshot } from "../workspace/backgroundRuns";
import { isAcceptedAnswer } from "../workspace/answerLifecycle";
import type { AgentTurnData } from "../workspace/types";

export type CompanionState = "idle" | "listening" | "looking" | "thinking" | "speaking";

/** One line of the conversation shown in the bubble. */
export interface ThreadItem {
  id: string;
  role: "user" | "agent";
  /** The question, or the accepted answer of an agent turn. */
  text: string;
  running: boolean;
  /** What a running agent turn is doing now (its latest step note). */
  note: string | null;
  /** Why a finished agent turn has no answer. */
  notice: string | null;
}

/** The last exchanges in the bubble; older ones stay in the chat. */
export const THREAD_LIMIT = 6;

export function lastAgentTurn(snapshot: RunSnapshot | null): AgentTurnData | undefined {
  const turns = snapshot?.turns ?? [];
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    const turn = turns[i];
    if (turn.kind === "agent") return turn;
  }
  return undefined;
}

export function noticeFor(turn: AgentTurnData): string {
  if (turn.error) return `Не получилось: ${turn.error}`;
  if (turn.stopReason === "cancelled") return "Остановлено.";
  if (turn.stopReason === "workflow_request" || turn.answerStatus === "needs_input") {
    return "Нужно подтверждение или ответ — открой этот разговор в чате.";
  }
  return "Ответа нет — открой разговор в чате, там видно, на чём остановилась.";
}

/** The latest step note of a running turn, shortened for the bubble. */
export function stepNote(turn: AgentTurnData | undefined): string | null {
  const note = turn?.running ? turn.stepNotes?.at(-1)?.text.trim() : "";
  if (!note) return null;
  const line = note.split("\n")[0];
  return line.length > 90 ? `${line.slice(0, 89)}…` : line;
}

/** Turns of this companion conversation the bubble shows: not hidden, user and agent only. */
export function threadOf(snapshot: RunSnapshot | null, hiddenIds: ReadonlySet<string>): ThreadItem[] {
  const items: ThreadItem[] = [];
  for (const turn of snapshot?.turns ?? []) {
    if (hiddenIds.has(turn.id)) continue;
    if (turn.kind === "user") {
      items.push({ id: turn.id, role: "user", text: turn.text, running: false, note: null, notice: null });
    } else if (turn.kind === "agent") {
      const answered = !turn.running && isAcceptedAnswer(turn) && Boolean(turn.text.trim());
      items.push({
        id: turn.id, role: "agent", text: answered ? turn.text : "", running: turn.running, note: stepNote(turn),
        notice: turn.running || answered ? null : noticeFor(turn),
      });
    }
  }
  return items.slice(-THREAD_LIMIT);
}

export interface StateInput {
  snapshot: RunSnapshot | null;
  capturing: boolean;
  inputOpen: boolean;
  /** Reading the answer aloud right now. */
  aloud: boolean;
  /** A fresh answer is typed out until this time. */
  speakingUntil: number;
  now: number;
}

export function deriveState({ snapshot, capturing, inputOpen, aloud, speakingUntil, now }: StateInput): CompanionState {
  if (capturing) return "looking";
  if (lastAgentTurn(snapshot)?.running) return "thinking";
  if (aloud || now < speakingUntil) return "speaking";
  return inputOpen ? "listening" : "idle";
}

/** How long a fresh answer is typed out: short, capped so long answers do not drag. */
export function speakingMs(answer: string): number {
  return Math.min(6000, 700 + answer.length * 14);
}
