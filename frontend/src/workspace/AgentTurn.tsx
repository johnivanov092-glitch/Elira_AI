import { Brain, ChevronDown, Loader2, MessageCircleQuestion, RotateCcw, Send, ShieldQuestion, Volume2 } from "lucide-react";
import { memo, useEffect, useRef, useState } from "react";
import MarkdownRenderer from "../components/MarkdownRenderer";
import { ToolCallGroup } from "./ToolCallGroup";
import type { CompletionStatus, CriterionState } from "../api/codeAgent";
import type { AgentTurnData, PendingApproval, PendingQuestion } from "./types";
import { getAutoSpeak, speak } from "./voice";
import { cn } from "../ui/cn";

type ApproveFn = (approvalId: string, decision: "approve" | "reject") => void;
type AnswerFn = (questionId: string, text: string) => void;

// Memoized (FIX-23): a streaming delta rebuilds the turns array but keeps the
// reference of every UNCHANGED turn, so memo skips re-rendering all the finished
// turns on each token (callbacks from useAgentRun are stable useCallbacks).
export const AgentTurnView = memo(function AgentTurnView({ turn, onApprove, onApproveAll, onResume, onAnswer }: { turn: AgentTurnData; onApprove?: ApproveFn; onApproveAll?: () => void; onResume?: (turnId: string, runId: string) => void; onAnswer?: AnswerFn }) {
  const idle = turn.running && !turn.text && !turn.reasoning && turn.toolCalls.length === 0 && !turn.activeTool && !turn.pendingApproval && !turn.pendingQuestion;
  const [speaking, setSpeaking] = useState(false);

  // Auto-speak: only when this turn transitions running -> done while mounted
  // (a live reply), never for already-finished turns rendered from history.
  const wasRunning = useRef(turn.running);
  useEffect(() => {
    if (wasRunning.current && !turn.running && turn.text && getAutoSpeak()) {
      void speak(turn.text);
    }
    wasRunning.current = turn.running;
  }, [turn.running, turn.text]);

  async function onSpeak() {
    if (!turn.text) return;
    setSpeaking(true);
    try {
      await speak(turn.text);
    } finally {
      setSpeaking(false);
    }
  }

  return (
    <div className="my-2 mb-6">
      <ToolCallGroup calls={turn.toolCalls} activeTool={turn.running ? turn.activeTool : undefined} stopReason={turn.running ? undefined : turn.stopReason} />

      {turn.reasoning && <ReasoningBlock text={turn.reasoning} running={turn.running} />}

      {turn.pendingApproval && <ApprovalPrompt approval={turn.pendingApproval} onApprove={onApprove} onApproveAll={onApproveAll} />}

      {turn.pendingQuestion && <QuestionPrompt question={turn.pendingQuestion} onAnswer={onAnswer} />}

      {idle && (
        <div className="flex items-center gap-2 text-[12.5px] text-mut">
          <Loader2 size={14} className="animate-spin" /> Думает…
        </div>
      )}

      {turn.text && (
        <div className="text-[13.8px] leading-relaxed">
          <MarkdownRenderer content={turn.text} />
        </div>
      )}

      {turn.text && !turn.running && (
        <button
          type="button"
          onClick={() => void onSpeak()}
          disabled={speaking}
          title="Озвучить голосом Elira"
          aria-label="Озвучить"
          className="mt-1.5 inline-flex items-center gap-1.5 rounded-lg border border-line px-2 py-1 text-[11px] text-mut transition-colors hover:bg-hover hover:text-tx disabled:opacity-60"
        >
          <Volume2 size={12} className={speaking ? "animate-pulse text-ac" : ""} /> Озвучить
        </button>
      )}

      {(turn.genTokens ?? 0) > 0 && (
        <div className="mt-1.5 flex items-center gap-1.5 text-[11px] text-mut">
          {turn.running && <Loader2 size={11} className="animate-spin" />}
          <span className="font-mono tabular-nums">{turn.genTokens!.toLocaleString("ru-RU")}</span>
          <span>токенов</span>
          {turn.tokensPerSecond ? (
            <span className="text-[10.5px]">· {turn.tokensPerSecond.toFixed(1)} т/с</span>
          ) : null}
        </div>
      )}

      {!turn.running && turn.criteria && turn.criteria.length > 0 && (
        <CriteriaPanel criteria={turn.criteria} status={turn.completionStatus} />
      )}

      {turn.error && (
        turn.stopReason === "loop_guard" || turn.stopReason === "no_progress" ? (
          // Engineering status, not a first-person "I stopped myself" — the run
          // ended incomplete; the deterministic report above says what's done.
          <div className="mt-2 rounded-lg border border-line bg-surface px-3 py-2 text-[12.5px] text-mut">
            <span className="font-medium text-t2">Не завершено</span> · остановлено:{" "}
            {turn.stopReason === "no_progress"
              ? "нет прогресса — стратегия зашла в тупик"
              : "повтор без прогресса"}
            . Итог — выше; уточни путь и продолжи.
          </div>
        ) : (
          <div className="mt-2 rounded-lg border border-line bg-surface px-3 py-2 text-[12.5px] text-t2">
            Ошибка: {turn.error}
          </div>
        )
      )}

      {!turn.running && !turn.text && !turn.error && turn.stopReason && turn.stopReason !== "answer" && (
        <div className="mt-2 text-[12px] text-mut">Остановлено: {turn.stopReason}</div>
      )}

      {!turn.running && turn.resumable && turn.runId && (
        <button
          type="button"
          onClick={() => onResume?.(turn.id, turn.runId!)}
          className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-acl px-2.5 py-1.5 text-[12px] text-ac transition-colors hover:bg-acs"
        >
          <RotateCcw size={12} /> Продолжить
        </button>
      )}
    </div>
  );
});

