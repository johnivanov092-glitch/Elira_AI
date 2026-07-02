import { Brain, ChevronDown, Loader2, RotateCcw, ShieldQuestion, Volume2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import MarkdownRenderer from "../components/MarkdownRenderer";
import { ToolCallGroup } from "./ToolCallGroup";
import type { AgentTurnData, PendingApproval } from "./types";
import { getAutoSpeak, speak } from "./voice";
import { cn } from "../ui/cn";

type ApproveFn = (approvalId: string, decision: "approve" | "reject") => void;

export function AgentTurnView({ turn, onApprove, onApproveAll, onResume }: { turn: AgentTurnData; onApprove?: ApproveFn; onApproveAll?: () => void; onResume?: (turnId: string, runId: string) => void }) {
  const idle = turn.running && !turn.text && !turn.reasoning && turn.toolCalls.length === 0 && !turn.activeTool && !turn.pendingApproval;
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
      <ToolCallGroup calls={turn.toolCalls} activeTool={turn.running ? turn.activeTool : undefined} />

      {turn.reasoning && <ReasoningBlock text={turn.reasoning} running={turn.running} />}

      {turn.pendingApproval && <ApprovalPrompt approval={turn.pendingApproval} onApprove={onApprove} onApproveAll={onApproveAll} />}

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

      {turn.error && (
        <div className="mt-2 rounded-lg border border-line bg-surface px-3 py-2 text-[12.5px] text-t2">
          Ошибка: {turn.error}
        </div>
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
