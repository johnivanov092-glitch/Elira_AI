import { FilePlus, Loader2, Play, Plus, RefreshCw, Square, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  getSshConfig, listMcpServers, restartMcpServer, setSshConfig,
  startMcpServer, stopMcpServer, type McpServerSpec, type SshConfig,
} from "../../api/codeAgent";
import {
  createPlugin, listPlugins, reloadPlugins, setPluginEnabled, uploadPlugin,
  type PluginItem,
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
  const [err, setErr] = useState("");
  const [showNew, setShowNew] = useState(false);
  const [newName, setNewName] = useState("");
  const [newCategory, setNewCategory] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const reload = useCallback(() => {
    listPlugins().then(setItems).catch(() => setItems([]));
  }, []);
  useEffect(() => { reload(); }, [reload]);

  async function doReload() {
    setBusy(true); setErr("");
    try { await reloadPlugins(); reload(); } catch { /* ignore */ } finally { setBusy(false); }
  }
  async function toggle(name: string, enabled: boolean) {
    setBusy(true); setErr("");
    try { await setPluginEnabled(name, !enabled); reload(); } catch { /* ignore */ } finally { setBusy(false); }
  }

  async function doCreate() {
    const name = newName.trim();
    if (!name) return;
    setBusy(true); setErr("");
    try {
      const r = await createPlugin(name, newCategory.trim(), newDesc.trim());
      if (r && r.ok === false) { setErr(String(r.error ?? "Не удалось создать плагин")); return; }
      setNewName(""); setNewCategory(""); setNewDesc(""); setShowNew(false);
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Сбой запроса");
    } finally { setBusy(false); }
  }

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    e.target.value = ""; // allow re-picking the same file
    if (files.length === 0) return;
    const py = files.find((f) => f.name.toLowerCase().endsWith(".py"));
    if (!py) { setErr("Нужен .py файл плагина"); return; }
    const manifestFile = files.find((f) => f.name.toLowerCase().endsWith(".json"));
    setBusy(true); setErr("");
    try {
      const pyText = await py.text();
      const manifestText = manifestFile ? await manifestFile.text() : null;
      const r = await uploadPlugin(py.name, pyText, manifestText);
      if (r && r.ok === false) { setErr(String(r.error ?? "Не удалось загрузить плагин")); return; }
      reload();
    } catch (er) {
      setErr(er instanceof Error ? er.message : "Сбой чтения файла");
    } finally { setBusy(false); }
  }

  return (
    <Wrap title="Плагины">
      <Note>Локальные плагины проекта (data/plugins) — дают агенту дополнительные инструменты. Новые создаются выключенными и требуют классификации админом.</Note>
      <div className="my-2 flex flex-wrap justify-end gap-2">
        <button type="button" onClick={() => { setShowNew((v) => !v); setErr(""); }} disabled={busy} className="flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1.5 text-[12px] text-t2 transition-colors hover:bg-hover hover:text-tx disabled:opacity-50">
          <FilePlus size={13} /> Создать
        </button>
        <button type="button" onClick={() => fileRef.current?.click()} disabled={busy} className="flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1.5 text-[12px] text-t2 transition-colors hover:bg-hover hover:text-tx disabled:opacity-50">
          <Upload size={13} /> Загрузить
        </button>
        <button type="button" onClick={doReload} disabled={busy} className="flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1.5 text-[12px] text-t2 transition-colors hover:bg-hover hover:text-tx disabled:opacity-50">
          {busy ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} Перезагрузить
        </button>
        <input ref={fileRef} type="file" accept=".py,.json" multiple onChange={onFile} className="hidden" />
      </div>

      {showNew && (
        <div className="mb-2.5 flex flex-col gap-2 rounded-lg border border-line bg-surface/40 p-2.5">
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") void doCreate(); }}
            placeholder="имя (a-z, 0-9, -, _)"
            className="rounded-lg border border-line bg-surface px-3 py-2 font-mono text-[12.5px] text-tx outline-none placeholder:text-mut focus:border-acl"
          />
          <div className="flex gap-2">
            <input
              value={newCategory}
              onChange={(e) => setNewCategory(e.target.value)}
              placeholder="категория (необяз.)"
              className="flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-[12.5px] text-tx outline-none placeholder:text-mut focus:border-acl"
            />
            <input
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") void doCreate(); }}
              placeholder="описание (необяз.)"
              className="flex-[2] rounded-lg border border-line bg-surface px-3 py-2 text-[12.5px] text-tx outline-none placeholder:text-mut focus:border-acl"
            />
          </div>
          <div className="flex justify-end">
            <button type="button" onClick={() => void doCreate()} disabled={busy || !newName.trim()} className={cn("flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[12px] text-[#14151b] transition-opacity", newName.trim() && !busy ? "bg-ac hover:opacity-90" : "cursor-not-allowed bg-ac/40")}>
              {busy ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />} Создать скелет
            </button>
          </div>
        </div>
      )}

      {err && <div className="mb-2 rounded-lg border border-[#c98a8a]/40 bg-[#c98a8a]/10 px-3 py-2 text-[12px] text-[#d99a9a]">{err}</div>}

      {items === null ? (
        <Loading />
      ) : items.length === 0 ? (
        <Note>Плагинов нет. Создай скелет, загрузи .py или положи файлы в data/plugins.</Note>
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
