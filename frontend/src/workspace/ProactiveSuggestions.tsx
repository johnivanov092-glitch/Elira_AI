/**
 * Living Persona step C — scheduled-proactivity surfacing.
 *
 * Polls the pending-suggestions queue (the scheduled check-in writes here, since
 * there's no open chat to deliver into) and surfaces items two ways:
 *   - kind "digest"     → in-app toast + best-effort desktop OS notification, marked read once shown.
 *   - kind "enable_ask" → a small banner with Включить / Не надо (the per-trigger first-fire gate).
 *
 * The desktop notification uses the Tauri notification plugin via a runtime
 * (variable) import so it stays out of the type graph — it lights up only after
 * the plugin is added and the Tauri shell is rebuilt; until then the in-app
 * toast/banner is the guaranteed path.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { request } from "../api/client";
import { toast } from "../components/ToastHost";

type Suggestion = {
  id: number;
  trigger_id: string;
  kind: "digest" | "enable_ask" | string;
  title: string;
  body: string;
};

const POLL_MS = 60000;

async function fireDesktopNotification(title: string, body: string): Promise<void> {
  try {
    // Variable specifier → not statically resolved by tsc/Vite. Activates once
    // `@tauri-apps/plugin-notification` is installed and the shell is rebuilt.
    const spec = "@tauri-apps/plugin-notification";
    const mod: { isPermissionGranted: () => Promise<boolean>; requestPermission: () => Promise<string>; sendNotification: (o: { title: string; body: string }) => void } =
      await import(/* @vite-ignore */ spec);
    let granted = await mod.isPermissionGranted();
    if (!granted) granted = (await mod.requestPermission()) === "granted";
    if (granted) mod.sendNotification({ title, body });
  } catch {
    /* plugin unavailable (no Tauri rebuild yet) — the in-app toast already fired */
  }
}

export default function ProactiveSuggestions() {
  const [asks, setAsks] = useState<Suggestion[]>([]);
  const shownDigests = useRef<Set<number>>(new Set());

  const poll = useCallback(async () => {
    let data: { suggestions?: Suggestion[] };
    try {
      data = await request<{ suggestions?: Suggestion[] }>("/api/persona/suggestions?only_unread=true");
    } catch {
      return; // offline / backend down — try again next tick
    }
    const items = Array.isArray(data?.suggestions) ? data.suggestions : [];
    const nextAsks: Suggestion[] = [];
    for (const s of items) {
      if (s.kind === "enable_ask") {
        nextAsks.push(s);
      } else if (!shownDigests.current.has(s.id)) {
        shownDigests.current.add(s.id);
        toast.info(`${s.title}: ${s.body}`, 8000);
        void fireDesktopNotification(s.title, s.body);
        void request(`/api/persona/suggestions/${s.id}/read`, { method: "POST" }).catch(() => {});
      }
    }
    setAsks(nextAsks);
  }, []);

  useEffect(() => {
    void poll();
    const timer = setInterval(() => void poll(), POLL_MS);
    const onFocus = () => void poll();
    window.addEventListener("focus", onFocus);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [poll]);

  const respond = useCallback(async (id: number, decision: "approve" | "deny") => {
    try {
      await request(`/api/persona/suggestions/${id}/respond?decision=${decision}`, { method: "POST" });
    } catch {
      /* leave it; next poll re-surfaces */
    }
    setAsks((a) => a.filter((s) => s.id !== id));
    if (decision === "approve") toast.success("Включено. Elira будет присылать ежедневный чек-ин.");
  }, []);

  if (!asks.length) return null;

  return (
    <div
      style={{
        position: "fixed",
        left: 16,
        bottom: 16,
        zIndex: 2000,
        display: "flex",
        flexDirection: "column",
        gap: 8,
        maxWidth: "min(420px, calc(100vw - 32px))",
      }}
    >
      {asks.map((s) => (
        <div
          key={s.id}
          role="status"
          style={{
            padding: "12px 14px",
            borderRadius: 8,
            background: "var(--bg-surface, #1a1a1a)",
            border: "1px solid rgba(99,102,241,0.5)",
            boxShadow: "0 8px 28px rgba(0,0,0,0.45)",
          }}
        >
          <div style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-primary)", marginBottom: 4 }}>{s.title}</div>
          <div style={{ fontSize: 12, lineHeight: 1.45, color: "var(--text-muted)", marginBottom: 10, whiteSpace: "pre-wrap" }}>{s.body}</div>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              onClick={() => void respond(s.id, "approve")}
              style={{ fontSize: 12, padding: "5px 12px", borderRadius: 6, border: "none", background: "#6366f1", color: "#fff", cursor: "pointer" }}
            >
              Включить
            </button>
            <button
              onClick={() => void respond(s.id, "deny")}
              style={{ fontSize: 12, padding: "5px 12px", borderRadius: 6, border: "1px solid var(--border, #333)", background: "transparent", color: "var(--text-muted)", cursor: "pointer" }}
            >
              Не надо
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
