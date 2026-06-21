import { useState } from "react";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import type { CodeAgentToolCall } from "../api/codeAgent";
import { toolIcon } from "./toolIcon";

function shortArg(args: Record<string, unknown>): string {
  for (const key of ["path", "command", "query", "pattern", "url", "task", "code", "host"]) {
    const value = args[key];
    if (typeof value === "string" && value) {
      const flat = value.replace(/\s+/g, " ").trim();
      return flat.length > 60 ? flat.slice(0, 60) + "…" : flat;
    }
  }
  return "";
}

function plural(n: number): string {
  const a = n % 10;
  const b = n % 100;
  if (a === 1 && b !== 11) return "вызов инструмента";
  if (a >= 2 && a <= 4 && (b < 10 || b >= 20)) return "вызова инструментов";
  return "вызовов инструментов";
}

export function ToolCallGroup({ calls, activeTool }: { calls: CodeAgentToolCall[]; activeTool?: string }) {
  const [open, setOpen] = useState(true);
  if (calls.length === 0 && !activeTool) return null;

  return (
    <div className="my-2.5 overflow-hidden rounded-xl border border-line bg-surface">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-[12.5px]"
      >
        {open ? <ChevronDown size={13} className="text-mut" /> : <ChevronRight size={13} className="text-mut" />}
        <span className="font-medium">{calls.length} {plural(calls.length)}</span>
        <span className="flex-1 truncate font-mono text-[11px] text-mut">{calls.map((c) => c.tool).join(" · ")}</span>
      </button>
      {open && (
        <div>
          {calls.map((call, i) => <ToolRow key={i} call={call} />)}
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

function ToolRow({ call }: { call: CodeAgentToolCall }) {
  const [exp, setExp] = useState(false);
  const Icon = toolIcon(call.tool);
  return (
    <div className="border-t border-line">
      <button
        type="button"
        onClick={() => setExp((e) => !e)}
        className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-[12.5px] hover:bg-hover"
      >
        <span className="grid h-[23px] w-[23px] shrink-0 place-items-center rounded-md border border-line bg-surface text-t2">
          <Icon size={14} />
        </span>
        <span className="flex-1 truncate">
          <span className="font-medium">{call.tool}</span> <span className="text-t2">{shortArg(call.arguments)}</span>
        </span>
      </button>
      {exp && call.result && (
        <pre className="mx-3.5 mb-3 max-h-60 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-[#121216] p-3 font-mono text-[11.5px] text-t2">
          {call.result.slice(0, 4000)}
        </pre>
      )}
    </div>
  );
}
