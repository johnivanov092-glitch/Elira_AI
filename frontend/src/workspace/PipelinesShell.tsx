import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, GitBranch, Loader2, Play, Plus, Power, RefreshCw, Trash2 } from "lucide-react";
import {
  createPipeline,
  deletePipeline,
  getPipelineLogs,
  listPipelines,
  runPipeline,
  updatePipeline,
  type PipelineItem,
  type PipelineLogEntry,
} from "../api/pipelines";
import { cn } from "../ui/cn";

function field(p: Record<string, unknown>, ...keys: string[]): string {
  for (const k of keys) {
    const v = p[k];
    if (typeof v === "string" && v) return v;
    if (typeof v === "number") return String(v);
  }
  return "";
}

/** `run_count` растёт на +1 после КАЖДОГО завершённого прогона. Это надёжный
 *  признак «пришёл свежий результат» для поллинга — точнее, чем сравнивать текст
 *  ответа (который мог совпасть) или `last_run` (строка времени). */
function runCount(p: Record<string, unknown>): number {
  const v = p["run_count"];
  return typeof v === "number" ? v : Number(v) || 0;
}

/** A pipeline run stores its result as a JSON string in `last_result` / log
 *  `result`. Pull out the human-facing answer (prompt tasks) or fall back to the
 *  raw JSON so the user can always see *where the response arrived*. */
function readableResult(raw: unknown): string {
  if (typeof raw !== "string" || !raw) return "";
  try {
    const obj = JSON.parse(raw) as Record<string, unknown>;
    const answer = obj.answer ?? obj.body ?? obj.summary;
    if (typeof answer === "string" && answer.trim()) return answer.trim();
    if (obj.error && typeof obj.error === "string" && obj.error.trim()) return `Ошибка: ${obj.error}`;
    return JSON.stringify(obj, null, 2);
  } catch {
    return raw;
  }
}

