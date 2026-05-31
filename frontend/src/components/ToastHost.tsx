/**
 * Toast host — non-blocking notifications.
 *
 * Replaces blocking `alert()` calls everywhere in the app. A toast
 * slides in from the bottom-right, auto-dismisses after 4s (longer
 * for errors), and can be dismissed manually by clicking the X.
 *
 * Usage:
 *   import { toast } from "./ToastHost";
 *   toast.success("Готово");
 *   toast.error("Не вышло: ...");
 *   toast.info("FYI");
 *
 * The `<ToastHost />` component must be mounted exactly once at the
 * app root — it owns the in-memory queue and the render surface.
 * The exported `toast` object is just a thin wrapper that pushes to
 * a module-level event emitter; this avoids React context plumbing
 * for what is fundamentally a global side-channel concern.
 */
import { useEffect, useState } from "react";
import { CheckCircle2, AlertTriangle, Info, X } from "lucide-react";

export type ToastKind = "success" | "error" | "info";

type ToastItem = {
  id: number;
  kind: ToastKind;
  text: string;
  ttlMs: number;
};

type Listener = (items: ToastItem[]) => void;

let _nextId = 1;
let _items: ToastItem[] = [];
const _listeners: Set<Listener> = new Set();

function emit() {
  for (const l of _listeners) l(_items);
}

function push(kind: ToastKind, text: string, ttlMs?: number): number {
  const id = _nextId++;
  const item: ToastItem = {
    id,
    kind,
    text,
    ttlMs: ttlMs ?? (kind === "error" ? 7000 : 4000),
  };
  _items = [..._items, item];
  emit();
  // Schedule auto-dismiss
  if (item.ttlMs > 0) {
    setTimeout(() => dismiss(id), item.ttlMs);
  }
  return id;
}

function dismiss(id: number) {
  const before = _items.length;
  _items = _items.filter((t) => t.id !== id);
  if (_items.length !== before) emit();
}

export const toast = {
  success: (text: string, ttlMs?: number) => push("success", text, ttlMs),
  error: (text: string, ttlMs?: number) => push("error", text, ttlMs),
  info: (text: string, ttlMs?: number) => push("info", text, ttlMs),
  dismiss,
};

function iconFor(kind: ToastKind) {
  switch (kind) {
    case "success":
      return CheckCircle2;
    case "error":
      return AlertTriangle;
    case "info":
      return Info;
  }
}

function colorsFor(kind: ToastKind): { border: string; accent: string } {
  switch (kind) {
    case "success":
      return { border: "rgba(74,222,128,0.5)", accent: "#4ade80" };
    case "error":
      return { border: "rgba(255,107,107,0.5)", accent: "#ff6b6b" };
    case "info":
      return { border: "rgba(99,102,241,0.5)", accent: "#6366f1" };
  }
}

export default function ToastHost() {
  const [items, setItems] = useState<ToastItem[]>(_items);

  useEffect(() => {
    const listener: Listener = (next) => setItems(next);
    _listeners.add(listener);
    // Sync once on mount in case toasts were emitted before this
    // component finished mounting.
    listener(_items);
    return () => {
      _listeners.delete(listener);
    };
  }, []);

  if (!items.length) return null;

  return (
    <div
      style={{
        position: "fixed",
        right: 16,
        bottom: 16,
        zIndex: 2000,
        display: "flex",
        flexDirection: "column",
        gap: 8,
        pointerEvents: "none",
        maxWidth: "min(420px, calc(100vw - 32px))",
      }}
    >
      {items.map((t) => {
        const Icon = iconFor(t.kind);
        const { border, accent } = colorsFor(t.kind);
        return (
          <div
            key={t.id}
            role="status"
            style={{
              pointerEvents: "auto",
              display: "flex",
              alignItems: "flex-start",
              gap: 10,
              padding: "10px 12px",
              borderRadius: 8,
              background: "var(--bg-surface, #1a1a1a)",
              border: `1px solid ${border}`,
              boxShadow: "0 8px 28px rgba(0,0,0,0.45)",
              animation: "elira-toast-slide-in 180ms ease-out",
              minWidth: 260,
            }}
          >
            <Icon size={16} style={{ color: accent, flexShrink: 0, marginTop: 1 }} />
            <div
              style={{
                flex: 1,
                fontSize: 12,
                lineHeight: 1.45,
                color: "var(--text-primary)",
                wordBreak: "break-word",
                whiteSpace: "pre-wrap",
              }}
            >
              {t.text}
            </div>
            <button
              onClick={() => dismiss(t.id)}
              style={{
                background: "transparent",
                border: "none",
                color: "var(--text-muted)",
                cursor: "pointer",
                padding: 0,
                flexShrink: 0,
              }}
              aria-label="Закрыть"
            >
              <X size={14} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
