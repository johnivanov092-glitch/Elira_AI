import { useMemo, useState } from "react";
import { ChevronRight, Loader2 } from "lucide-react";
import type { CodeAgentToolCall } from "../api/codeAgent";
import { toolIcon } from "./toolIcon";
import { cn } from "../ui/cn";
import "./ToolCallGroup.css";

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

// Terminal conditions remain available inside the action history.
const FAILED_STOP_LABELS: Record<string, string> = {
  timeout: "таймаут",
  context_limit: "переполнен контекст",
  error: "ошибка",
  cancelled: "остановлено",
};

// Presentation of actual tool_started events, not a timer or inferred progress.
const ACTIVE_LABELS: Record<string, string> = {
  read_file: "Читаю файл",
  write_file: "Записываю файл",
  edit_file: "Изменяю файл",
  glob: "Ищу файлы в проекте",
  grep: "Ищу в проекте",
  run_bash: "Выполняю команду",
  sandbox_run: "Выполняю код",
  web_search: "Ищу в интернете",
  web_fetch: "Читаю страницу",
  browser: "Работаю в браузере",
  computer: "Работаю с приложением",
  recall: "Ищу в памяти",
  todo_update: "Обновляю план",
  delegate_task: "Передаю задачу агенту",
  capability_load: "Подключаю инструменты",
  runtime_control: "Настраиваю рабочие инструменты",
  ssh_read: "Читаю файл на сервере",
  ssh_write: "Записываю файл на сервере",
  ssh_run: "Выполняю команду на сервере",
  ssh_list_hosts: "Проверяю доступные серверы",
  resource_publish: "Подготавливаю файл для скачивания",
};

function activeLabel(tool: string): string {
  // Workflow step events already carry their own human-readable label.
  if (tool.startsWith("Шаг ")) return tool;
  return ACTIVE_LABELS[tool] ?? "Вызываю инструмент";
}

// Only an explicit receipt confirms success; legacy/unknown results stay neutral.
function StatusDot({ ok, exitCode }: { ok?: boolean; exitCode?: number }) {
  const cls =
    ok === false ? "bg-danger" : ok === true ? "bg-success" : "bg-mut";
  return <span className={cn("h-[7px] w-[7px] shrink-0 rounded-full", cls)} title={exitCode != null ? `Код выхода: ${exitCode}` : undefined} aria-hidden />;
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
  const errors = useMemo(() => calls.filter((c) => c.ok === false), [calls]);
  const runs = useMemo(() => groupRuns(calls), [calls]);
  // Stop live animation at a terminal event. Diagnostics belong to the history,
  // not the collapsed action label; the turn owns the overall answer status.
  const failedLabel = stopReason ? FAILED_STOP_LABELS[stopReason] : undefined;
  const liveTool = failedLabel ? undefined : activeTool;

  if (calls.length === 0 && !activeTool) return null;

  return (
    <details className="tool-activity tool-activity-disclosure my-2.5 min-w-0">
      <summary className="flex cursor-pointer items-center gap-2 rounded-lg py-1.5 text-[12.5px] text-mut hover:text-t2">
        <ChevronRight size={13} className="tool-activity-chevron shrink-0" aria-hidden />
        <span className="shrink-0 font-medium">Действия</span>
        {liveTool && (
          <span role="status" aria-live="polite" aria-atomic="true" className="flex min-w-0 items-center gap-2 text-t2">
            <Loader2 size={13} className="tool-activity-spinner shrink-0" aria-hidden />
            <span className="tool-activity-live break-words">{activeLabel(liveTool)}</span>
          </span>
        )}
      </summary>
      <div className="mt-1 max-h-[46vh] overflow-y-auto rounded-lg border border-line bg-surface">
        <div className="flex flex-wrap gap-x-2 gap-y-1 px-3.5 py-2 text-[11.5px] text-mut">
          <span>{calls.length} {plural(calls.length)}</span>
          {(failedLabel || errors.length > 0) && (
            <span className="text-danger">
              {failedLabel ? `${failedLabel} · задача не завершена` : `${errors.length} ${errPlural(errors.length)}`}
            </span>
          )}
        </div>
        {runs.map((run, ri) =>
          run.items.length >= 3
            ? <RunGroup key={ri} run={run} />
            : run.items.map(({ call, idx }) => <ToolRow key={idx} call={call} />),
        )}
        {liveTool && <div className="border-t border-line px-3.5 py-2 text-[11.5px] text-t2">{liveTool} · результат ещё не получен</div>}
      </div>
    </details>
  );
}

