// Desktop companion (John 2026-10-15, variant B): Elira stands at the screen edge in a small
// transparent window. A click opens a question box, Ctrl+Shift+E (or the eye button) explains the
// window the user is looking at. Questions go to the same agent run in its own chat «Компаньон».
import { useCallback, useEffect, useRef, useState } from "react";
import { Copy, Eye, MessageSquare, ScanEye, Send, Volume2, X } from "lucide-react";
import MarkdownRenderer from "../components/MarkdownRenderer";
import { createCodeSession, getCodeSession, patchCodeSession, type ReasoningEffort } from "../api/codeAgent";
import { uploadResource, type ResourceAttachment } from "../api/resources";
import type { PermissionMode } from "../api/workflows";
import * as bg from "../workspace/backgroundRuns";
import { deserializeTurns, serializeTurns } from "../workspace/turnCodec";
import { isAcceptedAnswer } from "../workspace/answerLifecycle";
import { speak } from "../workspace/voice";
import { FIGURE_X, FRAMES, HEAD_CLIP, useLiveFace } from "./companionFrames";
import { deriveView, lastAgentTurn, speakingMs, type CompanionState } from "./companionState";
import { captureScreen, inTauri, onExplainScreen, openMainChat, pngFile, setHitRegions, startWindowDrag, type HitRegion } from "./tauriBridge";
import "./companion.css";

const SESSION_KEY = "elira.companion.sessionId";
const TITLE = "Компаньон";

const LABEL: Record<CompanionState, string> = {
  idle: "На связи",
  listening: "Слушаю…",
  looking: "Смотрю на экран…",
  thinking: "Думаю…",
  speaking: "Отвечаю",
};

/** The main window's composer choices (same WebView profile, same localStorage). */
function readPermissionMode(): PermissionMode {
  try {
    const value = localStorage.getItem("elira.permissionMode");
    return value === "ask" || value === "accept_edits" ? value : "bypass";
  } catch {
    return "bypass";
  }
}

function readReasoningEffort(): ReasoningEffort {
  try {
    const value = localStorage.getItem("elira.thinking");
    if (value === "1") return "xhigh";
    return value === "low" || value === "medium" || value === "xhigh" ? value : "none";
  } catch {
    return "none";
  }
}

const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));

