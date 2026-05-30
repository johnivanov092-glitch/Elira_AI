/**
 * McpConfigDialog — manages installed MCP servers.
 *
 * Each row is one server config (id + command + args + env + enabled).
 * Add a server, edit any field, save (one POST replaces the whole
 * list atomically). Per-row Start/Stop/Restart buttons drive the
 * live subprocess on the backend.
 *
 * Why a custom UI instead of a generic JSON editor: 90% of users
 * just paste a 3-line npx invocation. Treating it as structured
 * fields lets us validate and explain each piece.
 */
import { useEffect, useState, type KeyboardEvent } from "react";
import {
  Loader2,
  Play,
  Plug,
  Plus,
  RefreshCw,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { api } from "../api/ide";
import type { McpServerSpec } from "../api/codeAgent";
import { toast } from "./ToastHost";

type Props = {
  open: boolean;
  onClose: () => void;
  onChange?: (servers: McpServerSpec[]) => void;
};

type EditableServer = {
  id: string;
  command: string;
  args: string;       // newline-separated, edited as a single textarea
  env: string;        // "KEY=value" per line
  enabled: boolean;
  status?: string;
  last_error?: string | null;
  // Tracks if this row needs to be saved; reset after a successful save.
  dirty?: boolean;
};

const STATUS_COLORS: Record<string, string> = {
  running: "#4ade80",
  stopped: "var(--text-muted)",
  crashed: "#ff6b6b",
  error: "#ff6b6b",
};

function parseArgs(text: string): string[] {
  return text
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
}

function parseEnv(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    const value = line.slice(eq + 1);
    if (key) out[key] = value;
  }
  return out;
}

function specToEditable(spec: McpServerSpec): EditableServer {
  return {
    id: spec.id,
    command: spec.command,
    args: (spec.args || []).join("\n"),
    env: Object.entries(spec.env || {})
      .map(([k, v]) => `${k}=${v}`)
      .join("\n"),
    enabled: spec.enabled,
    status: spec.status,
    last_error: spec.last_error ?? null,
  };
}

function editableToSpec(row: EditableServer): Omit<McpServerSpec, "status" | "last_error"> {
  return {
    id: row.id.trim(),
    command: row.command.trim(),
    args: parseArgs(row.args),
    env: parseEnv(row.env),
    enabled: row.enabled,
  };
}

