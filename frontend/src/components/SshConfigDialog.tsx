/**
 * SshConfigDialog — manages the SSH allowlist.
 *
 * Opens as a modal over the code workspace. Add a host (e.g. an
 * alias from ~/.ssh/config), see the current list, remove with X.
 * Every mutation persists immediately via setSshConfig. The dialog
 * shows the agent's view: empty list = SSH provider is disabled.
 */
import { useEffect, useState, type KeyboardEvent } from "react";
import { Loader2, Plus, ShieldCheck, ShieldX, Trash2, X } from "lucide-react";
import { api } from "../api/ide";
import type { SshConfig } from "../api/codeAgent";
import { toast } from "./ToastHost";

type Props = {
  open: boolean;
  onClose: () => void;
  onChange?: (config: SshConfig) => void;
};

export default function SshConfigDialog({ open, onClose, onChange }: Props) {
  const [config, setConfig] = useState<SshConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  // Fetch current config every time the dialog opens — it's cheap and
  // ensures we don't show stale state if the user edited the JSON by hand.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    api.getSshConfig()
      .then((c) => { if (!cancelled) setConfig(c); })
      .catch((e) => {
        if (!cancelled) {
          toast.error(`SSH config: ${String((e as Error).message || e)}`);
          setConfig({ enabled: false, allowed_hosts: [] });
        }
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [open]);

  const persist = async (nextHosts: string[]) => {
    setBusy(true);
    try {
      const updated = await api.setSshConfig(nextHosts);
      setConfig(updated);
      onChange?.(updated);
    } catch (e) {
      toast.error(`SSH config save: ${String((e as Error).message || e)}`);
    } finally {
      setBusy(false);
    }
  };

  const addHost = async () => {
    const trimmed = input.trim();
    if (!trimmed || !config) return;
    if (config.allowed_hosts.includes(trimmed)) {
      toast.info(`${trimmed} уже в списке`);
      return;
    }
    setInput("");
    await persist([...config.allowed_hosts, trimmed]);
  };

  const removeHost = async (host: string) => {
    if (!config) return;
    await persist(config.allowed_hosts.filter((h) => h !== host));
  };

  const onInputKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      void addHost();
    }
  };

  if (!open) return null;

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
          width: "min(560px, 92vw)",
          maxHeight: "80vh",
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
          {config?.enabled ? (
            <ShieldCheck size={16} style={{ color: "#4ade80" }} />
          ) : (
            <ShieldX size={16} style={{ color: "var(--text-muted)" }} />
          )}
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 13, fontWeight: 600 }}>SSH allowlist</div>
            <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
              {config?.enabled
                ? `Агент может SSH-иться в ${config.allowed_hosts.length} хост(а/ов).`
                : "Список пуст — SSH-инструменты выключены для агента."}
            </div>
          </div>
          <button
            onClick={onClose}
            style={{ background: "transparent", border: "none", cursor: "pointer", color: "var(--text-muted)", padding: 4 }}
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>

        {/* Add new host */}
        <div
          style={{
            padding: "12px 16px",
            display: "flex",
            gap: 8,
            borderBottom: "1px solid var(--border)",
          }}
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onInputKey}
            placeholder="prod-1 или user@server.example.com"
            disabled={busy || loading}
            style={{
              flex: 1,
              fontSize: 12,
              padding: "7px 10px",
              borderRadius: 6,
              border: "1px solid var(--border)",
              background: "var(--bg-elevated, #222)",
              color: "var(--text-primary)",
              outline: "none",
            }}
          />
          <button
            onClick={() => void addHost()}
            disabled={!input.trim() || busy || loading}
            className="soft-btn"
            style={{
              fontSize: 11,
              padding: "6px 10px",
              opacity: !input.trim() || busy ? 0.4 : 1,
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            <Plus size={12} />
            <span>Добавить</span>
          </button>
        </div>

        {/* Hosts list */}
        <div style={{ overflowY: "auto", flex: 1, padding: "8px 0" }}>
          {loading && (
            <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "16px", color: "var(--text-muted)", fontSize: 12 }}>
              <Loader2 size={14} className="lucide-spin" />
              <span>Загружаю...</span>
            </div>
          )}
          {!loading && config && config.allowed_hosts.length === 0 && (
            <div style={{ padding: "16px", fontSize: 12, color: "var(--text-muted)" }}>
              Добавь имя хоста (можно использовать алиасы из ~/.ssh/config или полные user@server),
              и агент сможет вызывать <code>ssh_run</code>, <code>ssh_read</code>, <code>ssh_write</code> только на эти машины.
            </div>
          )}
          {!loading && config?.allowed_hosts.map((host) => (
            <div
              key={host}
              style={{
                display: "flex",
                alignItems: "center",
                padding: "6px 16px",
                gap: 8,
              }}
            >
              <span style={{ flex: 1, fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>
                {host}
              </span>
              <button
                onClick={() => void removeHost(host)}
                disabled={busy}
                title="Убрать из списка"
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
          ))}
        </div>

        {/* Footer hint */}
        <div
          style={{
            padding: "10px 16px",
            borderTop: "1px solid var(--border)",
            fontSize: 10,
            color: "var(--text-muted)",
            lineHeight: 1.5,
          }}
        >
          Безопасность: каждый <code>ssh_*</code>-вызов агента проверяется по этому списку.
          Хост должен совпадать буква-в-букву с тем, что агент передаёт. Используется твой
          локальный <code>ssh</code> + <code>~/.ssh/config</code> + ключи — никаких отдельных кред-сторов.
        </div>
      </div>
    </div>
  );
}