export default function CompanionApp() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<bg.RunSnapshot | null>(null);
  const [inputOpen, setInputOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [capturing, setCapturing] = useState(false);
  const [localNotice, setLocalNotice] = useState<string | null>(null);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set());
  const [speaking, setSpeaking] = useState<{ turnId: string; start: number; until: number } | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const rootRef = useRef<HTMLDivElement>(null);
  const sessionRef = useRef<string | null>(null);
  const capturingRef = useRef(false);
  const spokenRef = useRef<string | null>(null);
  const regionsRef = useRef("");
  const press = useRef<{ x: number; y: number; dragging: boolean } | null>(null);

  useEffect(() => {
    document.documentElement.classList.add("companion-page");
    return () => document.documentElement.classList.remove("companion-page");
  }, []);

  const attach = useCallback((id: string) => {
    sessionRef.current = id;
    setSessionId(id);
    bg.setPersist(id, (snap) => {
      if (!snap.turns.some((turn) => turn.kind === "user")) return;
      void patchCodeSession(id, {
        title: TITLE, turns: serializeTurns(snap.turns), contextState: snap.contextState, taskLedger: snap.taskLedger,
      }).catch(() => { /* backend restarting: the next answer saves again */ });
    });
  }, []);

  // Reopen the companion's chat; its last answer stays in the chat, not on screen.
  useEffect(() => {
    let stored: string | null = null;
    try { stored = localStorage.getItem(SESSION_KEY); } catch { /* private mode */ }
    if (!stored) return;
    const id = stored;
    void (async () => {
      try {
        const saved = await getCodeSession(id);
        if (!saved) return;
        bg.seed(id, deserializeTurns(saved.turns), saved.task_ledger || [], saved.context_state || null);
        const last = lastAgentTurn(bg.getSnapshot(id));
        if (last) setHidden(new Set([last.id]));
        attach(id);
      } catch { /* backend not up yet: the first question loads or creates the chat */ }
    })();
  }, [attach]);

  useEffect(() => {
    if (!sessionId) return;
    setSnapshot(bg.getSnapshot(sessionId));
    return bg.subscribe(sessionId, () => setSnapshot(bg.getSnapshot(sessionId)));
  }, [sessionId]);

  /** The chat, freshly loaded: the main window may have added turns to it. */
  const ensureSession = useCallback(async (): Promise<string> => {
    const known = sessionRef.current;
    if (known) {
      if (bg.isRunning(known)) return known;
      const saved = await getCodeSession(known).catch(() => undefined);
      if (saved) {
        bg.seed(known, deserializeTurns(saved.turns), saved.task_ledger || [], saved.context_state || null);
        return known;
      }
      if (saved === undefined) return known; // backend hiccup: keep the chat we have
    }
    const created = await createCodeSession({ title: TITLE });
    try { localStorage.setItem(SESSION_KEY, created.id); } catch { /* private mode */ }
    bg.seed(created.id, []);
    attach(created.id);
    return created.id;
  }, [attach]);

  const ask = useCallback(async (text: string, resources?: ResourceAttachment[]) => {
    const id = await ensureSession();
    bg.send({
      sessionId: id, text, mode: "code", projectRoot: "", model: "auto", resources,
      permissionMode: readPermissionMode(), reasoningEffort: readReasoningEffort(),
    });
  }, [ensureSession]);

  const running = Boolean(sessionId && snapshot?.running);

  const explainScreen = useCallback(async () => {
    if (capturingRef.current || (sessionRef.current && bg.isRunning(sessionRef.current))) return;
    capturingRef.current = true;
    setCapturing(true);
    setInputOpen(false);
    setLocalNotice(null);
    try {
      const shot = await captureScreen();
      const id = await ensureSession();
      const ref = await uploadResource(pngFile(shot), id);
      const where = shot.source === "window" && shot.title ? `окно «${shot.title}»` : "весь экран";
      await ask(`Объясни, что сейчас на экране (${where}). Снимок во вложении.`, [{ ...ref, status: "ready" }]);
    } catch (error) {
      setLocalNotice(`Не получилось посмотреть на экран: ${errorText(error)}`);
    } finally {
      capturingRef.current = false;
      setCapturing(false);
    }
  }, [ask, ensureSession]);

  const explainRef = useRef(explainScreen);
  explainRef.current = explainScreen;
  useEffect(() => {
    let off = () => {};
    let cancelled = false;
    void onExplainScreen(() => void explainRef.current()).then((stop) => {
      if (cancelled) stop(); else off = stop;
    });
    return () => { cancelled = true; off(); };
  }, []);

  const submit = async () => {
    const text = draft.trim();
    if (!text || running) return;
    setDraft("");
    setInputOpen(false);
    setLocalNotice(null);
    try { await ask(text); } catch (error) { setLocalNotice(`Не получилось отправить: ${errorText(error)}`); }
  };

  // A fresh answer is "spoken": typed out with lips moving, then it stays in the bubble.
  useEffect(() => {
    const turn = lastAgentTurn(snapshot);
    if (!turn || turn.running || hidden.has(turn.id) || spokenRef.current === turn.id) return;
    if (!isAcceptedAnswer(turn) || !turn.text.trim()) return;
    spokenRef.current = turn.id;
    const start = Date.now();
    setSpeaking({ turnId: turn.id, start, until: start + speakingMs(turn.text) });
  }, [snapshot, hidden]);

  useEffect(() => {
    if (!speaking) return;
    const timer = window.setInterval(() => {
      const current = Date.now();
      setNow(current);
      if (current >= speaking.until) window.clearInterval(timer);
    }, 40);
    return () => window.clearInterval(timer);
  }, [speaking]);

  const view = deriveView({
    snapshot, capturing, inputOpen, hiddenTurnIds: hidden,
    speakingUntil: speaking && speaking.turnId === lastAgentTurn(snapshot)?.id ? speaking.until : 0,
    now,
  });
  const face = useLiveFace(view.state);
  const notice = view.notice ?? localNotice;
  const typed = view.state === "speaking" && speaking
    ? view.answer.slice(0, Math.ceil(view.answer.length * Math.min(1, (now - speaking.start) / Math.max(1, speaking.until - speaking.start))))
    : view.answer;

  const closeBubble = () => {
    if (view.turnId) setHidden((previous) => new Set(previous).add(view.turnId!));
    setLocalNotice(null);
  };

  // Tell the native side where the companion takes the mouse; elsewhere clicks go through.
  useEffect(() => {
    if (!inTauri()) return;
    const frame = requestAnimationFrame(() => {
      const root = rootRef.current;
      if (!root) return;
      const regions: HitRegion[] = [...root.querySelectorAll<HTMLElement>("[data-hit]")].map((element) => {
        const rect = element.getBoundingClientRect();
        if (element.dataset.hit === "figure") {
          return { x: rect.left + rect.width * FIGURE_X.from, y: rect.top, width: rect.width * (FIGURE_X.to - FIGURE_X.from), height: rect.height };
        }
        return { x: rect.left, y: rect.top, width: rect.width, height: rect.height };
      });
      const key = JSON.stringify(regions.map((region) => [region.x, region.y, region.width, region.height].map(Math.round)));
      if (key === regionsRef.current) return;
      regionsRef.current = key;
      void setHitRegions(regions);
    });
    return () => cancelAnimationFrame(frame);
  });

  const bubble = Boolean(view.answer || notice);
  return (
    <div className={`cmp-root cmp--${view.state}`} ref={rootRef}>
      {bubble ? (
        <div className="cmp-bubble" data-hit="bubble">
          <button className="cmp-icon cmp-close" onClick={closeBubble} aria-label="Закрыть"><X size={14} /></button>
          {view.answer
            ? <div className="cmp-text"><MarkdownRenderer content={typed} />{view.state === "speaking" && <span className="cmp-caret" />}</div>
            : <p className="cmp-text">{notice}</p>}
          <div className="cmp-actions">
            {view.answer && <button className="cmp-chip" onClick={() => void speak(view.answer)}><Volume2 size={13} /> Озвучить</button>}
            {view.answer && <button className="cmp-chip" onClick={() => void navigator.clipboard?.writeText(view.answer)}><Copy size={13} /> Копировать</button>}
            <button className="cmp-chip" onClick={() => void openMainChat(sessionId)}><MessageSquare size={13} /> Открыть в чате</button>
          </div>
        </div>
      ) : inputOpen ? (
        <form className="cmp-input" data-hit="input" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
          <input
            autoFocus value={draft} placeholder="Спроси Элиру…"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Escape") setInputOpen(false); }}
          />
          <button type="submit" className="cmp-icon" disabled={!draft.trim()} aria-label="Отправить"><Send size={15} /></button>
          <button type="button" className="cmp-icon" onClick={() => void explainScreen()} title="Объясни экран (Ctrl+Shift+E)"><ScanEye size={15} /></button>
        </form>
      ) : (view.state === "looking" || view.state === "thinking") && (
        <div className="cmp-thought" data-hit="thought">
          {view.state === "looking" ? <><Eye size={15} /><span>{LABEL.looking}</span></> : <><span className="cmp-dots"><i /><i /><i /></span><span>{LABEL.thinking}</span></>}
        </div>
      )}
      <div
        className="cmp-figure" data-hit="figure" role="button" aria-label="Элира" title="Нажми, чтобы спросить; потяни, чтобы передвинуть"
        onPointerDown={(event) => { if (event.button === 0) press.current = { x: event.screenX, y: event.screenY, dragging: false }; }}
        onPointerMove={(event) => {
          const current = press.current;
          if (!current || current.dragging || !(event.buttons & 1)) return;
          if (Math.hypot(event.screenX - current.x, event.screenY - current.y) > 5) {
            current.dragging = true;
            void startWindowDrag();
          }
        }}
        onPointerUp={() => {
          const current = press.current;
          press.current = null;
          if (current && !current.dragging && !running && !capturing) setInputOpen((open) => !open);
        }}
      >
        <span className="cmp-aura" />
        <div className="cmp-body">
          <img src={FRAMES.idle} alt="" draggable={false} />
          <img src={FRAMES[face]} alt="" draggable={false} style={{ clipPath: HEAD_CLIP }} />
        </div>
      </div>
      <div className="cmp-dock" data-hit="dock">
        <button className="cmp-dock-btn" onClick={() => setInputOpen((open) => !open)} title="Спросить"><MessageSquare size={15} /></button>
        <button className="cmp-dock-btn" onClick={() => void explainScreen()} title="Объясни экран (Ctrl+Shift+E)"><ScanEye size={15} /></button>
      </div>
    </div>
  );
}