// Consecutive calls remain grouped inside the optional history.
function RunGroup({ run }: { run: Run }) {
  const Icon = toolIcon(run.tool);
  const errs = run.items.filter((x) => x.call.ok === false).length;
  return (
    <details className="tool-activity-disclosure border-t border-line">
      <summary className="flex cursor-pointer items-center gap-2.5 px-3.5 py-2 text-[12.5px] hover:bg-hover">
        <ChevronRight size={13} className="tool-activity-chevron shrink-0 text-mut" aria-hidden />
        <Icon size={14} className="shrink-0 text-t2" aria-hidden />
        <span className="min-w-0 truncate font-medium">{run.tool}</span>
        <span className="rounded border border-line bg-card px-1 text-[10.5px] text-mut">×{run.items.length}</span>
        {errs > 0 && <span className="text-[11.5px] text-danger">{errs} {errPlural(errs)}</span>}
      </summary>
      {run.items.map(({ call, idx }) => <ToolRow key={idx} call={call} nested />)}
    </details>
  );
}

function ToolRow({ call, nested }: { call: CodeAgentToolCall; nested?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const Icon = toolIcon(call.tool);
  const err = call.ok === false;
  const skillLoad = call.tool === "runtime_control" && call.arguments.operation === "skill_load";
  const label = skillLoad
    ? err ? "Ошибка загрузки навыка" : call.skill ? "Навык загружен" : "Загрузка навыка"
    : call.tool === "runtime_control" && call.arguments.operation === "skill_list" ? "Каталог навыков" : call.tool;
  const detail = skillLoad
    ? [call.skill?.title ?? String(call.arguments.name ?? ""), call.skill?.reason].filter(Boolean).join(" · ")
    : shortArg(call.arguments);
  return (
    <details
      className="tool-activity-disclosure border-t border-line"
      onToggle={(event) => setExpanded(event.currentTarget.open)}
      style={err ? { background: "color-mix(in srgb, var(--color-danger) 9%, transparent)" } : undefined}
    >
      <summary
        className={cn(
          "flex cursor-pointer items-center gap-2.5 px-3.5 py-2 text-[12.5px] hover:bg-hover",
          nested && "pl-7",
        )}
      >
        <StatusDot ok={call.ok} exitCode={call.exit_code} />
        <Icon size={14} className="shrink-0 text-t2" aria-hidden />
        <span className="min-w-0 flex-1 truncate">
          <span className={cn("font-medium", err && "text-danger")}>{label}</span>{" "}
          <span title={detail} className={cn("font-mono text-[11.5px]", err ? "text-danger" : "text-t2")}>{detail}</span>
        </span>
        {call.auto_verifier && (
          <span className="shrink-0 rounded border border-line px-1 text-[10px] text-t2" title="Проверка выполнена средой исполнения">runtime</span>
        )}
        <ChevronRight size={13} className="tool-activity-chevron shrink-0 text-mut" aria-hidden />
      </summary>
      {expanded && <div className="mx-3.5 mb-3 min-w-0 space-y-2 text-[11.5px]">
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-mut">
          <span>Инструмент: {call.tool}</span>
          <span>Шаг {call.step}</span>
          {call.exit_code != null && <span>Код выхода: {call.exit_code}</span>}
        </div>
        <div>
          <div className="mb-1 text-t2">Аргументы</div>
          <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-card p-3 font-mono text-t2">{JSON.stringify(call.arguments, null, 2)}</pre>
        </div>
        <div>
          <div className="mb-1 text-t2">Результат</div>
          <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-card p-3 font-mono text-t2">{call.result || "Пустой результат"}</pre>
        </div>
      </div>}
    </details>
  );
}