/** Task-completion panel — per-criterion verification state (Ph7.4/7.5). Shown
 *  from the done event's `criteria`, coloured by verifier verdict. This is the
 *  consumer that makes `completion_status` honest: "solved" is green ONLY when a
 *  verifier confirmed each criterion — never on the model's word. */
const COMPLETION_LABEL: Record<CompletionStatus, { text: string; cls: string } | undefined> = {
  confirmed: { text: "подтверждено", cls: "text-success" },
  partial: { text: "частично подтверждено", cls: "text-mut" },
  unverified: { text: "не подтверждено verifier'ом", cls: "text-mut" },
  failed: { text: "проверка НЕ пройдена", cls: "text-danger" },
  none: undefined,
};

/** Evidence longer than this is truncated in the list and revealed by a per-row
 *  toggle — so a huge rendered-DOM verdict never floods the chat. */
const CRITERION_EVIDENCE_MAX = 220;

function CriterionRow({ c }: { c: CriterionState }) {
  const [showEvidence, setShowEvidence] = useState(false);
  const evidence = c.evidence ?? "";
  const long = evidence.length > CRITERION_EVIDENCE_MAX;
  const shown = long && !showEvidence ? `${evidence.slice(0, CRITERION_EVIDENCE_MAX).trimEnd()}…` : evidence;
  return (
    <li className="flex items-start gap-2">
      <span
        className={cn(
          "mt-[5px] h-[7px] w-[7px] shrink-0 rounded-full",
          c.status === "confirmed" ? "bg-success" : c.status === "failed" ? "bg-danger" : "bg-mut",
        )}
        aria-hidden
      />
      <span className="min-w-0">
        <span className={cn(c.status === "failed" && "text-danger")}>{c.text}</span>
        {evidence && (
          <>
            <span className="break-words text-[11px] text-mut"> — {shown}</span>
            {long && (
              <button
                type="button"
                onClick={() => setShowEvidence((v) => !v)}
                className="ml-1 shrink-0 text-[10.5px] text-mut underline decoration-dotted underline-offset-2 hover:text-tx"
              >
                {showEvidence ? "скрыть" : "показать evidence"}
              </button>
            )}
          </>
        )}
      </span>
    </li>
  );
}

/** Collapsed by default, one line in the chat. The header carries the whole summary
 *  (status · X/Y confirmed · failed/unverified counts); the full per-criterion list
 *  + evidence stays available for audit on expand. */
function CriteriaPanel({ criteria, status }: { criteria: CriterionState[]; status?: CompletionStatus }) {
  const [open, setOpen] = useState(false);
  const badge = status ? COMPLETION_LABEL[status] : undefined;
  const total = criteria.length;
  const confirmed = criteria.filter((c) => c.status === "confirmed").length;
  const failed = criteria.filter((c) => c.status === "failed").length;
  const unconfirmed = total - confirmed - failed;
  return (
    <div className="mt-2 rounded-xl border border-line bg-surface text-[12.5px]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-x-2 px-3 py-2 text-left text-[12px] transition-colors hover:text-tx"
      >
        <span className="font-medium text-t2">Готовность задачи</span>
        {badge && <span className={cn("text-[11.5px] font-medium", badge.cls)}>· {badge.text}</span>}
        <span className="text-[11.5px] text-mut">· {confirmed}/{total}</span>
        {failed > 0 && <span className="text-[11.5px] font-medium text-danger">· провалено: {failed}</span>}
        {unconfirmed > 0 && <span className="text-[11.5px] text-mut">· не подтв.: {unconfirmed}</span>}
        <ChevronDown size={12} className={cn("ml-auto shrink-0 text-mut transition-transform", open ? "" : "-rotate-90")} />
      </button>
      {open && (
        <ul className="space-y-1 border-t border-line px-3 py-2">
          {criteria.map((c, i) => (
            <CriterionRow key={i} c={c} />
          ))}
        </ul>
      )}
    </div>
  );
}

/** Collapsible «Рассуждение» block — the model's chain-of-thought streamed on
 *  the separate reasoning channel (when the composer's think toggle is on).
 *  Opens live while the turn is thinking so you can watch it, then auto-collapses
 *  once done (unless the user manually toggled it). Never part of the answer. */
