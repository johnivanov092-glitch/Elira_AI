import { useEffect, useRef, useState } from "react";
import { Pencil, Play, Plus, RefreshCw, RotateCw, Square, Trash2 } from "lucide-react";
import {
  addMcpServer, controlMcpServer, deleteMcpServer, getMcpServerConfig, listMcpServers, setMcpServerEnabled,
  updateMcpServer, type McpAction, type McpServer,
} from "../../api/mcp";
import { Loading, McpBtn, Note, Wrap } from "./_shared";

const STATUS: Record<McpServer["status"], string> = {
  running: "Запущен", stopped: "Остановлен", crashed: "Соединение потеряно", error: "Ошибка запуска",
};

const btn = "flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-t2 hover:bg-hover disabled:opacity-50";

const TEMPLATE = {
  id: "",
  description: "",
  command: "",
  args: [],
  cwd: "",
  env: {},
  enabled: true,
};

/** Editing one entry of data/mcp_servers.json; `id === ""` adds a new server. */
type Editor = { id: string; text: string };

/** Settings → MCP: the servers of data/mcp_servers.json — the same file Elira edits.
 *  Start/stop, switch off, edit one entry as JSON (secret values stay masked), add, delete. */
export function McpSection() {
  const [servers, setServers] = useState<McpServer[] | null>(null);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState("");
  const [editor, setEditor] = useState<Editor | null>(null);
  const [confirmDelete, setConfirmDelete] = useState("");
  const mounted = useRef(false);
  const working = useRef(false);
  const refreshing = useRef(false);
  const generation = useRef(0);

  async function refresh() {
    if (working.current || refreshing.current) return;
    refreshing.current = true;
    const version = generation.current;
    try {
      const rows = await listMcpServers();
      if (mounted.current && version === generation.current) { setServers(rows); setLoadError(""); }
    } catch (err) {
      if (mounted.current && version === generation.current) setLoadError(String(err));
    } finally { refreshing.current = false; }
  }

  useEffect(() => {
    mounted.current = true;
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 5000);
    return () => { mounted.current = false; window.clearInterval(timer); };
  }, []);

  /** Runs one change, then reloads the list; errors stay on screen. */
  async function act(key: string, action: () => Promise<unknown>): Promise<boolean> {
    if (working.current) return false;
    working.current = true;
    generation.current += 1;
    setBusy(key);
    setError("");
    let ok = true;
    try { await action(); } catch (err) { ok = false; if (mounted.current) setError(String(err)); }
    try {
      const rows = await listMcpServers();
      if (mounted.current) setServers(rows);
    } catch (err) { if (mounted.current) setLoadError(String(err)); }
    working.current = false;
    if (mounted.current) setBusy("");
    return ok;
  }

  async function control(ids: string[], action: McpAction) {
    if (!ids.length) return;
    await act(ids.length > 1 ? "all" : ids[0], async () => {
      const failures: string[] = [];
      for (const id of ids) {
        try { await controlMcpServer(id, action); } catch (err) { failures.push(`${id}: ${String(err)}`); }
      }
      if (failures.length) throw new Error(failures.join("\n"));
    });
  }

  async function openEditor(id: string) {
    setError("");
    if (!id) { setEditor({ id: "", text: JSON.stringify(TEMPLATE, null, 2) }); return; }
    try {
      const config = await getMcpServerConfig(id);
      if (mounted.current) setEditor({ id, text: JSON.stringify(config, null, 2) });
    } catch (err) { if (mounted.current) setError(String(err)); }
  }

  async function saveEditor() {
    if (!editor) return;
    let config: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(editor.text);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("нужен JSON-объект");
      config = parsed as Record<string, unknown>;
    } catch (err) {
      setError(`JSON не разобран: ${String(err)}`);
      return;
    }
    const saved = await act(editor.id || "new", () => (editor.id ? updateMcpServer(editor.id, config) : addMcpServer(config)));
    if (saved && mounted.current) setEditor(null);
  }

  async function remove(id: string) {
    if (confirmDelete !== id) { setConfirmDelete(id); return; }
    setConfirmDelete("");
    if (editor?.id === id) setEditor(null);
    await act(id, () => deleteMcpServer(id));
  }

  const startIds = (servers ?? []).filter((s) => s.enabled && s.status !== "running").map((s) => s.id);
  const stopIds = (servers ?? []).filter((s) => s.status !== "stopped").map((s) => s.id);
  const editorPanel = editor && (
    <div className="mb-3 rounded-lg border border-line px-3 py-2.5">
      <div className="mb-1.5 text-[12.5px] font-medium">{editor.id ? `Запись ${editor.id}` : "Новый сервер"}</div>
      <div className="mb-2 text-[11.5px] text-mut">
        stdio: command, args, cwd (папка сервера, например data/mcp/&lt;id&gt;), env. HTTP: "transport": "http", url, headers.
        Значения секретов скрыты (●●●) — оставьте как есть, они сохранятся. Новый секрет добавьте в «Секреты» и
        укажите его ссылку sref_… в env_secret_refs или secret_header_refs.
      </div>
      <textarea
        aria-label="JSON записи MCP"
        value={editor.text}
        onChange={(e) => setEditor({ ...editor, text: e.target.value })}
        spellCheck={false}
        rows={14}
        className="w-full resize-y rounded-md border border-line bg-transparent px-2 py-1.5 font-mono text-[11.5px] text-tx outline-none focus:border-t2"
      />
      <div className="mt-2 flex gap-2 text-[12px]">
        <button type="button" disabled={!!busy} onClick={() => void saveEditor()} className={btn}>Сохранить</button>
        <button type="button" disabled={!!busy} onClick={() => setEditor(null)} className={btn}>Отмена</button>
      </div>
    </div>
  );

  return (
    <Wrap title="MCP">
      <Note>
        Серверы из data/mcp_servers.json — того же файла, который правит Elira. Elira запускает сервер, когда он
        нужен задаче; выключенный сервер не запускается. Для HTTP кнопка «Стоп» отключает Elira от сервера.
      </Note>
      <div className="my-3 flex flex-wrap items-center gap-2 text-[12px]">
        <button type="button" disabled={!!busy || !startIds.length} onClick={() => void control(startIds, "start")} className={btn}><Play size={13} /> Запустить все</button>
        <button type="button" disabled={!!busy || !stopIds.length} onClick={() => void control(stopIds, "stop")} className={btn}><Square size={13} /> Остановить все</button>
        <button type="button" disabled={!!busy} onClick={() => void openEditor("")} className={btn}><Plus size={13} /> Добавить</button>
        <McpBtn onClick={() => void refresh()} busy={!!busy} label="Обновить статус MCP"><RefreshCw size={13} /></McpBtn>
      </div>
      {error && <div role="alert" className="mb-3 whitespace-pre-wrap break-words text-[12px] text-red-400">{error}</div>}
      {loadError && <div role="alert" className="mb-3 break-words text-[12px] text-red-400">{loadError}</div>}
      {editor && !editor.id && editorPanel}
      {servers === null ? (!loadError && <Loading />) : servers.length === 0 ? <Note>Серверы MCP ещё не настроены.</Note> : (
        <div className="flex flex-col gap-2">
          {servers.map((server) => (
            <div key={server.id}>
              <div className="rounded-lg border border-line px-3 py-2.5">
                <div className="flex items-center gap-2.5 text-[12.5px]">
                  <span className={`h-2 w-2 shrink-0 rounded-full ${server.status === "running" ? "bg-green-400" : server.last_error || server.status === "crashed" ? "bg-red-400" : "bg-mut"}`} />
                  <div className="min-w-0 flex-1">
                    <div className="break-words font-medium">{server.id}</div>
                    {server.description && <div className="break-words text-[11.5px] text-t2">{server.description}</div>}
                    <div className="text-[11.5px] text-mut">{server.enabled ? STATUS[server.status] : "Выключен"} · {server.transport}</div>
                  </div>
                  <label className="flex shrink-0 items-center gap-1 text-[11.5px] text-t2" title="Выключенный сервер не запускается">
                    <input type="checkbox" checked={server.enabled} disabled={!!busy} aria-label={`Включён ${server.id}`}
                      onChange={(e) => void act(server.id, () => setMcpServerEnabled(server.id, e.target.checked))} />
                    вкл.
                  </label>
                  {server.enabled && (
                    <McpBtn busy={!!busy} label={`${server.status === "running" ? "Остановить" : "Запустить"} ${server.id}`} onClick={() => void control([server.id], server.status === "running" ? "stop" : "start")}>
                      {server.status === "running" ? <Square size={13} /> : <Play size={13} />}
                    </McpBtn>
                  )}
                  {server.enabled && (
                    <McpBtn busy={!!busy} label={`Перезапустить ${server.id}`} onClick={() => void control([server.id], "restart")}><RotateCw size={13} /></McpBtn>
                  )}
                  <McpBtn busy={!!busy} label={`Изменить ${server.id}`} onClick={() => void openEditor(server.id)}><Pencil size={13} /></McpBtn>
                  <McpBtn busy={!!busy} label={confirmDelete === server.id ? `Точно удалить ${server.id}? Нажмите ещё раз` : `Удалить ${server.id}`} onClick={() => void remove(server.id)}>
                    <Trash2 size={13} className={confirmDelete === server.id ? "text-red-400" : undefined} />
                  </McpBtn>
                </div>
                {confirmDelete === server.id && <div className="mt-2 text-[11.5px] text-red-400">Нажмите корзину ещё раз, чтобы удалить запись {server.id}.</div>}
                {server.last_error && <div className="mt-2 break-words text-[11.5px] text-red-400">{server.last_error}</div>}
              </div>
              {editor?.id === server.id && <div className="mt-2">{editorPanel}</div>}
            </div>
          ))}
        </div>
      )}
    </Wrap>
  );
}
