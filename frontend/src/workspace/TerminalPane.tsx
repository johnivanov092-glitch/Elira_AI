import { useCallback, useEffect, useRef, useState } from "react";
import { Bot, ChevronDown, Plus, TerminalSquare, X } from "lucide-react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import {
  closeTerminal, focusTerminal, listTerminals, openTerminal, terminalSocketUrl,
  type TerminalProfile, type TerminalSession,
} from "../api/terminal";
import { cn } from "../ui/cn";

const POLL_MS = 2500;

function themeColor(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

/**
 * Console panel (John 2026-10-12): real shell tabs — PowerShell, cmd, Git Bash.
 * Elira has full control: run_bash(terminal=…) types into these same tabs, so
 * her commands appear here live. `focusId` opens the tab her last command used.
 */
export function TerminalPane({ project, focusId, agentOutput }: { project: string; focusId?: string; agentOutput?: string }) {
  const [sessions, setSessions] = useState<TerminalSession[]>([]);
  const [profiles, setProfiles] = useState<TerminalProfile[]>([]);
  const [current, setCurrent] = useState<string>("");
  const [menu, setMenu] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAgent, setShowAgent] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const state = await listTerminals();
      setSessions(state.sessions);
      setProfiles(state.profiles);
      setError(null);
      setCurrent((id) => (state.sessions.some((s) => s.id === id) ? id : state.sessions.find((s) => s.active)?.id ?? state.sessions[0]?.id ?? ""));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Терминал недоступен");
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (!focusId) return;
    void refresh().then(() => { setCurrent(focusId); setShowAgent(false); });
  }, [focusId, refresh]);

  function select(id: string) {
    setCurrent(id);
    setShowAgent(false);
    void focusTerminal(id).catch(() => undefined);
  }

  async function create(profile: string) {
    setMenu(false);
    try {
      const state = await openTerminal(profile, project, 120, 30);
      setSessions(state.sessions);
      setProfiles(state.profiles);
      select(state.session.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось открыть терминал");
    }
  }

  async function close(id: string) {
    try {
      const state = await closeTerminal(id);
      setSessions(state.sessions);
      if (current === id) setCurrent(state.sessions.at(-1)?.id ?? "");
    } catch {
      void refresh();
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="relative flex items-center gap-0.5 overflow-x-auto border-b border-line px-1.5 py-1">
        {sessions.map((s) => (
          <div
            key={s.id}
            className={cn(
              "group flex shrink-0 items-center gap-1 rounded-md py-0.5 pl-2 pr-1 text-[11px]",
              s.id === current && !showAgent ? "bg-acs text-ac" : "text-mut hover:bg-hover hover:text-t2",
            )}
          >
            <button type="button" onClick={() => select(s.id)} className="flex items-center gap-1" title={s.cwd}>
              {s.title === "Elira" ? <Bot size={12} /> : <TerminalSquare size={12} />}
              <span>{s.title}</span>
              {s.busy && <span className="h-1.5 w-1.5 rounded-full bg-ac" title="Идёт команда" />}
            </button>
            <button type="button" onClick={() => void close(s.id)} aria-label={`Закрыть ${s.title}`} className="grid h-4 w-4 place-items-center rounded opacity-60 hover:bg-surface hover:opacity-100">
              <X size={11} />
            </button>
          </div>
        ))}
        {agentOutput && (
          <button
            type="button"
            onClick={() => setShowAgent(true)}
            className={cn("shrink-0 rounded-md px-2 py-0.5 text-[11px]", showAgent ? "bg-acs text-ac" : "text-mut hover:bg-hover hover:text-t2")}
            title="Вывод последней команды Elira вне терминала"
          >
            Вывод Elira
          </button>
        )}
        <button
          type="button"
          onClick={() => setMenu((open) => !open)}
          className="ml-0.5 flex shrink-0 items-center gap-0.5 rounded-md px-1.5 py-0.5 text-mut hover:bg-hover hover:text-t2"
          aria-label="Новый терминал"
          title="Новый терминал"
        >
          <Plus size={13} /><ChevronDown size={11} />
        </button>
        {menu && (
          <div className="absolute left-2 top-full z-20 mt-1 min-w-[150px] rounded-lg border border-line bg-card py-1 shadow-lg">
            {profiles.map((p) => (
              <button key={p.id} type="button" onClick={() => void create(p.id)} className="block w-full px-3 py-1.5 text-left text-[12px] text-t2 hover:bg-hover hover:text-tx">
                {p.title}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="relative min-h-0 flex-1">
        {showAgent && agentOutput ? (
          <pre className="h-full overflow-auto whitespace-pre-wrap px-3 py-3 font-mono text-[11.5px] leading-relaxed text-t2">{agentOutput}</pre>
        ) : sessions.length === 0 ? (
          <div className="grid h-full place-items-center px-4 text-center">
            <div>
              <p className="mb-3 text-[12.5px] text-mut">{error ?? "Терминал не открыт. Elira может работать в нём вместе с вами."}</p>
              <div className="flex flex-wrap justify-center gap-1.5">
                {profiles.map((p) => (
                  <button key={p.id} type="button" onClick={() => void create(p.id)} className="rounded-lg border border-line px-3 py-1.5 text-[11.5px] text-t2 hover:bg-hover hover:text-tx">
                    {p.title}
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : null}
        {/* Every tab stays mounted: switching keeps its screen, scroll and input. */}
        {sessions.map((s) => (
          <XtermView key={s.id} id={s.id} visible={!showAgent && s.id === current} />
        ))}
      </div>
    </div>
  );
}

function XtermView({ id, visible }: { id: string; visible: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const termRef = useRef<Terminal | null>(null);

  useEffect(() => {
    if (!host.current) return;
    const term = new Terminal({
      fontFamily: "'JetBrains Mono', Consolas, monospace",
      fontSize: 12,
      cursorBlink: true,
      scrollback: 5000,
      theme: {
        background: themeColor("--color-side", "#101216"),
        foreground: themeColor("--color-tx", "#e7e7ec"),
        cursor: themeColor("--color-ac", "#8b93f8"),
        selectionBackground: themeColor("--color-acl", "rgba(139,147,248,0.5)"),
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host.current);
    termRef.current = term;
    fitRef.current = fit;
    const socket = new WebSocket(terminalSocketUrl(id));
    const sendSize = () => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
    };
    socket.onopen = () => { try { fit.fit(); } catch { /* hidden tab: sized when shown */ } sendSize(); };
    socket.onmessage = (event) => term.write(typeof event.data === "string" ? event.data : "");
    socket.onclose = () => term.write("\r\n\x1b[90m[терминал закрыт]\x1b[0m\r\n");
    const input = term.onData((data) => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "input", data }));
    });
    const resized = term.onResize(sendSize);
    const observer = new ResizeObserver(() => { try { fit.fit(); } catch { /* not laid out yet */ } });
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      input.dispose();
      resized.dispose();
      socket.onclose = null;
      socket.close();
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, [id]);

  useEffect(() => {
    if (!visible) return;
    const frame = requestAnimationFrame(() => {
      try { fitRef.current?.fit(); } catch { /* not laid out yet */ }
      termRef.current?.focus();
    });
    return () => cancelAnimationFrame(frame);
  }, [visible]);

  return <div ref={host} className={cn("absolute inset-0 px-1.5 py-1", !visible && "invisible")} />;
}