export default function McpConfigDialog({ open, onClose, onChange }: Props) {
  const [rows, setRows] = useState<EditableServer[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  // Load on open
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    api.listMcpServers()
      .then((res) => {
        if (cancelled) return;
        setRows(res.servers.map(specToEditable));
      })
      .catch((e) => {
        if (!cancelled) toast.error(`MCP load: ${String((e as Error).message || e)}`);
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [open]);

  function addRow() {
    setRows((r) => [
      ...r,
      {
        id: "",
        command: "npx",
        args: "-y\n@modelcontextprotocol/server-github",
        env: "",
        enabled: true,
        dirty: true,
      },
    ]);
  }

  function removeRow(index: number) {
    setRows((r) => r.filter((_, i) => i !== index));
  }

  function patchRow(index: number, patch: Partial<EditableServer>) {
    setRows((r) =>
      r.map((row, i) => (i === index ? { ...row, ...patch, dirty: true } : row)),
    );
  }

  async function saveAll() {
    const specs = rows
      .map(editableToSpec)
      .filter((s) => s.id && s.command);
    try {
      const result = await api.saveMcpServers(specs);
      const next = result.servers.map(specToEditable);
      setRows(next);
      onChange?.(result.servers);
      toast.success("Конфиг MCP сохранён");
    } catch (e) {
      toast.error(`MCP save: ${String((e as Error).message || e)}`);
    }
  }

  async function refreshList() {
    setLoading(true);
    try {
      const res = await api.listMcpServers();
      setRows(res.servers.map(specToEditable));
      onChange?.(res.servers);
    } catch (e) {
      toast.error(`MCP reload: ${String((e as Error).message || e)}`);
    } finally {
      setLoading(false);
    }
  }

  async function runAction(
    row: EditableServer,
    action: "start" | "stop" | "restart",
  ) {
    if (!row.id) {
      toast.info("Сначала сохрани сервер (введи id + command).");
      return;
    }
    setBusyId(row.id);
    try {
      if (action === "start") await api.startMcpServer(row.id);
      else if (action === "stop") await api.stopMcpServer(row.id);
      else await api.restartMcpServer(row.id);
      await refreshList();
    } catch (e) {
      toast.error(`MCP ${action}: ${String((e as Error).message || e)}`);
    } finally {
      setBusyId(null);
    }
  }

  function onIdKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    // Prevent Enter from accidentally submitting nothing
    if (e.key === "Enter") e.preventDefault();
  }

  if (!open) return null;

  const anyDirty = rows.some((r) => r.dirty);

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1200,
        background: "rgba(0,0,0,0.55)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        backdropFilter: "blur(2px)",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(820px, 96vw)",
          maxHeight: "88vh",
          background: "var(--bg-surface, #1a1a1a)",
          border: "1px solid var(--border, #333)",
          borderRadius: 12,
          boxShadow: "0 18px 60px rgba(0,0,0,0.6)",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "12px 16px",
            borderBottom: "1px solid var(--border)",
          }}
        >
          <Plug size={16} style={{ color: "var(--accent, #6366f1)" }} />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 13, fontWeight: 600 }}>MCP-серверы</div>
            <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
              Подключи внешние tool-серверы (GitHub, Slack, Notion, …). Каждый
              запущенный сервер пополняет тул-список code-agent'а.
            </div>
          </div>
          <button
            onClick={refreshList}
            disabled={loading}
            title="Обновить статусы"
            style={{ background: "transparent", border: "none", cursor: "pointer", color: "var(--text-muted)", padding: 4 }}
          >
            <RefreshCw size={14} />
          </button>
          <button
            onClick={onClose}
            style={{ background: "transparent", border: "none", cursor: "pointer", color: "var(--text-muted)", padding: 4 }}
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>

        {/* Rows */}
        <div style={{ overflowY: "auto", flex: 1, padding: "8px 0" }}>
          {loading && rows.length === 0 && (
            <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "16px", color: "var(--text-muted)", fontSize: 12 }}>
              <Loader2 size={14} className="lucide-spin" />
              <span>Загружаю...</span>
            </div>
          )}
          {!loading && rows.length === 0 && (
            <div style={{ padding: "20px 16px", fontSize: 12, color: "var(--text-muted)" }}>
              Серверов нет. Нажми «Добавить» и впиши команду — например <code>npx</code>
              {" "}с аргументом <code>@modelcontextprotocol/server-github</code> для интеграции с GitHub.
            </div>
          )}

          {rows.map((row, idx) => {
            const statusColor = STATUS_COLORS[row.status || "stopped"] || "var(--text-muted)";
            const isBusy = busyId === row.id;
            const isRunning = row.status === "running";
            return (
              <div
                key={idx}
                style={{
                  padding: "12px 16px",
                  borderBottom: "1px solid var(--border)",
                  display: "flex",
                  flexDirection: "column",
                  gap: 8,
                }}
              >
                {/* Top row: id + status + actions */}
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <input
                    placeholder="id (например: github)"
                    value={row.id}
                    onChange={(e) => patchRow(idx, { id: e.target.value })}
                    onKeyDown={onIdKeyDown}
                    style={{
                      flex: 1,
                      fontSize: 12,
                      padding: "6px 10px",
                      borderRadius: 6,
                      border: "1px solid var(--border)",
                      background: "var(--bg-elevated, #222)",
                      color: "var(--text-primary)",
                      outline: "none",
                      fontFamily: "var(--font-mono)",
                    }}
                  />
                  <span
                    title={row.last_error || row.status || "stopped"}
                    style={{ fontSize: 11, color: statusColor, padding: "0 6px" }}
                  >
                    ● {row.status || "stopped"}
                  </span>
                  <label
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 4,
                      fontSize: 11,
                      color: row.enabled ? "var(--text-primary)" : "var(--text-muted)",
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={row.enabled}
                      onChange={(e) => patchRow(idx, { enabled: e.target.checked })}
                    />
                    <span>enabled</span>
                  </label>
                  <button
                    onClick={() => runAction(row, isRunning ? "stop" : "start")}
                    disabled={isBusy}
                    title={isRunning ? "Остановить" : "Запустить"}
                    className="soft-btn"
                    style={{ fontSize: 11, padding: "4px 8px" }}
                  >
                    {isBusy ? <Loader2 size={11} className="lucide-spin" /> : isRunning ? <Square size={11} /> : <Play size={11} />}
                  </button>
                  <button
                    onClick={() => runAction(row, "restart")}
                    disabled={isBusy}
                    title="Перезапустить"
                    className="soft-btn"
                    style={{ fontSize: 11, padding: "4px 8px" }}
                  >
                    <RefreshCw size={11} />
                  </button>
                  <button
                    onClick={() => removeRow(idx)}
                    title="Удалить сервер из списка"
                    style={{
                      background: "transparent",
                      border: "none",
                      cursor: "pointer",
                      color: "var(--text-muted)",
                      padding: 4,
                    }}
                  >
                    <Trash2 size={13} />
                  </button>
                </div>

                {/* Command */}
                <input
                  placeholder="command (например: npx)"
                  value={row.command}
                  onChange={(e) => patchRow(idx, { command: e.target.value })}
                  style={{
                    fontSize: 12,
                    padding: "6px 10px",
                    borderRadius: 6,
                    border: "1px solid var(--border)",
                    background: "var(--bg-elevated, #222)",
                    color: "var(--text-primary)",
                    outline: "none",
                    fontFamily: "var(--font-mono)",
                  }}
                />

                {/* Args (one per line) */}
                <textarea
                  placeholder="args (по одному в строке, например:&#10;-y&#10;@modelcontextprotocol/server-github)"
                  value={row.args}
                  onChange={(e) => patchRow(idx, { args: e.target.value })}
                  rows={3}
                  style={{
                    fontSize: 11,
                    padding: "6px 10px",
                    borderRadius: 6,
                    border: "1px solid var(--border)",
                    background: "var(--bg-elevated, #222)",
                    color: "var(--text-primary)",
                    outline: "none",
                    fontFamily: "var(--font-mono)",
                    resize: "vertical",
                  }}
                />

                {/* Env (KEY=value per line) */}
                <textarea
                  placeholder="env (KEY=value, по одному в строке)&#10;GITHUB_TOKEN=ghp_..."
                  value={row.env}
                  onChange={(e) => patchRow(idx, { env: e.target.value })}
                  rows={2}
                  style={{
                    fontSize: 11,
                    padding: "6px 10px",
                    borderRadius: 6,
                    border: "1px solid var(--border)",
                    background: "var(--bg-elevated, #222)",
                    color: "var(--text-primary)",
                    outline: "none",
                    fontFamily: "var(--font-mono)",
                    resize: "vertical",
                  }}
                />

                {row.last_error && (
                  <div style={{ fontSize: 10, color: "#ff6b6b", fontFamily: "var(--font-mono)" }}>
                    last error: {row.last_error}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Footer */}
        <div
          style={{
            padding: "10px 16px",
            borderTop: "1px solid var(--border)",
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <button
            onClick={addRow}
            className="soft-btn"
            style={{ fontSize: 11, padding: "5px 10px", display: "inline-flex", alignItems: "center", gap: 4 }}
          >
            <Plus size={12} /> Добавить
          </button>
          <div style={{ flex: 1 }} />
          <button
            onClick={saveAll}
            disabled={!anyDirty && rows.every((r) => !r.dirty)}
            className="soft-btn"
            style={{
              fontSize: 11,
              padding: "6px 14px",
              background: anyDirty ? "var(--accent, #6366f1)" : "var(--bg-elevated)",
              color: anyDirty ? "#fff" : "var(--text-muted)",
              border: "1px solid " + (anyDirty ? "var(--accent)" : "var(--border)"),
              fontWeight: 500,
            }}
          >
            Сохранить
          </button>
        </div>
      </div>
    </div>
  );
}
