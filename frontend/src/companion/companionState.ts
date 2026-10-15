import type { RunSnapshot } from "../workspace/backgroundRuns";
import { isAcceptedAnswer } from "../workspace/answerLifecycle";
import type { AgentTurnData } from "../workspace/types";

export type CompanionState = "idle" | "listening" | "looking" | "thinking" | "speaking";

export interface CompanionView {
  state: CompanionState;
  /** The finished answer of the latest turn, when it should be on screen. */
  answer: string;
  /** Why the latest turn has no answer (error, stop, a request that needs the chat). */
  notice: string | null;
  /** The turn the answer or notice belongs to. */
  turnId: string | null;
}

export interface ViewInput {
  snapshot: RunSnapshot | null;
  capturing: boolean;
  inputOpen: boolean;
  /** Turns the companion must not show: present at start or closed by the user. */
  hiddenTurnIds: ReadonlySet<string>;
  /** The answer is still being "spoken" (typed out) until this time. */
  speakingUntil: number;
  now: number;
}

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

/** What the companion shows, from the run snapshot and its own local flags. */
export function deriveView({ snapshot, capturing, inputOpen, hiddenTurnIds, speakingUntil, now }: ViewInput): CompanionView {
  const idle: CompanionView = { state: inputOpen ? "listening" : "idle", answer: "", notice: null, turnId: null };
  if (capturing) return { ...idle, state: "looking" };
  const turn = lastAgentTurn(snapshot);
  if (!turn) return idle;
  if (turn.running) return { state: "thinking", answer: "", notice: null, turnId: turn.id };
  if (hiddenTurnIds.has(turn.id)) return idle;
  if (isAcceptedAnswer(turn) && turn.text.trim()) {
    return { state: now < speakingUntil ? "speaking" : inputOpen ? "listening" : "idle", answer: turn.text, notice: null, turnId: turn.id };
  }
  return { ...idle, notice: noticeFor(turn), turnId: turn.id };
}

/** How long the answer is "spoken": a short typing animation, capped so long answers do not drag. */
export function speakingMs(answer: string): number {
  return Math.min(6000, 700 + answer.length * 14);
}