/** Pipelines as a separate shell behind the top «Пайплайны» tab (v4). */
export function PipelinesShell() {
  const [items, setItems] = useState<PipelineItem[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ name: "", prompt: "", interval: 60 });
  const [openId, setOpenId] = useState<string | null>(null);
  const [logs, setLogs] = useState<Record<string, PipelineLogEntry[]>>({});
  // pid -> «прогон идёт, ждём свежий результат» (фоновый прогон на бэке + поллинг).
  const [running, setRunning] = useState<Record<string, boolean>>({});
  const pollTimers = useRef<Record<string, ReturnType<typeof setInterval>>>({});

  const stopPoll = useCallback((id: string) => {
    const t = pollTimers.current[id];
    if (t) { clearInterval(t); delete pollTimers.current[id]; }
    setRunning((m) => { const next = { ...m }; delete next[id]; return next; });
  }, []);

  // Снять все таймеры при размонтировании, чтобы не поллить «в пустоту».
  useEffect(() => {
    const timers = pollTimers.current;
    return () => { for (const t of Object.values(timers)) clearInterval(t); };
  }, []);

  const load = useCallback(() => {
    listPipelines()
      .then(setItems)
      .catch(() => { setItems([]); setErr("Не удалось загрузить пайплайны."); });
  }, []);
  useEffect(() => { load(); }, [load]);

  async function create() {
    const name = form.name.trim();
    const prompt = form.prompt.trim();
    if (!name || !prompt) return;
    setBusy("__create__");
    try {
      await createPipeline({ name, task_type: "prompt", task_data: { prompt }, interval_minutes: Math.max(1, form.interval), enabled: true });
      setForm({ name: "", prompt: "", interval: 60 });
      setCreating(false);
      setErr("");
      load();
    } catch { setErr("Не удалось создать пайплайн."); }
    finally { setBusy(null); }
  }

  async function run(id: string) {
    if (running[id]) return;
    // Базовый run_count: ждём, пока он вырастет → значит фоновый прогон записал
    // свежий результат в БД. Полный ReAct-цикл агента на 35B занимает минуты.
    const baseCount = runCount((items ?? []).find((p) => String(p.id) === id) ?? {});
    setBusy(id);
    try {
      const resp = await runPipeline(id);
      setErr("");
      // Бэкенд вернул started=false только если прогон этого pipeline уже идёт —
      // всё равно встаём в режим ожидания и поллим тот же прогон.
      if (resp && (resp as Record<string, unknown>).ok === false) {
        setErr("Запуск не удался.");
        return;
      }
    } catch { setErr("Запуск не удался."); setBusy(null); return; }
    finally { setBusy(null); }

    // Раскрываем строку и поллим список+логи, пока run_count не вырастет.
    setRunning((m) => ({ ...m, [id]: true }));
    setOpenId(id);
    void loadLogs(id);
    const startedAt = Date.now();
    const MAX_WAIT_MS = 15 * 60 * 1000; // потолок ожидания фонового прогона
    if (pollTimers.current[id]) clearInterval(pollTimers.current[id]);
    pollTimers.current[id] = setInterval(async () => {
      try {
        const fresh = await listPipelines();
        setItems(fresh);
        void loadLogs(id);
        const row = fresh.find((p) => String(p.id) === id);
        const done = row && runCount(row) > baseCount;
        if (done || Date.now() - startedAt > MAX_WAIT_MS) {
          if (!done) setErr("Прогон выполняется дольше обычного — обновите вручную позже.");
          stopPoll(id);
        }
      } catch { /* транзиентная ошибка сети — продолжаем поллить */ }
    }, 5000);
  }

  async function toggle(p: PipelineItem) {
    const id = String(p.id);
    setBusy(id);
    try { await updatePipeline(id, { enabled: !p.enabled }); setErr(""); load(); }
    catch { setErr("Не удалось переключить."); }
    finally { setBusy(null); }
  }

  async function remove(id: string, name: string) {
    if (!window.confirm(`Удалить пайплайн «${name}»? Это действие нельзя отменить.`)) return;
    setBusy(id);
    try { await deletePipeline(id); setErr(""); if (openId === id) setOpenId(null); load(); }
    catch { setErr("Не удалось удалить."); }
    finally { setBusy(null); }
  }

  const loadLogs = useCallback(async (id: string) => {
    try {
      const rows = await getPipelineLogs(id, 10);
      setLogs((m) => ({ ...m, [id]: rows }));
    } catch { /* logs are best-effort */ }
  }, []);

  function toggleOpen(id: string) {
    setOpenId((cur) => {
      const next = cur === id ? null : id;
      if (next && !logs[id]) void loadLogs(id);
      return next;
    });
  }

  return (
    <div className="mx-auto max-w-[760px] px-6 py-6">
      <div className="mb-4 flex items-center gap-2">
        <GitBranch size={18} className="text-ac" />
        <span className="text-sm font-medium">Пайплайны</span>
        <button type="button" onClick={() => setCreating((v) => !v)} className="ml-auto flex items-center gap-1.5 rounded-lg border border-acl bg-acs px-2.5 py-1.5 text-[12px] font-medium text-ac transition-[filter] hover:brightness-110">
          <Plus size={14} /> Создать
        </button>
        <button type="button" onClick={load} aria-label="Обновить" className="grid h-7 w-7 place-items-center rounded-md border border-line text-t2 hover:bg-hover hover:text-tx">
          <RefreshCw size={14} />
        </button>
      </div>

      {creating && (
        <div className="mb-4 flex flex-col gap-2 rounded-xl border border-line bg-surface p-3">
          <input
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            placeholder="Название (напр. «Новости каждый час»)"
            className="rounded-lg border border-line bg-bg px-3 py-2 text-[12.5px] text-tx outline-none placeholder:text-mut focus:border-acl"
          />
          <textarea
            value={form.prompt}
            onChange={(e) => setForm((f) => ({ ...f, prompt: e.target.value }))}
            placeholder="Задача для агента (напр. «найди в интернете свежие новости по теме X и сделай сводку»)"
            rows={3}
            className="resize-y rounded-lg border border-line bg-bg px-3 py-2 text-[12.5px] text-tx outline-none placeholder:text-mut focus:border-acl"
          />
          <div className="flex items-center gap-2">
            <span className="text-[12px] text-t2">Каждые</span>
            <input
              type="number"
              min={1}
              value={form.interval}
              onChange={(e) => setForm((f) => ({ ...f, interval: Number(e.target.value) || 1 }))}
              className="w-20 rounded-lg border border-line bg-bg px-2 py-1.5 text-[12.5px] text-tx outline-none focus:border-acl"
            />
            <span className="text-[12px] text-t2">мин</span>
            <button
              type="button"
              onClick={create}
              disabled={busy === "__create__" || !form.name.trim() || !form.prompt.trim()}
              className="ml-auto flex items-center gap-1.5 rounded-lg bg-ac px-3 py-1.5 text-[12.5px] font-medium text-[#14151b] disabled:opacity-50"
            >
              {busy === "__create__" ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />} Создать пайплайн
            </button>
          </div>
        </div>
      )}

      {err && <div className="mb-3 rounded-lg border border-line bg-surface px-3 py-2 text-[12.5px] text-t2">{err}</div>}

      {items === null ? (
        <div className="flex items-center gap-2 text-[12.5px] text-mut"><Loader2 size={14} className="animate-spin" /> загрузка…</div>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-line p-4 text-center text-[12.5px] text-mut">Пайплайнов пока нет.</div>
      ) : (
        <div className="flex flex-col gap-2">
          {items.map((p, i) => {
            const id = String(p.id ?? i);
            const name = field(p, "name", "title", "id") || `pipeline ${i + 1}`;
            const schedule = field(p, "schedule", "cron");
            const last = field(p, "last_run", "updated_at");
            const enabled = !!p.enabled;
            const lastResult = readableResult(p.last_result ?? p.last_result_preview);
            const lastError = field(p, "last_error");
            const open = openId === id;
            const rowLogs = logs[id] ?? [];
            return (
              <div key={id} className="rounded-xl border border-line bg-surface">
                <div className="flex items-center gap-3 px-3.5 py-3">
                  <span className={cn("h-2 w-2 shrink-0 rounded-full", enabled ? "bg-ac" : "bg-mut")} />
                  <button type="button" onClick={() => toggleOpen(id)} className="min-w-0 flex-1 text-left">
                    <div className="truncate text-[13px]">{name}</div>
                    <div className="truncate text-[11px] text-mut">
                      {schedule ? `по расписанию: ${schedule}` : "ручной запуск"}{last ? ` · посл.: ${last}` : ""}
                    </div>
                  </button>
                  <button
                    type="button"
                    onClick={() => toggleOpen(id)}
                    title="Показать ответ и историю"
                    aria-label="Показать ответ и историю"
                    className="grid h-8 w-8 place-items-center rounded-lg border border-line text-t2 hover:bg-hover hover:text-tx"
                  >
                    <ChevronDown size={15} className={cn("transition-transform", open && "rotate-180")} />
                  </button>
                  <button
                    type="button"
                    onClick={() => toggle(p)}
                    disabled={busy === id}
                    title={enabled ? "Выключить" : "Включить"}
                    aria-label={enabled ? "Выключить" : "Включить"}
                    className={cn("grid h-8 w-8 place-items-center rounded-lg border transition-colors", enabled ? "border-acl bg-acs text-ac" : "border-line text-t2 hover:bg-hover hover:text-tx")}
                  >
                    <Power size={15} />
                  </button>
                  <button
                    type="button"
                    onClick={() => run(id)}
                    disabled={busy === id || !!running[id]}
                    title={running[id] ? "Прогон идёт — ждём ответ агента" : "Запустить сейчас"}
                    className="flex items-center gap-1.5 rounded-lg bg-ac px-3 py-1.5 text-[12px] font-medium text-[#14151b] disabled:opacity-50"
                  >
                    {busy === id || running[id] ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
                    {running[id] ? "Идёт…" : "Запуск"}
                  </button>
                  <button
                    type="button"
                    onClick={() => remove(id, name)}
                    disabled={busy === id}
                    title="Удалить"
                    aria-label="Удалить"
                    className="grid h-8 w-8 place-items-center rounded-lg border border-line text-t2 hover:border-red-500/40 hover:bg-red-500/10 hover:text-red-400 disabled:opacity-50"
                  >
                    <Trash2 size={15} />
                  </button>
                </div>

                {open && (
                  <div className="border-t border-line px-3.5 py-3">
                    <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-mut">Последний ответ</div>
                    {running[id] && (
                      <div className="mb-3 flex items-center gap-2 rounded-lg border border-acl bg-acs px-3 py-2 text-[12px] text-ac">
                        <Loader2 size={13} className="animate-spin" /> Агент выполняет задачу — ответ появится здесь автоматически…
                      </div>
                    )}
                    {lastError ? (
                      <div className="mb-3 whitespace-pre-wrap rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2 text-[12px] text-red-300">{lastError}</div>
                    ) : lastResult ? (
                      <div className="mb-3 max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-bg px-3 py-2 text-[12px] text-tx">{lastResult}</div>
                    ) : (
                      <div className="mb-3 text-[12px] text-mut">Пайплайн ещё не запускался. Нажмите «Запуск», чтобы получить ответ.</div>
                    )}

                    <div className="mb-1 flex items-center gap-2 text-[11px] font-medium uppercase tracking-wide text-mut">
                      История запусков
                      <button type="button" onClick={() => void loadLogs(id)} aria-label="Обновить историю" className="grid h-5 w-5 place-items-center rounded text-mut hover:text-tx">
                        <RefreshCw size={11} />
                      </button>
                    </div>
                    {rowLogs.length === 0 ? (
                      <div className="text-[12px] text-mut">Записей пока нет.</div>
                    ) : (
                      <div className="flex flex-col gap-1.5">
                        {rowLogs.map((log, li) => {
                          const ok = Number(log.ok) === 1 || log.ok === true;
                          const when = field(log, "started_at", "finished_at");
                          const text = field(log, "error") || readableResult(log.result);
                          return (
                            <div key={li} className="rounded-lg border border-line bg-bg px-3 py-2">
                              <div className="flex items-center gap-2 text-[11px] text-mut">
                                <span className={cn("h-1.5 w-1.5 rounded-full", ok ? "bg-ac" : "bg-red-400")} />
                                {ok ? "успешно" : "ошибка"}{when ? ` · ${when}` : ""}
                              </div>
                              {text && <div className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-[12px] text-t2">{text}</div>}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
