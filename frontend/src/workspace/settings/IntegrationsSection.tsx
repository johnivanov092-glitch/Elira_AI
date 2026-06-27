import { Loader2, Play, Plus, RefreshCw, Square, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  getSshConfig, listMcpServers, restartMcpServer, setSshConfig,
  startMcpServer, stopMcpServer, type McpServerSpec, type SshConfig,
} from "../../api/codeAgent";
import {
  listPlugins, reloadPlugins, setPluginEnabled, type PluginItem,
} from "../../api/plugins";
import { cn } from "../../ui/cn";
import { Loading, McpBtn, Note, Wrap } from "./_shared";

export function SshMcpSection() {
  return (
    <div className="flex flex-col gap-6">
      <PluginsBlock />
      <McpBlock />
      <SshBlock />
    </div>
  );
}

function PluginsBlock() {
  const [items, setItems] = useState<PluginItem[] | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    listPlugins().then(setItems).catch(() => setItems([]));
  }, []);
  useEffect(() => { reload(); }, [reload]);

  async function doReload() {
    setBusy(true);
    try { await reloadPlugins(); reload(); } catch { /* ignore */ } finally { setBusy(false); }
  }
  async function toggle(name: string, enabled: boolean) {
    setBusy(true);
    try { await setPluginEnabled(name, !enabled); reload(); } catch { /* ignore */ } finally { setBusy(false); }
  }

  return (
    <Wrap title="Плагины">
      <Note>Локальные плагины проекта (data/plugins) — дают агенту дополнительные инструменты.</Note>
      <div className="my-2 flex justify-end">
        <button type="button" onClick={doReload} disabled={busy} className="flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1.5 text-[12px] text-t2 transition-colors hover:bg-hover hover:text-tx disabled:opacity-50">
          {busy ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} Перезагрузить
        </button>
      </div>
      {items === null ? (
        <Loading />
      ) : items.length === 0 ? (
        <Note>Плагинов нет. Положи их в data/plugins и нажми «Перезагрузить».</Note>
      ) : (
        <div className="flex flex-col gap-1.5">
          {items.map((p, i) => {
            const name = String(p.name ?? p.id ?? `plugin ${i + 1}`);
            const enabled = p.enabled !== false;
            return (
              <div key={name} className="flex items-center gap-2.5 rounded-lg border border-line px-3 py-2 text-[12.5px]">
                <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", enabled ? "bg-ac" : "bg-mut")} />
                <span className="min-w-0 flex-1 truncate font-medium text-tx">{name}</span>
                <McpBtn onClick={() => toggle(name, enabled)} busy={busy} label={enabled ? "Выключить" : "Включить"}>
                  {enabled ? <Square size={13} /> : <Play size={13} />}
                </McpBtn>
              </div>
            );
          })}
        </div>
      )}
    </Wrap>
  );
}

function SshBlock() {
  const [cfg, setCfg] = useState<SshConfig | null>(null);
  const [host, setHost] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    getSshConfig().then((c) => { if (alive) setCfg(c); }).catch(() => { if (alive) setCfg({ enabled: false, allowed_hosts: [] }); });
    return () => { alive = false; };
  }, []);

  async function save(hosts: string[]) {
    setBusy(true);
    try { setCfg(await setSshConfig(hosts)); } catch { /* offline */ } finally { setBusy(false); }
  }
  function add() {
    const h = host.trim();
    if (!h || !cfg) return;
    setHost("");
    if (!cfg.allowed_hosts.includes(h)) void save([...cfg.allowed_hosts, h]);
  }

  return (
    <Wrap title="SSH-доступ агента">
      <Note>
        Агент выполняет команды по SSH только на хостах из списка.
        {cfg ? (cfg.enabled ? " Провайдер включён." : " Сейчас выключен (список пуст).") : ""}
      </Note>
      <div className="my-2.5 flex gap-2">
        <input
          value={host}
          onChange={(e) => setHost(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") add(); }}
          placeholder="user@host или host"
          className="flex-1 rounded-lg border border-line bg-surface px-3 py-2 font-mono text-[12.5px] text-tx outline-none placeholder:text-mut focus:border-acl"
        />
        <button type="button" onClick={add} disabled={busy || !host.trim()} aria-label="Добавить хост" className={cn("grid h-[34px] w-[34px] shrink-0 place-items-center rounded-lg text-[#14151b]", host.trim() && !busy ? "bg-ac" : "cursor-not-allowed bg-ac/40")}>
          <Plus size={16} />
        </button>
      </div>
      {cfg && cfg.allowed_hosts.length > 0 ? (
        <div className="flex flex-col gap-1.5">
          {cfg.allowed_hosts.map((h) => (
            <div key={h} className="group flex items-center gap-2 rounded-lg border border-line px-3 py-2 text-[12.5px] text-t2">
              <span className="flex-1 truncate font-mono">{h}</span>
              <button type="button" onClick={() => void save(cfg.allowed_hosts.filter((x) => x !== h))} aria-label="Удалить" className="grid h-5 w-5 place-items-center rounded text-mut opacity-0 transition-opacity hover:text-tx group-hover:opacity-100">
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </div>
      ) : (
        <Note>Список пуст — SSH-инструменты отключены.</Note>
      )}
    </Wrap>
  );
}

function McpBlock() {
  const [servers, setServers] = useState<McpServerSpec[] | null>(null);
  const [busy, setBusy] = useState("");

  const reload = useCallback(() => {
    listMcpServers().then((r) => setServers(r.servers ?? [])).catch(() => setServers([]));
  }, []);
  useEffect(() => { reload(); }, [reload]);

  async function act(id: string, fn: (id: string) => Promise<unknown>) {
    setBusy(id);
    try { await fn(id); } catch { /* ignore */ } finally { setBusy(""); reload(); }
  }

  return (
    <Wrap title="MCP-серверы">
      <Note>Внешние инструменты по Model Context Protocol. Запущенный сервер отдаёт свои инструменты агенту.</Note>
      {servers === null ? (
        <Loading />
      ) : servers.length === 0 ? (
        <Note>Серверов нет. Добавь их в конфиг MCP (data) — управление статусом появится здесь.</Note>
      ) : (
        <div className="mt-2 flex flex-col gap-1.5">
          {servers.map((s) => {
            const running = s.status === "running";
            return (
              <div key={s.id} className="flex items-center gap-2.5 rounded-lg border border-line px-3 py-2 text-[12.5px]">
                <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", running ? "bg-ac" : s.status === "crashed" || s.status === "error" ? "bg-[#e0a87a]" : "bg-mut")} />
                <span className="min-w-0 flex-1">
                  <span className="font-medium text-tx">{s.id}</span>{" "}
                  <span className="font-mono text-[11px] text-mut">{s.command}</span>
                  {s.last_error && <span className="block truncate text-[10.5px] text-[#c98a8a]">{s.last_error}</span>}
                </span>
                {running ? (
                  <>
                    <McpBtn onClick={() => act(s.id, restartMcpServer)} busy={busy === s.id} label="Перезапуск"><RefreshCw size={13} /></McpBtn>
                    <McpBtn onClick={() => act(s.id, stopMcpServer)} busy={busy === s.id} label="Остановить"><Square size={13} /></McpBtn>
                  </>
                ) : (
                  <McpBtn onClick={() => act(s.id, startMcpServer)} busy={busy === s.id} label="Запустить"><Play size={13} /></McpBtn>
                )}
              </div>
            );
          })}
        </div>
      )}
    </Wrap>
  );
}
