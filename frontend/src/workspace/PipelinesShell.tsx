import { useCallback, useEffect, useState } from "react";
import { GitBranch, Loader2, Play, Plus, Power, RefreshCw } from "lucide-react";
import { createPipeline, listPipelines, runPipeline, updatePipeline, type PipelineItem } from "../api/pipelines";
import { cn } from "../ui/cn";

function field(p: PipelineItem, ...keys: string[]): string {
  for (const k of keys) {
    const v = p[k];
    if (typeof v === "string" && v) return v;
    if (typeof v === "number") return String(v);
  }
  return "";
}

/** Pipelines as a separate shell behind the top «Пайплайны» tab (v4). */
export function PipelinesShell() {
  const [items, setItems] = useState<PipelineItem[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ name: "", prompt: "", interval: 60 });

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
    setBusy(id);
    try { await runPipeline(id); setErr(""); load(); }
    catch { setErr("Запуск не удался."); }
    finally { setBusy(null); }
  }

  async function toggle(p: PipelineItem) {
    const id = String(p.id);
    setBusy(id);
    try { await updatePipeline(id, { enabled: !p.enabled }); setErr(""); load(); }
    catch { setErr("Не удалось переключить."); }
    finally { setBusy(null); }
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
            return (
              <div key={id} className="flex items-center gap-3 rounded-xl border border-line bg-surface px-3.5 py-3">
                <span className={cn("h-2 w-2 shrink-0 rounded-full", enabled ? "bg-ac" : "bg-mut")} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px]">{name}</div>
                  <div className="truncate text-[11px] text-mut">
                    {schedule ? `по расписанию: ${schedule}` : "ручной запуск"}{last ? ` · посл.: ${last}` : ""}
                  </div>
                </div>
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
                  disabled={busy === id}
                  className="flex items-center gap-1.5 rounded-lg bg-ac px-3 py-1.5 text-[12px] font-medium text-[#14151b] disabled:opacity-50"
                >
                  {busy === id ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} Запуск
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
