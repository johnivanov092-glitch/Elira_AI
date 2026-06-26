import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  BookMarked, Brain, Check, Cpu, FolderSearch, LayoutDashboard, Loader2, MessageSquare,
  Palette, Play, Plus, RefreshCw, Send, Server, Square, Trash2, UserCog, X,
  type LucideIcon,
} from "lucide-react";
import { listLocalModels } from "../api/chat";
import { request } from "../api/client";
import {
  addRagItem, deleteRagItem, getRagStats, getSshConfig,
  indexProject, listMcpServers, listRagItems, restartMcpServer, setSshConfig,
  startMcpServer, stopMcpServer,
  type McpServerSpec, type RagListItem, type RagStats, type SshConfig,
} from "../api/codeAgent";
import { getDashboardOverview } from "../api/dashboard";
import {
  deleteLibraryFile, listLibraryFilesTyped, toggleLibraryFile, type LibraryFile,
} from "../api/library";
import { listPlugins, reloadPlugins, setPluginEnabled, type PluginItem } from "../api/plugins";
import {
  addSmartMemory, deleteSmartMemory, listSmartMemory, type SmartMemoryItem,
} from "../api/smartMemory";
import {
  getTelegramConfig, listTelegramUsers, startTelegramBot, stopTelegramBot,
  testTelegramBot, toggleTelegramUser, updateTelegramConfig, type TelegramUser,
} from "../api/telegram";
import { cn } from "../ui/cn";
import { getTheme, setTheme, type Theme } from "../ui/theme";

type Section =
  | "model" | "profiles" | "memory" | "library" | "chatmemory"
  | "dashboard" | "telegram" | "sshmcp" | "theme";

const NAV: { id: Section; label: string; icon: LucideIcon }[] = [
  { id: "model", label: "Модель", icon: Cpu },
  { id: "profiles", label: "Профили", icon: UserCog },
  { id: "memory", label: "Память", icon: Brain },
  { id: "library", label: "Библиотека", icon: BookMarked },
  { id: "chatmemory", label: "Память чата", icon: MessageSquare },
  { id: "dashboard", label: "Дашборд", icon: LayoutDashboard },
  { id: "telegram", label: "Telegram", icon: Send },
  { id: "sshmcp", label: "Интеграции", icon: Server },
  { id: "theme", label: "Тема", icon: Palette },
];

function modelName(m: unknown): string {
  if (typeof m === "string") return m;
  if (m && typeof m === "object") {
    const o = m as Record<string, unknown>;
    return String(o.name ?? o.id ?? o.model ?? "");
  }
  return "";
}

export function Settings({ model, onModel, onClose, project }: { model: string; onModel: (m: string) => void; onClose: () => void; project: string }) {
  const [section, setSection] = useState<Section>("model");

  return (
    <div className="fixed inset-0 z-30 flex justify-center bg-black/50 pt-[42px]" onClick={onClose}>
      <div className="flex h-[min(720px,88vh)] w-[min(920px,94vw)] overflow-hidden rounded-xl border border-line bg-card" onClick={(e) => e.stopPropagation()}>
        <nav className="flex w-[170px] shrink-0 flex-col gap-0.5 border-r border-line p-2">
          <div className="px-2.5 pb-1.5 pt-1 text-[13.5px] font-medium">Настройки</div>
          {NAV.map((n) => (
            <button
              key={n.id}
              type="button"
              onClick={() => setSection(n.id)}
              className={cn(
                "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[12.5px] transition-colors",
                section === n.id ? "bg-acs text-ac" : "text-t2 hover:bg-hover hover:text-tx",
              )}
            >
              <n.icon size={15} /> {n.label}
            </button>
          ))}
        </nav>

        <div className="relative min-w-0 flex-1 overflow-auto p-4">
          <button type="button" onClick={onClose} aria-label="Закрыть" className="absolute right-3 top-3 grid h-6 w-6 place-items-center rounded-md border border-line text-t2 hover:bg-hover hover:text-tx">
            <X size={14} />
          </button>
          {section === "model" && <ModelSection model={model} onModel={onModel} />}
          {section === "profiles" && <ProfilesSection />}
          {section === "memory" && <MemorySection project={project} />}
          {section === "library" && <LibrarySection />}
          {section === "chatmemory" && <ChatMemorySection />}
          {section === "dashboard" && <Lazy load={getDashboardOverview} title="Дашборд" />}
          {section === "telegram" && <TelegramSection />}
          {section === "sshmcp" && <SshMcpSection />}
          {section === "theme" && <ThemeSection />}
        </div>
      </div>
    </div>
  );
}

