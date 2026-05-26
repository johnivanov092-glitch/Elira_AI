/**
 * SpotlightOverlay — Cmd/Ctrl+K global navigation.
 *
 * Modal full-screen overlay that fans a single query across chats,
 * code-agent sessions, RAG memory and library files (one backend
 * call to /api/spotlight/search). Renders results as four grouped
 * sections; arrow keys move the selection across the flat ordered
 * list of all hits; Enter activates the highlighted one.
 *
 * Owned by EliraChatShell — that's where the actual navigation
 * handlers (openChat, switchToCodeWithSession, switchToFilesTab)
 * live. Spotlight itself does no navigation; it only emits picks.
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { MessageSquare, Code2, Brain, FileText, Loader2, Search, X } from "lucide-react";
import {
  spotlightSearch,
  type SpotlightHit,
  type SpotlightResponse,
} from "../api/spotlight";

const DEBOUNCE_MS = 200;

type Props = {
  open: boolean;
  onClose: () => void;
  onPick: (hit: SpotlightHit) => void;
};

const EMPTY_RESPONSE: SpotlightResponse = {
  query: "",
  chats: [],
  sessions: [],
  rag: [],
  files: [],
  total: 0,
};

/** Flatten the grouped response into a single ordered list — needed
 * for arrow-key navigation. Order matches visual order in the UI. */
function flattenHits(resp: SpotlightResponse): SpotlightHit[] {
  return [...resp.chats, ...resp.sessions, ...resp.rag, ...resp.files];
}

function iconFor(type: SpotlightHit["type"]) {
  switch (type) {
    case "chat":
      return MessageSquare;
    case "session":
      return Code2;
    case "rag":
      return Brain;
    case "file":
      return FileText;
  }
}

function labelFor(type: SpotlightHit["type"]): string {
  switch (type) {
    case "chat":
      return "Чаты";
    case "session":
      return "Code-agent сессии";
    case "rag":
      return "Память (RAG)";
    case "file":
      return "Файлы библиотеки";
  }
}

