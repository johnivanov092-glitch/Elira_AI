import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import type { CodeAgentToolCall } from "../api/codeAgent";
import { toolIcon } from "./toolIcon";
import { cn } from "../ui/cn";

// The single meaningful bit of an args object, flattened + capped — the "gist"
// shown on a collapsed row instead of the raw command wall.
function shortArg(args: Record<string, unknown>): string {
  for (const key of ["path", "command", "query", "pattern", "url", "task", "code", "host", "action", "text"]) {
    const value = args[key];
    if (typeof value === "string" && value) {
      const flat = value.replace(/\s+/g, " ").trim();
      return flat.length > 72 ? flat.slice(0, 72) + "…" : flat;
    }
  }
  return "";
}

function plural(n: number): string {
  const a = n % 10, b = n % 100;
  if (a === 1 && b !== 11) return "вызов инструмента";
  if (a >= 2 && a <= 4 && (b < 10 || b >= 20)) return "вызова инструментов";
  return "вызовов инструментов";
}

function errPlural(n: number): string {
  const a = n % 10, b = n % 100;
  if (a === 1 && b !== 11) return "ошибка";
  if (a >= 2 && a <= 4 && (b < 10 || b >= 20)) return "ошибки";
  return "ошибок";
}

// Runtime/physical terminal conditions shown instead of a green success count.
const FAILED_STOP_LABELS: Record<string, string> = {
  timeout: "таймаут",
  context_limit: "переполнен контекст",
  error: "ошибка",
  cancelled: "остановлено",
};

// `ok === false` is a real failure (red). A non-zero shell exit that isn't an
// explicit ok is "ran, but not clean" (grey) — a bare exit=0 no longer reads as
// verified success by omission. Everything else stays green.
function StatusDot({ ok, exitCode }: { ok?: boolean; exitCode?: number }) {
  const cls =
    ok === false ? "bg-danger" : exitCode != null && exitCode !== 0 && ok !== true ? "bg-mut" : "bg-success";
  return <span className={cn("h-[7px] w-[7px] shrink-0 rounded-full", cls)} aria-hidden />;
}

type Run = { tool: string; items: { call: CodeAgentToolCall; idx: number }[] };

function groupRuns(calls: CodeAgentToolCall[]): Run[] {
  const runs: Run[] = [];
  calls.forEach((call, idx) => {
    const last = runs[runs.length - 1];
    if (last && last.tool === call.tool) last.items.push({ call, idx });
    else runs.push({ tool: call.tool, items: [{ call, idx }] });
  });
  return runs;
}

