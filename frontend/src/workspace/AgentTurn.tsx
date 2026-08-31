import { Brain, CheckCircle2, ChevronDown, Download, Loader2, RotateCcw, Volume2 } from "lucide-react";
import { memo, useEffect, useRef, useState } from "react";
import MarkdownRenderer from "../components/MarkdownRenderer";
import { DownloadLink } from "../components/DownloadLink";
import { AnswerMediaGallery } from "./AnswerMediaGallery";
import { ToolCallGroup } from "./ToolCallGroup";
import type { AnswerMediaItem, CompletionStatus, CriterionState } from "../api/codeAgent";
import type { AgentTurnData } from "./types";
import { deriveArtifacts } from "./artifacts";
import { getAutoSpeak, speak } from "./voice";
import { cn } from "../ui/cn";

function splitAnswerIntro(text: string): [string, string] {
  const blocks: string[] = [];
  let current: string[] = [];
  let fence = "";
  for (const line of text.trim().split("\n")) {
    const marker = line.trim().match(/^(```|~~~)/)?.[1] ?? "";
    if (marker) fence = fence ? (marker === fence ? "" : fence) : marker;
    if (!fence && !line.trim()) {
      if (current.length) {
        blocks.push(current.join("\n"));
        current = [];
      }
      continue;
    }
    current.push(line);
  }
  if (current.length) blocks.push(current.join("\n"));
  if (blocks.length < 2) return [text, ""];
  const introBlocks = /^#{1,6}\s/.test(blocks[0]) && blocks.length > 2 ? 2 : 1;
  return [blocks.slice(0, introBlocks).join("\n\n"), blocks.slice(introBlocks).join("\n\n")];
}

function AnswerWithMedia({ text, media }: { text: string; media: AnswerMediaItem[] }) {
  const [intro, details] = splitAnswerIntro(text);
  return (
    <>
      <MarkdownRenderer content={intro} />
      <AnswerMediaGallery media={media} />
      {details && <MarkdownRenderer content={details} />}
    </>
  );
}

// Memoized (FIX-23): a streaming delta rebuilds the turns array but keeps the
// reference of every UNCHANGED turn, so memo skips re-rendering all the finished
// turns on each token (callbacks from useAgentRun are stable useCallbacks).
export const AgentTurnView = memo(function AgentTurnView({ turn, onResume }: { turn: AgentTurnData; onResume?: (turnId: string, runId: string) => void }) {
  const idle = turn.running && !turn.text && !turn.reasoning && !turn.brainPhase && turn.toolCalls.length === 0 && !turn.activeTool;
  const downloads = deriveArtifacts([turn]).downloads;
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

      {turn.running && turn.brainPhase && (
        <div className="my-2 flex items-center gap-2 text-[12.5px] text-mut">
          <Loader2 size={14} className="animate-spin" />
          {turn.brainPhase === "planning"
            ? "Строит план…"
            : turn.brainPhase === "verification"
              ? "Проверяет выводы…"
              : "Выполняет задачу…"}
        </div>
      )}

      {turn.reasoning && <ReasoningBlock text={turn.reasoning} running={turn.running} />}

      {idle && (
        <div className="flex items-center gap-2 text-[12.5px] text-mut">
          <Loader2 size={14} className="animate-spin" /> Думает…
        </div>
      )}

      {turn.text && (
        <div className="text-[13.8px] leading-relaxed">
          {turn.media?.length
            ? <AnswerWithMedia text={turn.text} media={turn.media} />
            : <MarkdownRenderer content={turn.text} />}
        </div>
      )}

      {!turn.text && turn.media && turn.media.length > 0 && <AnswerMediaGallery media={turn.media} />}

      {!turn.running && turn.answerStatus && turn.answerStatus !== "complete" && (
        <div className="mt-2 text-[11.5px] text-mut">
          {turn.answerStatus === "needs_input"
            ? "Статус: нужен ответ пользователя"
            : "Статус: ответ с ограничениями из-за нехватки данных"}
        </div>
      )}

      {!turn.running && (turn.text || downloads.length > 0) && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          {turn.text && (
            <button
              type="button"
              onClick={() => void onSpeak()}
              disabled={speaking}
              title="Озвучить голосом Elira"
              aria-label="Озвучить"
              className="inline-flex items-center gap-1.5 rounded-lg border border-line px-2 py-1 text-[11px] text-mut transition-colors hover:bg-hover hover:text-tx disabled:opacity-60"
            >
              <Volume2 size={12} className={speaking ? "animate-pulse text-ac" : ""} /> Озвучить
            </button>
          )}
          {downloads.length > 0 && <span className="ml-0.5 text-[11px] text-mut">Файлы:</span>}
          {downloads.map((download) => (
            <DownloadLink
              key={download.key}
              data-download-chip={download.key}
              url={download.url}
              name={download.name}
              title={`Скачать ${download.name}`}
              aria-label={`Скачать ${download.name}`}
              className="inline-flex max-w-full items-center gap-1.5 rounded-xl border border-acl bg-card px-2.5 py-1.5 text-[11px] font-medium text-ac transition-colors hover:bg-acs"
            >
              <Download size={12} className="shrink-0" />
              <span className="max-w-[260px] truncate">{download.name}</span>
              {download.documentQa?.status === "passed" && (
                <span
                  data-document-qa="passed"
                  className="inline-flex shrink-0 items-center gap-1 border-l border-acl pl-1.5 text-[10px] text-success"
                >
                  <CheckCircle2 size={11} />
                  {download.documentQa.page_count ? `${download.documentQa.page_count} стр.` : "Проверено"}
                </span>
              )}
            </DownloadLink>
          ))}
        </div>
      )}

      {(turn.genTokens ?? 0) > 0 && (
        <div className="mt-1.5 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[11px] text-mut">
          {turn.running && <Loader2 size={11} className="animate-spin" />}
          <span className="font-mono tabular-nums">{turn.genTokens!.toLocaleString("ru-RU")}</span>
          <span>токенов</span>
          {turn.tokensPerSecond ? (
            <span className="text-[10.5px]">· {turn.tokensPerSecond.toFixed(1)} т/с</span>
          ) : null}
          {turn.promptTokensPerSecond ? (
            <span className="text-[10.5px]">· prompt {turn.promptTokensPerSecond.toFixed(1)} т/с</span>
          ) : null}
          {turn.promptTokens && turn.cacheHitRatio !== undefined ? (
            <span
              className="text-[10.5px]"
              title={`${(turn.cachedPromptTokens ?? 0).toLocaleString("ru-RU")} из ${turn.promptTokens.toLocaleString("ru-RU")} prompt-токенов взято из кэша`}
            >
              · cache {(turn.cacheHitRatio * 100).toFixed(1)}%
            </span>
          ) : null}
          {turn.ttftMs ? (
            <span className="text-[10.5px]">· TTFT {(turn.ttftMs / 1000).toFixed(2)} с</span>
          ) : null}
        </div>
      )}

      {!turn.running && turn.criteria && turn.criteria.length > 0 && (
        <CriteriaPanel criteria={turn.criteria} status={turn.completionStatus} />
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

function CriterionRow({ c, duplicate }: { c: CriterionState; duplicate?: boolean }) {
  const [showEvidence, setShowEvidence] = useState(false);
  const evidence = c.evidence ?? "";
  // Evidence is hidden by default (reveal on click) and always capped, so a rendered-
  // DOM verdict never floods the chat even when shown.
  const shown =
    evidence.length > CRITERION_EVIDENCE_MAX ? `${evidence.slice(0, CRITERION_EVIDENCE_MAX).trimEnd()}…` : evidence;
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
        <span className={cn(c.status === "failed" && "text-danger", c.status === "skipped" && "text-mut")}>{c.text}</span>
        {c.status === "skipped" && <span className="ml-1 text-[10.5px] text-mut">— n/a (условный)</span>}
        {/* R4: the criterion was closed by the runtime's own verifier call. */}
        {c.auto_verified && (
          <span className="ml-1 text-[10.5px] text-mut" title="Критерий закрыт вызовом runtime (auto-verifier pass)">
            ⚙ runtime
          </span>
        )}
        {/* Same evidence shared across criteria (e.g. one rendered-DOM verdict covering
            several dom_contains checks) is shown once — later rows just point back. */}
        {evidence && duplicate && <span className="ml-1 text-[10.5px] text-mut">— то же evidence, см. выше</span>}
        {evidence && !duplicate && (
          <>
            <button
              type="button"
              onClick={() => setShowEvidence((v) => !v)}
              className="ml-1 shrink-0 text-[10.5px] text-mut underline decoration-dotted underline-offset-2 hover:text-tx"
            >
              {showEvidence ? "скрыть evidence" : "показать evidence"}
            </button>
            {showEvidence && <span className="mt-0.5 block break-words text-[11px] text-mut">{shown}</span>}
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
  const skipped = criteria.filter((c) => c.status === "skipped").length;
  const unconfirmed = total - confirmed - failed - skipped;
  // conditional (n/a) criteria are excluded from the mandatory denominator
  const mandatory = total - skipped;
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
        <span className="text-[11.5px] text-mut">· {confirmed}/{mandatory}</span>
        {failed > 0 && <span className="text-[11.5px] font-medium text-danger">· провалено: {failed}</span>}
        {unconfirmed > 0 && <span className="text-[11.5px] text-mut">· не подтв.: {unconfirmed}</span>}
        {skipped > 0 && <span className="text-[11.5px] text-mut">· n/a: {skipped}</span>}
        <ChevronDown size={12} className={cn("ml-auto shrink-0 text-mut transition-transform", open ? "" : "-rotate-90")} />
      </button>
      {open && (
        <ul className="space-y-1 border-t border-line px-3 py-2">
          {(() => {
            const seen = new Set<string>();
            return criteria.map((c, i) => {
              const ev = c.evidence ?? "";
              const duplicate = ev !== "" && seen.has(ev);
              if (ev !== "") seen.add(ev);
              return <CriterionRow key={i} c={c} duplicate={duplicate} />;
            });
          })()}
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