function ModelSection({ model, onModel }: { model: string; onModel: (m: string) => void }) {
  const [models, setModels] = useState<string[]>([]);
  useEffect(() => {
    let alive = true;
    listLocalModels()
      .then((r) => { if (alive) setModels(Array.from(new Set((r.models ?? []).map(modelName).filter(Boolean)))); })
      .catch(() => { /* offline */ });
    return () => { alive = false; };
  }, []);
  const options = ["auto", ...models.filter((m) => m !== "auto")];
  return (
    <Wrap title="Модель и провайдер">
      <div className="flex flex-col gap-1">
        {options.map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => onModel(m)}
            className={cn(
              "flex items-center gap-2.5 rounded-lg border px-3 py-2 text-left text-[13px] transition-colors",
              m === model ? "border-acl bg-acs text-ac" : "border-line text-t2 hover:bg-hover hover:text-tx",
            )}
          >
            <span className="flex-1 font-mono">{m === "auto" ? "auto (оркестрация)" : m}</span>
            {m === model && <Check size={15} />}
          </button>
        ))}
        {options.length === 1 && <Note>Сервер недоступен — список моделей пуст.</Note>}
      </div>
    </Wrap>
  );
}

function MemorySection({ project }: { project: string }) {
  const [stats, setStats] = useState<RagStats | null>(null);
  const [items, setItems] = useState<RagListItem[] | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const reload = useCallback(() => {
    getRagStats().then(setStats).catch(() => setStats(null));
    listRagItems(100).then((r) => setItems(r.items ?? [])).catch(() => setItems([]));
  }, []);
  useEffect(() => { reload(); }, [reload]);

  async function add() {
    const t = text.trim();
    if (!t || busy) return;
    setBusy(true);
    try { await addRagItem(t); setText(""); reload(); } catch { setMsg("Не удалось добавить."); } finally { setBusy(false); }
  }
  async function remove(id: number) {
    try { await deleteRagItem(id); reload(); } catch { /* ignore */ }
  }
  async function indexNow() {
    if (!project || busy) return;
    setBusy(true);
    setMsg("Индексирую проект…");
    try {
      const r = await indexProject({ projectRoot: project });
      setMsg(r.ok ? `Готово: ${r.files_processed ?? 0} файлов, ${r.chunks_indexed ?? 0} фрагментов.` : (r.error || "Ошибка индексации."));
      reload();
    } catch { setMsg("Ошибка индексации."); } finally { setBusy(false); }
  }

  return (
    <Wrap title={`Память (RAG)${stats ? ` · ${stats.total}` : ""}`}>
      <div className="mb-3 flex items-center gap-2 text-[12px] text-t2">
        <span className="flex-1">{stats ? `${stats.with_embeddings}/${stats.total} с эмбеддингами${stats.model ? ` · ${stats.model}` : ""}` : "загрузка…"}</span>
        <button
          type="button"
          onClick={indexNow}
          disabled={!project || busy}
          title={project ? "Проиндексировать текущий проект в RAG" : "Сначала выбери проект"}
          className={cn("flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[12px] transition-colors", project && !busy ? "border-line text-t2 hover:bg-hover hover:text-tx" : "cursor-not-allowed border-line text-mut")}
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : <FolderSearch size={13} />} Индексировать проект
        </button>
      </div>

      <div className="mb-3 flex gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void add(); }}
          placeholder="Добавить факт в память…"
          className="flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-[12.5px] text-tx outline-none placeholder:text-mut focus:border-acl"
        />
        <button type="button" onClick={add} disabled={busy || !text.trim()} aria-label="Добавить" className={cn("grid h-[34px] w-[34px] shrink-0 place-items-center rounded-lg text-[#14151b]", text.trim() && !busy ? "bg-ac" : "cursor-not-allowed bg-ac/40")}>
          <Plus size={16} />
        </button>
      </div>

      {msg && <Note>{msg}</Note>}

      {items === null ? (
        <Loading />
      ) : items.length === 0 ? (
        <Note>Память пуста. Добавь факт или проиндексируй проект.</Note>
      ) : (
        <div className="mt-2 flex flex-col gap-1.5">
          {items.map((it) => (
            <div key={it.id} className="group flex items-start gap-2 rounded-lg border border-line px-3 py-2 text-[12.5px] text-t2">
              <span className="mt-0.5 shrink-0 rounded border border-line px-1.5 text-[10px] text-mut">{it.category}</span>
              <span className="flex-1 break-words">{String(it.text ?? "").slice(0, 240)}</span>
              <button type="button" onClick={() => remove(it.id)} aria-label="Удалить" className="grid h-5 w-5 shrink-0 place-items-center rounded text-mut opacity-0 transition-opacity hover:text-tx group-hover:opacity-100">
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </div>
      )}
    </Wrap>
  );
}

// Number of freshest active files build_library_context() actually injects.
// Keep in sync with backend build_library_context(max_files=...).
const LIB_CONTEXT_LIMIT = 3;

function fmtSize(bytes: number): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} КБ`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
}

function LibrarySection() {
  const [items, setItems] = useState<LibraryFile[] | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const reload = useCallback(() => {
    listLibraryFilesTyped().then(setItems).catch(() => setItems([]));
  }, []);
  useEffect(() => { reload(); }, [reload]);

  async function toggle(f: LibraryFile) {
    setBusy(f.id);
    try { await toggleLibraryFile(f.id, !f.active); reload(); } catch { /* ignore */ } finally { setBusy(null); }
  }
  async function remove(id: number) {
    setBusy(id);
    try { await deleteLibraryFile(id); reload(); } catch { /* ignore */ } finally { setBusy(null); }
  }

  // The first LIB_CONTEXT_LIMIT *active* files (in freshest-first order) are the
  // ones the backend actually injects; older active files stay bookmarked but
  // fall out of the window. Mark exactly those.
  let injected = 0;
  const inContext = new Set<number>();
  for (const f of items ?? []) {
    if (f.active && injected < LIB_CONTEXT_LIMIT) { inContext.add(f.id); injected += 1; }
  }

  return (
    <Wrap title={`Библиотека${items ? ` · ${items.length}` : ""}`}>
      <Note>Набукмаренные файлы. Активные подмешиваются в контекст код-агента — реально попадают только {LIB_CONTEXT_LIMIT} самых свежих (бейдж «в контексте»), остальные ждут очереди. Файлы добавляются из чата (скрепка).</Note>
      {items === null ? (
        <Loading />
      ) : items.length === 0 ? (
        <Note>Пусто. Прикрепи файл в чате код-агента — он сохранится сюда.</Note>
      ) : (
        <div className="mt-2 flex flex-col gap-1.5">
          {items.map((f) => (
            <div key={f.id} className="group flex items-center gap-2.5 rounded-lg border border-line px-3 py-2 text-[12.5px]">
              <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", f.active ? "bg-ac" : "bg-mut")} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-tx">{f.name}</span>
                <span className="text-[10.5px] text-mut">{[f.type, fmtSize(f.size)].filter(Boolean).join(" · ")}</span>
              </span>
              {inContext.has(f.id) && <span className="shrink-0 rounded border border-acl bg-acs px-1.5 py-0.5 text-[10px] text-ac">в контексте</span>}
              <McpBtn onClick={() => toggle(f)} busy={busy === f.id} label={f.active ? "Убрать из контекста" : "Добавить в контекст"}>
                {f.active ? <Square size={13} /> : <Play size={13} />}
              </McpBtn>
              <button type="button" onClick={() => remove(f.id)} disabled={busy === f.id} aria-label="Удалить" className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-mut opacity-0 transition-opacity hover:text-tx group-hover:opacity-100 disabled:opacity-50">
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </div>
      )}
    </Wrap>
  );
}

function ChatMemorySection() {
  const [items, setItems] = useState<SmartMemoryItem[] | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    listSmartMemory(100).then(setItems).catch(() => setItems([]));
  }, []);
  useEffect(() => { reload(); }, [reload]);

  async function add() {
    const t = text.trim();
    if (!t || busy) return;
    setBusy(true);
    try { await addSmartMemory({ text: t }); setText(""); reload(); } catch { /* ignore */ } finally { setBusy(false); }
  }
  async function remove(id: SmartMemoryItem["id"]) {
    if (id === undefined) return;
    try { await deleteSmartMemory(id); reload(); } catch { /* ignore */ }
  }

  return (
    <Wrap title={`Память чата${items ? ` · ${items.length}` : ""}`}>
      <Note>Факты код-чата (chat-agent): что-то агент запоминает сам из разговора, что-то можно добавить вручную. Используются как краткосрочная память диалога — отдельно от RAG.</Note>
      <div className="my-3 flex gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void add(); }}
          placeholder="Добавить факт в память чата…"
          className="flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-[12.5px] text-tx outline-none placeholder:text-mut focus:border-acl"
        />
        <button type="button" onClick={add} disabled={busy || !text.trim()} aria-label="Добавить" className={cn("grid h-[34px] w-[34px] shrink-0 place-items-center rounded-lg text-[#14151b]", text.trim() && !busy ? "bg-ac" : "cursor-not-allowed bg-ac/40")}>
          <Plus size={16} />
        </button>
      </div>
      {items === null ? (
        <Loading />
      ) : items.length === 0 ? (
        <Note>Пока пусто. Память наполняется по ходу диалога с код-агентом.</Note>
      ) : (
        <div className="mt-2 flex flex-col gap-1.5">
          {items.map((it, i) => (
            <div key={it.id ?? i} className="group flex items-start gap-2 rounded-lg border border-line px-3 py-2 text-[12.5px] text-t2">
              {it.category && <span className="mt-0.5 shrink-0 rounded border border-line px-1.5 text-[10px] text-mut">{it.category}</span>}
              <span className="flex-1 break-words">{String(it.text ?? "").slice(0, 240)}</span>
              {it.source && <span className="mt-0.5 shrink-0 text-[10px] text-mut">{it.source}</span>}
              <button type="button" onClick={() => remove(it.id)} aria-label="Удалить" className="grid h-5 w-5 shrink-0 place-items-center rounded text-mut opacity-0 transition-opacity hover:text-tx group-hover:opacity-100">
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </div>
      )}
    </Wrap>
  );
}

function Lazy({ load, title }: { load: () => Promise<unknown>; title: string }) {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    let alive = true;
    load().then((r) => { if (alive) setData((r ?? {}) as Record<string, unknown>); }).catch(() => { if (alive) setData({}); });
    return () => { alive = false; };
  }, [load]);
  return (
    <Wrap title={title}>
      {data === null ? <Loading /> : <KV data={data} />}
    </Wrap>
  );
}

// Human labels for the top-level dashboard groups; anything unmapped falls back
// to its raw key so a new backend section still shows up.
const DASH_GROUP_LABELS: Record<string, string> = {
  stats: "Статистика",
  projectBrainStatus: "Память проекта",
  personaStatus: "Персона",
  runtimeStatus: "Среда выполнения",
  agentOsHealth: "Agent OS · здоровье",
  agentOsDashboard: "Agent OS · прогоны",
  agentOsLimits: "Agent OS · лимиты",
};

function fmtVal(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "да" : "нет";
  if (Array.isArray(v)) return v.length ? `${v.length} шт.` : "—";
  return String(v);
}

/** One label/value row in a dashboard card. */
function Row({ k, v }: { k: string; v: unknown }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-line px-3 py-1.5 text-[12.5px]">
      <span className="text-mut">{k}</span>
      <span className="max-w-[55%] truncate font-mono text-t2">{fmtVal(v)}</span>
    </div>
  );
}

const isScalar = (v: unknown) =>
  v !== null && (["string", "number", "boolean"].includes(typeof v) || Array.isArray(v));

/** Flatten one group's fields into rows, descending one level into nested
 *  status maps (e.g. runtimeStatus.api_keys_present.tavily) so their flags —
 *  Tavily and the like — still surface instead of being silently dropped. */
function groupRows(data: Record<string, unknown>): { k: string; v: unknown }[] {
  const rows: { k: string; v: unknown }[] = [];
  for (const [k, v] of Object.entries(data)) {
    if (isScalar(v)) {
      rows.push({ k, v });
    } else if (v && typeof v === "object") {
      for (const [ck, cv] of Object.entries(v as Record<string, unknown>)) {
        if (isScalar(cv)) rows.push({ k: `${k} · ${ck}`, v: cv });
      }
    }
  }
  return rows;
}

/** A titled card listing the (flattened) fields of one nested group. */
function Group({ title, data }: { title: string; data: Record<string, unknown> }) {
  const rows = groupRows(data);
  if (rows.length === 0) return null;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="px-0.5 text-[11.5px] font-medium uppercase tracking-wide text-mut">{title}</div>
      {rows.map(({ k, v }) => <Row key={k} k={k} v={v} />)}
    </div>
  );
}

function KV({ data }: { data: Record<string, unknown> }) {
  const errors = Array.isArray(data.errors) ? (data.errors as string[]) : [];
  // Top-level dashboard payload is a map of nested status objects — render each
  // as its own card. Scalar top-level fields (if any) collapse into one group.
  const groups: { key: string; obj: Record<string, unknown> }[] = [];
  const flat: Record<string, unknown> = {};
  for (const [key, val] of Object.entries(data)) {
    if (key === "errors") continue;
    if (val && typeof val === "object" && !Array.isArray(val)) {
      groups.push({ key, obj: val as Record<string, unknown> });
    } else {
      flat[key] = val;
    }
  }

  const cards = groups
    .map(({ key, obj }) => <Group key={key} title={DASH_GROUP_LABELS[key] ?? key} data={obj} />)
    .filter(Boolean);
  if (Object.keys(flat).length) cards.unshift(<Group key="__flat" title="Общее" data={flat} />);

  if (cards.length === 0 && errors.length === 0) return <Note>Нет данных для отображения.</Note>;
  return (
    <div className="flex flex-col gap-4">
      {cards}
      {errors.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <div className="px-0.5 text-[11.5px] font-medium uppercase tracking-wide text-mut">Недоступно</div>
          {errors.map((e, i) => (
            <div key={i} className="rounded-lg border border-line px-3 py-1.5 text-[12px] text-mut">{e}</div>
          ))}
        </div>
      )}
    </div>
  );
}

function ThemeSection() {
  const [t, setT] = useState<Theme>(getTheme());
  function pick(v: Theme) { setTheme(v); setT(v); }
  return (
    <Wrap title="Тема">
      <div className="flex gap-2">
        {(["dark", "cursor"] as Theme[]).map((v) => (
          <button
            key={v}
            type="button"
            onClick={() => pick(v)}
            className={cn(
              "flex-1 rounded-lg border px-3 py-2.5 text-[13px] transition-colors",
              t === v ? "border-acl bg-acs text-ac" : "border-line text-t2 hover:bg-hover hover:text-tx",
            )}
          >
            {v === "dark" ? "Тёмная" : "Cursor"}
          </button>
        ))}
      </div>
    </Wrap>
  );
}

type ProfileInfo = { name: string; is_default?: boolean; icon?: string; tags?: string[]; short?: string };

function ProfilesSection() {
  const [profiles, setProfiles] = useState<ProfileInfo[] | null>(null);
  const [active, setActive] = useState("");
  const [settings, setSettings] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    request<{ profiles?: ProfileInfo[] }>("/api/profiles")
      .then((r) => { if (alive) setProfiles(Array.isArray(r.profiles) ? r.profiles : []); })
      .catch(() => { if (alive) setProfiles([]); });
    request<Record<string, unknown>>("/api/elira/settings")
      .then((s) => { if (alive) { setSettings(s); setActive(String(s.agent_profile ?? "")); } })
      .catch(() => { /* offline */ });
    return () => { alive = false; };
  }, []);

  async function pick(name: string) {
    if (busy || !settings) return;
    setBusy(true);
    try {
      await request("/api/elira/settings", { method: "PUT", body: { ...settings, agent_profile: name } });
      setActive(name);
    } catch { /* ignore */ } finally { setBusy(false); }
  }

  return (
    <Wrap title="Профили агента">
      <Note>Профиль задаёт персону и стиль агента (системный промпт + маршрутизацию). Активный применяется к новым запросам.</Note>
      {profiles === null ? (
        <Loading />
      ) : profiles.length === 0 ? (
        <Note>Профили недоступны (сервер не отвечает).</Note>
      ) : (
        <div className="mt-2 flex flex-col gap-1.5">
          {profiles.map((p) => (
            <button
              key={p.name}
              type="button"
              onClick={() => pick(p.name)}
              disabled={busy}
              className={cn(
                "flex items-start gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors disabled:opacity-60",
                p.name === active ? "border-acl bg-acs" : "border-line hover:bg-hover",
              )}
            >
              <span className="mt-0.5 w-4 shrink-0 text-center text-[14px]">{p.icon || "•"}</span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2 text-[13px] text-tx">
                  {p.name}
                  {p.name === active && <Check size={13} className="text-ac" />}
                  {p.is_default && p.name !== active && <span className="text-[10px] text-mut">по умолчанию</span>}
                </span>
                {p.short && <span className="block text-[11.5px] text-mut">{p.short}</span>}
              </span>
            </button>
          ))}
        </div>
      )}
    </Wrap>
  );
}

function TelegramSection() {
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null);
  const [users, setUsers] = useState<TelegramUser[] | null>(null);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const reload = useCallback(() => {
    getTelegramConfig().then((c) => setCfg(c as Record<string, unknown>)).catch(() => setCfg({}));
    listTelegramUsers().then((r) => setUsers(Array.isArray(r.users) ? (r.users as TelegramUser[]) : [])).catch(() => setUsers([]));
  }, []);
  useEffect(() => { reload(); }, [reload]);

  const running = !!cfg?.running;
  const hasToken = !!cfg?.has_token;

  async function act(fn: () => Promise<unknown>, okMsg: string) {
    setBusy(true); setMsg("");
    try { await fn(); if (okMsg) setMsg(okMsg); reload(); }
    catch (e) { setMsg(String((e as Error).message)); }
    finally { setBusy(false); }
  }
  async function saveToken() {
    const t = token.trim();
    if (!t) return;
    await act(() => updateTelegramConfig({ bot_token: t }), "Токен сохранён.");
    setToken("");
  }
  function toggleUser(u: TelegramUser) {
    const chatId = (u.chat_id ?? u.id) as string | number | undefined;
    const allowed = (u.allowed ?? u.is_allowed) === false; // flip
    void act(() => toggleTelegramUser({ chat_id: chatId, allowed }), "");
  }

  return (
    <Wrap title="Telegram-бот">
      <div className="mb-3 flex items-center gap-2">
        <span className={cn("h-2 w-2 shrink-0 rounded-full", running ? "bg-ac" : "bg-mut")} />
        <span className="text-[12.5px] text-t2">{running ? "запущен" : "остановлен"}{hasToken ? "" : " · нет токена"}</span>
        <div className="ml-auto flex gap-1.5">
          {running ? (
            <button type="button" onClick={() => act(stopTelegramBot, "Остановлен.")} disabled={busy} className="rounded-lg border border-line px-2.5 py-1.5 text-[12px] text-t2 hover:bg-hover hover:text-tx disabled:opacity-50">Стоп</button>
          ) : (
            <button type="button" onClick={() => act(startTelegramBot, "Запущен.")} disabled={busy || !hasToken} className="rounded-lg bg-ac px-2.5 py-1.5 text-[12px] font-medium text-[#14151b] disabled:opacity-50">Запустить</button>
          )}
          <button type="button" onClick={() => act(testTelegramBot, "Тест отправлен.")} disabled={busy} className="rounded-lg border border-line px-2.5 py-1.5 text-[12px] text-t2 hover:bg-hover hover:text-tx disabled:opacity-50">Тест</button>
        </div>
      </div>

      <div className="mb-1 text-[11.5px] text-mut">Токен бота {hasToken ? "(установлен — вставь новый, чтобы заменить)" : "— получи у @BotFather"}</div>
      <div className="mb-3 flex gap-2">
        <input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="123456:ABC-DEF… от @BotFather" className="flex-1 rounded-lg border border-line bg-surface px-3 py-2 font-mono text-[12px] text-tx outline-none placeholder:text-mut focus:border-acl" />
        <button type="button" onClick={saveToken} disabled={busy || !token.trim()} className="rounded-lg bg-ac px-3 py-1.5 text-[12.5px] font-medium text-[#14151b] disabled:opacity-50">Сохранить</button>
      </div>

      {msg && <Note>{msg}</Note>}

      <div className="mb-1.5 mt-3 text-[11.5px] font-medium text-t2">Пользователи (whitelist)</div>
      {users === null ? (
        <Loading />
      ) : users.length === 0 ? (
        <Note>Пока нет. Появятся после первого сообщения боту — затем можно блокировать/разрешать.</Note>
      ) : (
        <div className="flex flex-col gap-1.5">
          {users.map((u, i) => {
            const name = String(u.username ?? u.name ?? u.chat_id ?? u.id ?? `user ${i + 1}`);
            const allowed = (u.allowed ?? u.is_allowed) !== false;
            return (
              <div key={i} className="flex items-center gap-2.5 rounded-lg border border-line px-3 py-2 text-[12.5px]">
                <span className="min-w-0 flex-1 truncate text-tx">{name}</span>
                <span className={cn("text-[11px]", allowed ? "text-ac" : "text-mut")}>{allowed ? "разрешён" : "заблокирован"}</span>
                <McpBtn onClick={() => toggleUser(u)} busy={busy} label={allowed ? "Заблокировать" : "Разрешить"}>
                  {allowed ? <Square size={13} /> : <Play size={13} />}
                </McpBtn>
              </div>
            );
          })}
        </div>
      )}

      <div className="mt-3"><Note>Создай бота у @BotFather → вставь токен → «Запустить». В режиме whitelist бот отвечает только разрешённым; блокируй/разрешай кнопкой справа.</Note></div>
    </Wrap>
  );
}

function SshMcpSection() {
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

function McpBtn({ onClick, busy, label, children }: { onClick: () => void; busy: boolean; label: string; children: ReactNode }) {
  return (
    <button type="button" onClick={onClick} disabled={busy} aria-label={label} title={label} className="grid h-6 w-6 shrink-0 place-items-center rounded-md border border-line text-t2 transition-colors hover:bg-hover hover:text-tx disabled:opacity-50">
      {busy ? <Loader2 size={12} className="animate-spin" /> : children}
    </button>
  );
}

function Wrap({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <div className="mb-3 pr-8 text-[13px] font-medium">{title}</div>
      {children}
    </div>
  );
}

function Note({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-line px-3 py-2.5 text-[12.5px] text-mut">{children}</div>;
}

function Loading() {
  return <div className="flex items-center gap-2 px-1 py-2 text-[12.5px] text-mut"><Loader2 size={14} className="animate-spin" /> загрузка…</div>;
}