export default function SpotlightOverlay({ open, onClose, onPick }: Props) {
  const [query, setQuery] = useState("");
  const [response, setResponse] = useState<SpotlightResponse>(EMPTY_RESPONSE);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Reset on open: clear stale query/results, focus the input.
  useEffect(() => {
    if (open) {
      setQuery("");
      setResponse(EMPTY_RESPONSE);
      setError(null);
      setSelectedIdx(0);
      // Focus after the modal has mounted
      requestAnimationFrame(() => inputRef.current?.focus());
    }
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, [open]);

  // Debounced search on query change.
  useEffect(() => {
    if (!open) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const trimmed = query.trim();
    if (trimmed.length < 2) {
      setResponse(EMPTY_RESPONSE);
      setLoading(false);
      setError(null);
      return;
    }
    debounceRef.current = setTimeout(async () => {
      // Cancel any in-flight call before kicking off a new one
      abortRef.current?.abort();
      abortRef.current = new AbortController();
      setLoading(true);
      setError(null);
      try {
        const data = await spotlightSearch(trimmed);
        setResponse(data);
        setSelectedIdx(0);
      } catch (e) {
        if ((e as DOMException)?.name === "AbortError") return;
        setError(String((e as Error)?.message || e));
        setResponse(EMPTY_RESPONSE);
      } finally {
        setLoading(false);
      }
    }, DEBOUNCE_MS);
  }, [query, open]);

  const flat = useMemo(() => flattenHits(response), [response]);

  const handleKeyDown = useCallback(
    (e: ReactKeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (!flat.length) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIdx((cur) => (cur + 1) % flat.length);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIdx((cur) => (cur - 1 + flat.length) % flat.length);
      } else if (e.key === "Enter") {
        e.preventDefault();
        const hit = flat[selectedIdx];
        if (hit) {
          onPick(hit);
          onClose();
        }
      }
    },
    [flat, selectedIdx, onPick, onClose],
  );

  if (!open) return null;

  // Index → flatIdx, so each group's items know their selection index.
  let cursor = 0;

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        background: "rgba(0,0,0,0.55)",
        display: "flex",
        justifyContent: "center",
        paddingTop: "12vh",
        backdropFilter: "blur(2px)",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(720px, 92vw)",
          maxHeight: "76vh",
          display: "flex",
          flexDirection: "column",
          background: "var(--bg-surface, #1a1a1a)",
          border: "1px solid var(--border, #333)",
          borderRadius: 12,
          boxShadow: "0 18px 60px rgba(0,0,0,0.6)",
          overflow: "hidden",
        }}
      >
        {/* Input row */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "12px 14px",
            borderBottom: "1px solid var(--border, #333)",
          }}
        >
          {loading ? (
            <Loader2 size={16} className="lucide-spin" style={{ color: "var(--text-muted)" }} />
          ) : (
            <Search size={16} style={{ color: "var(--text-muted)" }} />
          )}
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Найти чат, сессию, заметку в памяти, файл..."
            style={{
              flex: 1,
              background: "transparent",
              border: "none",
              outline: "none",
              fontSize: 14,
              color: "var(--text-primary)",
            }}
          />
          <kbd style={{ fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "var(--bg-elevated, #222)", color: "var(--text-muted)", border: "1px solid var(--border)" }}>
            esc
          </kbd>
          <button
            onClick={onClose}
            style={{ background: "transparent", border: "none", cursor: "pointer", color: "var(--text-muted)", padding: 2 }}
            aria-label="Close"
          >
            <X size={14} />
          </button>
        </div>

        {/* Results */}
        <div style={{ overflowY: "auto", padding: "6px 0" }}>
          {error && (
            <div style={{ padding: "12px 16px", fontSize: 12, color: "#ff6b6b" }}>
              {error}
            </div>
          )}
          {!error && query.trim().length < 2 && (
            <div style={{ padding: "20px 16px", fontSize: 12, color: "var(--text-muted)" }}>
              Введи минимум 2 символа. Поиск идёт по заголовкам и содержимому
              чатов, сессий code-агента, заметкам в памяти и файлам.
            </div>
          )}
          {!error && query.trim().length >= 2 && !loading && response.total === 0 && (
            <div style={{ padding: "20px 16px", fontSize: 12, color: "var(--text-muted)" }}>
              Ничего не найдено по «{query.trim()}».
            </div>
          )}
          {([
            { key: "chats", items: response.chats },
            { key: "sessions", items: response.sessions },
            { key: "rag", items: response.rag },
            { key: "files", items: response.files },
          ] as const).map((group) => {
            if (!group.items.length) return null;
            const Icon = iconFor(group.items[0].type);
            return (
              <div key={group.key} style={{ marginBottom: 6 }}>
                <div
                  style={{
                    padding: "8px 16px 4px",
                    fontSize: 10,
                    fontWeight: 600,
                    letterSpacing: 0.5,
                    textTransform: "uppercase",
                    color: "var(--text-muted)",
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                >
                  <Icon size={12} />
                  {labelFor(group.items[0].type)}
                  <span style={{ fontWeight: 400 }}>· {group.items.length}</span>
                </div>
                {group.items.map((hit) => {
                  const idx = cursor++;
                  const active = idx === selectedIdx;
                  return (
                    <button
                      key={`${hit.type}-${hit.id}`}
                      onClick={() => {
                        onPick(hit);
                        onClose();
                      }}
                      onMouseEnter={() => setSelectedIdx(idx)}
                      style={{
                        width: "100%",
                        textAlign: "left",
                        padding: "8px 16px",
                        background: active ? "var(--accent-soft, rgba(99,102,241,0.18))" : "transparent",
                        border: "none",
                        cursor: "pointer",
                        display: "flex",
                        flexDirection: "column",
                        gap: 2,
                        color: "var(--text-primary)",
                        borderLeft: active ? "2px solid var(--accent, #6366f1)" : "2px solid transparent",
                      }}
                    >
                      <div style={{ fontSize: 13, fontWeight: 500 }}>{hit.title}</div>
                      {hit.snippet && (
                        <div
                          style={{
                            fontSize: 11,
                            color: "var(--text-muted)",
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                          }}
                        >
                          {hit.snippet}
                        </div>
                      )}
                    </button>
                  );
                })}
              </div>
            );
          })}
        </div>

        {/* Footer hints */}
        <div
          style={{
            padding: "8px 14px",
            borderTop: "1px solid var(--border, #333)",
            fontSize: 10,
            color: "var(--text-muted)",
            display: "flex",
            gap: 14,
          }}
        >
          <span><kbd style={kbdStyle}>↑↓</kbd> навигация</span>
          <span><kbd style={kbdStyle}>↵</kbd> открыть</span>
          <span><kbd style={kbdStyle}>esc</kbd> закрыть</span>
        </div>
      </div>
    </div>
  );
}

const kbdStyle: React.CSSProperties = {
  padding: "1px 5px",
  borderRadius: 3,
  background: "var(--bg-elevated, #222)",
  border: "1px solid var(--border)",
  fontSize: 10,
};