export function ToolCallGroup({ calls, activeTool, stopReason }: { calls: CodeAgentToolCall[]; activeTool?: string; stopReason?: string }) {
  // Collapse big groups by default so they don't sprawl. A live run that starts
  // small stays expanded (you watch it grow); a large/historical group mounts
  // collapsed.
  const [open, setOpen] = useState(() => calls.length <= 5);
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (open && activeTool && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [calls.length, activeTool, open]);

  const counts = useMemo(() => {
    const m = new Map<string, number>();
    for (const c of calls) m.set(c.tool, (m.get(c.tool) ?? 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [calls]);
  const errors = useMemo(() => calls.filter((c) => c.ok === false).length, [calls]);
  const oks = calls.length - errors;
  const runs = useMemo(() => groupRuns(calls), [calls]);
  // A terminal non-answer stop = the run ended without finishing. Surface it in
  // the header (over the "N ok" summary) so a stopped run reads as stopped.
  const failedLabel = stopReason ? FAILED_STOP_LABELS[stopReason] : undefined;

  if (calls.length === 0 && !activeTool) return null;

  return (
    <div className="my-2.5 overflow-hidden rounded-xl border border-line bg-surface">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-[12.5px]"
      >
        {open ? <ChevronDown size={13} className="shrink-0 text-mut" /> : <ChevronRight size={13} className="shrink-0 text-mut" />}
        <span className="shrink-0 font-medium">{calls.length} {plural(calls.length)}</span>
        <span className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
          {counts.slice(0, 4).map(([tool, n]) => (
            <span key={tool} className="rounded-md border border-line bg-card px-1.5 py-px font-mono text-[10.5px] text-t2">
              {n}× {tool}
            </span>
          ))}
          {counts.length > 4 && <span className="text-[10.5px] text-mut">+{counts.length - 4}</span>}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-2 text-[11.5px]">
          {failedLabel ? (
            <span className="font-medium text-danger">⛔ {failedLabel} · задача не завершена</span>
          ) : (
            <>
              {oks > 0 && <span className="text-success">{oks} ok</span>}
              {errors > 0 && <span className="text-danger">{errors} {errPlural(errors)}</span>}
            </>
          )}
        </span>
      </button>
      {open && (
        <div>
          <div ref={scrollRef} className="max-h-[46vh] overflow-y-auto">
            {runs.map((run, ri) =>
              run.items.length >= 3
                ? <RunGroup key={ri} run={run} />
                : run.items.map(({ call, idx }) => <ToolRow key={idx} call={call} />),
            )}
          </div>
          {activeTool && (
            <div className="flex items-center gap-2.5 border-t border-line px-3.5 py-2.5 text-[12.5px] text-t2">
              <Loader2 size={14} className="animate-spin text-ac" /> выполняется {activeTool}…
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// A run of 3+ consecutive calls of the same tool, collapsed to one line by
// default so a storm of run_bash/ssh_read doesn't sprawl. Expands to the rows.
function RunGroup({ run }: { run: Run }) {
  const [open, setOpen] = useState(false);
  const Icon = toolIcon(run.tool);
  const errs = run.items.filter((x) => x.call.ok === false).length;
  return (
    <div className="border-t border-line">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2.5 px-3.5 py-2 text-left text-[12.5px] hover:bg-hover"
      >
        {open ? <ChevronDown size={13} className="shrink-0 text-mut" /> : <ChevronRight size={13} className="shrink-0 text-mut" />}
        <span className="grid h-[23px] w-[23px] shrink-0 place-items-center rounded-md border border-line bg-surface text-t2">
          <Icon size={14} />
        </span>
        <span className="font-medium">{run.tool}</span>
        <span className="rounded border border-line bg-card px-1 text-[10.5px] text-mut">×{run.items.length}</span>
        {errs > 0 ? <span className="text-[11.5px] text-danger">{errs} {errPlural(errs)}</span> : <StatusDot ok />}
        <span className="ml-auto shrink-0 text-[11px] text-mut">{open ? "свернуть" : "развернуть"}</span>
      </button>
      {open && run.items.map(({ call, idx }) => <ToolRow key={idx} call={call} nested />)}
    </div>
  );
}

function ToolRow({ call, nested }: { call: CodeAgentToolCall; nested?: boolean }) {
  const [exp, setExp] = useState(false);
  const Icon = toolIcon(call.tool);
  const err = call.ok === false;
  return (
    <div
      className="border-t border-line"
      style={err ? { background: "color-mix(in srgb, var(--color-danger) 9%, transparent)" } : undefined}
    >
      <button
        type="button"
        onClick={() => setExp((e) => !e)}
        className={cn(
          "flex w-full items-center gap-2.5 px-3.5 py-2 text-left text-[12.5px] hover:bg-hover",
          nested && "pl-9",
        )}
      >
        <StatusDot ok={call.ok} exitCode={call.exit_code} />
        <span className="grid h-[23px] w-[23px] shrink-0 place-items-center rounded-md border border-line bg-surface text-t2">
          <Icon size={14} />
        </span>
        <span className="min-w-0 flex-1 truncate">
          <span className={cn("font-medium", err && "text-danger")}>{call.tool}</span>{" "}
          <span className={cn("font-mono text-[11.5px]", err ? "text-danger" : "text-t2")}>{shortArg(call.arguments)}</span>
        </span>
        {/* R4: the runtime made this call itself (auto-verifier closure) — make it
            visibly distinct from model-chosen calls. */}
        {call.auto_verifier && (
          <span
            className="ml-2 shrink-0 rounded-md border border-line bg-surface px-1.5 py-px text-[10px] text-t2"
            title="Вызов выполнен runtime'ом (auto-verifier pass), не моделью"
          >
            ⚙ runtime
          </span>
        )}
        <ChevronRight size={13} className="ml-auto shrink-0 text-mut" />
      </button>
      {exp && call.result && (
        <pre className="mx-3.5 mb-3 max-h-60 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-[#121216] p-3 font-mono text-[11.5px] text-t2">
          {call.result.slice(0, 4000)}
        </pre>
      )}
    </div>
  );
}
