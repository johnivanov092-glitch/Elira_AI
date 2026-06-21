import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Loader2, TerminalSquare, X } from "lucide-react";
import { executeTerminal, getTerminalCwd } from "../api/terminal";
import { cn } from "../ui/cn";

type Line = { cmd?: string; out?: string; err?: boolean };

/** Terminal bottom dock (v4): real exec via /api/terminal. Sits above the
 *  composer. Blocked/destructive commands are refused server-side. */
export function TerminalDock({ onClose }: { onClose: () => void }) {
  const [cwd, setCwd] = useState("");
  const [lines, setLines] = useState<Line[]>([]);
  const [cmd, setCmd] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getTerminalCwd().then((r) => { if (r?.cwd) setCwd(r.cwd); }).catch(() => { /* offline */ });
  }, []);
  useEffect(() => { endRef.current?.scrollIntoView(); }, [lines]);

  async function run() {
    const c = cmd.trim();
    if (!c || busy) return;
    setLines((p) => [...p, { cmd: c }]);
    setCmd("");
    setBusy(true);
    try {
      const r = await executeTerminal({ command: c, cwd: cwd || undefined });
      if (r.cwd) setCwd(r.cwd);
      const out = r.output ?? [r.stdout, r.stderr].filter(Boolean).join("\n") ?? "";
      const text = String(out || r.error || "").trimEnd();
      setLines((p) => [...p, { out: text || "(нет вывода)", err: !!r.error || (r.returncode ?? 0) !== 0 }]);
    } catch (e) {
      setLines((p) => [...p, { out: String((e as Error).message), err: true }]);
    } finally {
      setBusy(false);
    }
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") { e.preventDefault(); run(); }
  }

  return (
    <div className="flex h-[240px] flex-col border-t border-line bg-side">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2 text-[12px]">
        <TerminalSquare size={14} className="text-ac" />
        <span className="truncate font-mono text-[11px] text-t2">{cwd || "терминал"}</span>
        <button type="button" onClick={onClose} aria-label="Закрыть терминал" className="ml-auto grid h-6 w-6 place-items-center rounded-md border border-line text-t2 hover:bg-hover hover:text-tx">
          <X size={14} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto px-3 py-2 font-mono text-[11.5px] leading-relaxed">
        {lines.length === 0 && <div className="text-mut">Введи команду. Опасные команды блокируются на сервере.</div>}
        {lines.map((l, i) =>
          l.cmd !== undefined ? (
            <div key={i} className="text-ac">$ {l.cmd}</div>
          ) : (
            <div key={i} className={cn("whitespace-pre-wrap", l.err ? "text-[#c98a8a]" : "text-t2")}>{l.out}</div>
          ),
        )}
        <div ref={endRef} />
      </div>
      <div className="flex items-center gap-2 border-t border-line px-3 py-2">
        <span className="font-mono text-[12px] text-ac">$</span>
        <input
          value={cmd}
          onChange={(e) => setCmd(e.target.value)}
          onKeyDown={onKey}
          placeholder="команда…"
          className="flex-1 bg-transparent font-mono text-[12px] text-tx outline-none placeholder:text-mut"
        />
        {busy && <Loader2 size={13} className="animate-spin text-mut" />}
      </div>
    </div>
  );
}
