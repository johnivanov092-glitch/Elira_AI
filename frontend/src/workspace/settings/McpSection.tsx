import { useEffect, useRef, useState } from "react";
import { Play, RefreshCw, RotateCw, Square } from "lucide-react";
import { controlMcpServer, listMcpServers, type McpAction, type McpServer } from "../../api/mcp";
import { Loading, McpBtn, Note, Wrap } from "./_shared";

const STATUS: Record<McpServer["status"], string> = {
  running: "Запущен", stopped: "Остановлен", crashed: "Соединение потеряно", error: "Ошибка запуска",
};

export function McpSection() {
  const [servers, setServers] = useState<McpServer[] | null>(null);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState("");
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

  async function control(ids: string[], action: McpAction) {
    if (working.current || !ids.length) return;
    working.current = true;
    generation.current += 1;
    setBusy(ids.length > 1 ? "all" : ids[0]);
    setError("");
    const failures: string[] = [];
    try {
      for (const id of ids) {
        try { await controlMcpServer(id, action); }
        catch (err) { failures.push(`${id}: ${String(err)}`); }
        try {
          const rows = await listMcpServers();
          if (mounted.current) setServers(rows);
        } catch (err) { failures.push(String(err)); }
      }
    } finally {
      working.current = false;
      if (mounted.current) { setBusy(""); setError(failures.join("\n")); }
    }
  }

  const startIds = (servers ?? []).filter((s) => s.enabled && s.status !== "running").map((s) => s.id);
  const stopIds = (servers ?? []).filter((s) => s.status !== "stopped").map((s) => s.id);
  return (
    <Wrap title="MCP">
      <Note>Запуск и остановка подключений MCP. Для HTTP кнопка «Стоп» отключает Elira от сервера.</Note>
      <div className="my-3 flex flex-wrap items-center gap-2 text-[12px]">
        <button type="button" disabled={!!busy || !startIds.length} onClick={() => void control(startIds, "start")} className="flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-t2 hover:bg-hover disabled:opacity-50"><Play size={13} /> Запустить все</button>
        <button type="button" disabled={!!busy || !stopIds.length} onClick={() => void control(stopIds, "stop")} className="flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-t2 hover:bg-hover disabled:opacity-50"><Square size={13} /> Остановить все</button>
        <McpBtn onClick={() => void refresh()} busy={!!busy} label="Обновить статус MCP"><RefreshCw size={13} /></McpBtn>
      </div>
      {error && <div role="alert" className="mb-3 whitespace-pre-wrap break-words text-[12px] text-red-400">{error}</div>}
      {loadError && <div role="alert" className="mb-3 break-words text-[12px] text-red-400">{loadError}</div>}
      {servers === null ? (!loadError && <Loading />) : servers.length === 0 ? <Note>Серверы MCP ещё не настроены.</Note> : (
        <div className="flex flex-col gap-2">
          {servers.map((server) => (
            <div key={server.id} className="rounded-lg border border-line px-3 py-2.5">
              <div className="flex items-center gap-2.5 text-[12.5px]">
                <span className={`h-2 w-2 shrink-0 rounded-full ${server.status === "running" ? "bg-green-400" : server.last_error || server.status === "crashed" ? "bg-red-400" : "bg-mut"}`} />
                <div className="min-w-0 flex-1">
                  <div className="break-words font-medium">{server.id}</div>
                  <div className="text-[11.5px] text-mut">{STATUS[server.status]} · {server.transport}{!server.enabled && " · исключён из запуска всех"}</div>
                </div>
                <McpBtn busy={!!busy} label={`${server.status === "running" ? "Остановить" : "Запустить"} ${server.id}`} onClick={() => void control([server.id], server.status === "running" ? "stop" : "start")}>
                  {server.status === "running" ? <Square size={13} /> : <Play size={13} />}
                </McpBtn>
                <McpBtn busy={!!busy} label={`Перезапустить ${server.id}`} onClick={() => void control([server.id], "restart")}><RotateCw size={13} /></McpBtn>
              </div>
              {server.last_error && <div className="mt-2 break-words text-[11.5px] text-red-400">{server.last_error}</div>}
            </div>
          ))}
        </div>
      )}
    </Wrap>
  );
}