function ReasoningBlock({ text, running }: { text: string; running: boolean }) {
  const [open, setOpen] = useState(running);
  const touched = useRef(false);
  useEffect(() => {
    if (!running && !touched.current) setOpen(false);
  }, [running]);
  return (
    <div className="mb-2 rounded-lg border border-line bg-surface/60">
      <button
        type="button"
        onClick={() => { touched.current = true; setOpen((v) => !v); }}
        className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-[11.5px] text-mut transition-colors hover:text-tx"
      >
        {running ? <Loader2 size={12} className="shrink-0 animate-spin" /> : <Brain size={12} className="shrink-0" />}
        <span>Рассуждение</span>
        <ChevronDown size={12} className={cn("ml-auto shrink-0 transition-transform", open ? "" : "-rotate-90")} />
      </button>
      {open && (
        <div className="border-t border-line px-2.5 py-2 text-[12px] leading-relaxed text-t2">
          <MarkdownRenderer content={text} />
        </div>
      )}
    </div>
  );
}

/** Elira asked a clarifying question mid-run (ask_user) — the run is paused,
 *  waiting for the answer. Option buttons answer instantly; the text field
 *  handles a free-form reply. The SSE stream stays alive during the wait. */
function QuestionPrompt({ question, onAnswer }: { question: PendingQuestion; onAnswer?: AnswerFn }) {
  const [text, setText] = useState("");
  const busy = question.answering;
  return (
    <div className="my-2.5 rounded-xl border border-acl bg-acs p-3 text-[12.5px]">
      <div className="mb-2 flex items-start gap-2 font-medium text-tx">
        <MessageCircleQuestion size={15} className="mt-0.5 shrink-0 text-ac" />
        <span className="min-w-0 whitespace-pre-wrap">{question.question || "Уточняющий вопрос"}</span>
      </div>
      {question.options.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {question.options.map((opt, i) => (
            <button
              key={`${opt}-${i}`}
              type="button"
              disabled={busy}
              onClick={() => onAnswer?.(question.questionId, opt)}
              className="rounded-lg border border-acl bg-card px-2.5 py-1.5 text-[12px] text-ac transition-colors hover:bg-hover disabled:opacity-50"
            >
              {opt}
            </button>
          ))}
        </div>
      )}
      <div className="flex items-center gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={busy}
          placeholder="Свой ответ…"
          onKeyDown={(e) => { if (e.key === "Enter" && text.trim()) { onAnswer?.(question.questionId, text.trim()); setText(""); } }}
          className="flex-1 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-[12.5px] text-tx outline-none focus:border-acl disabled:opacity-60"
        />
        <button
          type="button"
          disabled={busy || !text.trim()}
          onClick={() => { onAnswer?.(question.questionId, text.trim()); setText(""); }}
          aria-label="Ответить"
          className="grid h-[33px] w-[33px] shrink-0 place-items-center rounded-lg bg-ac text-[#14151b] transition-opacity hover:opacity-90 disabled:opacity-40"
        >
          {busy ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
        </button>
      </div>
      {question.waitedS ? <div className="mt-1.5 text-[11px] text-mut">ждём ответа · {question.waitedS}с</div> : null}
    </div>
  );
}

function ApprovalPrompt({ approval, onApprove, onApproveAll }: { approval: PendingApproval; onApprove?: ApproveFn; onApproveAll?: () => void }) {
  return (
    <div className="my-2.5 rounded-xl border border-acl bg-acs p-3 text-[12.5px]">
      <div className="mb-2 flex items-center gap-2 font-medium text-tx">
        <ShieldQuestion size={15} className="shrink-0 text-ac" />
        Разрешить действие: <span className="font-mono text-ac">{approval.tool}</span>
        {approval.waitedS ? <span className="text-mut">· ждём {approval.waitedS}с</span> : null}
      </div>
      <pre className="mb-2.5 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-[#121216] p-2 font-mono text-[11px] text-t2">
        {JSON.stringify(approval.arguments, null, 2)}
      </pre>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={approval.resolving}
          onClick={() => onApprove?.(approval.approvalId, "approve")}
          className="rounded-lg bg-ac px-3 py-1.5 text-[12.5px] font-medium text-[#14151b] transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          Разрешить
        </button>
        <button
          type="button"
          disabled={approval.resolving}
          onClick={() => onApproveAll?.()}
          title="Не спрашивать до конца этого чата"
          className="rounded-lg border border-acl px-3 py-1.5 text-[12.5px] font-medium text-ac transition-colors hover:bg-hover disabled:opacity-50"
        >
          Разрешить всё в сессии
        </button>
        <button
          type="button"
          disabled={approval.resolving}
          onClick={() => onApprove?.(approval.approvalId, "reject")}
          className="rounded-lg border border-line px-3 py-1.5 text-[12.5px] text-t2 transition-colors hover:bg-hover hover:text-tx disabled:opacity-50"
        >
          Отклонить
        </button>
        {approval.resolving && (
          <span className="flex items-center gap-1.5 text-[11.5px] text-mut">
            <Loader2 size={12} className="animate-spin" /> отправлено…
          </span>
        )}
      </div>
    </div>
  );
}
