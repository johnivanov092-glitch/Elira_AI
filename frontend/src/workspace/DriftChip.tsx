import { Server } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ackDrift, getDriftAlerts, type DriftFact } from "../api/drift";
import { IconButton } from "../ui/Button";

const POLL_MS = 45_000;

const LABELS: Record<string, string> = {
  active_model: "Активная модель",
  server_context_window: "Окно контекста (n_ctx)",
};

/** Show only the filename for a model path; pass other values through. */
function display(key: string, v: string | null | undefined): string {
  if (!v) return "—";
  if (key === "active_model") {
    const parts = v.split("/");
    return parts[parts.length - 1] || v;
  }
  return v;
}

/** Topbar chip next to the context library: badges a red count when the live
 *  server has drifted from what was recorded (active model / context window).
 *  Alerts live here — they are NOT injected into chats. */
export function DriftChip() {
  const [open, setOpen] = useState(false);
  const [drifts, setDrifts] = useState<DriftFact[]>([]);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  function reload() {
    getDriftAlerts().then((a) => setDrifts(a.drifts)).catch(() => { /* offline */ });
  }
  // Poll ambiently + refresh when the popover opens.
  useEffect(() => {
    reload();
    const t = setInterval(reload, POLL_MS);
    return () => clearInterval(t);
  }, []);
  useEffect(() => { if (open) reload(); }, [open]);

  // Close on outside click (same pattern as the other topbar popovers).
  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  const count = drifts.length;

  async function ack() {
    setBusy(true);
    try { await ackDrift(); reload(); } catch { /* offline */ } finally { setBusy(false); }
  }

  return (
    <div ref={ref} className="relative">
      <IconButton
        onClick={() => setOpen((v) => !v)}
        active={open || count > 0}
        title="Сверка с сервером — дрейф активной модели / окна контекста"
        aria-label="Дрейф-детектор сервера"
      >
        <Server size={16} />
      </IconButton>
      {count > 0 && (
        <span className="pointer-events-none absolute -right-1 -top-1 rounded-full bg-danger px-1 text-[9px] font-semibold text-white">
          {count}
        </span>
      )}
      {open && (
        <div className="absolute right-0 top-full z-30 mt-1.5 w-[320px] rounded-lg border border-line bg-card p-1 shadow-lg">
          {count === 0 ? (
            <div className="px-2.5 py-2 text-[11px] text-mut">
              Дрейфа нет — записанные факты сходятся с живым сервером.
            </div>
          ) : (
            <>
              <div className="px-2.5 pt-1.5 pb-1 text-[10px] font-semibold text-danger">
                Расхождение с живым сервером:
              </div>
              {drifts.map((d) => (
                <div key={d.key} className="rounded-md px-2.5 py-1.5">
                  <div className="text-[11.5px] text-tx">{LABELS[d.key] ?? d.key}</div>
                  <div className="text-[10.5px] text-mut">
                    было {display(d.key, d.previous_value)} → стало {display(d.key, d.value)}
                  </div>
                  {d.changed_at && <div className="text-[9.5px] text-mut">{d.changed_at}</div>}
                </div>
              ))}
              <div className="flex justify-end px-2 py-1.5">
                <button
                  type="button"
                  onClick={ack}
                  disabled={busy}
                  className="rounded-md border border-line px-2 py-1 text-[11px] text-t2 hover:bg-hover disabled:opacity-50"
                >
                  {busy ? "…" : "Прочитано"}
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
