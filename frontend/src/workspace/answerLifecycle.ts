import type { AgentTurnData } from "./types";

/** One eligibility rule for history and speech, including readable old chats. */
export function isAcceptedAnswer(turn: AgentTurnData): boolean {
  if (turn.running || turn.error) return false;
  if (turn.answerState) return turn.answerState === "accepted" && turn.stopReason === "answer";
  return !turn.stopReason || turn.stopReason === "answer";
}
